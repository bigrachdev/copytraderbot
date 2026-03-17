"""
Wallet monitoring service for tracking watched wallets
"""
import logging
import asyncio
from typing import Dict, List
from datetime import datetime
from trading.copy_trader import copy_trader
from data.database import db

logger = logging.getLogger(__name__)


class WalletMonitor:
    """Monitor all user wallets and execute copy trades"""
    
    def __init__(self):
        self.active_monitors = {}  # Track active monitoring tasks
    
    async def start_all_monitors(self):
        """Start monitoring all users' watched wallets"""
        try:
            logger.info("🚀 Starting wallet monitoring service...")
            
            # Get all users with watched wallets
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT DISTINCT user_id FROM watched_wallets WHERE is_active = 1')
            user_ids = [row[0] for row in cursor.fetchall()]
            conn.close()
            
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
                
                # Check for new users
                conn = db.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT user_id FROM watched_wallets WHERE is_active = 1')
                user_ids = [row[0] for row in cursor.fetchall()]
                conn.close()
                
                for user_id in user_ids:
                    if user_id not in self.active_monitors:
                        task = asyncio.create_task(copy_trader.start_monitoring_for_user(user_id))
                        self.active_monitors[user_id] = task
        
        except asyncio.CancelledError:
            logger.info("Wallet monitor cancelled")
            await self.stop_all_monitors()
        except Exception as e:
            logger.error(f"Error in wallet monitor: {e}")


monitor = WalletMonitor()
