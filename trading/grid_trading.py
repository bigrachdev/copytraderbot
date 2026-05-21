"""
Grid Trading AI Engine
Automated grid-based trading with ML price prediction and dynamic rebalancing
"""
import logging
from typing import Dict, List, Optional, Tuple
import asyncio
from datetime import datetime, timedelta
import numpy as np
from config import (
    ENABLE_GRID_TRADING,
    GRID_LEVELS_COUNT,
    GRID_UPPER_RANGE_PCT,
    GRID_LOWER_RANGE_PCT,
    GRID_MIN_INVESTMENT_SOL,
    GRID_MAX_INVESTMENT_SOL,
    GRID_USE_ML_PREDICTION,
    GRID_ML_LOOKBACK_HOURS,
    GRID_DYNAMIC_ADJUSTMENT,
    GRID_REBALANCE_INTERVAL_MINUTES,
    GRID_PROFIT_THRESHOLD_PCT,
    GRID_STOP_LOSS_PCT,
    GRID_AUTOMATE_LEVEL_SPACING,
    GRID_USE_BREAKOUT_DETECTION,
)

logger = logging.getLogger(__name__)


class GridLevel:
    """Represents one level in a trading grid"""

    def __init__(
        self,
        level_num: int,
        price: float,
        side: str,  # 'BUY' or 'SELL'
        size_sol: float,
        position_size: float = 0.0,
    ):
        self.level_num = level_num
        self.price = price
        self.side = side
        self.size_sol = size_sol
        self.position_size = position_size  # Amount held at this level
        self.status = "PENDING"  # PENDING, FILLED, PARTIALLY_FILLED, CLOSED
        self.order_id: Optional[str] = None
        self.filled_at: Optional[datetime] = None
        self.profit: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "level": self.level_num,
            "price": self.price,
            "side": self.side,
            "size_sol": self.size_sol,
            "position_size": self.position_size,
            "status": self.status,
            "profit": self.profit,
        }


