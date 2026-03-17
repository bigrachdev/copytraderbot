"""
Smart Trader - Auto-trading with intelligent risk assessment
Calculates trade amounts based on token risk, executes trades, monitors positions
"""
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime
import asyncio

from data.database import db
from chains.solana.wallet import SolanaWallet
from trading.copy_trader import copy_trader
from utils.notifications import notification_engine
from trading.token_analyzer import token_analyzer

logger = logging.getLogger(__name__)


class SmartTrader:
    """Intelligent trading with risk assessment"""
    
    def __init__(self):
        """Initialize smart trader"""
        self.wallet = SolanaWallet()
        self.min_trade_amount = 0.1  # 0.1 SOL minimum
        self.max_trade_percent = 50.0  # 50% max per trade
        self.profit_target = 0.30  # 30% profit auto-sell
        self.monitoring_active = False
        
        logger.info("✅ Smart trader initialized")
    
    async def analyze_and_trade(
        self,
        user_id: int,
        token_address: str,
        user_trade_percent: float = 20.0,
        dex: str = "jupiter"
    ) -> Dict:
        """
        Comprehensive analyze-and-trade flow
        """
        result = {
            'user_id': user_id,
            'token_address': token_address,
            'status': 'PENDING',
            'trade_percent_selected': user_trade_percent,
            'trade_amount_sol': 0,
            'tx_signature': None,
            'risk_assessment': {},
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # 1. Get user's wallet SOL balance
            wallet = db.get_user_wallet(user_id)
            if not wallet:
                result['status'] = 'ERROR'
                result['error'] = 'No wallet configured'
                return result
            
            # Get SOL balance (using copy trader's fetch method)
            balance = await copy_trader.get_wallet_balance(user_id, wallet['address'])
            result['wallet_balance'] = balance
            
            if balance < self.min_trade_amount:
                result['status'] = 'INSUFFICIENT_BALANCE'
                result['error'] = f"Minimum {self.min_trade_amount} SOL required"
                return result
            
            # 2. Analyze token for safety and risk
            logger.info(f"🔍 Analyzing token: {token_address[:10]}...")
            analysis = token_analyzer.analyze_token(token_address)
            result['risk_assessment'] = analysis
            
            # Check if analysis rejected the token
            if analysis['trade_recommendation'] in ['REJECT_HONEYPOT', 'REJECT_CONCENTRATED', 'REJECT_TOO_RISKY']:
                result['status'] = 'REJECTED'
                result['reason'] = analysis['trade_recommendation']
                logger.warning(f"⚠️  Token rejected: {analysis['trade_recommendation']}")
                
                # Notify user
                await notification_engine.notify_user(
                    user_id,
                    f"🚫 **Token Analysis Rejected**\n"
                    f"Token: `{token_address[:10]}`\n"
                    f"Reason: {analysis['trade_recommendation']}\n"
                    f"Risk Score: {analysis['risk_score']}/100"
                )
                return result
            
            # 3. Calculate trade amount based on user's % preference and risk score
            trade_amount_sol = self._calculate_trade_amount(
                balance,
                user_trade_percent,
                analysis['suggested_trade_percent'],
                analysis['risk_score']
            )
            
            result['trade_amount_sol'] = trade_amount_sol
            
            logger.info(f"💰 Trade amount: {trade_amount_sol} SOL")
            
            # 4. Execute swap
            logger.info(f"🔄 Executing swap via {dex}...")
            tx_result = await copy_trader.execute_swap(
                user_id=user_id,
                token_to_sell='SOL',
                token_to_buy=token_address,
                amount_in=trade_amount_sol,
                dex=dex,
                slippage=2.0
            )
            
            if tx_result.get('status') == 'success':
                result['status'] = 'SUCCESS'
                result['tx_signature'] = tx_result.get('tx_signature')
                
                # Record trade in database
                db.add_pending_trade(
                    user_id=user_id,
                    token_address=token_address,
                    token_amount=tx_result.get('received_amount', 0),
                    sol_spent=trade_amount_sol,
                    entry_price=trade_amount_sol / tx_result.get('received_amount', 1),
                    dex=dex,
                    swap_signature=result['tx_signature']
                )
                
                logger.info(f"✅ Trade executed: {tx_result}")
                
                # Notify user
                await notification_engine.notify_user(
                    user_id,
                    f"✅ **Trade Executed**\n"
                    f"Token: `{token_address[:10]}`\n"
                    f"Amount: {trade_amount_sol} SOL\n"
                    f"Risk Score: {analysis['risk_score']:.1f}/100\n"
                    f"Target: +30% auto-sell\n"
                    f"TX: `{result['tx_signature'][:10]}`"
                )
                
                # Start monitoring for 30% profit
                asyncio.create_task(self.monitor_position_for_profit(
                    user_id,
                    token_address,
                    result['tx_signature'],
                    self.profit_target
                ))
                
            else:
                result['status'] = 'SWAP_FAILED'
                result['error'] = tx_result.get('error', 'Unknown swap error')
                logger.error(f"❌ Swap failed: {result['error']}")
                
                await notification_engine.notify_user(
                    user_id,
                    f"❌ **Swap Failed**\n"
                    f"Token: `{token_address[:10]}`\n"
                    f"Error: {result['error']}"
                )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error in analyze_and_trade: {e}")
            result['status'] = 'ERROR'
            result['error'] = str(e)
            return result
    
    def _calculate_trade_amount(
        self,
        wallet_balance: float,
        user_selected_percent: float,
        analyzer_suggested_percent: float,
        risk_score: float
    ) -> float:
        """
        Calculate trade amount using:
        1. User's selected % (5-50%)
        2. Analyzer's suggested % based on risk
        3. Risk score adjustment
        
        Final amount = MIN(user_selected, analyzer_suggested, adjusted_for_risk)
        """
        try:
            # Clamp user preference to 5-50%
            user_percent = max(5.0, min(50.0, user_selected_percent))
            
            # Use analyzer's suggestion if it's lower (conservative approach)
            recommended_percent = min(user_percent, analyzer_suggested_percent)
            
            # Adjust for risk: higher risk = smaller trade
            risk_adjustment = 1.0
            if risk_score > 60:
                risk_adjustment = 0.5  # Half the normal amount
            elif risk_score > 75:
                risk_adjustment = 0.25  # Quarter amount for very risky
            
            adjusted_percent = recommended_percent * risk_adjustment
            
            # Calculate actual amount
            trade_amount = (wallet_balance * adjusted_percent) / 100.0
            
            # Ensure minimum trade amount
            trade_amount = max(self.min_trade_amount, trade_amount)
            
            logger.info(f"💡 Trade calc: balance={wallet_balance}, "
                       f"user={user_percent}%, rec={recommended_percent}%, "
                       f"risk_adj={risk_adjustment}, final={trade_amount}")
            
            return round(trade_amount, 4)
            
        except Exception as e:
            logger.error(f"Error calculating trade amount: {e}")
            # Fallback to 10% of balance
            return round((wallet_balance * 0.10), 4)
    
    async def monitor_position_for_profit(
        self,
        user_id: int,
        token_address: str,
        entry_tx: str,
        profit_target: float = 0.30
    ):
        """
        Monitor token position and auto-sell when profit_target is reached
        """
        try:
            logger.info(f"📊 Starting position monitor for {token_address[:10]}...")
            
            max_monitoring_hours = 24
            check_interval_seconds = 30
            max_checks = (max_monitoring_hours * 3600) // check_interval_seconds
            checks_made = 0
            
            while checks_made < max_checks:
                try:
                    # Get current token price and position
                    position = db.get_pending_trade_by_token(user_id, token_address)
                    
                    if not position:
                        logger.info(f"Position closed or not found")
                        break
                    
                    # Get current price from DEX
                    current_price = await self._get_token_price(token_address)
                    entry_price = position.get('entry_price', 0)
                    token_amount = position.get('token_amount', 0)
                    
                    if entry_price and token_amount and current_price:
                        profit_percent = (current_price - entry_price) / entry_price
                        
                        logger.info(f"Position: Entry={entry_price:.8f}, "
                                   f"Current={current_price:.8f}, "
                                   f"Profit={profit_percent*100:.2f}%")
                        
                        # Check if profit target reached
                        if profit_percent >= profit_target:
                            logger.info(f"🎯 Profit target {profit_target*100}% reached!")
                            
                            # Auto-sell back to SOL
                            await self._auto_sell_for_profit(
                                user_id,
                                token_address,
                                token_amount,
                                profit_percent
                            )
                            break
                    
                    checks_made += 1
                    
                    # Wait before next check
                    await asyncio.sleep(check_interval_seconds)
                    
                except Exception as check_error:
                    logger.error(f"Error during position check: {check_error}")
                    checks_made += 1
                    await asyncio.sleep(check_interval_seconds)
            
            if checks_made >= max_checks:
                logger.warning(f"Position monitoring timeout after {max_monitoring_hours} hours")
                
        except Exception as e:
            logger.error(f"❌ Error monitoring position: {e}")
    
    async def _get_token_price(self, token_address: str) -> Optional[float]:
        """Get current token price in SOL"""
        try:
            # Use copy trader's price fetching
            price_info = await copy_trader.get_token_price(token_address)
            if price_info and price_info.get('price_sol'):
                return price_info['price_sol']
            return None
        except Exception as e:
            logger.error(f"Error getting token price: {e}")
            return None
    
    async def _auto_sell_for_profit(
        self,
        user_id: int,
        token_address: str,
        token_amount: float,
        profit_percentage: float
    ):
        """Auto-sell token back to SOL when profit target reached"""
        try:
            logger.info(f"🚀 Auto-selling for {profit_percentage*100:.2f}% profit...")
            
            # Execute sell swap
            sell_result = await copy_trader.execute_swap(
                user_id=user_id,
                token_to_sell=token_address,
                token_to_buy='SOL',
                amount_in=token_amount,
                dex="jupiter",
                slippage=2.0
            )
            
            if sell_result.get('status') == 'success':
                sol_received = sell_result.get('received_amount', 0)
                
                logger.info(f"✅ Auto-sell successful: {token_amount} tokens -> {sol_received} SOL")
                
                # Update database - mark trade as closed
                db.update_pending_trade_closed(
                    user_id,
                    token_address,
                    sol_received,
                    sell_result.get('tx_signature')
                )
                
                # Calculate actual profit
                entry_position = db.get_pending_trade_by_token(user_id, token_address)
                if entry_position:
                    sol_spent = entry_position.get('sol_spent', 0)
                    actual_profit_sol = sol_received - sol_spent
                    actual_profit_percent = (actual_profit_sol / sol_spent) * 100 if sol_spent > 0 else 0
                    
                    # Notify user with profit details
                    await notification_engine.notify_user(
                        user_id,
                        f"🎉 **Auto-Sell Executed!**\n"
                        f"Token: `{token_address[:10]}`\n"
                        f"Profit: +{actual_profit_percent:.2f}%\n"
                        f"Profit Amount: +{actual_profit_sol:.4f} SOL\n"
                        f"TX: `{sell_result.get('tx_signature', '')[:10]}`"
                    )
                    
                    # Record this profit in user stats
                    db.record_profit_trade(
                        user_id,
                        token_address,
                        sol_spent,
                        sol_received,
                        actual_profit_sol
                    )
            else:
                logger.error(f"❌ Auto-sell failed: {sell_result.get('error')}")
                
                await notification_engine.notify_user(
                    user_id,
                    f"⚠️ **Auto-Sell Failed**\n"
                    f"Token: `{token_address[:10]}`\n"
                    f"You need to sell manually\n"
                    f"Error: {sell_result.get('error')}"
                )
        
        except Exception as e:
            logger.error(f"❌ Error during auto-sell: {e}")
            
            await notification_engine.notify_user(
                user_id,
                f"❌ **Auto-Sell Error**\n"
                f"Token: `{token_address[:10]}`\n"
                f"Error: {str(e)}"
            )
    
    def get_user_trade_percent(self, user_id: int) -> float:
        """Get user's configured trade % (5-50)"""
        try:
            user = db.get_user(user_id)
            if user and user.get('trade_percent'):
                percent = float(user['trade_percent'])
                return max(5.0, min(50.0, percent))
            return 20.0  # Default 20%
        except:
            return 20.0
    
    def set_user_trade_percent(self, user_id: int, percent: float):
        """Set user's trade % (5-50)"""
        try:
            percent = max(5.0, min(50.0, percent))
            db.update_user_trade_percent(user_id, percent)
            logger.info(f"Updated user {user_id} trade percent to {percent}%")
        except Exception as e:
            logger.error(f"Error setting trade percent: {e}")


# Global smart trader instance
smart_trader = SmartTrader()
