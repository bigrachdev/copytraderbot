"""
Risk management - stop loss, take profit, and order management
"""
import logging
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from data.database import db

logger = logging.getLogger(__name__)


class RiskManager:
    """Manage trading risks with stop-loss and take-profit"""
    
    def __init__(self):
        self.active_orders = {}  # Track active orders
    
    def create_stop_loss_order(self, user_id: int, token_address: str,
                              entry_price: float, stop_loss_percent: float) -> Dict:
        """Create stop-loss order"""
        stop_price = entry_price * (1 - stop_loss_percent / 100)
        
        order = {
            'type': 'stop_loss',
            'user_id': user_id,
            'token_address': token_address,
            'entry_price': entry_price,
            'stop_price': stop_price,
            'percent': stop_loss_percent,
            'created_at': datetime.now(),
            'status': 'active'
        }
        
        logger.info(f"📍 Stop-loss order created: {token_address} at ${stop_price:.8f}")
        return order
    
    def create_take_profit_order(self, user_id: int, token_address: str,
                                entry_price: float, take_profit_percent: float) -> Dict:
        """Create take-profit order"""
        tp_price = entry_price * (1 + take_profit_percent / 100)
        
        order = {
            'type': 'take_profit',
            'user_id': user_id,
            'token_address': token_address,
            'entry_price': entry_price,
            'target_price': tp_price,
            'percent': take_profit_percent,
            'created_at': datetime.now(),
            'status': 'active'
        }
        
        logger.info(f"🎯 Take-profit order created: {token_address} at ${tp_price:.8f}")
        return order
    
    def check_order_trigger(self, current_price: float, order: Dict) -> bool:
        """Check if order should trigger"""
        if order['type'] == 'stop_loss':
            return current_price <= order['stop_price']
        elif order['type'] == 'take_profit':
            return current_price >= order['target_price']
        return False
    
    def calculate_portfolio_value(self, user_id: int, holdings: Dict) -> float:
        """Calculate total portfolio value"""
        total_value = 0
        
        for token_address, amount in holdings.items():
            # In production, fetch current price from DEX
            # Simplified for now
            total_value += amount
        
        return total_value
    
    def calculate_max_position_size(self, portfolio_value: float,
                                   max_risk_percent: float = 2.0) -> float:
        """Calculate max position size based on risk tolerance"""
        return portfolio_value * (max_risk_percent / 100)
    
    def get_risk_adjusted_order_size(self, position_size: float,
                                    stop_loss_percent: float,
                                    max_portfolio_loss: float = 0.02) -> float:
        """Calculate order size given stop-loss and max loss"""
        if stop_loss_percent == 0:
            return position_size
        
        max_position_risk = 1.0 * (stop_loss_percent / 100)
        return min(position_size, max_portfolio_loss / max_position_risk)
    
    def create_trailing_stop(self, user_id: int, token_address: str,
                            entry_price: float, trail_percent: float) -> Dict:
        """Create trailing stop order"""
        order = {
            'type': 'trailing_stop',
            'user_id': user_id,
            'token_address': token_address,
            'entry_price': entry_price,
            'trail_percent': trail_percent,
            'highest_price': entry_price,
            'stop_price': entry_price * (1 - trail_percent / 100),
            'created_at': datetime.now(),
            'status': 'active'
        }
        
        logger.info(f"📊 Trailing stop created: {token_address} trail {trail_percent}%")
        return order
    
    def update_trailing_stop(self, order: Dict, current_price: float) -> bool:
        """Update trailing stop for new high"""
        if current_price > order['highest_price']:
            order['highest_price'] = current_price
            order['stop_price'] = current_price * (1 - order['trail_percent'] / 100)
            logger.debug(f"📈 Trailing stop updated: new stop at ${order['stop_price']:.8f}")
            return True
        return False


risk_manager = RiskManager()
