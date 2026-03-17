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

logger = logging.getLogger(__name__)


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
        """Check if user is admin"""
        return user_id in self.admin_ids
    
    def promote_user_to_admin(self, user_id: int) -> bool:
        """Promote user to admin"""
        try:
            self.admin_ids.add(user_id)
            db.conn.execute(
                "UPDATE users SET is_admin=1 WHERE telegram_id=?",
                (user_id,)
            )
            db.conn.commit()
            logger.info(f"✅ User {user_id} promoted to admin")
            return True
        except Exception as e:
            logger.error(f"❌ Error promoting user: {e}")
            return False
    
    def demote_admin(self, user_id: int) -> bool:
        """Demote admin to regular user"""
        try:
            self.admin_ids.discard(user_id)
            db.conn.execute(
                "UPDATE users SET is_admin=0 WHERE telegram_id=?",
                (user_id,)
            )
            db.conn.commit()
            logger.info(f"✅ User {user_id} demoted from admin")
            return True
        except Exception as e:
            logger.error(f"❌ Error demoting admin: {e}")
            return False
    
    def get_all_users(self) -> List[Dict]:
        """Get all users"""
        try:
            cursor = db.conn.execute(
                "SELECT telegram_id, wallet_address, is_admin, created_at FROM users"
            )
            users = []
            for row in cursor.fetchall():
                users.append({
                    'telegram_id': row[0],
                    'wallet_address': row[1],
                    'is_admin': bool(row[2]),
                    'created_at': row[3]
                })
            return users
        except Exception as e:
            logger.error(f"❌ Error getting users: {e}")
            return []
    
    def get_user_wallets(self, user_id: int) -> List[Dict]:
        """Get all wallets for a user"""
        try:
            user = db.get_user(user_id)
            if not user:
                return []
            
            wallets = []
            
            # Main wallet
            if user.get('wallet_address'):
                try:
                    balance = self.wallet.get_balance(user['wallet_address'])
                    wallets.append({
                        'type': 'main',
                        'address': user['wallet_address'],
                        'balance': balance,
                        'is_encrypted': True,
                        'created_at': user.get('created_at')
                    })
                except:
                    wallets.append({
                        'type': 'main',
                        'address': user['wallet_address'],
                        'balance': 0,
                        'is_encrypted': True
                    })
            
            # Vanity wallets
            cursor = db.conn.execute(
                "SELECT wallet_address, prefix, difficulty, created_at FROM vanity_wallets WHERE telegram_id=?",
                (user_id,)
            )
            for row in cursor.fetchall():
                try:
                    balance = self.wallet.get_balance(row[0])
                except:
                    balance = 0
                
                wallets.append({
                    'type': 'vanity',
                    'address': row[0],
                    'prefix': row[1],
                    'difficulty': row[2],
                    'balance': balance,
                    'is_encrypted': True,
                    'created_at': row[3]
                })
            
            return wallets
        except Exception as e:
            logger.error(f"❌ Error getting user wallets: {e}")
            return []
    
    def get_wallet_info(self, wallet_address: str) -> Optional[Dict]:
        """Get detailed wallet info"""
        try:
            balance = self.wallet.get_balance(wallet_address)
            
            # Get associated trades
            cursor = db.conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) FROM trades WHERE input_mint=?",
                (wallet_address,)
            )
            trade_data = cursor.fetchone()
            total_trades = trade_data[0] or 0
            winning_trades = trade_data[1] or 0
            
            # Calculate total profit
            cursor = db.conn.execute(
                "SELECT SUM(output_amount - input_amount) FROM trades WHERE input_mint=? OR output_mint=?",
                (wallet_address, wallet_address)
            )
            profit = cursor.fetchone()[0] or 0
            
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
            return self.wallet.get_balance(wallet_address)
        except Exception as e:
            logger.error(f"❌ Error getting balance: {e}")
            return 0.0
    
    def get_wallet_profit(self, wallet_address: str) -> Dict:
        """Calculate profit/loss for wallet"""
        try:
            cursor = db.conn.execute("""
                SELECT 
                    SUM(CASE WHEN output_amount > input_amount THEN (output_amount - input_amount) ELSE 0 END) as total_profit,
                    SUM(CASE WHEN output_amount < input_amount THEN (output_amount - input_amount) ELSE 0 END) as total_loss,
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN output_amount > input_amount THEN 1 ELSE 0 END) as winning_trades
                FROM trades 
                WHERE telegram_id IN (SELECT telegram_id FROM users WHERE wallet_address=?)
            """, (wallet_address,))
            
            row = cursor.fetchone()
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
            import os
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
    
    def delete_user_wallet(self, user_id: int, confirm: bool = False) -> Tuple[bool, str]:
        """Delete user wallet and all associated data"""
        try:
            if not confirm:
                return False, "Confirmation required"
            
            # Delete trades
            db.conn.execute("DELETE FROM trades WHERE telegram_id=?", (user_id,))
            
            # Delete risk orders
            db.conn.execute("DELETE FROM risk_orders WHERE telegram_id=?", (user_id,))
            
            # Delete vanity wallets
            db.conn.execute("DELETE FROM vanity_wallets WHERE telegram_id=?", (user_id,))
            
            # Delete watched wallets
            db.conn.execute("DELETE FROM watched_wallets WHERE telegram_id=?", (user_id,))
            
            # Delete pending trades
            db.conn.execute("DELETE FROM pending_trades WHERE telegram_id=?", (user_id,))
            
            # Delete user
            db.conn.execute("DELETE FROM users WHERE telegram_id=?", (user_id,))
            
            db.conn.commit()
            logger.info(f"✅ User {user_id} and all wallet data deleted")
            return True, "User wallet deleted successfully"
        except Exception as e:
            logger.error(f"❌ Error deleting wallet: {e}")
            return False, f"Error: {str(e)}"
    
    def get_bot_stats(self) -> Dict:
        """Get overall bot statistics"""
        try:
            # User count
            cursor = db.conn.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0] or 0
            
            # Admin count
            cursor = db.conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=1")
            total_admins = cursor.fetchone()[0] or 0
            
            # Trade count
            cursor = db.conn.execute("SELECT COUNT(*) FROM trades")
            total_trades = cursor.fetchone()[0] or 0
            
            # Total profit
            cursor = db.conn.execute(
                "SELECT SUM(output_amount - input_amount) FROM trades"
            )
            total_profit = cursor.fetchone()[0] or 0
            
            # Vanity wallets generated
            cursor = db.conn.execute("SELECT COUNT(*) FROM vanity_wallets")
            total_vanity = cursor.fetchone()[0] or 0
            
            # Active risk orders
            cursor = db.conn.execute("SELECT COUNT(*) FROM risk_orders WHERE status='active'")
            active_orders = cursor.fetchone()[0] or 0
            
            # Copy trading wallets
            cursor = db.conn.execute("SELECT COUNT(*) FROM watched_wallets")
            copy_targets = cursor.fetchone()[0] or 0
            
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
