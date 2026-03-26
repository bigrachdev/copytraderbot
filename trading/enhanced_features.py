"""
Enhanced Trading Features - Advanced improvements for copy trade and smart trade
All features are toggleable via environment variables or user settings
"""
import logging
import time
import math
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import aiohttp

from config import (
    # Copy Trade toggles
    ENABLE_DYNAMIC_COPY_SCALE, ENABLE_ENHANCED_WHALE_QUAL,
    ENABLE_LATENCY_OPTIMIZATION, ENABLE_SIGNAL_AGGREGATION,
    # Smart Trade toggles
    ENABLE_KELLY_COPY_TRADES, ENABLE_TOKEN_DISCOVERY_PLUS,
    ENABLE_TP_LADDER_OPT, ENABLE_REBUY_ENHANCED,
    # Risk management
    ENABLE_DAILY_LOSS_LIMIT, ENABLE_COOL_OFF_PERIOD,
    # MEV Protection
    ENABLE_JITO_PROTECTION,
    # Token Safety
    ENABLE_RUGCHECK_FILTER,
    # Feature defaults
    DYNAMIC_COPY_SCALE_FACTOR, WHALE_MIN_TRADES_ENHANCED, WHALE_MAX_DRAWDOWN,
    LATENCY_HIGH_THRESHOLD_MS, LATENCY_SLIPPAGE_ADJUST, SIGNAL_MIN_WHALES_RISKY,
    KELLY_FRACTION_CAP, KELLY_MAX_POSITION_PCT, TP_LADDER_VOLATILITY_ADJ,
    TP_BREAKEVEN_AFTER_TP1, REBUY_MAX_PER_TOKEN, REBUY_PROFIT_REDUCTION,
    DAILY_LOSS_LIMIT_PCT, COOL_OFF_LOSSES, COOL_OFF_MINUTES,
    JITO_MIN_TRADE_SOL, RUGCHECK_MIN_SCORE,
)
from data.database import db

logger = logging.getLogger(__name__)


