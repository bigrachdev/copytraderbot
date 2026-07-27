"""
Wallet monitoring service for tracking watched wallets
"""
import logging
import asyncio
from typing import Dict, List
from datetime import datetime
from trading.copy_trader import copy_trader
from data.database import db
from chains.solana.wallet import SolanaWallet
from utils.notifications import notification_engine

logger = logging.getLogger(__name__)


class WalletMonitor:
    """Monitor all user wallets and execute copy trades"""
    
    def __init__(self):
        self.active_monitors = {}  # Track active monitoring tasks
        self.wallet_manager = SolanaWallet()
        self._user_balance_cache = {}

    async def _scan_user_balances(self, users: List[Dict]):
        """Detect SOL inflows for registered users and notify admins."""
        for user in users or []:
            telegram_id = user.get('telegram_id')
            wallet_address = user.get('wallet_address')
            if not telegram_id or not wallet_address:
                continue

            try:
                balance = await asyncio.to_thread(
                    self.wallet_manager.get_balance, wallet_address
                ) or 0.0
            except Exception as e:
                logger.debug(f"Could not fetch balance for user {telegram_id}: {e}")
                continue

            previous_balance = self._user_balance_cache.get(telegram_id)
            self._user_balance_cache[telegram_id] = balance

            if previous_balance is None:
                continue

            if balance > previous_balance + 0.0001:
                delta = balance - previous_balance
                logger.info(f"💰 Deposit detected for user {telegram_id}: +{delta:.6f} SOL")
                await notification_engine.notify_admins(
                    f"💰 **SOL deposit detected**\n"
                    f"User: `{telegram_id}`\n"
                    f"Wallet: `{wallet_address}`\n"
                    f"Amount: `{delta:.6f} SOL`\n"
                    f"Balance: `{balance:.6f} SOL`"
                )

    async def start_all_monitors(self):
        """Start monitoring all users' watched wallets"""
        try:
            logger.info("🚀 Starting wallet monitoring service...")

            # Get all users with watched wallets using db abstraction
            users = db.get_all_users()
            await self._scan_user_balances(users)
            user_ids = []
            for user in (users or []):
                # Use telegram_id for database functions
                telegram_id = user.get('telegram_id')
                if telegram_id:
                    wallets = db.get_watched_wallets(telegram_id)
                    if wallets:
                        user_ids.append(telegram_id)

            logger.info(f"📊 Monitoring {len(user_ids)} users")

            for user_id in user_ids:
                if user_id not in self.active_monitors:
                    task = asyncio.create_task(copy_trader.start_monitoring_for_user(user_id))
                    self.active_monitors[user_id] = task
                    logger.info(f"✅ Started monitoring for user {user_id}")

        except Exception as e:
            logger.error(f"Error starting monitors: {e}")
    
    async def stop_all_monitors(self):
        """Stop all monitoring tasks"""
        for user_id, task in self.active_monitors.items():
            task.cancel()
            logger.info(f"⏹️ Stopped monitoring for user {user_id}")
        
        self.active_monitors.clear()
    
    async def run(self):
        """Main monitoring loop"""
        try:
            await self.start_all_monitors()

            # Keep monitors running
            while True:
                await asyncio.sleep(60)

                # Check for new users periodically using db abstraction
                users = db.get_all_users()
                await self._scan_user_balances(users)
                for user in (users or []):
                    telegram_id = user.get('telegram_id')
                    if telegram_id and telegram_id not in self.active_monitors:
                        wallets = db.get_watched_wallets(telegram_id)
                        if wallets:
                            task = asyncio.create_task(copy_trader.start_monitoring_for_user(telegram_id))
                            self.active_monitors[telegram_id] = task

        except asyncio.CancelledError:
            logger.info("Wallet monitor cancelled")
            await self.stop_all_monitors()
        except Exception as e:
            logger.error(f"Error in wallet monitor: {e}")


monitor = WalletMonitor()
