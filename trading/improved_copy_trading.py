"""
Integration Module - Ties all improvements together
Provides unified API for copy trading with all enhancements enabled
"""
import logging
from typing import Dict, Tuple, Optional, List
from utils.circuit_breaker import (
    circuit_breaker_manager,
    rpc_breaker,
    token_analyzer_breaker,
)
from trading.whale_scorer import whale_scorer
from trading.exit_strategy import exit_strategy_manager
from trading.grid_trading import grid_manager
from utils.speed_optimizer import latency_optimizer
from config import (
    ENABLE_CIRCUIT_BREAKER,
    ENABLE_ADVANCED_WHALE_SCORING,
    ENABLE_VOLATILITY_ADJUSTED_TP,
    ENABLE_DYNAMIC_TRAILING_STOP,
    ENABLE_GRID_TRADING,
    LATENCY_OPTIMIZATION_LEVEL,
)

logger = logging.getLogger(__name__)


class ImprovedCopyTradingEngine:
    """
    Unified copy trading engine with all improvements integrated:
    1. Speed optimization (latency < 5s)
    2. Advanced whale filtering (multi-factor scoring)
    3. Exit strategy optimization (volatility-adjusted TP)
    4. Resilience (circuit breaker protection)
    5. Grid trading (optional AI grid mode)
    """

    def __init__(self):
        self.latency_optimization_level = LATENCY_OPTIMIZATION_LEVEL
        logger.info(
            "🚀 ImprovedCopyTradingEngine initialized with all enhancements:\n"
            f"   ✅ Speed optimization (level {LATENCY_OPTIMIZATION_LEVEL})\n"
            f"   ✅ Advanced whale scoring: {ENABLE_ADVANCED_WHALE_SCORING}\n"
            f"   ✅ Volatility-adjusted TP: {ENABLE_VOLATILITY_ADJUSTED_TP}\n"
            f"   ✅ Dynamic trailing stops: {ENABLE_DYNAMIC_TRAILING_STOP}\n"
            f"   ✅ Circuit breaker protection: {ENABLE_CIRCUIT_BREAKER}\n"
            f"   ✅ Grid trading: {ENABLE_GRID_TRADING}"
        )

    async def initialize(self) -> None:
        """Initialize all subsystems"""
        logger.info("⚙️ Initializing all subsystems...")
        await latency_optimizer.initialize()
        logger.info("✅ All subsystems initialized")

    async def qualify_whale_for_copy(
        self, user_id: int, whale_address: str
    ) -> Tuple[bool, float, str]:
        """
        Qualify whale using advanced scoring.
        Returns (should_copy, score, reason)
        """
        if not ENABLE_ADVANCED_WHALE_SCORING:
            # Fall back to simple qualification
            return True, 50.0, "Simple qualification (advanced scoring disabled)"

        try:
            score = whale_scorer.score_whale(user_id, whale_address)
            should_copy, loss_count = whale_scorer.check_consecutive_losses(user_id, whale_address)

            if not should_copy:
                return False, score, f"Too many consecutive losses ({loss_count})"

            reason = f"Advanced scoring: {score:.1f}/100"
            return score > 50, score, reason

        except Exception as e:
            logger.error(f"Error qualifying whale: {e}")
            return False, 0.0, f"Error: {str(e)}"

    async def get_optimized_exit_strategy(
        self, token_address: str, volatility: Optional[float] = None
    ) -> Dict:
        """
        Get exit strategy optimized for token volatility.
        Integrates TP ladder + trailing stops + breakeven logic
        """
        if not ENABLE_VOLATILITY_ADJUSTED_TP:
            # Return default
            return {"tp_ladder": [(0.30, 0.25), (0.60, 0.50), (1.00, 1.00)], "trailing_stop": 0.15}

        try:
            tp_ladder = exit_strategy_manager.get_tp_ladder_for_token(token_address, volatility)
            trailing_stop = exit_strategy_manager.get_dynamic_trailing_stop(
                token_address, volatility, tp_hit=False
            )

            return {
                "tp_ladder": tp_ladder,
                "trailing_stop": trailing_stop,
                "breakeven_enabled": ENABLE_DYNAMIC_TRAILING_STOP,
                "summary": exit_strategy_manager.get_exit_strategy_summary(token_address, volatility),
            }
        except Exception as e:
            logger.error(f"Error getting exit strategy: {e}")
            return {"error": str(e)}

    async def start_grid_trading(
        self, token_address: str, current_price: float, investment_sol: float
    ) -> Optional[str]:
        """
        Start grid trading for a token (optional).
        Returns grid ID if successful.
        """
        if not ENABLE_GRID_TRADING:
            logger.debug("Grid trading is disabled")
            return None

        try:
            grid = grid_manager.create_grid(token_address, current_price, investment_sol)
            logger.info(f"✅ Grid trading started for {token_address[:8]}…")
            return token_address
        except Exception as e:
            logger.error(f"Error starting grid trading: {e}")
            return None

    async def get_grid_status(self, token_address: str) -> Optional[Dict]:
        """Get status of grid trading for a token"""
        grid = grid_manager.get_grid(token_address)
        if not grid:
            return None
        return grid.get_status()

    def get_system_status(self) -> Dict:
        """Get overall system status"""
        return {
            "latency_optimization_level": self.latency_optimization_level,
            "expected_latency_ms": latency_optimizer._get_expected_latency(),
            "circuit_breakers": circuit_breaker_manager.get_status_all() if ENABLE_CIRCUIT_BREAKER else {},
            "active_grids": len(grid_manager.grids),
            "advanced_whale_scoring": ENABLE_ADVANCED_WHALE_SCORING,
            "volatility_adjusted_tp": ENABLE_VOLATILITY_ADJUSTED_TP,
            "dynamic_trailing_stops": ENABLE_DYNAMIC_TRAILING_STOP,
        }

    def get_status_report(self) -> str:
        """Get human-readable status report"""
        status = self.get_system_status()
        report = (
            "**🚀 Improved Copy Trading Engine Status**\n\n"
            f"**Latency Optimization**: Level {status['latency_optimization_level']} "
            f"({status['expected_latency_ms']}ms)\n"
            f"**Active Grids**: {status['active_grids']}\n"
            f"**Advanced Whale Scoring**: {'✅ ON' if status['advanced_whale_scoring'] else '❌ OFF'}\n"
            f"**Volatility-Adjusted TP**: {'✅ ON' if status['volatility_adjusted_tp'] else '❌ OFF'}\n"
            f"**Dynamic Trailing Stops**: {'✅ ON' if status['dynamic_trailing_stops'] else '❌ OFF'}\n"
        )

        if status["circuit_breakers"]:
            report += "\n**Circuit Breakers**:\n"
            for name, cb_status in status["circuit_breakers"].items():
                report += f"  • {name}: {cb_status['state']}\n"

        return report


# Global instance
improved_copy_trading = ImprovedCopyTradingEngine()