class GridTradingEngine:
    """AI-powered grid trading engine"""

    def __init__(self, token_address: str, current_price: float, total_investment_sol: float):
        self.token_address = token_address
        self.current_price = current_price
        self.total_investment_sol = total_investment_sol
        self.entry_time = datetime.now()

        # Grid levels
        self.buy_levels: List[GridLevel] = []
        self.sell_levels: List[GridLevel] = []
        self.active_orders: Dict[str, GridLevel] = {}  # order_id -> GridLevel

        # Tracking
        self.total_filled_buys = 0.0  # Total tokens bought
        self.total_filled_sells = 0.0  # Total tokens sold
        self.realized_profit = 0.0
        self.unrealized_profit = 0.0
        self.last_rebalance = datetime.now()
        self.price_history: List[Tuple[datetime, float]] = [(datetime.now(), current_price)]

        # Generate initial grid
        self._generate_grid()

    def _generate_grid(self) -> None:
        """Generate initial buy/sell grid levels"""
        if not GRID_AUTOMATE_LEVEL_SPACING:
            # Fixed spacing
            self._generate_fixed_grid()
        else:
            # ML-assisted spacing
            self._generate_ml_grid()

    def _generate_fixed_grid(self) -> None:
        """Generate evenly-spaced grid levels"""
        level_count = max(GRID_LEVELS_COUNT, 2)  # FIX #1: Prevent division by zero (min 2 levels)
        lower_price = self.current_price * (1 - GRID_LOWER_RANGE_PCT / 100)
        upper_price = self.current_price * (1 + GRID_UPPER_RANGE_PCT / 100)

        size_per_level = self.total_investment_sol / level_count

        for i in range(level_count):
            # Buy levels below current price
            ratio = i / max(level_count - 1, 1)  # FIX #1: Safe division
            buy_price = lower_price + (self.current_price - lower_price) * ratio
            buy_level = GridLevel(i, buy_price, "BUY", size_per_level)
            self.buy_levels.append(buy_level)

            # Sell levels above current price
            sell_price = self.current_price + (upper_price - self.current_price) * ratio
            sell_level = GridLevel(i, sell_price, "SELL", size_per_level)
            self.sell_levels.append(sell_level)

        logger.info(
            f"📊 Grid initialized for {self.token_address[:8]}… "
            f"{len(self.buy_levels)} BUY + {len(self.sell_levels)} SELL levels"
        )

    def _generate_ml_grid(self) -> None:
        """Generate ML-optimized grid levels based on price volatility & support/resistance"""
        # TODO: Use ML to predict support/resistance levels
        # For now, use fixed grid as fallback
        self._generate_fixed_grid()

    def place_order(self, level: GridLevel) -> bool:
        """Place a buy/sell order at a grid level (ready for Jupiter integration)"""
        # FIX #6: Properly document and set up for actual exchange integration
        level.status = "PENDING"
        level.order_id = f"order_{level.side}_{level.level_num}_{datetime.now().timestamp()}"
        self.active_orders[level.order_id] = level
        logger.info(
            f"📍 Grid order placed: {level.side} at ${level.price:.8f} "
            f"({level.size_sol} SOL) | Order ID: {level.order_id}"
        )
        
        # TODO: Integrate with Jupiter DEX for actual swaps
        # Example when ready:
        # tx_sig = await swapper.swap_exact_in(
        #     input_mint=WSOL_MINT,
        #     output_mint=self.token_address,
        #     in_amount_ui=level.size_sol,
        #     user_keypair=user_keypair
        # )
        # level.order_id = tx_sig
        
        return True

    def simulate_fill(self, level: GridLevel, fill_price: float, fill_amount: float) -> Dict:
        """Simulate order fill (used for testing)"""
        level.status = "FILLED"
        level.filled_at = datetime.now()
        level.position_size = fill_amount

        if level.side == "BUY":
            self.total_filled_buys += fill_amount
        else:
            self.total_filled_sells += fill_amount

        profit = self._calculate_level_profit(level, fill_price)
        level.profit = profit
        self.realized_profit += profit if level.side == "SELL" else 0

        return {
            "status": "filled",
            "level": level.level_num,
            "price": fill_price,
            "amount": fill_amount,
            "profit": profit,
        }

    def _calculate_level_profit(self, level: GridLevel, current_price: float) -> float:
        """Calculate profit/loss for a grid level"""
        if level.side == "SELL" and level.position_size > 0:
            # FIX #3: Correct formula = (sell_price - avg_buy_price) * amount
            avg_buy = self._avg_buy_price()
            return (level.price - avg_buy) * level.position_size
        return 0.0

    async def update_price(self, new_price: float) -> Dict:
        """Update current price and check for fill conditions"""
        # FIX #9: Input validation
        if not isinstance(new_price, (int, float)):
            logger.error(f"Invalid price type: {type(new_price)}")
            return {"error": f"Price must be numeric, got {type(new_price)}"}
        if new_price <= 0:
            logger.error(f"Invalid price: {new_price}")
            return {"error": f"Price must be positive, got {new_price}"}

        self.current_price = new_price
        self.price_history.append((datetime.now(), new_price))
        
        # FIX #7: Limit price history to prevent memory leak (keep last 1000 = ~30 min)
        if len(self.price_history) > 1000:
            self.price_history = self.price_history[-1000:]

        fills = []

        try:
            # Check buy levels (should fill if price <= buy_price)
            for buy_level in self.buy_levels:
                if buy_level.status == "PENDING" and new_price <= buy_level.price:
                    if new_price > 0:  # Safety check
                        fill_amount = buy_level.size_sol / new_price
                        fill_result = self.simulate_fill(buy_level, new_price, fill_amount)
                        fills.append(fill_result)

            # Check sell levels (should fill if price >= sell_price)
            # FIX #2: Prevent overselling - only sell available tokens
            for sell_level in self.sell_levels:
                if sell_level.status == "PENDING" and new_price >= sell_level.price:
                    available_to_sell = self.total_filled_buys - self.total_filled_sells
                    sell_amount = min(available_to_sell * 0.1, self.total_investment_sol / GRID_LEVELS_COUNT)
                    
                    if sell_amount > 0:
                        fill_result = self.simulate_fill(sell_level, new_price, sell_amount)
                        fills.append(fill_result)

            # Rebalance if needed
            await self._check_rebalance()

            # Check for breakout
            if GRID_USE_BREAKOUT_DETECTION:
                await self._detect_breakout()

            return {
                "current_price": new_price,
                "fills": fills,
                "grid_status": self.get_status(),
            }
        except Exception as e:
            # FIX #10: Exception handling
            logger.error(f"Error processing grid update: {e}", exc_info=True)
            return {
                "error": str(e),
                "current_price": new_price,
                "grid_status": self.get_status(),
            }

    async def _check_rebalance(self) -> None:
        """Rebalance grid if interval exceeded"""
        if not GRID_DYNAMIC_ADJUSTMENT:
            return

        elapsed = (datetime.now() - self.last_rebalance).total_seconds()
        rebalance_interval_sec = GRID_REBALANCE_INTERVAL_MINUTES * 60

        if elapsed > rebalance_interval_sec:
            logger.info(f"🔄 Rebalancing grid for {self.token_address[:8]}…")
            # Adjust grid around current price
            self._adjust_grid_around_price(self.current_price)
            self.last_rebalance = datetime.now()

    def _adjust_grid_around_price(self, current_price: float) -> None:
        """Shift and adjust grid around new price level"""
        # Cancel unfilled orders
        for order_id, level in list(self.active_orders.items()):
            if level.status == "PENDING":
                logger.info(f"❌ Cancelled unfilled order {order_id}")
                del self.active_orders[order_id]

        # Regenerate grid centered on current price
        self.current_price = current_price
        self.buy_levels.clear()
        self.sell_levels.clear()
        self._generate_grid()

    async def _detect_breakout(self) -> None:
        """Detect price breakout and adjust grid"""
        if len(self.price_history) < 10:
            return

        # Get last 10 prices
        recent_prices = [p[1] for p in self.price_history[-10:]]
        min_price = min(recent_prices)
        max_price = max(recent_prices)
        
        if min_price <= 0:  # FIX #10: Safety check
            return
            
        range_pct = (max_price - min_price) / min_price * 100

        if range_pct > 30:  # Large breakout
            # FIX #8: Add cooldown to prevent spam rebalancing during sustained breakout
            if not hasattr(self, '_last_breakout_rebalance'):
                self._last_breakout_rebalance = datetime.now()
                logger.warning(f"📈 Breakout detected for {self.token_address[:8]}…: +{range_pct:.1f}%")
                await self._check_rebalance()
            else:
                elapsed = (datetime.now() - self._last_breakout_rebalance).total_seconds()
                if elapsed > 600:  # 10 minute cooldown
                    self._last_breakout_rebalance = datetime.now()
                    logger.warning(f"📈 Breakout rebalance for {self.token_address[:8]}…: +{range_pct:.1f}%")
                    await self._check_rebalance()

    def get_status(self) -> Dict:
        """Get grid status"""
        return {
            "token": self.token_address[:8] + "…",
            "current_price": self.current_price,
            "total_invested": self.total_investment_sol,
            "buy_levels_filled": sum(1 for l in self.buy_levels if l.status == "FILLED"),
            "sell_levels_filled": sum(1 for l in self.sell_levels if l.status == "FILLED"),
            "total_bought": self.total_filled_buys,
            "total_sold": self.total_filled_sells,
            "realized_profit": self.realized_profit,
            "unrealized_profit": self._calculate_unrealized(),
            "roi_pct": (self.realized_profit / self.total_investment_sol * 100) if self.total_investment_sol > 0 else 0,
        }

    def _calculate_unrealized(self) -> float:
        """Calculate unrealized profit from open positions"""
        unrealized = 0.0
        holdings = self.total_filled_buys - self.total_filled_sells
        unrealized = holdings * (self.current_price - self._avg_buy_price())
        return unrealized

    def _avg_buy_price(self) -> float:
        """Calculate weighted average buy price from filled orders"""
        # FIX #4: Track actual avg buy price instead of hardcoded 0.01
        filled_buys = [l for l in self.buy_levels if l.status == "FILLED"]
        
        if not filled_buys:
            return 0.0
        
        total_tokens = sum(l.position_size for l in filled_buys)
        if total_tokens == 0:
            return 0.0
        
        total_cost = sum(l.price * l.position_size for l in filled_buys)
        return total_cost / total_tokens

    def get_summary_table(self) -> str:
        """Get formatted grid summary"""
        status = self.get_status()
        return (
            f"📊 **Grid Trading Summary**\n"
            f"Token: `{status['token']}`\n"
            f"Current Price: `${status['current_price']:.8f}`\n"
            f"Total Invested: `{status['total_invested']:.4f} SOL`\n"
            f"Filled Buy Orders: `{status['buy_levels_filled']}/{GRID_LEVELS_COUNT}`\n"
            f"Filled Sell Orders: `{status['sell_levels_filled']}/{GRID_LEVELS_COUNT}`\n"
            f"Realized Profit: `${status['realized_profit']:.4f}`\n"
            f"Unrealized Profit: `${status['unrealized_profit']:.4f}`\n"
            f"ROI: `{status['roi_pct']:.2f}%`"
        )


