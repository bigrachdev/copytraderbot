"""
Database layer for managing users, wallets, and trade history
"""
import sqlite3
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from config import DB_PATH

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                wallet_address TEXT,
                encrypted_private_key TEXT,
                public_key TEXT,
                is_active BOOLEAN DEFAULT 1,
                is_admin BOOLEAN DEFAULT 0,
                trade_percent REAL DEFAULT 20.0,
                trading_wallet_address TEXT,
                encrypted_trading_key TEXT,
                use_separate_trading_wallet BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Watched wallets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watched_wallets (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                alias TEXT,
                copy_scale REAL DEFAULT 1.0,
                copy_delay_seconds INTEGER DEFAULT 0,
                max_loss_percent REAL DEFAULT 20.0,
                weight REAL DEFAULT 1.0,
                is_active BOOLEAN DEFAULT 1,
                is_paused BOOLEAN DEFAULT 0,
                pause_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        # Migrate existing watched_wallets rows that lack new columns
        for col, definition in [
            ('copy_delay_seconds', 'INTEGER DEFAULT 0'),
            ('max_loss_percent', 'REAL DEFAULT 20.0'),
            ('weight', 'REAL DEFAULT 1.0'),
            ('is_paused', 'BOOLEAN DEFAULT 0'),
            ('pause_reason', 'TEXT'),
        ]:
            try:
                cursor.execute(f'ALTER TABLE watched_wallets ADD COLUMN {col} {definition}')
            except Exception:
                pass  # column already exists

        # Copy trade performance tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS copy_performance (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                watched_wallet TEXT NOT NULL,
                token_address TEXT NOT NULL,
                whale_entry_price REAL,
                whale_exit_price REAL,
                whale_profit_percent REAL,
                user_entry_price REAL,
                user_exit_price REAL,
                user_profit_percent REAL,
                copy_scale REAL,
                sol_spent REAL,
                sol_received REAL,
                status TEXT DEFAULT 'open',
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Trade history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                watched_wallet TEXT,
                input_mint TEXT,
                output_mint TEXT,
                input_amount REAL,
                output_amount REAL,
                dex TEXT,
                price REAL,
                slippage REAL,
                is_copy BOOLEAN DEFAULT 0,
                tx_hash TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Pending trades (for copy trading)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_trades (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                watched_wallet TEXT,
                signature TEXT,
                input_mint TEXT,
                output_mint TEXT,
                input_amount REAL,
                dex TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Smart trades (for token analyzer and smart trader)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS smart_trades (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_address TEXT NOT NULL,
                token_amount REAL,
                sol_spent REAL,
                entry_price REAL,
                dex TEXT,
                entry_tx TEXT,
                exit_tx TEXT,
                sol_received REAL,
                is_closed BOOLEAN DEFAULT 0,
                profit_percent REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Risk management orders (stop-loss, take-profit, trailing stop)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_address TEXT,
                order_type TEXT,
                entry_price REAL,
                trigger_price REAL,
                original_amount REAL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                triggered_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Vanity wallets created by users
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vanity_wallets (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                address TEXT UNIQUE,
                prefix TEXT,
                difficulty INTEGER,
                encrypted_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized")
    
    # User operations
    def add_user(self, telegram_id: int, wallet_address: str, 
                 encrypted_key: str = None, public_key: str = None) -> bool:
        """Add new user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (telegram_id, wallet_address, encrypted_private_key, public_key)
                VALUES (?, ?, ?, ?)
            ''', (telegram_id, wallet_address, encrypted_key, public_key))
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"User {telegram_id} already exists")
            return False
    
    def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Get user by telegram ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    
    def update_user_active_time(self, telegram_id: int):
        """Update last active time"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE telegram_id = ?
        ''', (telegram_id,))
        conn.commit()
        conn.close()
    
    # Watched wallet operations
    def add_watched_wallet(self, user_id: int, wallet_address: str,
                           alias: str = None, copy_scale: float = 1.0,
                           copy_delay_seconds: int = 0,
                           max_loss_percent: float = 20.0,
                           weight: float = 1.0) -> bool:
        """Add watched wallet for copy trading"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO watched_wallets
                (user_id, wallet_address, alias, copy_scale, copy_delay_seconds, max_loss_percent, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, wallet_address, alias, copy_scale, copy_delay_seconds, max_loss_percent, weight))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error adding watched wallet: {e}")
            return False

    def update_watched_wallet_config(self, wallet_id: int, copy_scale: float = None,
                                     copy_delay_seconds: int = None,
                                     max_loss_percent: float = None,
                                     weight: float = None) -> bool:
        """Update configuration for a watched wallet"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if copy_scale is not None:
                cursor.execute('UPDATE watched_wallets SET copy_scale = ? WHERE id = ?',
                               (copy_scale, wallet_id))
            if copy_delay_seconds is not None:
                cursor.execute('UPDATE watched_wallets SET copy_delay_seconds = ? WHERE id = ?',
                               (copy_delay_seconds, wallet_id))
            if max_loss_percent is not None:
                cursor.execute('UPDATE watched_wallets SET max_loss_percent = ? WHERE id = ?',
                               (max_loss_percent, wallet_id))
            if weight is not None:
                cursor.execute('UPDATE watched_wallets SET weight = ? WHERE id = ?',
                               (weight, wallet_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error updating watched wallet config: {e}")
            return False

    def pause_watched_wallet(self, wallet_id: int, reason: str = '') -> bool:
        """Pause copy trading for a whale wallet"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE watched_wallets SET is_paused = 1, pause_reason = ? WHERE id = ?
        ''', (reason, wallet_id))
        conn.commit()
        conn.close()
        return True

    def resume_watched_wallet(self, wallet_id: int) -> bool:
        """Resume copy trading for a paused whale wallet"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE watched_wallets SET is_paused = 0, pause_reason = NULL WHERE id = ?
        ''', (wallet_id,))
        conn.commit()
        conn.close()
        return True
    
    def get_watched_wallets(self, user_id: int) -> List[Dict]:
        """Get all watched wallets for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM watched_wallets WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        wallets = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return wallets
    
    def remove_watched_wallet(self, wallet_id: int) -> bool:
        """Deactivate watched wallet"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE watched_wallets SET is_active = 0 WHERE id = ?
        ''', (wallet_id,))
        conn.commit()
        conn.close()
        return True
    
    # Trade history operations
    def add_trade(self, user_id: int, input_mint: str, output_mint: str,
                  input_amount: float, output_amount: float, dex: str,
                  price: float, slippage: float, tx_hash: str,
                  watched_wallet: str = None, is_copy: bool = False) -> bool:
        """Record trade"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trades 
                (user_id, watched_wallet, input_mint, output_mint, input_amount, 
                 output_amount, dex, price, slippage, is_copy, tx_hash, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, watched_wallet, input_mint, output_mint, input_amount,
                  output_amount, dex, price, slippage, is_copy, tx_hash, 'confirmed'))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error adding trade: {e}")
            return False
    
    def get_user_trades(self, user_id: int, limit: int = 20) -> List[Dict]:
        """Get recent trades for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM trades WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit))
        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return trades
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Get trading statistics"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) as total FROM trades WHERE user_id = ?', (user_id,))
        total_trades = cursor.fetchone()['total']
        
        cursor.execute('''
            SELECT COUNT(*) as copy FROM trades WHERE user_id = ? AND is_copy = 1
        ''', (user_id,))
        copy_trades = cursor.fetchone()['copy']
        
        cursor.execute('''
            SELECT SUM(input_amount) as total_volume FROM trades WHERE user_id = ?
        ''', (user_id,))
        total_volume = cursor.fetchone()['total_volume'] or 0
        
        conn.close()
        return {
            'total_trades': total_trades,
            'copy_trades': copy_trades,
            'total_volume': total_volume
        }
    
    # Risk order operations
    def add_risk_order(self, user_id: int, token_address: str, order_type: str,
                      entry_price: float, trigger_price: float,
                      original_amount: float) -> bool:
        """Add risk management order"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO risk_orders 
                (user_id, token_address, order_type, entry_price, trigger_price, original_amount)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, token_address, order_type, entry_price, trigger_price, original_amount))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error adding risk order: {e}")
            return False
    
    def get_active_risk_orders(self, user_id: int) -> List[Dict]:
        """Get active risk orders"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM risk_orders WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        orders = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return orders
    
    def trigger_risk_order(self, order_id: int) -> bool:
        """Mark order as triggered"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE risk_orders SET is_active = 0, triggered_at = CURRENT_TIMESTAMP WHERE id = ?
        ''', (order_id,))
        conn.commit()
        conn.close()
        return True
    
    # Vanity wallet operations
    def add_vanity_wallet(self, user_id: int, address: str, prefix: str,
                         difficulty: int, encrypted_key: str) -> bool:
        """Store generated vanity wallet"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO vanity_wallets (user_id, address, prefix, difficulty, encrypted_key)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, address, prefix, difficulty, encrypted_key))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error storing vanity wallet: {e}")
            return False
    
    def get_vanity_wallets(self, user_id: int) -> List[Dict]:
        """Get all vanity wallets for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, address, prefix, difficulty, created_at FROM vanity_wallets WHERE user_id = ?
        ''', (user_id,))
        wallets = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return wallets
    
    # Admin panel methods
    
    def toggle_user_admin(self, user_id: int, is_admin: bool) -> bool:
        """Grant or revoke admin privileges"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET is_admin = ? WHERE user_id = ?
            ''', (1 if is_admin else 0, user_id))
            conn.commit()
            conn.close()
            logger.info(f"✅ User {user_id} admin status set to {is_admin}")
            return True
        except Exception as e:
            logger.error(f"Error toggling admin: {e}")
            return False
    
    def is_user_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return bool(result[0]) if result else False
        except Exception:
            return False
    
    def get_all_users_list(self) -> List[Dict]:
        """Get all users with minimal info"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, telegram_id, wallet_address, is_admin, created_at FROM users
            ''')
            users = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return users
        except Exception as e:
            logger.error(f"Error getting users list: {e}")
            return []
    
    def get_user_trade_stats(self, user_id: int) -> Dict:
        """Get trade statistics for a user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) as wins,
                    SUM(output_amount - input_amount) as net_profit
                FROM trades
                WHERE user_id = ?
            ''', (user_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result and result[0]:
                return {
                    'total_trades': result[0],
                    'wins': result[1] or 0,
                    'net_profit': result[2] or 0
                }
            return {'total_trades': 0, 'wins': 0, 'net_profit': 0}
        except Exception as e:
            logger.error(f"Error getting trade stats: {e}")
            return {'total_trades': 0, 'wins': 0, 'net_profit': 0}
    
    def get_user_balance_total(self, user_id: int) -> float:
        """Get total balance across all wallets this user owns"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Get main wallet address
            cursor.execute('SELECT wallet_address FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            total_balance = 0.0
            
            if result and result[0]:
                # Would need to call wallet balance API
                # For now, return placeholder
                total_balance += 0.0
            
            conn.close()
            return total_balance
        except Exception as e:
            logger.error(f"Error getting user balance: {e}")
            return 0.0
    
    # Smart trading methods
    
    def add_pending_trade(self, user_id: int, token_address: str, token_amount: float,
                         sol_spent: float, entry_price: float, dex: str,
                         swap_signature: str) -> bool:
        """Record a pending smart trade"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO smart_trades 
                (user_id, token_address, token_amount, sol_spent, entry_price, dex, entry_tx)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, token_address, token_amount, sol_spent, entry_price, dex, swap_signature))
            conn.commit()
            conn.close()
            logger.info(f"✅ Pending trade recorded: {token_address[:10]}...")
            return True
        except Exception as e:
            logger.error(f"Error recording pending trade: {e}")
            return False
    
    def get_pending_trade_by_token(self, user_id: int, token_address: str) -> Optional[Dict]:
        """Get pending trade for token"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM smart_trades 
                WHERE user_id = ? AND token_address = ? AND is_closed = 0
                ORDER BY created_at DESC LIMIT 1
            ''', (user_id, token_address))
            trade = cursor.fetchone()
            conn.close()
            return dict(trade) if trade else None
        except Exception as e:
            logger.error(f"Error getting pending trade: {e}")
            return None
    
    def update_pending_trade_closed(self, user_id: int, token_address: str,
                                   sol_received: float, exit_tx: str) -> bool:
        """Mark trade as closed after exit"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Get the trade to calculate profit
            cursor.execute('''
                SELECT sol_spent FROM smart_trades 
                WHERE user_id = ? AND token_address = ? AND is_closed = 0
            ''', (user_id, token_address))
            trade = cursor.fetchone()
            
            if trade:
                sol_spent = trade[0]
                profit_percent = ((sol_received - sol_spent) / sol_spent * 100) if sol_spent > 0 else 0
                
                cursor.execute('''
                    UPDATE smart_trades 
                    SET is_closed = 1, sol_received = ?, exit_tx = ?, profit_percent = ?, closed_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND token_address = ? AND is_closed = 0
                ''', (sol_received, exit_tx, profit_percent, user_id, token_address))
                conn.commit()
                conn.close()
                logger.info(f"✅ Trade closed: {token_address[:10]}... Profit: {profit_percent:.2f}%")
                return True
            
            conn.close()
            return False
        except Exception as e:
            logger.error(f"Error closing trade: {e}")
            return False
    
    def record_profit_trade(self, user_id: int, token_address: str, sol_spent: float,
                           sol_received: float, profit_sol: float) -> bool:
        """Record a completed profitable trade"""
        try:
            profit_percent = (profit_sol / sol_spent * 100) if sol_spent > 0 else 0
            
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id FROM smart_trades 
                WHERE user_id = ? AND token_address = ? AND is_closed = 0
                ORDER BY created_at DESC LIMIT 1
            ''', (user_id, token_address))
            
            trade = cursor.fetchone()
            if trade:
                cursor.execute('''
                    UPDATE smart_trades
                    SET profit_percent = ?
                    WHERE id = ?
                ''', (profit_percent, trade[0]))
                conn.commit()
            
            conn.close()
            logger.info(f"✅ Profit recorded: {profit_percent:.2f}% on {token_address[:10]}...")
            return True
        except Exception as e:
            logger.error(f"Error recording profit: {e}")
            return False
    
    def update_user_trade_percent(self, user_id: int, percent: float) -> bool:
        """Update user's trade percentage"""
        try:
            # Clamp to 5-50%
            percent = max(5.0, min(50.0, percent))
            
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET trade_percent = ? WHERE user_id = ?
            ''', (percent, user_id))
            conn.commit()
            conn.close()
            logger.info(f"✅ Trade percent updated to {percent}% for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating trade percent: {e}")
            return False
    
    def get_user_smart_trades(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Get recent smart trades for user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM smart_trades
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ''', (user_id, limit))
            trades = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return trades
        except Exception as e:
            logger.error(f"Error getting smart trades: {e}")
            return []
    
    # Trading wallet management
    
    def set_trading_wallet(self, user_id: int, wallet_address: str, 
                          encrypted_key: str = None, use_separate: bool = True) -> bool:
        """Set or update trading wallet for user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET trading_wallet_address = ?, encrypted_trading_key = ?, use_separate_trading_wallet = ?
                WHERE user_id = ?
            ''', (wallet_address, encrypted_key, 1 if use_separate else 0, user_id))
            conn.commit()
            conn.close()
            logger.info(f"✅ Trading wallet set for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting trading wallet: {e}")
            return False
    
    def get_trading_wallet(self, user_id: int) -> Optional[Dict]:
        """Get trading wallet info for user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT trading_wallet_address, use_separate_trading_wallet
                FROM users WHERE user_id = ?
            ''', (user_id,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return {
                    'address': result[0],
                    'is_separate': bool(result[1])
                }
            return None
        except Exception as e:
            logger.error(f"Error getting trading wallet: {e}")
            return None
    
    def is_using_separate_trading_wallet(self, user_id: int) -> bool:
        """Check if user has separate trading wallet"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT use_separate_trading_wallet FROM users WHERE user_id = ?
            ''', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return bool(result[0]) if result else False
        except Exception:
            return False
    
    def get_wallet_pair(self, user_id: int) -> Dict:
        """Get both main and trading wallet info"""
        try:
            user = self.get_user(user_id)
            if not user:
                return {}
            
            return {
                'main_wallet': user.get('wallet_address'),
                'trading_wallet': user.get('trading_wallet_address'),
                'is_separate': bool(user.get('use_separate_trading_wallet')),
                'main_public_key': user.get('public_key')
            }
        except Exception as e:
            logger.error(f"Error getting wallet pair: {e}")
            return {}


    # Copy performance tracking

    def open_copy_position(self, user_id: int, watched_wallet: str, token_address: str,
                           whale_entry_price: float, user_entry_price: float,
                           copy_scale: float, sol_spent: float) -> int:
        """Record opening of a copy trade position. Returns row id."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO copy_performance
                (user_id, watched_wallet, token_address, whale_entry_price,
                 user_entry_price, copy_scale, sol_spent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, watched_wallet, token_address, whale_entry_price,
                  user_entry_price, copy_scale, sol_spent))
            row_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            logger.error(f"Error opening copy position: {e}")
            return -1

    def close_copy_position(self, position_id: int, whale_exit_price: float,
                            user_exit_price: float, sol_received: float) -> bool:
        """Record closing of a copy trade position with profit comparison."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT whale_entry_price, user_entry_price FROM copy_performance WHERE id = ?',
                           (position_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return False
            whale_entry, user_entry = row[0], row[1]
            whale_profit = ((whale_exit_price - whale_entry) / whale_entry * 100) if whale_entry else 0
            user_profit = ((user_exit_price - user_entry) / user_entry * 100) if user_entry else 0
            cursor.execute('''
                UPDATE copy_performance
                SET whale_exit_price = ?, whale_profit_percent = ?,
                    user_exit_price = ?, user_profit_percent = ?,
                    sol_received = ?, status = 'closed',
                    closed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (whale_exit_price, whale_profit, user_exit_price, user_profit,
                  sol_received, position_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error closing copy position: {e}")
            return False

    def get_copy_performance(self, user_id: int, watched_wallet: str = None,
                             limit: int = 20) -> List[Dict]:
        """Get copy trade performance history."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if watched_wallet:
                cursor.execute('''
                    SELECT * FROM copy_performance
                    WHERE user_id = ? AND watched_wallet = ?
                    ORDER BY opened_at DESC LIMIT ?
                ''', (user_id, watched_wallet, limit))
            else:
                cursor.execute('''
                    SELECT * FROM copy_performance
                    WHERE user_id = ?
                    ORDER BY opened_at DESC LIMIT ?
                ''', (user_id, limit))
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"Error getting copy performance: {e}")
            return []

    def get_whale_recent_loss(self, user_id: int, watched_wallet: str,
                              last_n: int = 5) -> float:
        """Return average whale profit % over last N closed trades (negative = loss)."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT whale_profit_percent FROM copy_performance
                WHERE user_id = ? AND watched_wallet = ? AND status = 'closed'
                ORDER BY closed_at DESC LIMIT ?
            ''', (user_id, watched_wallet, last_n))
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                return 0.0
            return sum(r[0] for r in rows if r[0] is not None) / len(rows)
        except Exception as e:
            logger.error(f"Error getting whale loss: {e}")
            return 0.0


# Singleton instance
db = Database()