class EnhancedFeatures:
    """Advanced trading enhancements with toggleable features."""

    def __init__(self):
        # Daily loss tracking: {user_id: {'date': str, 'loss': float}}
        self._daily_loss: Dict[int, Dict] = {}
        # Consecutive loss tracking: {user_id: {'count': int, 'last_loss_time': float}}
        self._consecutive_losses: Dict[int, Dict] = {}
        # Rebuy tracking: {(user_id, token): count}
        self._rebuy_counts: Dict[tuple, int] = {}
        logger.info("✅ Enhanced Features initialized")

    # =========================================================================
    # 1. Dynamic Copy Scaling
    # =========================================================================

    def get_dynamic_copy_scale(self, user_id: int, whale_address: str,
                                base_scale: float = 1.0) -> float:
        """
        Adjust copy scale based on whale's recent performance.
        Scale = base_scale × (1 + factor × (win_rate - 0.5))

        Winners get larger allocation, losers get reduced.
        """
        if not ENABLE_DYNAMIC_COPY_SCALE:
            return base_scale

        # Check user toggle
        user_enabled = db.get_user_setting(user_id, 'enable_dynamic_copy_scale', True)
        if not user_enabled:
            return base_scale

        try:
            records = db.get_copy_performance(user_id, whale_address, limit=20)
            closed = [r for r in records if r.get('status') == 'closed'
                      and r.get('user_profit_percent') is not None]

            if len(closed) < 3:
                return base_scale  # Not enough data

            wins = sum(1 for r in closed if r['user_profit_percent'] > 0)
            win_rate = wins / len(closed)

            # Dynamic adjustment
            adjustment = 1.0 + (DYNAMIC_COPY_SCALE_FACTOR * (win_rate - 0.5))
            adjustment = max(0.5, min(2.0, adjustment))  # Clamp to 0.5x - 2.0x

            new_scale = base_scale * adjustment
            logger.info(
                f"[DynamicScale] {whale_address[:8]}… win_rate={win_rate:.0%}  "
                f"base={base_scale}x → {new_scale:.2f}x"
            )
            return new_scale

        except Exception as e:
            logger.error(f"Dynamic copy scale error: {e}")
            return base_scale

    # =========================================================================
    # 2. Enhanced Whale Qualification
    # =========================================================================

    def is_whale_qualified_enhanced(self, user_id: int, whale_address: str) -> Tuple[bool, str]:
        """
        Enhanced whale qualification with Sharpe ratio and drawdown checks.
        """
        if not ENABLE_ENHANCED_WHALE_QUAL:
            return True, "enhanced_qual_disabled"

        # Check user toggle
        user_enabled = db.get_user_setting(user_id, 'enable_enhanced_whale_qual', True)
        if not user_enabled:
            return True, "user_disabled"

        try:
            records = db.get_copy_performance(user_id, whale_address, limit=50)
            closed = [
                r for r in records
                if r.get('status') == 'closed' and r.get('user_profit_percent') is not None
            ]

            # Minimum trades (enhanced requires more history)
            if len(closed) < WHALE_MIN_TRADES_ENHANCED:
                # Fall back to basic qualification
                if len(closed) >= 5:
                    return True, f"insufficient_enhanced_data ({len(closed)} trades)"
                return True, "insufficient_basic_data"

            # Calculate metrics
            returns = [r['user_profit_percent'] for r in closed]
            wins = [r for r in returns if r > 0]
            losses = [abs(r) for r in returns if r <= 0]

            win_rate = len(wins) / len(returns)
            avg_profit = sum(returns) / len(returns)

            # Sharpe ratio (simplified - using std dev of returns)
            if len(returns) > 1:
                mean_ret = avg_profit
                variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
                std_dev = math.sqrt(variance) if variance > 0 else 1
                sharpe = (avg_profit / std_dev) if std_dev > 0 else 0
            else:
                sharpe = 0

            # Maximum drawdown
            cumulative = 0
            peak = 0
            max_drawdown = 0
            for ret in returns:
                cumulative += ret
                if cumulative > peak:
                    peak = cumulative
                drawdown = (peak - cumulative) / peak if peak > 0 else 0
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

            max_dd_pct = max_drawdown * 100

            # Qualification checks
            if win_rate < 0.45:
                return False, f"win_rate {win_rate:.0%} < 45%"
            if avg_profit < -5:
                return False, f"avg_profit {avg_profit:.1f}% < -5%"
            if sharpe < 0.3:
                return False, f"sharpe {sharpe:.2f} < 0.3 (inconsistent)"
            if max_dd_pct > WHALE_MAX_DRAWDOWN:
                return False, f"max_drawdown {max_dd_pct:.1f}% > {WHALE_MAX_DRAWDOWN}%"

            logger.info(
                f"✅ Whale {whale_address[:8]}… qualified: win={win_rate:.0%}  "
                f"sharpe={sharpe:.2f}  max_dd={max_dd_pct:.1f}%"
            )
            return True, f"qualified  win={win_rate:.0%}  sharpe={sharpe:.2f}  dd={max_dd_pct:.1f}%"

        except Exception as e:
            logger.error(f"Enhanced whale qual error: {e}")
            return True, "qualification_error"

    # =========================================================================
    # 3. Copy Latency Optimization
    # =========================================================================

    def get_latency_adjusted_slippage(self, base_slippage: float,
                                       latency_ms: int) -> float:
        """
        Adjust slippage tolerance based on copy latency.
        High latency = higher slippage needed to ensure transaction lands.
        """
        if not ENABLE_LATENCY_OPTIMIZATION:
            return base_slippage

        if latency_ms > LATENCY_HIGH_THRESHOLD_MS:
            adjusted = base_slippage + LATENCY_SLIPPAGE_ADJUST
            logger.info(
                f"[LatencyOpt] High latency detected ({latency_ms}ms)  "
                f"slippage {base_slippage}% → {adjusted}%"
            )
            return adjusted

        return base_slippage

    async def should_use_jito(self, trade_amount_sol: float) -> bool:
        """
        Determine if trade should use Jito private pool for MEV protection.
        """
        if not ENABLE_JITO_PROTECTION:
            return False

        if trade_amount_sol >= JITO_MIN_TRADE_SOL:
            logger.info(f"[JitoProtect] Trade {trade_amount_sol} SOL ≥ {JITO_MIN_TRADE_SOL} → using Jito")
            return True

        return False

    # =========================================================================
    # 4. Signal Aggregation Enhancements
    # =========================================================================

    def get_signal_multiplier_enhanced(self, user_id: int, token_address: str,
                                        unique_whales: int,
                                        whale_ranks: List[float]) -> float:
        """
        Enhanced signal multiplier considering whale performance ranks.
        """
        if not ENABLE_SIGNAL_AGGREGATION:
            return 1.0

        # Base multiplier from signal count
        base_mult = 1.0 + min(unique_whales - 1, 2) * 0.25

        # Weight by whale ranks (if available)
        if whale_ranks and len(whale_ranks) > 0:
            avg_rank_score = sum(whale_ranks) / len(whale_ranks)
            # Rank score is typically 0-100, normalize to 0.8-1.2 multiplier
            rank_adj = 0.8 + (avg_rank_score / 100) * 0.4
            base_mult *= rank_adj

        logger.info(f"[SignalAgg] {unique_whales} whales → multiplier {base_mult:.2f}x")
        return base_mult

    def should_require_min_whales(self, token_risk_score: float) -> Tuple[bool, int]:
        """
        For risky tokens, require multiple whale confirmations.
        """
        if not ENABLE_SIGNAL_AGGREGATION:
            return False, 1

        if token_risk_score > 50:
            logger.info(f"[SignalAgg] Risky token (risk={token_risk_score}) requires {SIGNAL_MIN_WHALES_RISKY}+ whales")
            return True, SIGNAL_MIN_WHALES_RISKY

        return False, 1

    # =========================================================================
    # 5. Kelly Criterion for Copy Trades
    # =========================================================================

    def calculate_kelly_copy_amount(self, user_balance: float,
                                     whale_win_rate: float,
                                     whale_avg_win: float,
                                     whale_avg_loss: float,
                                     base_amount: float) -> float:
        """
        Kelly Criterion for position sizing in copy trades.
        Uses fractional Kelly (50%) to reduce variance.
        """
        if not ENABLE_KELLY_COPY_TRADES:
            return base_amount

        # Check user toggle
        user_enabled = db.get_user_setting(user_id, 'enable_kelly_sizing', False)
        if not user_enabled:
            return base_amount

        try:
            # Kelly formula: f = (p × b - q) / b
            # where p=win_prob, q=loss_prob, b=win/loss ratio
            p = whale_win_rate
            q = 1 - p

            if whale_avg_loss == 0:
                kelly_frac = 0.10  # Conservative if no loss data
            else:
                b = whale_avg_win / whale_avg_loss
                kelly_frac = (p * b - q) / b

            # Apply fractional Kelly cap
            kelly_frac = max(0.02, min(kelly_frac, KELLY_FRACTION_CAP))

            # Convert to position size
            kelly_amount = user_balance * kelly_frac

            # Cap at max position %
            max_amount = user_balance * (KELLY_MAX_POSITION_PCT / 100)
            kelly_amount = min(kelly_amount, max_amount)

            logger.info(
                f"[KellyCopy] win_rate={whale_win_rate:.0%}  b={whale_avg_win/whale_avg_loss:.2f}  "
                f"Kelly={kelly_frac*100:.1f}% → {kelly_amount:.4f} SOL"
            )
            return kelly_amount

        except Exception as e:
            logger.error(f"Kelly copy sizing error: {e}")
            return base_amount

    # =========================================================================
    # 6. Token Discovery Enhancements
    # =========================================================================

    async def get_social_sentiment(self, token_address: str) -> Dict:
        """
        Fetch social sentiment metrics (Twitter/X, Telegram mentions).
        Returns sentiment score and mention count.
        """
        if not ENABLE_TOKEN_DISCOVERY_PLUS:
            return {'sentiment_score': 50, 'mentions_24h': 0}

        try:
            # Placeholder - would integrate with social APIs
            # In production: use LunarCrush, Santiment, or Twitter API
            logger.debug(f"[SocialSentiment] Checking {token_address[:8]}…")
            return {'sentiment_score': 50, 'mentions_24h': 0}
        except Exception as e:
            logger.error(f"Social sentiment error: {e}")
            return {'sentiment_score': 50, 'mentions_24h': 0}

    async def check_smart_money_flows(self, token_address: str) -> Dict:
        """
        Track if known profitable wallets are buying.
        """
        if not ENABLE_TOKEN_DISCOVERY_PLUS:
            return {'smart_money_buying': False, 'smart_wallets': []}

        try:
            # Placeholder - would track known smart wallets
            # In production: maintain database of historically profitable wallets
            return {'smart_money_buying': False, 'smart_wallets': []}
        except Exception as e:
            logger.error(f"Smart money flows error: {e}")
            return {'smart_money_buying': False, 'smart_wallets': []}

    # =========================================================================
    # 7. Take-Profit Ladder Optimization
    # =========================================================================

    def get_adjusted_tp_ladder(self, token_volatility: float,
                                base_ladder: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """
        Adjust TP ladder based on token volatility.
        High vol = take profits earlier.
        """
        if not ENABLE_TP_LADDER_OPT:
            return base_ladder

        # Check user toggle
        user_enabled = db.get_user_setting(None, 'enable_tp_volatility_adj', True)
        if not user_enabled:
            return base_ladder

        if token_volatility > 100:  # High volatility
            # Move TPs earlier: 20%/40%/80% instead of 30%/60%/100%
            adjusted = [
                (0.20, base_ladder[0][1]),  # TP1 at +20%
                (0.40, base_ladder[1][1]),  # TP2 at +40%
                (0.80, base_ladder[2][1]),  # TP3 at +80%
            ]
            logger.info(f"[TPOpt] High vol ({token_volatility}%) → earlier TPs: 20/40/80%")
            return adjusted

        return base_ladder

    def should_move_stop_to_breakeven(self, tp1_hit: bool, entry_price: float,
                                       current_price: float) -> bool:
        """
        Determine if stop-loss should move to breakeven after TP1.
        """
        if not ENABLE_TP_LADDER_OPT or not TP_BREAKEVEN_AFTER_TP1:
            return False

        if tp1_hit and current_price >= entry_price:
            logger.info(f"[TPOpt] TP1 hit → moving stop to breakeven")
            return True

        return False

    # =========================================================================
    # 8. Auto-Rebuy Enhancements
    # =========================================================================

    def can_rebuy_token(self, user_id: int, token_address: str) -> Tuple[bool, str]:
        """
        Check if rebuy is allowed (respecting max rebuys per token).
        """
        if not ENABLE_REBUY_ENHANCED:
            return True, "rebuy_enhanced_disabled"

        key = (user_id, token_address)
        count = self._rebuy_counts.get(key, 0)

        if count >= REBUY_MAX_PER_TOKEN:
            logger.info(f"[RebuyLimit] {token_address[:8]}… hit max ({REBUY_MAX_PER_TOKEN} rebuys)")
            return False, f"max_rebuys_reached ({count})"

        return True, f"rebuy_count={count}"

    def increment_rebuy_count(self, user_id: int, token_address: str):
        """Track rebuy count for token."""
        key = (user_id, token_address)
        self._rebuy_counts[key] = self._rebuy_counts.get(key, 0) + 1
        logger.info(f"[RebuyTrack] {token_address[:8]}… rebuy #{self._rebuy_counts[key]}")

    def get_rebuy_cooldown(self, base_cooldown: int, last_trade_profit: float) -> int:
        """
        Reduce cooldown if last trade was profitable.
        """
        if not ENABLE_REBUY_ENHANCED:
            return base_cooldown

        if last_trade_profit > 0:
            reduced = int(base_cooldown * REBUY_PROFIT_REDUCTION)
            logger.info(f"[RebuyCool] Profitable last trade → cooldown {base_cooldown}s → {reduced}s")
            return reduced

        return base_cooldown

    # =========================================================================
    # 9. Risk Management Upgrades
    # =========================================================================

    def check_daily_loss_limit(self, user_id: int) -> Tuple[bool, float]:
        """
        Check if user has hit daily loss limit.
        Returns (can_trade, current_daily_loss).
        """
        if not ENABLE_DAILY_LOSS_LIMIT:
            return True, 0.0

        # Check user toggle
        user_enabled = db.get_user_setting(user_id, 'enable_daily_loss_limit', False)
        if not user_enabled:
            return True, 0.0

        today = datetime.now().date().isoformat()

        # Get or initialize daily loss
        if user_id not in self._daily_loss or self._daily_loss[user_id]['date'] != today:
            self._daily_loss[user_id] = {'date': today, 'loss': 0.0}

        current_loss = self._daily_loss[user_id]['loss']

        if current_loss >= DAILY_LOSS_LIMIT_PCT:
            logger.warning(
                f"[DailyLoss] User {user_id} hit daily limit "
                f"({current_loss:.1f}% ≥ {DAILY_LOSS_LIMIT_PCT}%)"
            )
            return False, current_loss

        return True, current_loss

    def record_daily_loss(self, user_id: int, profit_loss_pct: float):
        """Record daily PnL for loss limit tracking."""
        today = datetime.now().date().isoformat()

        if user_id not in self._daily_loss or self._daily_loss[user_id]['date'] != today:
            self._daily_loss[user_id] = {'date': today, 'loss': 0.0}

        if profit_loss_pct < 0:
            self._daily_loss[user_id]['loss'] += abs(profit_loss_pct)

        logger.debug(
            f"[DailyLoss] User {user_id} recorded {profit_loss_pct:.1f}%  "
            f"total={self._daily_loss[user_id]['loss']:.1f}%"
        )

    def check_cool_off_period(self, user_id: int) -> Tuple[bool, int]:
        """
        Check if user is in cool-off period after consecutive losses.
        Returns (can_trade, minutes_remaining).
        """
        if not ENABLE_COOL_OFF_PERIOD:
            return True, 0

        # Check user toggle
        user_enabled = db.get_user_setting(user_id, 'enable_cool_off_period', False)
        if not user_enabled:
            return True, 0

        if user_id not in self._consecutive_losses:
            self._consecutive_losses[user_id] = {'count': 0, 'cool_off_until': 0}

        user_data = self._consecutive_losses[user_id]

        # Check if in cool-off
        if user_data['cool_off_until'] > time.time():
            remaining = int((user_data['cool_off_until'] - time.time()) / 60)
            logger.warning(f"[CoolOff] User {user_id} in cool-off for {remaining} more minutes")
            return False, remaining

        return True, 0

    def record_trade_result(self, user_id: int, is_profitable: bool):
        """
        Track consecutive losses and trigger cool-off if needed.
        """
        if user_id not in self._consecutive_losses:
            self._consecutive_losses[user_id] = {'count': 0, 'cool_off_until': 0}

        user_data = self._consecutive_losses[user_id]

        if is_profitable:
            user_data['count'] = 0  # Reset on profit
        else:
            user_data['count'] += 1

            if user_data['count'] >= COOL_OFF_LOSSES:
                user_data['cool_off_until'] = time.time() + (COOL_OFF_MINUTES * 60)
                logger.warning(
                    f"[CoolOff] User {user_id} triggered cool-off "
                    f"({COOL_OFF_MINUTES}min) after {user_data['count']} losses"
                )
                user_data['count'] = 0  # Reset after triggering

    # =========================================================================
    # 10. RugCheck Integration
    # =========================================================================

    async def check_rugcheck_score(self, token_address: str) -> Tuple[bool, int]:
        """
        Fetch RugCheck score for Solana token.
        Returns (passes_check, score).
        """
        if not ENABLE_RUGCHECK_FILTER:
            return True, 100

        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report"
                async with session.get(url, timeout=8) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        score = data.get('score', 0)
                        if score >= RUGCHECK_MIN_SCORE:
                            logger.info(f"[RugCheck] {token_address[:8]}… score={score} ✅")
                            return True, score
                        else:
                            logger.warning(f"[RugCheck] {token_address[:8]}… score={score} ❌")
                            return False, score
        except Exception as e:
            logger.error(f"RugCheck API error: {e}")
            return True, 100  # Allow on API error (fail-open)

        return True, 100


# Singleton instance
enhanced_features = EnhancedFeatures()
