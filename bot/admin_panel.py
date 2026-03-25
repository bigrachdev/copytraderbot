"""
Admin panel for managing bot, wallets, and user access
Provides secure admin interface for wallet management and monitoring
"""
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from data.database import db
from chains.solana.wallet import SolanaWallet
from chains.solana.dex_swaps import swapper
from wallet.encryption import encryption
import os
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


def _execute_query(conn, query, params=None):
    """Execute query with proper parameter placeholders for PostgreSQL/SQLite"""
    cursor = conn.cursor()
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    return cursor


class AdminPanel:
    """Admin panel for bot management"""

    def __init__(self):
        """Initialize admin panel"""
        self.admin_ids = set()  # Loaded from config
        self.load_admins()
        self.wallet = SolanaWallet()
        logger.info("✅ Admin panel initialized")

    def load_admins(self):
        """Load admin IDs from environment"""
        admin_list = os.getenv('ADMIN_IDS', '')
        if admin_list:
            self.admin_ids = set(int(uid.strip()) for uid in admin_list.split(','))
            logger.info(f"📍 Loaded {len(self.admin_ids)} admin(s)")
        else:
            logger.warning("⚠️  No admin IDs configured in ADMIN_IDS")

    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin — env var list OR database flag."""
        if user_id in self.admin_ids:
            return True
        # Fallback: check is_admin flag in the database
        return db.is_user_admin(user_id)

    def promote_user_to_admin(self, user_id: int) -> bool:
        """Promote user to admin (user_id = telegram_id)"""
        try:
            self.admin_ids.add(user_id)
            db.update_user_setting(user_id, 'is_admin', True)
            logger.info(f"✅ User {user_id} promoted to admin")
            return True
        except Exception as e:
            logger.error(f"❌ Error promoting user: {e}")
            return False

    def demote_admin(self, user_id: int) -> bool:
        """Demote admin to regular user (user_id = telegram_id)"""
        try:
            self.admin_ids.discard(user_id)
            db.update_user_setting(user_id, 'is_admin', False)
            logger.info(f"✅ User {user_id} demoted from admin")
            return True
        except Exception as e:
            logger.error(f"❌ Error demoting admin: {e}")
            return False

    def get_all_users(self) -> List[Dict]:
        """Get all users"""
        return db.get_all_users_list()

    def get_user_wallets(self, user_id: int) -> List[Dict]:
        """Get all wallets for a user (user_id = telegram_id) including all types"""
        try:
            user = db.get_user(user_id)
            if not user:
                return []

            wallets = []
            internal_user_id = user.get('user_id')

            # 1. Main wallet
            if user.get('wallet_address'):
                try:
                    balance = self.wallet.get_balance(user['wallet_address'])
                except Exception as e:
                    logger.error(f"❌ Error getting main wallet balance: {e}")
                    balance = 0
                wallets.append({
                    'type': 'main',
                    'address': user['wallet_address'],
                    'balance': balance,
                    'is_encrypted': bool(user.get('encrypted_private_key')),
                    'encrypted_key_preview': self._get_key_preview(user.get('encrypted_private_key')),
                    'public_key': user.get('public_key'),
                    'created_at': user.get('created_at')
                })

            # 2. Trading wallet (separate wallet for copy trading)
            if user.get('trading_wallet_address'):
                try:
                    balance = self.wallet.get_balance(user['trading_wallet_address'])
                except Exception as e:
                    logger.error(f"❌ Error getting trading wallet balance: {e}")
                    balance = 0
                wallets.append({
                    'type': 'trading',
                    'address': user['trading_wallet_address'],
                    'balance': balance,
                    'is_encrypted': bool(user.get('encrypted_trading_key')),
                    'encrypted_key_preview': self._get_key_preview(user.get('encrypted_trading_key')),
                    'is_separate': bool(user.get('use_separate_trading_wallet')),
                    'created_at': user.get('created_at')
                })

            # 3. Base (EVM) wallet
            if user.get('base_wallet_address'):
                try:
                    # For EVM wallets, we can't use Solana wallet class
                    balance = 0  # Would need EVM wallet integration
                except Exception as e:
                    logger.error(f"❌ Error getting Base wallet balance: {e}")
                    balance = 0
                wallets.append({
                    'type': 'base_evm',
                    'address': user['base_wallet_address'],
                    'balance': balance,
                    'is_encrypted': bool(user.get('encrypted_base_key')),
                    'encrypted_key_preview': self._get_key_preview(user.get('encrypted_base_key')),
                    'created_at': user.get('created_at')
                })

            # 4. Chain wallets (ETH, BSC, TON, etc.)
            chain_wallets = db.get_all_chain_wallets(user_id)
            for chain, wallet_info in chain_wallets.items():
                try:
                    # Try to get balance based on chain type
                    if chain.lower() == 'solana':
                        balance = self.wallet.get_balance(wallet_info['address']) or 0
                    else:
                        balance = 0  # EVM chains need separate integration
                except Exception as e:
                    logger.error(f"❌ Error getting {chain} wallet balance: {e}")
                    balance = 0
                wallets.append({
                    'type': f'chain_{chain.lower()}',
                    'chain': chain,
                    'address': wallet_info['address'],
                    'balance': balance,
                    'is_encrypted': bool(wallet_info.get('encrypted_key')),
                    'encrypted_key_preview': self._get_key_preview(wallet_info.get('encrypted_key')),
                    'created_at': wallet_info.get('created_at')
                })

            # 5. Vanity wallets
            vanity_wallets = db.get_vanity_wallets(internal_user_id)
            for vanity in vanity_wallets:
                try:
                    balance = self.wallet.get_balance(vanity['address'])
                except Exception as e:
                    logger.error(f"❌ Error getting vanity wallet balance: {e}")
                    balance = 0
                wallets.append({
                    'type': 'vanity',
                    'address': vanity['address'],
                    'prefix': vanity['prefix'],
                    'match_position': vanity['match_position'],
                    'case_sensitive': vanity['case_sensitive'],
                    'difficulty': vanity['difficulty'],
                    'balance': balance,
                    'is_encrypted': True,  # Vanity wallets always have encrypted keys
                    'encrypted_key_preview': '••••••••',  # Vanity keys are stored
                    'created_at': vanity['created_at']
                })

            return wallets
        except Exception as e:
            logger.error(f"❌ Error getting user wallets: {e}")
            return []

    def _get_key_preview(self, encrypted_key: str) -> str:
        """Get a safe preview of encrypted key (first/last chars)"""
        if not encrypted_key:
            return 'Not stored'
        if len(encrypted_key) > 20:
            return f"{encrypted_key[:8]}...{encrypted_key[-8:]}"
        return "••••••••" * 3

    def get_wallet_info(self, wallet_address: str) -> Optional[Dict]:
        """Get detailed wallet info"""
        try:
            balance = self.wallet.get_balance(wallet_address)

            # Get user trades from database - use a simple count
            conn = db.get_connection()
            cursor = conn.cursor()
            
            # Get associated trades - PostgreSQL compatible
            try:
                cursor.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) FROM trades WHERE input_mint=%s",
                    (wallet_address,)
                )
            except Exception:
                # Fallback for SQLite
                cursor.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) FROM trades WHERE input_mint=?",
                    (wallet_address,)
                )
            trade_data = cursor.fetchone()
            total_trades = trade_data[0] or 0
            winning_trades = trade_data[1] or 0

            # Calculate total profit
            try:
                cursor.execute(
                    "SELECT SUM(output_amount - input_amount) FROM trades WHERE input_mint=%s OR output_mint=%s",
                    (wallet_address, wallet_address)
                )
            except Exception:
                cursor.execute(
                    "SELECT SUM(output_amount - input_amount) FROM trades WHERE input_mint=? OR output_mint=?",
                    (wallet_address, wallet_address)
                )
            profit = cursor.fetchone()[0] or 0
            conn.close()

            return {
                'address': wallet_address,
                'balance': balance,
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'win_rate': (winning_trades / total_trades * 100) if total_trades > 0 else 0,
                'estimated_profit': profit,
                'last_updated': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ Error getting wallet info: {e}")
            return None

    def get_wallet_balance(self, wallet_address: str) -> float:
        """Get SOL balance for wallet"""
        try:
            return self.wallet.get_balance(wallet_address) or 0.0
        except Exception as e:
            logger.error(f"❌ Error getting balance: {e}")
            return 0.0

    def get_wallet_profit(self, wallet_address: str) -> Dict:
        """Calculate profit/loss for wallet"""
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT
                        SUM(CASE WHEN output_amount > input_amount THEN (output_amount - input_amount) ELSE 0 END) as total_profit,
                        SUM(CASE WHEN output_amount < input_amount THEN (output_amount - input_amount) ELSE 0 END) as total_loss,
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) as winning_trades
                    FROM trades
                    WHERE user_id IN (SELECT user_id FROM users WHERE wallet_address=%s)
                """, (wallet_address,))
            except Exception:
                cursor.execute("""
                    SELECT
                        SUM(CASE WHEN output_amount > input_amount THEN (output_amount - input_amount) ELSE 0 END) as total_profit,
                        SUM(CASE WHEN output_amount < input_amount THEN (output_amount - input_amount) ELSE 0 END) as total_loss,
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) as winning_trades
                    FROM trades
                    WHERE user_id IN (SELECT user_id FROM users WHERE wallet_address=?)
                """, (wallet_address,))

            row = cursor.fetchone()
            conn.close()
            if not row:
                return {
                    'total_profit': 0,
                    'total_loss': 0,
                    'net_profit': 0,
                    'total_trades': 0,
                    'win_rate': 0.0
                }

            total_profit = row[0] or 0
            total_loss = row[1] or 0
            total_trades = row[2] or 0
            winning_trades = row[3] or 0
            net_profit = total_profit + total_loss
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

            return {
                'total_profit': total_profit,
                'total_loss': abs(total_loss),
                'net_profit': net_profit,
                'total_trades': total_trades,
                'win_rate': win_rate
            }
        except Exception as e:
            logger.error(f"❌ Error calculating profit: {e}")
            return {
                'total_profit': 0,
                'total_loss': 0,
                'net_profit': 0,
                'total_trades': 0,
                'win_rate': 0.0
            }

    def get_encrypted_key_preview(self, user_id: int) -> Optional[str]:
        """Get safe preview of encrypted private key (first/last few chars only)"""
        try:
            user = db.get_user(user_id)
            if not user or not user.get('encrypted_private_key'):
                return None

            encrypted = user['encrypted_private_key']
            if len(encrypted) > 20:
                return f"{encrypted[:8]}...{encrypted[-8:]}"
            else:
                return "••••••••" * 3
        except Exception as e:
            logger.error(f"❌ Error getting key preview: {e}")
            return None

    def decrypt_wallet_key(self, user_id: int, master_password: str) -> Optional[str]:
        """Decrypt wallet private key for viewing (admin only)"""
        try:
            # Verify master password
            if master_password != os.getenv('ENCRYPTION_MASTER_PASSWORD'):
                logger.warning(f"⚠️  Failed decrypt attempt by {user_id} - wrong password")
                return None

            user = db.get_user(user_id)
            if not user or not user.get('encrypted_private_key'):
                return None

            # Decrypt using encryption instance
            decrypted = encryption.decrypt(user['encrypted_private_key'])
            if decrypted:
                logger.info(f"✅ Private key decrypted by admin for user {user_id}")
            return decrypted
        except Exception as e:
            logger.error(f"❌ Error decrypting key: {e}")
            return None

    def decrypt_specific_wallet_key(self, user_id: int, wallet_address: str, 
                                     master_password: str) -> Optional[str]:
        """Decrypt private key for a specific wallet (main, trading, base, chain, or vanity)"""
        try:
            # Verify master password
            if master_password != os.getenv('ENCRYPTION_MASTER_PASSWORD'):
                logger.warning(f"⚠️  Failed decrypt attempt by {user_id} - wrong password")
                return None

            user = db.get_user(user_id)
            if not user:
                return None

            encrypted_key = None

            # Check main wallet
            if user.get('wallet_address') == wallet_address:
                encrypted_key = user.get('encrypted_private_key')
            # Check trading wallet
            elif user.get('trading_wallet_address') == wallet_address:
                encrypted_key = user.get('encrypted_trading_key')
            # Check Base wallet
            elif user.get('base_wallet_address') == wallet_address:
                encrypted_key = user.get('encrypted_base_key')
            else:
                # Check chain wallets
                chain_wallets = db.get_all_chain_wallets(user_id)
                for chain, wallet_info in chain_wallets.items():
                    if wallet_info['address'] == wallet_address:
                        encrypted_key = wallet_info.get('encrypted_key')
                        break

                # Check vanity wallets if still not found
                if not encrypted_key:
                    internal_user_id = user.get('user_id')
                    vanity_wallets = db.get_vanity_wallets(internal_user_id)
                    for vanity in vanity_wallets:
                        if vanity['address'] == wallet_address:
                            # Vanity wallets store encrypted_key directly
                            encrypted_key = vanity.get('encrypted_key')
                            break

            if not encrypted_key:
                logger.warning(f"⚠️  No encrypted key found for wallet {wallet_address[:10]}...")
                return None

            # Decrypt
            decrypted = encryption.decrypt(encrypted_key)
            if decrypted:
                logger.info(f"✅ Private key decrypted by admin for wallet {wallet_address[:10]}...")
            return decrypted
        except Exception as e:
            logger.error(f"❌ Error decrypting specific wallet key: {e}")
            return None

    def get_complete_wallet_report(self, user_id: int) -> Dict:
        """Get comprehensive wallet report for admin (includes all wallet types)"""
        try:
            user = db.get_user(user_id)
            if not user:
                return {'error': 'User not found'}

            internal_user_id = user.get('user_id')
            report = {
                'user_id': user_id,
                'internal_id': internal_user_id,
                'wallet_address': user.get('wallet_address'),
                'is_admin': bool(user.get('is_admin')),
                'created_at': user.get('created_at'),
                'wallets': self.get_user_wallets(user_id),
                'summary': {
                    'total_wallets': 0,
                    'main_wallet': False,
                    'trading_wallet': False,
                    'base_wallet': False,
                    'chain_wallets_count': 0,
                    'vanity_wallets_count': 0,
                    'total_vanity_difficulty': 0,
                    'wallets_with_keys': 0
                }
            }

            # Calculate summary
            wallets = report['wallets']
            report['summary']['total_wallets'] = len(wallets)
            
            for w in wallets:
                if w['type'] == 'main':
                    report['summary']['main_wallet'] = True
                elif w['type'] == 'trading':
                    report['summary']['trading_wallet'] = True
                elif w['type'] == 'base_evm':
                    report['summary']['base_wallet'] = True
                elif w['type'].startswith('chain_'):
                    report['summary']['chain_wallets_count'] += 1
                elif w['type'] == 'vanity':
                    report['summary']['vanity_wallets_count'] += 1
                    report['summary']['total_vanity_difficulty'] += w.get('difficulty', 0)
                
                if w.get('is_encrypted'):
                    report['summary']['wallets_with_keys'] += 1

            return report
        except Exception as e:
            logger.error(f"❌ Error generating wallet report: {e}")
            return {'error': str(e)}

    def delete_user_wallet(self, user_id: int, confirm: bool = False) -> Tuple[bool, str]:
        """Delete user wallet and all associated data (user_id = telegram_id)"""
        try:
            if not confirm:
                return False, "Confirmation required"

            # Get user to find internal user_id
            user = db.get_user(user_id)
            if not user:
                return False, "User not found"
            internal_id = user.get('user_id')

            conn = db.get_connection()
            cursor = conn.cursor()
            
            # Delete all associated data - PostgreSQL compatible
            try:
                cursor.execute("DELETE FROM trades WHERE user_id=%s", (internal_id,))
                cursor.execute("DELETE FROM risk_orders WHERE user_id=%s", (internal_id,))
                cursor.execute("DELETE FROM vanity_wallets WHERE user_id=%s", (internal_id,))
                cursor.execute("DELETE FROM watched_wallets WHERE user_id=%s", (internal_id,))
                cursor.execute("DELETE FROM pending_trades WHERE user_id=%s", (internal_id,))
                cursor.execute("DELETE FROM users WHERE user_id=%s", (internal_id,))
            except Exception:
                # Fallback for SQLite
                cursor.execute("DELETE FROM trades WHERE user_id=?", (internal_id,))
                cursor.execute("DELETE FROM risk_orders WHERE user_id=?", (internal_id,))
                cursor.execute("DELETE FROM vanity_wallets WHERE user_id=?", (internal_id,))
                cursor.execute("DELETE FROM watched_wallets WHERE user_id=?", (internal_id,))
                cursor.execute("DELETE FROM pending_trades WHERE user_id=?", (internal_id,))
                cursor.execute("DELETE FROM users WHERE user_id=?", (internal_id,))

            conn.commit()
            conn.close()
            logger.info(f"✅ User {user_id} and all wallet data deleted")
            return True, "User wallet deleted successfully"
        except Exception as e:
            logger.error(f"❌ Error deleting wallet: {e}")
            return False, f"Error: {str(e)}"

    def get_bot_stats(self) -> Dict:
        """Get overall bot statistics"""
        try:
            conn = db.get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute("SELECT COUNT(*) FROM users")
                total_users = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM users WHERE is_admin=%s", (True,))
                total_admins = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM trades")
                total_trades = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT SUM(output_amount - input_amount) FROM trades")
                total_profit = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM vanity_wallets")
                total_vanity = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM risk_orders WHERE is_active=%s", (True,))
                active_orders = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM watched_wallets")
                copy_targets = cursor.fetchone()[0] or 0
            except Exception:
                # Fallback for SQLite
                cursor.execute("SELECT COUNT(*) FROM users")
                total_users = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM users WHERE is_admin=1")
                total_admins = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM trades")
                total_trades = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT SUM(output_amount - input_amount) FROM trades")
                total_profit = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM vanity_wallets")
                total_vanity = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM risk_orders WHERE is_active=1")
                active_orders = cursor.fetchone()[0] or 0
                
                cursor.execute("SELECT COUNT(*) FROM watched_wallets")
                copy_targets = cursor.fetchone()[0] or 0

            conn.close()

            return {
                'total_users': total_users,
                'total_admins': total_admins,
                'total_trades': total_trades,
                'total_profit': total_profit,
                'total_vanity_wallets': total_vanity,
                'active_risk_orders': active_orders,
                'copy_trading_targets': copy_targets,
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ Error getting bot stats: {e}")
            return {}

    def generate_admin_report(self) -> str:
        """Generate comprehensive admin report"""
        try:
            stats = self.get_bot_stats()
            users = self.get_all_users()

            report = (
                f"📊 **BOT ADMIN REPORT**\n\n"
                f"**User Statistics:**\n"
                f"  • Total Users: {stats['total_users']}\n"
                f"  • Total Admins: {stats['total_admins']}\n"
                f"  • Active Users: {len([u for u in users if u])}\n\n"
                f"**Trading Statistics:**\n"
                f"  • Total Trades: {stats['total_trades']}\n"
                f"  • Total Profit: ${stats['total_profit']:.2f}\n"
                f"  • Avg Profit/Trade: ${stats['total_profit'] / max(stats['total_trades'], 1):.2f}\n\n"
                f"**Feature Usage:**\n"
                f"  • Vanity Wallets: {stats['total_vanity_wallets']}\n"
                f"  • Active Risk Orders: {stats['active_risk_orders']}\n"
                f"  • Copy Trading Targets: {stats['copy_trading_targets']}\n\n"
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return report
        except Exception as e:
            logger.error(f"❌ Error generating report: {e}")
            return "❌ Error generating report"


# Global admin instance
admin_panel = AdminPanel()