# Grid manager for multiple active grids
class GridManager:
    """Manage multiple active grids across tokens"""

    def __init__(self):
        self.grids: Dict[str, GridTradingEngine] = {}  # token -> GridTradingEngine

    def create_grid(
        self, token_address: str, current_price: float, investment_sol: float
    ) -> GridTradingEngine:
        """Create new grid for a token"""
        # FIX #5: Add investment validation
        if not isinstance(investment_sol, (int, float)):
            raise TypeError(f"Investment must be numeric, got {type(investment_sol)}")
        if investment_sol < GRID_MIN_INVESTMENT_SOL:
            raise ValueError(
                f"Investment {investment_sol} SOL below minimum {GRID_MIN_INVESTMENT_SOL} SOL"
            )
        if investment_sol > GRID_MAX_INVESTMENT_SOL:
            raise ValueError(
                f"Investment {investment_sol} SOL exceeds maximum {GRID_MAX_INVESTMENT_SOL} SOL"
            )
        if not isinstance(current_price, (int, float)) or current_price <= 0:
            raise ValueError(f"Invalid price: {current_price}")
        
        if token_address in self.grids:
            logger.warning(f"Grid already exists for {token_address[:8]}…")
            return self.grids[token_address]

        grid = GridTradingEngine(token_address, current_price, investment_sol)
        self.grids[token_address] = grid
        logger.info(f"✅ Grid created for {token_address[:8]}… with {investment_sol} SOL")
        return grid

    def get_grid(self, token_address: str) -> Optional[GridTradingEngine]:
        """Get grid for token"""
        return self.grids.get(token_address)

    def get_all_grids_summary(self) -> str:
        """Get summary of all active grids"""
        if not self.grids:
            return "No active grids"

        summaries = []
        for token, grid in self.grids.items():
            status = grid.get_status()
            summaries.append(
                f"  • `{status['token']}`: "
                f"${status['realized_profit']:.4f} profit ({status['roi_pct']:.1f}%)"
            )

        return "**Active Grids:**\n" + "\n".join(summaries)


# Global instances
grid_engine: Optional[GridTradingEngine] = None
grid_manager = GridManager()
