"""
Advanced Web UI with Telegram Mini App Support
"""
import os
import sys
import json
import logging
from pathlib import Path

# Add parent directory to path so imports work when running from different directories
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from flask_session import Session
from functools import wraps
from datetime import datetime, timedelta
import hmac
import hashlib
from data.database import db
from chains.solana.wallet import SolanaWallet
from data.analytics import analytics
from chains.solana.vanity_wallet import vanity_generator
from chains.solana.spl_tokens import token_manager
from wallet.encryption import encryption
from trading.smart_trader import smart_trader
from trading.copy_trader import copy_trader
from utils.notifications import notification_engine

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='../templates', static_folder='../static')
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY') or os.urandom(32)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

CORS(app)
Session(app)

wallet_manager = SolanaWallet()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')


def verify_telegram_auth(data: dict) -> bool:
    """
    Verify Telegram Mini App authentication data
    Using the method from Telegram: https://core.telegram.org/bots/webapps#validating-data-received-from-the-web-app
    """
    if not TELEGRAM_BOT_TOKEN:
        return False
    
    # Get hash from data
    check_hash = data.pop('hash', '')
    if not check_hash:
        return False
    
    # Build data check string
    data_check_list = [f"{k}={v}" for k, v in sorted(data.items())]
    data_check_string = "\n".join(data_check_list)
    
    # Create secret key
    secret_key = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
    
    # Generate hash
    hash_result = hmac.new(secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()
    
    return hash_result == check_hash


def require_auth(f):
    """Decorator for routes requiring authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Development mode - skip auth if X-Dev-Mode header is present
        if request.headers.get('X-Dev-Mode') == 'true':
            return f(*args, **kwargs)
        
        # Check session or Telegram Mini App auth
        if 'user_id' in session:
            return f(*args, **kwargs)
        
        # Try Telegram Mini App auth
        auth_data = request.headers.get('X-Telegram-Auth-Data')
        if auth_data:
            try:
                data = json.loads(auth_data)
                if verify_telegram_auth(data):
                    telegram_id = data.get('user', {}).get('id')
                    user = db.get_user(telegram_id)
                    if user:
                        session['user_id'] = user['user_id']
                        session['telegram_id'] = telegram_id
                        return f(*args, **kwargs)
            except:
                pass
        
        return jsonify({'error': 'Unauthorized'}), 401
    
    return decorated


# ────────────────────────────────────────────────────────────────────────────────
# AUTH ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/api/auth/telegram', methods=['POST'])
def auth_telegram():
    """Authenticate user via Telegram Mini App"""
    data = request.json
    
    # Verify Telegram auth
    if not verify_telegram_auth(data):
        return jsonify({'error': 'Invalid Telegram authentication'}), 401
    
    telegram_id = data['user']['id']
    first_name = data['user'].get('first_name', '')
    last_name = data['user'].get('last_name', '')
    username = data['user'].get('username', '')
    
    # Get or create user
    user = db.get_user(telegram_id)
    if not user:
        user = db.create_user(
            telegram_id=telegram_id,
            first_name=first_name,
            last_name=last_name,
            username=username
        )
    
    session['user_id'] = user['user_id']
    session['telegram_id'] = telegram_id
    session.permanent = True
    
    return jsonify({
        'success': True,
        'user_id': user['user_id'],
        'wallet': user.get('wallet_address'),
        'is_new': not user.get('setup_complete')
    })


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Logout current user"""
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Get current authentication status"""
    if 'user_id' in session:
        user = db.get_user(session.get('telegram_id'))
        return jsonify({
            'authenticated': True,
            'user_id': session['user_id'],
            'username': user.get('username') if user else None
        })
    return jsonify({'authenticated': False})


# ────────────────────────────────────────────────────────────────────────────────
# DASHBOARD ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/api/dashboard', methods=['GET'])
@require_auth
def get_dashboard():
    """Get dashboard overview data"""
    user_id = session['user_id']
    telegram_id = session.get('telegram_id')
    
    user = db.get_user(telegram_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    try:
        balance = wallet_manager.get_balance(user['wallet_address']) or 0
        stats = db.get_user_stats(user_id)
        metrics = analytics.calculate_performance_metrics(user_id)
        
        return jsonify({
            'user': {
                'user_id': user_id,
                'username': user.get('username'),
                'wallet': user['wallet_address'],
                'balance': balance
            },
            'stats': {
                'total_trades': stats.get('total_trades', 0),
                'winning_trades': stats.get('winning_trades', 0),
                'total_profit': stats.get('total_profit', 0),
                'win_rate': stats.get('win_rate', 0)
            },
            'metrics': metrics,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return jsonify({'error': str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────────
# TRADE ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/api/trades', methods=['GET'])
@require_auth
def get_trades():
    """Get recent trades"""
    limit = request.args.get('limit', 20, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    trades = db.get_user_trades(session['user_id'], limit=limit, offset=offset)
    total = db.count_user_trades(session['user_id'])
    
    return jsonify({
        'trades': trades,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@app.route('/api/trades/<trade_id>', methods=['GET'])
@require_auth
def get_trade_detail(trade_id):
    """Get detailed trade information"""
    trade = db.get_trade(trade_id)
    
    if not trade or trade['user_id'] != session['user_id']:
        return jsonify({'error': 'Trade not found'}), 404
    
    return jsonify(trade)


# ────────────────────────────────────────────────────────────────────────────────
# WALLET ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/api/wallet', methods=['GET'])
@require_auth
def get_wallet():
    """Get user wallet information"""
    user = db.get_user(session.get('telegram_id'))
    
    try:
        balance = wallet_manager.get_balance(user['wallet_address'])
        
        return jsonify({
            'address': user['wallet_address'],
            'balance': balance,
            'token_accounts': wallet_manager.get_token_accounts(user['wallet_address'])
        })
    except Exception as e:
        logger.error(f"Wallet fetch error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/wallet/tokens', methods=['GET'])
@require_auth
def get_wallet_tokens():
    """Get all tokens in wallet"""
    user = db.get_user(session.get('telegram_id'))
    
    try:
        tokens = wallet_manager.get_token_holdings(user['wallet_address'])
        return jsonify({'tokens': tokens})
    except Exception as e:
        logger.error(f"Token fetch error: {e}")
        return jsonify({'error': str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────────
# COPY TRADING ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/api/copy-trading/whales', methods=['GET'])
@require_auth
def get_whales():
    """Get list of whale traders"""
    try:
        whales = copy_trader.get_top_whales(limit=10)
        return jsonify({'whales': whales})
    except Exception as e:
        logger.error(f"Whale fetch error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/copy-trading/watch', methods=['POST'])
@require_auth
def watch_whale():
    """Start watching a whale"""
    data = request.json
    wallet_address = data.get('wallet_address')
    copy_scale = data.get('copy_scale', 1.0)

    if not wallet_address:
        return jsonify({'error': 'Wallet address required'}), 400

    try:
        result = db.add_watched_wallet(
            session['telegram_id'],
            wallet_address,
            copy_scale=copy_scale
        )
        return jsonify({'success': result})
    except Exception as e:
        logger.error(f"Watch error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/copy-trading/watched', methods=['GET'])
@require_auth
def get_watched_wallets():
    """Get watched wallets"""
    try:
        watched = db.get_watched_wallets(session['telegram_id'])
        return jsonify({'watched': watched})
    except Exception as e:
        logger.error(f"Watched wallets error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/copy-trading/unwatch/<wallet_id>', methods=['DELETE'])
@require_auth
def unwatch_wallet(wallet_id):
    """Stop watching a wallet"""
    try:
        db.remove_watched_wallet(session['user_id'], wallet_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Unwatch error: {e}")
        return jsonify({'error': str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────────
# SMART TRADING ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/api/trading/analyze', methods=['POST'])
@require_auth
def analyze_token():
    """Analyze a token"""
    data = request.json
    token_address = data.get('token_address')
    
    if not token_address:
        return jsonify({'error': 'Token address required'}), 400
    
    try:
        analysis = smart_trader.analyze_token(token_address)
        return jsonify(analysis)
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/trading/swap', methods=['POST'])
@require_auth
def create_swap():
    """Create a swap order"""
    data = request.json
    
    required = ['input_mint', 'output_mint', 'amount']
    if not all(k in data for k in required):
        return jsonify({'error': 'Missing required fields'}), 400
    
    try:
        user = db.get_user(session.get('telegram_id'))
        swap_result = wallet_manager.create_swap(
            user_id=session['user_id'],
            input_mint=data['input_mint'],
            output_mint=data['output_mint'],
            amount=data['amount'],
            slippage=data.get('slippage', 2.0),
            priority_fee=data.get('priority_fee', 0)
        )
        return jsonify(swap_result)
    except Exception as e:
        logger.error(f"Swap error: {e}")
        return jsonify({'error': str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────────
# SETTINGS ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/api/settings', methods=['GET'])
@require_auth
def get_settings():
    """Get user settings"""
    settings = db.get_user_settings(session['user_id'])
    return jsonify(settings or {})


@app.route('/api/settings', methods=['PUT'])
@require_auth
def update_settings():
    """Update user settings"""
    data = request.json
    
    try:
        db.update_user_settings(session['user_id'], data)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Settings update error: {e}")
        return jsonify({'error': str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────────
# PAGE ROUTES
# ────────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Main app page"""
    return render_template('index.html')


@app.route('/mini-app')
def mini_app():
    """Telegram Mini App view"""
    return render_template('mini-app.html')


@app.route('/dashboard')
def dashboard_page():
    """Dashboard page (served as index for SPA)"""
    return render_template('index.html')


@app.route('/trading')
def trading_page():
    """Trading page (served as index for SPA)"""
    return render_template('index.html')


@app.route('/copy-trading')
def copy_trading_page():
    """Copy trading page (served as index for SPA)"""
    return render_template('index.html')


@app.route('/wallets')
def wallets_page():
    """Wallets page (served as index for SPA)"""
    return render_template('index.html')


@app.route('/settings')
def settings_page():
    """Settings page (served as index for SPA)"""
    return render_template('index.html')


# ────────────────────────────────────────────────────────────────────────────────
# ERROR HANDLERS
# ────────────────────────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    """404 handler"""
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(e):
    """500 handler"""
    logger.error(f"Server error: {e}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    debug = os.getenv('FLASK_ENV') == 'development'
    port = int(os.getenv('WEB_UI_PORT', 3000))
    app.run(debug=debug, host='0.0.0.0', port=port)
