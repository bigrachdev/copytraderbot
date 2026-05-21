"""
Advanced Exit Strategy with Volatility-Adjusted Take-Profits
and Dynamic Trailing Stops
"""
import logging
from typing import Dict, Tuple, List, Optional
import asyncio
from datetime import datetime, timedelta
from config import (
    ENABLE_VOLATILITY_ADJUSTED_TP,
    ENABLE_DYNAMIC_TRAILING_STOP,
    ENABLE_BREAKEVEN_STOP,
    TP_VOLATILITY_LOW,
    TP_VOLATILITY_MID,
    TP_VOLATILITY_HIGH,
    TP_VOLATILITY_THRESHOLD_LOW,
    TP_VOLATILITY_THRESHOLD_HIGH,
    TRAILING_STOP_ACTIVATION_TP,
    TRAILING_STOP_HIGH_VOL_PCT,
    TRAILING_STOP_LOW_VOL_PCT,
    BREAKEVEN_AFTER_TP1,
)

logger = logging.getLogger(__name__)


class ExitStrategyManager:
    """Manage advanced exit strategies with volatility awareness"""

    def __init__(self):
        self.volatility_cache: Dict[str, Tuple[float, float]] = {}  # {token: (vol, timestamp)}
        self.cache_ttl = 300  # 5 minute cache

    def get_tp_ladder_for_token(
        self, token_address: str, volatility_pct: Optional[float] = None
    ) -> List[Tuple[float, float]]:
        """
        Get take-profit ladder adjusted for token volatility.
        Returns list of (threshold_pct, fraction_to_sell)
        
        - Low volatility: 30% -> 60% -> 100% (hold longer, less risky)
        - Mid volatility: 50% -> 100% -> 200% (normal)
        - High volatility: 75% -> 150% -> 300% (shitcoins, exit faster)
        """
        if not ENABLE_VOLATILITY_ADJUSTED_TP:
            # Return default ladder
            return self._parse_tp_string("0.30,0.60,1.00")

        if volatility_pct is None:
            # Estimate volatility from price changes
            volatility_pct = self._estimate_volatility(token_address)

        # Select ladder based on volatility
        if volatility_pct < TP_VOLATILITY_THRESHOLD_LOW:
            logger.debug(f"📊 Low volatility ({volatility_pct:.2f}%) for {token_address[:8]}… → Safe ladder")
            return self._parse_tp_string(TP_VOLATILITY_LOW)
        elif volatility_pct > TP_VOLATILITY_THRESHOLD_HIGH:
            logger.debug(f"📊 High volatility ({volatility_pct:.2f}%) for {token_address[:8]}… → Aggressive ladder")
            return self._parse_tp_string(TP_VOLATILITY_HIGH)
        else:
            logger.debug(f"📊 Mid volatility ({volatility_pct:.2f}%) for {token_address[:8]}… → Standard ladder")
            return self._parse_tp_string(TP_VOLATILITY_MID)

    def get_dynamic_trailing_stop(
        self, token_address: str, volatility_pct: Optional[float] = None, tp_hit: bool = False
    ) -> float:
        """
        Get trailing stop percentage adjusted for volatility.
        
        - Low volatility: 10%
        - High volatility: 20%
        - Activates only after first TP hit
        
        Returns trailing stop percentage (e.g., 0.15 for 15% trailing stop)
        """
        if not ENABLE_DYNAMIC_TRAILING_STOP:
            return 0.15  # Default 15%

        if not tp_hit:
            return 0.0  # Don't use trailing stop until TP1 hit

        if volatility_pct is None:
            volatility_pct = self._estimate_volatility(token_address)

        if volatility_pct < TP_VOLATILITY_THRESHOLD_LOW:
            return TRAILING_STOP_LOW_VOL_PCT
        elif volatility_pct > TP_VOLATILITY_THRESHOLD_HIGH:
            return TRAILING_STOP_HIGH_VOL_PCT
        else:
            # Average of high and low
            return (TRAILING_STOP_HIGH_VOL_PCT + TRAILING_STOP_LOW_VOL_PCT) / 2

    def should_enable_breakeven_stop(self, tp1_hit: bool, current_gain: float) -> bool:
        """
        After TP1 is hit, move stop-loss to breakeven (0% loss).
        This protects remaining position while holding for TP2 and TP3.
        """
        if not ENABLE_BREAKEVEN_STOP:
            return False
        if not BREAKEVEN_AFTER_TP1:
            return False
        
        return tp1_hit and current_gain > TRAILING_STOP_ACTIVATION_TP

    async def monitor_advanced_exit(
        self,
        token_address: str,
        entry_price: float,
        current_price: float,
        tp_ladder: List[Tuple[float, float]],
        position_size: float,
        already_sold_fraction: float = 0.0,
    ) -> Dict:
        """
        Monitor position with advanced exit logic.
        Returns action: {action: 'SELL'|'HOLD', amount: float, reason: str}
        """
        gain_pct = (current_price - entry_price) / entry_price

        # Check each TP level
        for i, (tp_threshold, tp_fraction) in enumerate(tp_ladder):
            if gain_pct >= tp_threshold:
                remaining = position_size * (1 - already_sold_fraction)
                amount_to_sell = remaining * tp_fraction

                return {
                    "action": "SELL",
                    "amount": amount_to_sell,
                    "reason": f"TP{i+1} reached: {gain_pct*100:.1f}% (threshold: {tp_threshold*100:.0f}%)",
                    "tp_level": i + 1,
                    "tp_threshold": tp_threshold,
                }

        return {"action": "HOLD", "amount": 0, "reason": "Below all TP levels"}

    def _estimate_volatility(self, token_address: str) -> float:
        """
        Estimate token volatility (1h price change %).
        This is a placeholder - in production you'd calculate from real price data.
        """
        # TODO: Fetch 1h price change from DexScreener/Birdeye
        # For now return a default estimate
        return 10.0

    def _parse_tp_string(self, tp_string: str) -> List[Tuple[float, float]]:
        """Parse TP string like '0.30,0.60,1.00' to ladder"""
        levels = tp_string.split(",")
        result = []
        for i, level in enumerate(levels):
            try:
                threshold = float(level)
                if i == len(levels) - 1:
                    fraction = 1.0  # Last level sells everything
                elif i == 0:
                    fraction = 0.25  # First level sells 25%
                else:
                    fraction = 0.50  # Middle levels sell 50%
                result.append((threshold, fraction))
            except ValueError:
                logger.warning(f"Invalid TP level: {level}")
        return result

    def get_exit_strategy_summary(
        self, token_address: str, volatility_pct: Optional[float] = None
    ) -> Dict:
        """Get full exit strategy for a token"""
        tp_ladder = self.get_tp_ladder_for_token(token_address, volatility_pct)
        trailing_stop = self.get_dynamic_trailing_stop(token_address, volatility_pct, tp_hit=False)

        return {
            "token": token_address[:8] + "…",
            "tp_ladder": [f"+{t*100:.0f}%→sell{f*100:.0f}%" for t, f in tp_ladder],
            "trailing_stop": f"{trailing_stop*100:.0f}%",
            "breakeven_enabled": ENABLE_BREAKEVEN_STOP,
            "volatility_adjustment": ENABLE_VOLATILITY_ADJUSTED_TP,
        }


# Global instance
exit_strategy_manager = ExitStrategyManager()
