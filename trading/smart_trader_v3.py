"""
Smart Trader v3 - Professional autonomous trading engine.

ARCHITECTURAL IMPROVEMENTS:
- Confidence-based trade quality filter (reject weak trades)
- Database-persisted risk state
- Adaptive position sizing with Kelly
- Explainable decisions
- Reduced trade frequency, higher quality
"""
import asyncio
import logging
import time
from typing import Dict, Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass

from core.http_client import http_client
from core.cache import cache
from core.confidence_engine import confidence_engine, ConfidenceResult
from core.risk_engine import risk_engine, RiskLevel
from core.execution_engine import execution_engine, SwapResult
from trading.token_analyzer_v2 import token_analyzer
from data.database import db
from utils.notifications import notification_engine
from config import (
    WSOL_MINT, BIRDEYE_API_KEY,
    SMART_MIN_TRADE_SOL, SMART_MAX_HOLD_HOURS,
    SMART_TRAILING_STOP_PCT, POSITION_CHECK_INTERVAL,
)

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    """Result of trade analysis."""
    should_trade: bool
    confidence: float
    position_size_sol: float
    reasons: List[str]
    warnings: List[str]


class SmartTraderV3:
    """
    Professional trading engine with confidence scoring.
    
    CORE PHILOSOPHY:
    Trade less. Trade smarter. Protect capital first.
    
    PROFITABILITY IMPROVEMENTS:
    - Rejects trades below confidence threshold
    - Explains every decision for learning
    - Adaptive sizing based on historical performance
    - All risk state persisted to database
    """
    
    _instance: Optional['SmartTraderV3'] = None
    
    MIN_CONFIDENCE = 75.0  # Minimum to trade
    
    def __new__(cls) -> 'SmartTraderV3':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        
        # Position monitors
        self._position_monitors: Dict[Tuple, asyncio.Task] = {}
        self._auto_smart_tasks: Dict[int, asyncio.Task] = {}
        self._blacklist: Dict[int, set] = {}
        self._whitelist: Dict[int, set] = {}
        
        logger.info("✅ Smart Trader v3 initialized")
    
    async def evaluate_trade(
        self, user_id: int, token_address: str
    ) -> TradeDecision:
        """
        Evaluate whether to trade a token.
        
        Returns TradeDecision with confidence and sizing.
        """
        decision = TradeDecision(
            should_trade=False, confidence=0, position_size_sol=0,
            reasons=[], warnings=[]
        )
        
        try:
            # Full analysis
            analysis = await token_analyzer.analyze(token_address)
            decision.confidence = analysis.confidence
            
            if analysis.confidence < self.MIN_CONFIDENCE:
                decision.warnings.append(f"Confidence {analysis.confidence:.0f} < {self.MIN_CONFIDENCE}")
                if analysis.weaknesses:
                    decision.warnings.extend(analysis.weaknesses[:3])
                return decision
            
            # Check blacklist
            if token_address in self._blacklist.get(user_id, set()):
                decision.warnings.append("Token blacklisted")
                return decision
            
            # Risk check
            can_trade, reason, risk_level = await risk_engine.can_open_position(
                user_id, token_address, SMART_MIN_TRADE_SOL
            )
            if not can_trade:
                decision.warnings.append(reason)
                return decision
            
            # Calculate position size
            user = db.get_user(user_id)
            if not user:
                decision.warnings.append("No user found")
                return decision
            
            balance = self._get_wallet_balance(user)
            if balance < SMART_MIN_TRADE_SOL:
                decision.warnings.append(f"Insufficient balance: {balance:.4f} SOL")
                return decision
            
            position_size = await risk_engine.calculate_position_size(
                user_id, token_address, analysis.confidence, balance, risk_level
            )
            
            if position_size < SMART_MIN_TRADE_SOL:
                decision.warnings.append(f"Position size {position_size:.4f} below minimum")
                return decision
            
            # All checks passed
            decision.should_trade = True
            decision.position_size_sol = position_size
            decision.reasons = analysis.strengths[:5]
            
        except Exception as e:
            decision.warnings.append(f"Analysis error: {e}")
            logger.error(f"Trade evaluation error: {e}")
        
        return decision
    
    async def execute_trade(
        self, user_id: int, token_address: str,
        position_size_sol: Optional[float] = None
    ) -> Optional[Dict]:
        """Execute trade with full risk management."""
        result = {
            'user_id': user_id,
            'token_address': token_address,
            'status': 'pending',
            'tx_signature': None,
            'confidence': 0,
        }
        
        try:
            # Evaluate first
            decision = await self.evaluate_trade(user_id, token_address)
            result['confidence'] = decision.confidence
            
            if not decision.should_trade:
                result['status'] = 'rejected'
                result['reasons'] = decision.warnings
                logger.info(f"Trade rejected: {decision.warnings}")
                return result
            
            # Use calculated or provided size
            amount = position_size_sol or decision.position_size_sol
            
            # Get keypair
            keypair = self._get_user_keypair(user_id)
            if not keypair:
                result['status'] = 'error'
                result['error'] = 'No keypair'
                return result
            
            # Execute swap
            swap_result = await execution_engine.execute_swap(
                WSOL_MINT, token_address, amount, keypair
            )
            
            if swap_result.status == 'confirmed':
                result['status'] = 'success'
                result['tx_signature'] = swap_result.signature
                result['amount_sol'] = swap_result.input_amount
                result['tokens_received'] = swap_result.output_amount
                
                # Get entry price
                entry_price = amount / swap_result.output_amount if swap_result.output_amount else 0
                
                # Record in DB
                db.add_pending_trade(
                    user_id, token_address, swap_result.output_amount,
                    amount, entry_price, 'jupiter', swap_result.signature
                )
                
                # Start position monitor
                asyncio.create_task(self._monitor_position(
                    user_id, token_address, entry_price, swap_result.output_amount
                ))
                
                # Notify
                await notification_engine.notify_trade_opened(
                    user_id, 0,
                    f"✅ **Smart Trade Executed**\n"
                    f"Tokens: {swap_result.output_amount:.2f}\n"
                    f"Confidence: {decision.confidence:.0f}/100\n"
                    f"Reasons: {', '.join(decision.reasons[:3])}"
                )
                
                logger.info(f"Trade executed: {token_address[:10]}... {amount:.4f} SOL")
            else:
                result['status'] = 'failed'
                result['error'] = swap_result.error
        
        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            logger.error(f"Execute trade error: {e}")
        
        return result
    
    async def _monitor_position(
        self, user_id: int, token_address: str,
        entry_price: float, token_amount: float
    ):
        """Monitor position with trailing stop and TP ladder."""
        remaining = token_amount
        peak_price = entry_price
        tp_idx = 0
        start_time = time.time()
        
        # Get user settings
        analysis = await token_analyzer.analyze(token_address)
        volatility = abs(analysis.price_change_1h) / 100 + 0.05
        
        tp_ladder = risk_engine.get_take_profit_ladder(entry_price, volatility, analysis.confidence)
        stop_price = risk_engine.get_stop_loss(entry_price, volatility, analysis.confidence)
        trailing_stop = SMART_TRAILING_STOP_PCT
        trailing_active = False
        
        while remaining > 0:
            try:
                elapsed_h = (time.time() - start_time) / 3600
                
                # Time decay
                if elapsed_h >= SMART_MAX_HOLD_HOURS:
                    await self._exit_position(user_id, token_address, remaining)
                    await risk_engine.record_trade_result(user_id, -5, 'smart')
                    return
                
                # Get current price
                current_price = await self._get_price(token_address)
                if not current_price:
                    await asyncio.sleep(POSITION_CHECK_INTERVAL)
                    continue
                
                pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0
                peak_price = max(peak_price, current_price)
                
                # Hard stop
                if current_price <= stop_price:
                    await self._exit_position(user_id, token_address, remaining)
                    await risk_engine.record_trade_result(user_id, pnl_pct * 100, 'smart')
                    return
                
                # Take profit levels
                if tp_idx < len(tp_ladder):
                    threshold, fraction = tp_ladder[tp_idx]
                    if pnl_pct >= threshold:
                        sell_qty = remaining * fraction
                        remaining -= sell_qty
                        tp_idx += 1
                        trailing_active = True
                        await self._partial_exit(user_id, token_address, sell_qty, remaining)
                
                # Trailing stop
                if trailing_active and peak_price > 0:
                    drawdown = (peak_price - current_price) / peak_price
                    if drawdown >= trailing_stop:
                        await self._exit_position(user_id, token_address, remaining)
                        await risk_engine.record_trade_result(user_id, pnl_pct * 100, 'smart')
                        return
                
                await asyncio.sleep(POSITION_CHECK_INTERVAL)
                
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                await asyncio.sleep(POSITION_CHECK_INTERVAL)
    
    async def _get_price(self, token_address: str) -> Optional[float]:
        """Get current token price."""
        quote = await execution_engine.get_quote(WSOL_MINT, token_address, 1.0)
        if quote and quote.get('outAmount'):
            return 1.0 / quote['outAmount']
        return None
    
    async def _exit_position(self, user_id: int, token_address: str, amount: float):
        """Exit position."""
        keypair = self._get_user_keypair(user_id)
        if not keypair:
            return
        
        result = await execution_engine.execute_swap(
            token_address, WSOL_MINT, amount, keypair
        )
        
        if result.status == 'confirmed':
            db.update_pending_trade_closed(user_id, token_address, result.output_amount, result.signature)
            logger.info(f"Position exited: {token_address[:10]}...")
    
    async def _partial_exit(self, user_id: int, token_address: str, 
                            sell_qty: float, remaining: float):
        """Partial exit at TP level."""
        keypair = self._get_user_keypair(user_id)
        if not keypair:
            return
        
        result = await execution_engine.execute_swap(
            token_address, WSOL_MINT, sell_qty, keypair
        )
        
        if result.status == 'confirmed':
            db.update_pending_trade_token_amount(user_id, token_address, remaining)
    
    def _get_user_keypair(self, user_id: int):
        """Get user's signing keypair."""
        try:
            from wallet.encryption import encryption
            from chains.solana.wallet import SolanaWallet
            
            user = db.get_user(user_id)
            if not user:
                return None
            
            enc_key = user.get('encrypted_trading_key') or user.get('encrypted_private_key')
            if not enc_key:
                return None
            
            private_key = encryption.decrypt(enc_key)
            return SolanaWallet().import_keypair(private_key)
        except Exception as e:
            logger.error(f"Keypair error: {e}")
            return None
    
    def _get_wallet_balance(self, user: Dict) -> float:
        """Get wallet balance."""
        try:
            from chains.solana.wallet import SolanaWallet
            addr = user.get('trading_wallet_address') or user.get('wallet_address')
            if not addr:
                return 0
            return SolanaWallet().get_balance(addr) or 0
        except:
            return 0


smart_trader_v3 = SmartTraderV3()
