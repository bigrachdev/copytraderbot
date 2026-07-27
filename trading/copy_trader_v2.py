"""
Enhanced Copy Trader v2 - Professional whale-following engine.

PROFITABILITY IMPROVEMENTS:
- Only follows whales with proven track records
- Dynamic allocation based on whale performance
- Learns from trade outcomes
"""
import asyncio
import logging
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

from core.risk_engine import risk_engine
from core.execution_engine import execution_engine
from trading.token_analyzer_v2 import token_analyzer
from data.database import db
from utils.notifications import notification_engine
from config import WSOL_MINT, MIN_TRADE_AMOUNT, WHALE_MIN_WIN_RATE, WHALE_MIN_AVG_PROFIT

logger = logging.getLogger(__name__)


@dataclass
class WhaleMetrics:
    address: str
    total_trades: int = 0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    rank_score: float = 0.0


class CopyTraderV2:
    """Professional copy trading with intelligent whale selection."""
    
    _instance: Optional['CopyTraderV2'] = None
    
    def __new__(cls) -> 'CopyTraderV2':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        self._whale_metrics: Dict[Tuple, WhaleMetrics] = {}
    
    def _calculate_whale_metrics(self, user_id: int, whale_addr: str) -> WhaleMetrics:
        metrics = WhaleMetrics(address=whale_addr)
        
        try:
            records = db.get_copy_performance(user_id, whale_addr, limit=50)
            closed = [r for r in records if r.get('status') == 'closed' and r.get('user_profit_percent') is not None]
            
            if len(closed) < 3:
                metrics.rank_score = 30
                return metrics
            
            profits = [r['user_profit_percent'] for r in closed]
            wins = [p for p in profits if p > 0]
            
            metrics.total_trades = len(closed)
            metrics.win_rate = len(wins) / len(closed)
            metrics.avg_profit = sum(profits) / len(closed)
            
            # Sharpe
            if len(profits) > 1:
                mean = metrics.avg_profit
                var = sum((p - mean) ** 2 for p in profits) / len(profits)
                metrics.sharpe = mean / (var ** 0.5) if var > 0 else 0
            
            # Rank score
            win_score = metrics.win_rate * 100 * 0.30
            profit_score = max(0, min(100, metrics.avg_profit + 20)) * 0.25
            sharpe_score = max(0, min(100, (metrics.sharpe + 1) * 50)) * 0.20
            dd_score = max(0, 100 - metrics.max_drawdown * 2) * 0.25
            
            metrics.rank_score = win_score + profit_score + sharpe_score + dd_score
            
        except Exception as e:
            logger.error(f"Whale metrics error: {e}")
        
        return metrics
    
    async def should_copy(self, user_id: int, whale_addr: str) -> Tuple[bool, str]:
        metrics = self._whale_metrics.get((user_id, whale_addr))
        if not metrics:
            metrics = self._calculate_whale_metrics(user_id, whale_addr)
            self._whale_metrics[(user_id, whale_addr)] = metrics
        
        if metrics.win_rate < WHALE_MIN_WIN_RATE:
            return False, f"Win rate {metrics.win_rate:.0%} too low"
        if metrics.avg_profit < WHALE_MIN_AVG_PROFIT:
            return False, f"Avg profit {metrics.avg_profit:.1f}% too low"
        if metrics.rank_score < 40:
            return False, f"Rank score {metrics.rank_score:.0f} too low"
        
        return True, f"Qualified (score={metrics.rank_score:.0f})"
    
    async def execute_copy(self, user_id: int, whale_addr: str, 
                           output_mint: str, whale_amount: float,
                           wallet_config: Dict) -> bool:
        """Execute copy trade with risk management."""
        if output_mint == WSOL_MINT:
            return False
        
        should_copy, reason = await self.should_copy(user_id, whale_addr)
        if not should_copy:
            logger.info(f"Skip copy: {reason}")
            return False
        
        is_safe, _ = await token_analyzer.quick_check(output_mint)
        if not is_safe:
            return False
        
        can_trade, _, _ = await risk_engine.can_open_position(user_id, output_mint, MIN_TRADE_AMOUNT)
        if not can_trade:
            return False
        
        metrics = self._whale_metrics.get((user_id, whale_addr))
        rank_mult = min(2.0, max(0.5, (metrics.rank_score / 50) if metrics else 1.0))
        
        base_scale = float(wallet_config.get('copy_scale', 1.0))
        trade_amount = whale_amount * base_scale * rank_mult
        
        keypair = self._get_keypair(user_id)
        if not keypair:
            return False
        
        result = await execution_engine.execute_swap(WSOL_MINT, output_mint, trade_amount, keypair)
        
        if result.status == 'confirmed':
            db.open_copy_position(user_id, whale_addr, output_mint, 0,
                                   trade_amount / max(result.output_amount, 1),
                                   base_scale * rank_mult, trade_amount)
            return True
        
        return False
    
    def _get_keypair(self, user_id: int):
        try:
            from wallet.encryption import encryption
            from chains.solana.wallet import SolanaWallet
            user = db.get_user(user_id)
            if not user:
                return None
            enc = user.get('encrypted_trading_key') or user.get('encrypted_private_key')
            key = encryption.decrypt(enc)
            return SolanaWallet().import_keypair(key)
        except Exception as e:
            logger.error(f"Keypair error: {e}")
            return None


copy_trader_v2 = CopyTraderV2()
