"""
Database layer for managing users, wallets, and trade history
Using PostgreSQL (Neon)
"""
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from config import DATABASE_URL, DB_PATH

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_url: str = DATABASE_URL):
        if not db_url:
            logger.warning("DATABASE_URL not set, falling back to SQLite")
            self.use_postgres = False
            self.db_path = DB_PATH
        else:
            self.use_postgres = True
            self.db_url = db_url
        self.init_db()

    def get_connection(self):
        """Get database connection"""
        if self.use_postgres:
            conn = psycopg2.connect(self.db_url)
            return conn
        else:
            import sqlite3
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn

    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if self.use_postgres:
            # PostgreSQL schema
            self._init_postgres_tables(cursor)
        else:
            # SQLite schema (fallback)
            self._init_sqlite_tables(cursor)

        conn.commit()
        conn.close()
        logger.info("✅ Database initialized")

    def _init_postgres_tables(self, cursor):
        """Initialize PostgreSQL tables"""
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                wallet_address TEXT,
                encrypted_private_key TEXT,
                public_key TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                is_admin BOOLEAN DEFAULT FALSE,
                trade_percent REAL DEFAULT 20.0,
                trading_wallet_address TEXT,
                encrypted_trading_key TEXT,
                use_separate_trading_wallet BOOLEAN DEFAULT FALSE,
                base_wallet_address TEXT,
                encrypted_base_key TEXT,
                stop_loss_percent REAL DEFAULT 0,
                take_profit_percent REAL DEFAULT 0,
                trailing_stop_percent REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Watched wallets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watched_wallets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                wallet_address TEXT NOT NULL,
                alias TEXT,
                chain TEXT DEFAULT 'solana',
                copy_scale REAL DEFAULT 1.0,
                copy_delay_seconds INTEGER DEFAULT 0,
                max_loss_percent REAL DEFAULT 20.0,
                weight REAL DEFAULT 1.0,
                is_active BOOLEAN DEFAULT TRUE,
                is_paused BOOLEAN DEFAULT FALSE,
                pause_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Copy trade performance tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS copy_performance (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
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
                whale_block_time INTEGER DEFAULT 0,
                copy_latency_ms INTEGER DEFAULT 0,
                signal_count INTEGER DEFAULT 1,
                exit_reason TEXT,
                token_amount REAL DEFAULT 0,
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        ''')

        # Trade history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                watched_wallet TEXT,
                chain TEXT DEFAULT 'solana',
                input_mint TEXT,
                output_mint TEXT,
                input_amount REAL,
                output_amount REAL,
                dex TEXT,
                price REAL,
                slippage REAL,
                is_copy BOOLEAN DEFAULT FALSE,
                tx_hash TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Pending trades (for copy trading)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_trades (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                watched_wallet TEXT,
                signature TEXT,
                input_mint TEXT,
                output_mint TEXT,
                input_amount REAL,
                dex TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Smart trades
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS smart_trades (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                token_address TEXT NOT NULL,
                token_amount REAL,
                sol_spent REAL,
                entry_price REAL,
                dex TEXT,
                entry_tx TEXT,
                exit_tx TEXT,
                sol_received REAL,
                is_closed BOOLEAN DEFAULT FALSE,
                profit_percent REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        ''')

        # Risk management orders
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_orders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                token_address TEXT,
                order_type TEXT,
                entry_price REAL,
                trigger_price REAL,
                original_amount REAL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                triggered_at TIMESTAMP
            )
        ''')

        # Vanity wallets
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vanity_wallets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                address TEXT UNIQUE,
                prefix TEXT,
                match_position TEXT DEFAULT 'start',
                case_sensitive BOOLEAN DEFAULT TRUE,
                difficulty INTEGER,
                encrypted_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Multi-chain wallet storage
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chain_wallets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                chain TEXT NOT NULL,
                address TEXT NOT NULL,
                encrypted_key TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, chain)
            )
        ''')

        # Auto-trade settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_trade_settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
                is_active BOOLEAN DEFAULT FALSE,
                trade_percent REAL DEFAULT 20.0,
                max_trades_per_cycle INTEGER DEFAULT 2,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Auto-smart trade settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_smart_settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
                is_active BOOLEAN DEFAULT FALSE,
                trade_percent REAL DEFAULT 10.0,
                max_positions INTEGER DEFAULT 4,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Per-user key-value settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, setting_key)
            )
        ''')

        # Token lists (blacklist/whitelist)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_token_lists (
                user_id INTEGER NOT NULL REFERENCES users(user_id),
                list_type TEXT NOT NULL,
                token_address TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, list_type, token_address)
            )
        ''')

        logger.info("✅ PostgreSQL tables initialized")

    def _init_sqlite_tables(self, cursor):
        """Initialize SQLite tables (fallback)"""
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
                base_wallet_address TEXT,
                encrypted_base_key TEXT,
                stop_loss_percent REAL DEFAULT 0,
                take_profit_percent REAL DEFAULT 0,
                trailing_stop_percent REAL DEFAULT 0,
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
                chain TEXT DEFAULT 'solana',
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
                whale_block_time INTEGER DEFAULT 0,
                copy_latency_ms INTEGER DEFAULT 0,
                signal_count INTEGER DEFAULT 1,
                exit_reason TEXT,
                token_amount REAL DEFAULT 0,
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
                chain TEXT DEFAULT 'solana',
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

        # Pending trades
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

        # Smart trades
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

        # Risk orders
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

        # Vanity wallets
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vanity_wallets (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                address TEXT UNIQUE,
                prefix TEXT,
                match_position TEXT DEFAULT 'start',
                case_sensitive INTEGER DEFAULT 1,
                difficulty INTEGER,
                encrypted_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        # Chain wallets
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chain_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chain TEXT NOT NULL,
                address TEXT NOT NULL,
                encrypted_key TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, chain),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        # Auto-trade settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_trade_settings (
                user_id INTEGER PRIMARY KEY,
                is_active BOOLEAN DEFAULT 0,
                trade_percent REAL DEFAULT 20.0,
                max_trades_per_cycle INTEGER DEFAULT 2,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Auto-smart settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_smart_settings (
                user_id INTEGER PRIMARY KEY,
                is_active BOOLEAN DEFAULT 0,
                trade_percent REAL DEFAULT 10.0,
                max_positions INTEGER DEFAULT 4,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # User settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, setting_key)
            )
        ''')

        # Token lists
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_token_lists (
                user_id INTEGER NOT NULL,
                list_type TEXT NOT NULL,
                token_address TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, list_type, token_address)
            )
        ''')

        logger.info("✅ SQLite tables initialized (fallback)")

    # User operations
    def add_user(self, telegram_id: int, wallet_address: str,
                 encrypted_key: str = None, public_key: str = None) -> bool:
        """Add new user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO users (telegram_id, wallet_address, encrypted_private_key, public_key)
                    VALUES (%s, %s, %s, %s)
                ''', (telegram_id, wallet_address, encrypted_key, public_key))
            else:
                cursor.execute('''
                    INSERT INTO users (telegram_id, wallet_address, encrypted_private_key, public_key)
                    VALUES (?, ?, ?, ?)
                ''', (telegram_id, wallet_address, encrypted_key, public_key))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"User {telegram_id} already exists or error: {e}")
            return False

    def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Get user by telegram ID"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
        if self.use_postgres:
            cursor.execute('SELECT * FROM users WHERE telegram_id = %s', (telegram_id,))
        else:
            cursor.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None

    def update_user_active_time(self, telegram_id: int):
        """Update last active time"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if self.use_postgres:
            cursor.execute('''
                UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE telegram_id = %s
            ''', (telegram_id,))
        else:
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
                           weight: float = 1.0,
                           chain: str = 'solana') -> bool:
        """Add watched wallet for copy trading"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO watched_wallets
                    (user_id, wallet_address, alias, copy_scale, copy_delay_seconds, max_loss_percent, weight, chain)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (user_id, wallet_address, alias, copy_scale, copy_delay_seconds,
                      max_loss_percent, weight, chain))
            else:
                cursor.execute('''
                    INSERT INTO watched_wallets
                    (user_id, wallet_address, alias, copy_scale, copy_delay_seconds, max_loss_percent, weight, chain)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, wallet_address, alias, copy_scale, copy_delay_seconds,
                      max_loss_percent, weight, chain))
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
                if self.use_postgres:
                    cursor.execute('UPDATE watched_wallets SET copy_scale = %s WHERE id = %s',
                                   (copy_scale, wallet_id))
                else:
                    cursor.execute('UPDATE watched_wallets SET copy_scale = ? WHERE id = ?',
                                   (copy_scale, wallet_id))
            if copy_delay_seconds is not None:
                if self.use_postgres:
                    cursor.execute('UPDATE watched_wallets SET copy_delay_seconds = %s WHERE id = %s',
                                   (copy_delay_seconds, wallet_id))
                else:
                    cursor.execute('UPDATE watched_wallets SET copy_delay_seconds = ? WHERE id = ?',
                                   (copy_delay_seconds, wallet_id))
            if max_loss_percent is not None:
                if self.use_postgres:
                    cursor.execute('UPDATE watched_wallets SET max_loss_percent = %s WHERE id = %s',
                                   (max_loss_percent, wallet_id))
                else:
                    cursor.execute('UPDATE watched_wallets SET max_loss_percent = ? WHERE id = ?',
                                   (max_loss_percent, wallet_id))
            if weight is not None:
                if self.use_postgres:
                    cursor.execute('UPDATE watched_wallets SET weight = %s WHERE id = %s',
                                   (weight, wallet_id))
                else:
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
        if self.use_postgres:
            cursor.execute('''
                UPDATE watched_wallets SET is_paused = TRUE, pause_reason = %s WHERE id = %s
            ''', (reason, wallet_id))
        else:
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
        if self.use_postgres:
            cursor.execute('''
                UPDATE watched_wallets SET is_paused = FALSE, pause_reason = NULL WHERE id = %s
            ''', (wallet_id,))
        else:
            cursor.execute('''
                UPDATE watched_wallets SET is_paused = 0, pause_reason = NULL WHERE id = ?
            ''', (wallet_id,))
        conn.commit()
        conn.close()
        return True

    def get_watched_wallets(self, user_id: int) -> List[Dict]:
        """Get all watched wallets for user"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
        if self.use_postgres:
            cursor.execute('''
                SELECT * FROM watched_wallets WHERE user_id = %s AND is_active = TRUE
            ''', (user_id,))
        else:
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
        if self.use_postgres:
            cursor.execute('UPDATE watched_wallets SET is_active = FALSE WHERE id = %s', (wallet_id,))
        else:
            cursor.execute('UPDATE watched_wallets SET is_active = 0 WHERE id = ?', (wallet_id,))
        conn.commit()
        conn.close()
        return True

    # Trade history operations
    def add_trade(self, user_id: int, input_mint: str, output_mint: str,
                  input_amount: float, output_amount: float, dex: str,
                  price: float, slippage: float, tx_hash: str,
                  watched_wallet: str = None, is_copy: bool = False,
                  chain: str = 'solana') -> bool:
        """Record trade"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO trades
                    (user_id, watched_wallet, chain, input_mint, output_mint, input_amount,
                     output_amount, dex, price, slippage, is_copy, tx_hash, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (user_id, watched_wallet, chain, input_mint, output_mint, input_amount,
                      output_amount, dex, price, slippage, is_copy, tx_hash, 'confirmed'))
            else:
                cursor.execute('''
                    INSERT INTO trades
                    (user_id, watched_wallet, chain, input_mint, output_mint, input_amount,
                     output_amount, dex, price, slippage, is_copy, tx_hash, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, watched_wallet, chain, input_mint, output_mint, input_amount,
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
        cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
        if self.use_postgres:
            cursor.execute('''
                SELECT * FROM trades WHERE user_id = %s
                ORDER BY created_at DESC LIMIT %s
            ''', (user_id, limit))
        else:
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
        cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)

        if self.use_postgres:
            cursor.execute('SELECT COUNT(*) as total FROM trades WHERE user_id = %s', (user_id,))
            total_trades = cursor.fetchone()['total']

            cursor.execute('''
                SELECT COUNT(*) as copy FROM trades WHERE user_id = %s AND is_copy = TRUE
            ''', (user_id,))
            copy_trades = cursor.fetchone()['copy']

            cursor.execute('''
                SELECT SUM(input_amount) as total_volume FROM trades WHERE user_id = %s
            ''', (user_id,))
            total_volume = cursor.fetchone()['total_volume'] or 0
        else:
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
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO risk_orders
                    (user_id, token_address, order_type, entry_price, trigger_price, original_amount)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (user_id, token_address, order_type, entry_price, trigger_price, original_amount))
            else:
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
        cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
        if self.use_postgres:
            cursor.execute('''
                SELECT * FROM risk_orders WHERE user_id = %s AND is_active = TRUE
            ''', (user_id,))
        else:
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
        if self.use_postgres:
            cursor.execute('''
                UPDATE risk_orders SET is_active = FALSE, triggered_at = CURRENT_TIMESTAMP WHERE id = %s
            ''', (order_id,))
        else:
            cursor.execute('''
                UPDATE risk_orders SET is_active = 0, triggered_at = CURRENT_TIMESTAMP WHERE id = ?
            ''', (order_id,))
        conn.commit()
        conn.close()
        return True

    # Vanity wallet operations
    def add_vanity_wallet(self, user_id: int, address: str, prefix: str,
                         difficulty: int, encrypted_key: str, match_position: str = "start",
                         case_sensitive: bool = True) -> bool:
        """Store generated vanity wallet"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO vanity_wallets (user_id, address, prefix, match_position, case_sensitive, difficulty, encrypted_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (user_id, address, prefix, match_position, case_sensitive, difficulty, encrypted_key))
            else:
                cursor.execute('''
                    INSERT INTO vanity_wallets (user_id, address, prefix, match_position, case_sensitive, difficulty, encrypted_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, address, prefix, match_position, int(bool(case_sensitive)), difficulty, encrypted_key))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error storing vanity wallet: {e}")
            return False

    def get_vanity_wallets(self, user_id: int) -> List[Dict]:
        """Get all vanity wallets for user"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
        if self.use_postgres:
            cursor.execute('''
                SELECT id, address, prefix, match_position, case_sensitive, difficulty, created_at FROM vanity_wallets WHERE user_id = %s
            ''', (user_id,))
        else:
            cursor.execute('''
                SELECT id, address, prefix, match_position, case_sensitive, difficulty, created_at FROM vanity_wallets WHERE user_id = ?
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
            if self.use_postgres:
                cursor.execute('''
                    UPDATE users SET is_admin = %s WHERE telegram_id = %s
                ''', (True if is_admin else False, user_id))
            else:
                cursor.execute('''
                    UPDATE users SET is_admin = ? WHERE telegram_id = ?
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
            if self.use_postgres:
                cursor.execute('SELECT is_admin FROM users WHERE telegram_id = %s', (user_id,))
            else:
                cursor.execute('SELECT is_admin FROM users WHERE telegram_id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return bool(result[0]) if result else False
        except Exception:
            return False

    def get_all_users_list(self) -> List[Dict]:
        """Get all users with minimal info"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
            if self.use_postgres:
                cursor.execute('''
                    SELECT user_id, telegram_id, wallet_address, is_admin, created_at FROM users
                ''')
            else:
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
            if self.use_postgres:
                cursor.execute('''
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) as wins,
                        SUM(output_amount - input_amount) as net_profit
                    FROM trades
                    WHERE user_id = %s
                ''', (user_id,))
            else:
                cursor.execute('''
                    SELECT
                        COUNT(*) as total,
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

            if self.use_postgres:
                cursor.execute('SELECT wallet_address FROM users WHERE telegram_id = %s', (user_id,))
            else:
                cursor.execute('SELECT wallet_address FROM users WHERE telegram_id = ?', (user_id,))
            result = cursor.fetchone()
            total_balance = 0.0

            if result and result[0]:
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
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO smart_trades
                    (user_id, token_address, token_amount, sol_spent, entry_price, dex, entry_tx)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (user_id, token_address, token_amount, sol_spent, entry_price, dex, swap_signature))
            else:
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
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
            if self.use_postgres:
                cursor.execute('''
                    SELECT * FROM smart_trades
                    WHERE user_id = %s AND token_address = %s AND is_closed = FALSE
                    ORDER BY created_at DESC LIMIT 1
                ''', (user_id, token_address))
            else:
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

            if self.use_postgres:
                cursor.execute('''
                    SELECT sol_spent FROM smart_trades
                    WHERE user_id = %s AND token_address = %s AND is_closed = FALSE
                ''', (user_id, token_address))
            else:
                cursor.execute('''
                    SELECT sol_spent FROM smart_trades
                    WHERE user_id = ? AND token_address = ? AND is_closed = 0
                ''', (user_id, token_address))
            trade = cursor.fetchone()

            if trade:
                sol_spent = trade[0]
                profit_percent = ((sol_received - sol_spent) / sol_spent * 100) if sol_spent > 0 else 0

                if self.use_postgres:
                    cursor.execute('''
                        UPDATE smart_trades
                        SET is_closed = FALSE, sol_received = %s, exit_tx = %s, profit_percent = %s, closed_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s AND token_address = %s AND is_closed = FALSE
                    ''', (sol_received, exit_tx, profit_percent, user_id, token_address))
                else:
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
            if self.use_postgres:
                cursor.execute('''
                    SELECT id FROM smart_trades
                    WHERE user_id = %s AND token_address = %s AND is_closed = FALSE
                    ORDER BY created_at DESC LIMIT 1
                ''', (user_id, token_address))
            else:
                cursor.execute('''
                    SELECT id FROM smart_trades
                    WHERE user_id = ? AND token_address = ? AND is_closed = 0
                    ORDER BY created_at DESC LIMIT 1
                ''', (user_id, token_address))

            trade = cursor.fetchone()
            if trade:
                if self.use_postgres:
                    cursor.execute('''
                        UPDATE smart_trades SET profit_percent = %s WHERE id = %s
                    ''', (profit_percent, trade[0]))
                else:
                    cursor.execute('''
                        UPDATE smart_trades SET profit_percent = ? WHERE id = ?
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
            percent = max(5.0, min(50.0, percent))

            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    UPDATE users SET trade_percent = %s WHERE telegram_id = %s
                ''', (percent, user_id))
            else:
                cursor.execute('''
                    UPDATE users SET trade_percent = ? WHERE telegram_id = ?
                ''', (percent, user_id))
            conn.commit()
            conn.close()
            logger.info(f"✅ Trade percent updated to {percent}% for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating trade percent: {e}")
            return False

    def update_user_setting(self, user_id: int, field: str, value) -> bool:
        """Generic setter for a single column in the users table."""
        _ALLOWED = {
            'stop_loss_percent', 'take_profit_percent', 'trade_percent',
            'trailing_stop_percent', 'use_separate_trading_wallet',
        }
        if field not in _ALLOWED:
            logger.warning(f"update_user_setting: field '{field}' not in whitelist")
            return False
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute(
                    f'UPDATE users SET {field} = %s WHERE telegram_id = %s',
                    (value, user_id)
                )
            else:
                cursor.execute(
                    f'UPDATE users SET {field} = ? WHERE telegram_id = ?',
                    (value, user_id)
                )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"update_user_setting error ({field}): {e}")
            return False

    def get_user_setting(self, user_id: int, key: str, default=None):
        """Return a stored user setting (string), cast to float/int if numeric."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute(
                    'SELECT setting_value FROM user_settings WHERE user_id = %s AND setting_key = %s',
                    (user_id, key)
                )
            else:
                cursor.execute(
                    'SELECT setting_value FROM user_settings WHERE user_id = ? AND setting_key = ?',
                    (user_id, key)
                )
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return default
            val = row[0]
            try:
                return float(val) if '.' in val else int(val)
            except (ValueError, TypeError):
                return val
        except Exception as e:
            logger.error(f"get_user_setting error ({key}): {e}")
            return default

    def set_user_setting(self, user_id: int, key: str, value) -> bool:
        """Persist a user setting. Value is stored as string."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO user_settings (user_id, setting_key, setting_value, updated_at)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, setting_key) DO UPDATE SET
                        setting_value = excluded.setting_value,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, key, str(value)))
            else:
                cursor.execute('''
                    INSERT INTO user_settings (user_id, setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, setting_key) DO UPDATE SET
                        setting_value = excluded.setting_value,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, key, str(value)))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"set_user_setting error ({key}): {e}")
            return False

    def get_all_user_settings(self, user_id: int) -> dict:
        """Return all settings for a user as a dict."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute(
                    'SELECT setting_key, setting_value FROM user_settings WHERE user_id = %s',
                    (user_id,)
                )
            else:
                cursor.execute(
                    'SELECT setting_key, setting_value FROM user_settings WHERE user_id = ?',
                    (user_id,)
                )
            rows = cursor.fetchall()
            conn.close()
            result = {}
            for key, val in rows:
                try:
                    result[key] = float(val) if '.' in val else int(val)
                except (ValueError, TypeError):
                    result[key] = val
            return result
        except Exception as e:
            logger.error(f"get_all_user_settings error: {e}")
            return {}

    def get_user_smart_trades(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Get recent smart trades for user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
            if self.use_postgres:
                cursor.execute('''
                    SELECT * FROM smart_trades
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ''', (user_id, limit))
            else:
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
            if self.use_postgres:
                cursor.execute('''
                    UPDATE users
                    SET trading_wallet_address = %s, encrypted_trading_key = %s, use_separate_trading_wallet = %s
                    WHERE telegram_id = %s
                ''', (wallet_address, encrypted_key, True if use_separate else False, user_id))
            else:
                cursor.execute('''
                    UPDATE users
                    SET trading_wallet_address = ?, encrypted_trading_key = ?, use_separate_trading_wallet = ?
                    WHERE telegram_id = ?
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
            if self.use_postgres:
                cursor.execute('''
                    SELECT trading_wallet_address, use_separate_trading_wallet
                    FROM users WHERE telegram_id = %s
                ''', (user_id,))
            else:
                cursor.execute('''
                    SELECT trading_wallet_address, use_separate_trading_wallet
                    FROM users WHERE telegram_id = ?
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
            if self.use_postgres:
                cursor.execute('''
                    SELECT use_separate_trading_wallet FROM users WHERE telegram_id = %s
                ''', (user_id,))
            else:
                cursor.execute('''
                    SELECT use_separate_trading_wallet FROM users WHERE telegram_id = ?
                ''', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return bool(result[0]) if result else False
        except Exception:
            return False

    # Base wallet management

    def set_base_wallet(self, user_id: int, wallet_address: str,
                        encrypted_key: str = None) -> bool:
        """Set or update Base (EVM) wallet for a user."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    UPDATE users
                    SET base_wallet_address = %s, encrypted_base_key = %s
                    WHERE telegram_id = %s
                ''', (wallet_address, encrypted_key, user_id))
            else:
                cursor.execute('''
                    UPDATE users
                    SET base_wallet_address = ?, encrypted_base_key = ?
                    WHERE telegram_id = ?
                ''', (wallet_address, encrypted_key, user_id))
            conn.commit()
            conn.close()
            logger.info(f"✅ Base wallet set for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting Base wallet: {e}")
            return False

    def get_base_wallet(self, user_id: int) -> Optional[Dict]:
        """Get Base wallet info for a user."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    SELECT base_wallet_address, encrypted_base_key
                    FROM users WHERE telegram_id = %s
                ''', (user_id,))
            else:
                cursor.execute('''
                    SELECT base_wallet_address, encrypted_base_key
                    FROM users WHERE telegram_id = ?
                ''', (user_id,))
            result = cursor.fetchone()
            conn.close()
            if result and result[0]:
                return {
                    'address': result[0],
                    'encrypted_key': result[1],
                }
            return None
        except Exception as e:
            logger.error(f"Error getting Base wallet: {e}")
            return None

    # Chain wallet management

    def set_chain_wallet(self, user_id: int, chain: str, address: str,
                         encrypted_key: str = None) -> bool:
        """Insert or update wallet for a given chain."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO chain_wallets (user_id, chain, address, encrypted_key)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(user_id, chain) DO UPDATE SET
                        address = excluded.address,
                        encrypted_key = excluded.encrypted_key,
                        is_active = TRUE
                ''', (user_id, chain, address, encrypted_key))
            else:
                cursor.execute('''
                    INSERT INTO chain_wallets (user_id, chain, address, encrypted_key)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, chain) DO UPDATE SET
                        address = excluded.address,
                        encrypted_key = excluded.encrypted_key,
                        is_active = 1
                ''', (user_id, chain, address, encrypted_key))
            conn.commit()
            conn.close()
            logger.info(f"✅ Chain wallet set: user={user_id} chain={chain}")
            return True
        except Exception as e:
            logger.error(f"Error setting chain wallet: {e}")
            return False

    def set_chain_wallet_by_telegram(self, telegram_id: int, chain: str, address: str,
                                     encrypted_key: str = None) -> bool:
        """Set chain wallet using telegram_id."""
        try:
            user = self.get_user(telegram_id)
            if not user:
                return False
            return self.set_chain_wallet(user['user_id'], chain, address, encrypted_key)
        except Exception as e:
            logger.error(f"Error setting chain wallet by telegram: {e}")
            return False

    def get_chain_wallet(self, telegram_id: int, chain: str) -> Optional[Dict]:
        """Get wallet for a specific chain by telegram_id."""
        try:
            user = self.get_user(telegram_id)
            if not user:
                return None
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    SELECT address, encrypted_key, created_at FROM chain_wallets
                    WHERE user_id = %s AND chain = %s AND is_active = TRUE
                ''', (user['user_id'], chain))
            else:
                cursor.execute('''
                    SELECT address, encrypted_key, created_at FROM chain_wallets
                    WHERE user_id = ? AND chain = ? AND is_active = 1
                ''', (user['user_id'], chain))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {'address': row[0], 'encrypted_key': row[1], 'created_at': row[2]}
            return None
        except Exception as e:
            logger.error(f"Error getting chain wallet: {e}")
            return None

    def get_all_chain_wallets(self, telegram_id: int) -> Dict:
        """Get all chain wallets for a user."""
        try:
            user = self.get_user(telegram_id)
            if not user:
                return {}
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    SELECT chain, address, encrypted_key, created_at FROM chain_wallets
                    WHERE user_id = %s AND is_active = TRUE
                ''', (user['user_id'],))
            else:
                cursor.execute('''
                    SELECT chain, address, encrypted_key, created_at FROM chain_wallets
                    WHERE user_id = ? AND is_active = 1
                ''', (user['user_id'],))
            result = {}
            for row in cursor.fetchall():
                result[row[0]] = {
                    'address': row[1],
                    'encrypted_key': row[2],
                    'created_at': row[3],
                }
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Error getting all chain wallets: {e}")
            return {}

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
                           copy_scale: float, sol_spent: float,
                           whale_block_time: int = 0, copy_latency_ms: int = 0,
                           signal_count: int = 1) -> int:
        """Record opening of a copy trade position. Returns row id."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO copy_performance
                    (user_id, watched_wallet, token_address, whale_entry_price,
                     user_entry_price, copy_scale, sol_spent,
                     whale_block_time, copy_latency_ms, signal_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (user_id, watched_wallet, token_address, whale_entry_price,
                      user_entry_price, copy_scale, sol_spent,
                      whale_block_time, copy_latency_ms, signal_count))
                row_id = cursor.fetchone()[0] if cursor.fetchone() else -1
            else:
                cursor.execute('''
                    INSERT INTO copy_performance
                    (user_id, watched_wallet, token_address, whale_entry_price,
                     user_entry_price, copy_scale, sol_spent,
                     whale_block_time, copy_latency_ms, signal_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, watched_wallet, token_address, whale_entry_price,
                      user_entry_price, copy_scale, sol_spent,
                      whale_block_time, copy_latency_ms, signal_count))
                row_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            logger.error(f"Error opening copy position: {e}")
            return -1

    def update_copy_position_token_amount(self, position_id: int, token_amount: float) -> bool:
        """Update the token_amount field after a copy trade is confirmed."""
        try:
            conn = self.get_connection()
            if self.use_postgres:
                conn.execute(
                    'UPDATE copy_performance SET token_amount = %s WHERE id = %s',
                    (token_amount, position_id)
                )
            else:
                conn.execute(
                    'UPDATE copy_performance SET token_amount = ? WHERE id = ?',
                    (token_amount, position_id)
                )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error updating copy position token amount: {e}")
            return False

    def close_copy_position(self, position_id: int, whale_exit_price: float,
                            user_exit_price: float, sol_received: float,
                            exit_reason: str = None) -> bool:
        """Record closing of a copy trade position with profit comparison."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('SELECT whale_entry_price, user_entry_price FROM copy_performance WHERE id = %s',
                               (position_id,))
            else:
                cursor.execute('SELECT whale_entry_price, user_entry_price FROM copy_performance WHERE id = ?',
                               (position_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return False
            whale_entry, user_entry = row[0], row[1]
            whale_profit = ((whale_exit_price - whale_entry) / whale_entry * 100) if whale_entry else 0
            user_profit = ((user_exit_price - user_entry) / user_entry * 100) if user_entry else 0
            if self.use_postgres:
                cursor.execute('''
                    UPDATE copy_performance
                    SET whale_exit_price = %s, whale_profit_percent = %s,
                        user_exit_price = %s, user_profit_percent = %s,
                        sol_received = %s, status = 'closed',
                        exit_reason = %s,
                        closed_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                ''', (whale_exit_price, whale_profit, user_exit_price, user_profit,
                      sol_received, exit_reason, position_id))
            else:
                cursor.execute('''
                    UPDATE copy_performance
                    SET whale_exit_price = ?, whale_profit_percent = ?,
                        user_exit_price = ?, user_profit_percent = ?,
                        sol_received = ?, status = 'closed',
                        exit_reason = ?,
                        closed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (whale_exit_price, whale_profit, user_exit_price, user_profit,
                      sol_received, exit_reason, position_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error closing copy position: {e}")
            return False

    def get_all_open_positions(self, user_id: int) -> Dict:
        """Return all open positions for a user."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)

            if self.use_postgres:
                cursor.execute('''
                    SELECT token_address, watched_wallet, whale_entry_price,
                           user_entry_price, copy_scale, sol_spent, opened_at,
                           token_amount
                    FROM copy_performance
                    WHERE user_id = %s AND status = 'open'
                    ORDER BY opened_at DESC
                ''', (user_id,))
                copy_positions = [dict(r) for r in cursor.fetchall()]

                cursor.execute('''
                    SELECT token_address, token_amount, sol_spent, entry_price,
                           dex, entry_tx, created_at
                    FROM smart_trades
                    WHERE user_id = %s AND is_closed = FALSE
                    ORDER BY created_at DESC
                ''', (user_id,))
                smart_positions = [dict(r) for r in cursor.fetchall()]
            else:
                cursor.execute('''
                    SELECT token_address, watched_wallet, whale_entry_price,
                           user_entry_price, copy_scale, sol_spent, opened_at,
                           token_amount
                    FROM copy_performance
                    WHERE user_id = ? AND status = 'open'
                    ORDER BY opened_at DESC
                ''', (user_id,))
                copy_positions = [dict(r) for r in cursor.fetchall()]

                cursor.execute('''
                    SELECT token_address, token_amount, sol_spent, entry_price,
                           dex, entry_tx, created_at
                    FROM smart_trades
                    WHERE user_id = ? AND is_closed = 0
                    ORDER BY created_at DESC
                ''', (user_id,))
                smart_positions = [dict(r) for r in cursor.fetchall()]

            conn.close()
            return {'copy': copy_positions, 'smart': smart_positions}
        except Exception as e:
            logger.error(f"Error getting open positions: {e}")
            return {'copy': [], 'smart': []}

    # Auto-trade settings

    def save_auto_trade_settings(self, user_id: int, is_active: bool,
                                  trade_percent: float = 20.0,
                                  max_trades_per_cycle: int = 2) -> None:
        """Persist auto-trade settings for a user."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO auto_trade_settings (user_id, is_active, trade_percent, max_trades_per_cycle, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        is_active = excluded.is_active,
                        trade_percent = excluded.trade_percent,
                        max_trades_per_cycle = excluded.max_trades_per_cycle,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, True if is_active else False, trade_percent, max_trades_per_cycle))
            else:
                cursor.execute('''
                    INSERT INTO auto_trade_settings (user_id, is_active, trade_percent, max_trades_per_cycle, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        is_active = excluded.is_active,
                        trade_percent = excluded.trade_percent,
                        max_trades_per_cycle = excluded.max_trades_per_cycle,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, 1 if is_active else 0, trade_percent, max_trades_per_cycle))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"save_auto_trade_settings error: {e}")

    def get_active_auto_traders(self) -> list:
        """Return list of active auto-traders."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
            if self.use_postgres:
                cursor.execute('''
                    SELECT user_id, trade_percent, max_trades_per_cycle
                    FROM auto_trade_settings
                    WHERE is_active = TRUE
                ''')
            else:
                cursor.execute('''
                    SELECT user_id, trade_percent, max_trades_per_cycle
                    FROM auto_trade_settings
                    WHERE is_active = 1
                ''')
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"get_active_auto_traders error: {e}")
            return []

    def save_auto_smart_settings(self, user_id: int, is_active: bool,
                                  trade_percent: float = 10.0,
                                  max_positions: int = 4) -> None:
        """Persist auto-smart trade settings for a user."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO auto_smart_settings (user_id, is_active, trade_percent, max_positions, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        is_active = excluded.is_active,
                        trade_percent = excluded.trade_percent,
                        max_positions = excluded.max_positions,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, True if is_active else False, trade_percent, max_positions))
            else:
                cursor.execute('''
                    INSERT INTO auto_smart_settings (user_id, is_active, trade_percent, max_positions, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        is_active = excluded.is_active,
                        trade_percent = excluded.trade_percent,
                        max_positions = excluded.max_positions,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, 1 if is_active else 0, trade_percent, max_positions))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"save_auto_smart_settings error: {e}")

    def get_active_auto_smart_traders(self) -> list:
        """Return all users who have auto-smart trading enabled."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
            if self.use_postgres:
                cursor.execute('''
                    SELECT user_id, trade_percent, max_positions
                    FROM auto_smart_settings
                    WHERE is_active = TRUE
                ''')
            else:
                cursor.execute('''
                    SELECT user_id, trade_percent, max_positions
                    FROM auto_smart_settings
                    WHERE is_active = 1
                ''')
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"get_active_auto_smart_traders error: {e}")
            return []

    def get_open_copy_position(self, user_id: int, token_address: str) -> Optional[Dict]:
        """Get the most recent open copy position for a user+token."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
            if self.use_postgres:
                cursor.execute('''
                    SELECT * FROM copy_performance
                    WHERE user_id = %s AND token_address = %s AND status = 'open'
                    ORDER BY opened_at DESC LIMIT 1
                ''', (user_id, token_address))
            else:
                cursor.execute('''
                    SELECT * FROM copy_performance
                    WHERE user_id = ? AND token_address = ? AND status = 'open'
                    ORDER BY opened_at DESC LIMIT 1
                ''', (user_id, token_address))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting open copy position: {e}")
            return None

    def get_copy_performance(self, user_id: int, watched_wallet: str = None,
                             limit: int = 20) -> List[Dict]:
        """Get copy trade performance history."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor if self.use_postgres else None)
            if watched_wallet:
                if self.use_postgres:
                    cursor.execute('''
                        SELECT * FROM copy_performance
                        WHERE user_id = %s AND watched_wallet = %s
                        ORDER BY opened_at DESC LIMIT %s
                    ''', (user_id, watched_wallet, limit))
                else:
                    cursor.execute('''
                        SELECT * FROM copy_performance
                        WHERE user_id = ? AND watched_wallet = ?
                        ORDER BY opened_at DESC LIMIT ?
                    ''', (user_id, watched_wallet, limit))
            else:
                if self.use_postgres:
                    cursor.execute('''
                        SELECT * FROM copy_performance
                        WHERE user_id = %s
                        ORDER BY opened_at DESC LIMIT %s
                    ''', (user_id, limit))
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

    def get_whale_rankings(self, user_id: int,
                           min_trades: int = 3,
                           lookback_days: int = 30) -> List[Dict]:
        """Rank all watched whales by performance."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    SELECT
                        cp.watched_wallet,
                        ww.chain,
                        ww.is_paused,
                        COUNT(*)                                                  AS total_trades,
                        SUM(CASE WHEN cp.whale_profit_percent > 0 THEN 1 ELSE 0 END) AS winning_trades,
                        AVG(cp.whale_profit_percent)                              AS avg_profit
                    FROM copy_performance cp
                    LEFT JOIN watched_wallets ww
                        ON ww.wallet_address = cp.watched_wallet
                        AND ww.user_id = cp.user_id
                    WHERE cp.user_id = %s
                      AND cp.status  = 'closed'
                      AND cp.closed_at >= CURRENT_TIMESTAMP + (%s || ' days')::INTERVAL
                    GROUP BY cp.watched_wallet
                    HAVING COUNT(*) >= %s
                    ORDER BY avg_profit DESC
                ''', (user_id, f'-{lookback_days}', min_trades))
            else:
                cursor.execute('''
                    SELECT
                        cp.watched_wallet,
                        ww.chain,
                        ww.is_paused,
                        COUNT(*)                                                  AS total_trades,
                        SUM(CASE WHEN cp.whale_profit_percent > 0 THEN 1 ELSE 0 END) AS winning_trades,
                        AVG(cp.whale_profit_percent)                              AS avg_profit
                    FROM copy_performance cp
                    LEFT JOIN watched_wallets ww
                        ON ww.wallet_address = cp.watched_wallet
                        AND ww.user_id = cp.user_id
                    WHERE cp.user_id = ?
                      AND cp.status  = 'closed'
                      AND cp.closed_at >= datetime('now', ? || ' days')
                    GROUP BY cp.watched_wallet
                    HAVING total_trades >= ?
                    ORDER BY avg_profit DESC
                ''', (user_id, f'-{lookback_days}', min_trades))
            rows = cursor.fetchall()
            conn.close()

            ranked = []
            for r in rows:
                total    = r['total_trades'] or 1
                winning  = r['winning_trades'] or 0
                win_rate = winning / total
                avg_p    = r['avg_profit'] or 0.0
                score    = win_rate * max(avg_p, 0)
                ranked.append({
                    'watched_wallet': r['watched_wallet'],
                    'chain':          r['chain'] or 'solana',
                    'is_paused':      bool(r['is_paused']),
                    'total_trades':   total,
                    'winning_trades': winning,
                    'win_rate':       round(win_rate, 3),
                    'avg_profit':     round(avg_p, 2),
                    'score':          round(score, 4),
                })
            ranked.sort(key=lambda x: x['score'], reverse=True)
            return ranked
        except Exception as e:
            logger.error(f"Error getting whale rankings: {e}")
            return []

    def get_whale_recent_loss(self, user_id: int, watched_wallet: str,
                              last_n: int = 5) -> float:
        """Return average whale profit % over last N closed trades."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute('''
                    SELECT whale_profit_percent FROM copy_performance
                    WHERE user_id = %s AND watched_wallet = %s AND status = 'closed'
                    ORDER BY closed_at DESC LIMIT %s
                ''', (user_id, watched_wallet, last_n))
            else:
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

    # Token blacklist / whitelist persistence

    def _ensure_token_list_table(self):
        try:
            conn = self.get_connection()
            if self.use_postgres:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_token_lists (
                        user_id INTEGER NOT NULL REFERENCES users(user_id),
                        list_type TEXT NOT NULL,
                        token_address TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, list_type, token_address)
                    )
                ''')
            else:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_token_lists (
                        user_id INTEGER NOT NULL,
                        list_type TEXT NOT NULL,
                        token_address TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, list_type, token_address)
                    )
                ''')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"_ensure_token_list_table error: {e}")

    def get_token_list(self, user_id: int, list_type: str) -> set:
        """Return the set of token addresses for 'blacklist' or 'whitelist'."""
        self._ensure_token_list_table()
        try:
            conn = self.get_connection()
            if self.use_postgres:
                rows = conn.execute(
                    'SELECT token_address FROM user_token_lists WHERE user_id=%s AND list_type=%s',
                    (user_id, list_type)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT token_address FROM user_token_lists WHERE user_id=? AND list_type=?',
                    (user_id, list_type)
                ).fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception as e:
            logger.error(f"get_token_list error: {e}")
            return set()

    def add_to_token_list(self, user_id: int, list_type: str, token_address: str):
        self._ensure_token_list_table()
        try:
            conn = self.get_connection()
            if self.use_postgres:
                conn.execute(
                    'INSERT INTO user_token_lists (user_id, list_type, token_address) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                    (user_id, list_type, token_address)
                )
            else:
                conn.execute(
                    'INSERT OR IGNORE INTO user_token_lists (user_id, list_type, token_address) VALUES (?,?,?)',
                    (user_id, list_type, token_address)
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"add_to_token_list error: {e}")

    def remove_from_token_list(self, user_id: int, list_type: str, token_address: str):
        self._ensure_token_list_table()
        try:
            conn = self.get_connection()
            if self.use_postgres:
                conn.execute(
                    'DELETE FROM user_token_lists WHERE user_id=%s AND list_type=%s AND token_address=%s',
                    (user_id, list_type, token_address)
                )
            else:
                conn.execute(
                    'DELETE FROM user_token_lists WHERE user_id=? AND list_type=? AND token_address=?',
                    (user_id, list_type, token_address)
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"remove_from_token_list error: {e}")

    def update_pending_trade_token_amount(self, user_id: int, token_address: str, amount: float):
        """Update the remaining token amount for an open smart trade."""
        try:
            conn = self.get_connection()
            if self.use_postgres:
                conn.execute(
                    'UPDATE smart_trades SET token_amount=%s WHERE user_id=%s AND token_address=%s AND is_closed=FALSE',
                    (amount, user_id, token_address)
                )
            else:
                conn.execute(
                    'UPDATE smart_trades SET token_amount=? WHERE user_id=? AND token_address=? AND is_closed=0',
                    (amount, user_id, token_address)
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"update_pending_trade_token_amount error: {e}")


# Singleton instance
db = Database()