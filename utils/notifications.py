"""
Smart notifications system - alerts users on profits and suggests selling
"""
import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from data.database import db
from chains.solana.spl_tokens import token_manager
from chains.solana.dex_swaps import swapper

logger = logging.getLogger(__name__)


class SmartNotificationEngine:
    """Send smart profit alerts and sell suggestions"""
    
    def __init__(self):
        self.profit_thresholds = [10, 25, 50, 100, 250, 500]  # % gains
        self.check_interval = 60  # Check every minute
        self.active_positions = {}  # Track monitored positions
    
    def track_position(self, user_id: int, token_address: str, 
                      amount_bought: float, entry_price: float,
                      dex: str) -> Dict:
        """Start tracking a position"""
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
            'active': True
        }
        
        position_id = f"{user_id}_{token_address}_{datetime.now().timestamp()}"
        self.active_positions[position_id] = position
        
        logger.info(f"📍 Position tracked: {position_id}")
        return position
    
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
                if roi <= -50 and 'cutloss' not in position['alerts_sent']:
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
                if time_held > timedelta(hours=24) and roi > 20 and 'aging' not in position['alerts_sent']:
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
    
    async def monitor_all_users(self, telegram_send_callback):
        """Continuously monitor all active positions"""
        logger.info("🎯 Smart notification engine started")
        
        while True:
            try:
                alerts = await self.check_positions()
                
                # Group alerts by user
                alerts_by_user = {}
                for alert in alerts:
                    user_id = alert['user_id']
                    if user_id not in alerts_by_user:
                        alerts_by_user[user_id] = []
                    alerts_by_user[user_id].append(alert)
                
                # Send alerts via Telegram
                for user_id, user_alerts in alerts_by_user.items():
                    for alert in user_alerts:
                        await telegram_send_callback(user_id, alert)
                
                await asyncio.sleep(self.check_interval)
            
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(self.check_interval)


notification_engine = SmartNotificationEngine()
