"""
Smart notifications system - alerts users on profits and suggests selling
"""
import logging
import asyncio
import uuid
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from data.database import db
from chains.solana.spl_tokens import token_manager
from chains.solana.dex_swaps import swapper
from config import (
    NOTIFICATION_CHECK_INTERVAL,
    NOTIFICATION_PROFIT_MILESTONES,
    NOTIFICATION_CUTLOSS_THRESHOLD,
    NOTIFICATION_AGING_HOURS,
    NOTIFICATION_AGING_MIN_ROI,
)

logger = logging.getLogger(__name__)


class SmartNotificationEngine:
    """Send smart profit alerts and sell suggestions"""

    def __init__(self):
        self.profit_thresholds = NOTIFICATION_PROFIT_MILESTONES
        self.check_interval = NOTIFICATION_CHECK_INTERVAL
        self.active_positions = {}  # Track monitored positions
        self._send_callback = None        # async (user_id, text) -> None
        self._trade_opened_callback = None  # async (user_id, pos_id, text) -> None

    def set_send_callback(self, callback):
        """Register the async callback used to send plain Telegram messages.
        Signature: async (user_id: int, message: str) -> None
        """
        self._send_callback = callback

    def set_trade_opened_callback(self, callback):
        """Register the callback for rich trade-opened notifications (with sell buttons).
        Signature: async (user_id: int, position_id: str, text: str) -> None
        """
        self._trade_opened_callback = callback

    async def notify_user(self, user_id: int, message: str):
        """Send a plain text notification to a user via the registered Telegram callback."""
        if self._send_callback is None:
            logger.warning(f"notify_user called but no send callback registered (user {user_id})")
            return
        try:
            await self._send_callback(user_id, message)
        except Exception as e:
            logger.error(f"Error sending notification to user {user_id}: {e}")

    async def notify_trade_opened(self, user_id: int, position_id: str, message: str):
        """Send a trade-opened notification with inline sell/view buttons.
        Falls back to plain text if the rich callback is not registered."""
        if self._trade_opened_callback:
            try:
                await self._trade_opened_callback(user_id, position_id, message)
                return
            except Exception as e:
                logger.error(f"notify_trade_opened callback failed for {user_id}: {e}")
        # fallback
        await self.notify_user(user_id, message)
    
    def track_position(self, user_id: int, token_address: str,
                      amount_bought: float, entry_price: float,
                      dex: str, position_type: str = 'smart',
                      db_position_id: int = None) -> str:
        """Start tracking a position. Returns position_id (short key safe for Telegram callbacks)."""
        position_id = uuid.uuid4().hex[:10]  # 10-char hex — fits well within 64-byte callback limit
        position = {
            'user_id': user_id,
            'token_address': token_address,
            'amount_bought': amount_bought,
            'entry_price': entry_price,
            'entry_time': datetime.now(),
            'dex': dex,
            'current_price': entry_price,
            'roi': 0,
            'profit': 0,
            'alerts_sent': set(),
            'active': True,
            'position_type': position_type,   # 'smart' | 'copy'
            'db_position_id': db_position_id, # copy_performance row id (copy trades only)
        }
        self.active_positions[position_id] = position
        logger.info(f"📍 Position tracked: {position_id} ({token_address[:8]}… {position_type})")
        return position_id
    
    def calculate_roi(self, current_price: float, entry_price: float) -> float:
        """Calculate ROI %"""
        if entry_price == 0:
            return 0
        return ((current_price - entry_price) / entry_price) * 100
    
    def calculate_profit(self, amount: float, current_price: float, 
                        entry_price: float) -> float:
        """Calculate profit in SOL"""
        return amount * (current_price - entry_price)
    
    async def check_positions(self) -> List[Dict]:
        """Check all positions for alerts"""
        alerts = []
        
        for position_id, position in list(self.active_positions.items()):
            if not position['active']:
                continue
            
            try:
                # Get current price (simplified - in production use actual price feed)
                current_price = await self.get_current_price(
                    position['token_address'],
                    position['dex']
                )
                
                if current_price is None:
                    continue
                
                position['current_price'] = current_price
                roi = self.calculate_roi(current_price, position['entry_price'])
                profit = self.calculate_profit(
                    position['amount_bought'],
                    current_price,
                    position['entry_price']
                )
                
                position['roi'] = roi
                position['profit'] = profit
                
                # Check for profit milestones
                for threshold in self.profit_thresholds:
                    if roi >= threshold and threshold not in position['alerts_sent']:
                        alerts.append({
                            'type': 'profit_milestone',
                            'position_id': position_id,
                            'user_id': position['user_id'],
                            'token': position['token_address'],
                            'roi': roi,
                            'profit': profit,
                            'threshold': threshold,
                            'current_price': current_price,
                            'entry_price': position['entry_price']
                        })
                        position['alerts_sent'].add(threshold)
                        logger.info(f"🎉 Profit alert: {roi:.1f}% ROI")
                
                # Check for losses (suggest cut losses)
                if roi <= NOTIFICATION_CUTLOSS_THRESHOLD and 'cutloss' not in position['alerts_sent']:
                    alerts.append({
                        'type': 'cutloss_suggestion',
                        'position_id': position_id,
                        'user_id': position['user_id'],
                        'token': position['token_address'],
                        'roi': roi,
                        'profit': profit,
                        'current_price': current_price
                    })
                    position['alerts_sent'].add('cutloss')
                    logger.warning(f"⚠️ Cut-loss alert: {roi:.1f}% loss")
                
                # Check for time-based holding (over 24h in profit)
                time_held = datetime.now() - position['entry_time']
                if time_held > timedelta(hours=NOTIFICATION_AGING_HOURS) and roi > NOTIFICATION_AGING_MIN_ROI and 'aging' not in position['alerts_sent']:
                    alerts.append({
                        'type': 'aging_position',
                        'position_id': position_id,
                        'user_id': position['user_id'],
                        'token': position['token_address'],
                        'roi': roi,
                        'profit': profit,
                        'hours_held': time_held.total_seconds() / 3600
                    })
                    position['alerts_sent'].add('aging')
                    logger.info(f"⏰ Aging position alert: held {time_held.total_seconds()/3600:.1f}h")
            
            except Exception as e:
                logger.error(f"Error checking position {position_id}: {e}")
        
        return alerts
    
    async def get_current_price(self, token_address: str, dex: str) -> Optional[float]:
        """Get current token price"""
        try:
            # In production, use actual price feed or DEX quote
            quote = await swapper.get_best_price(
                "So11111111111111111111111111111111111111112",  # WSOL
                token_address,
                1.0
            )
            
            if quote and 'price' in quote:
                return quote['price']
        
        except Exception as e:
            logger.error(f"Error getting price for {token_address}: {e}")
        
        return None
    
    def close_position(self, position_id: str):
        """Close position (user sold)"""
        if position_id in self.active_positions:
            position = self.active_positions[position_id]
            position['active'] = False
            logger.info(f"✅ Position closed: {position_id} with ROI {position['roi']:.1f}%")
            return True
        return False
    
    async def check_once(self, telegram_send_callback):
        """Run a single check pass and send any alerts. Called by the job queue."""
        try:
            alerts = await self.check_positions()

            alerts_by_user = {}
            for alert in alerts:
                user_id = alert['user_id']
                alerts_by_user.setdefault(user_id, []).append(alert)

            for user_id, user_alerts in alerts_by_user.items():
                for alert in user_alerts:
                    await telegram_send_callback(user_id, alert)

        except Exception as e:
            logger.error(f"Error in notification check: {e}")

    async def monitor_all_users(self, telegram_send_callback):
        """Continuously monitor all active positions (standalone mode)."""
        logger.info("🎯 Smart notification engine started")
        while True:
            await self.check_once(telegram_send_callback)
            await asyncio.sleep(self.check_interval)


notification_engine = SmartNotificationEngine()
