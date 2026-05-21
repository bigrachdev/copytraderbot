"""
Advanced Whale Scoring Algorithm
Ranks whales by: win_rate, avg_profit, drawdown, recency, consistency
This is more sophisticated than the simple threshold-based filtering.
"""
import logging
from typing import Dict, Tuple, Optional, List
from datetime import datetime, timedelta
import numpy as np
from data.database import db
from config import (
    WHALE_SCORE_WIN_RATE_WEIGHT,
    WHALE_SCORE_PROFIT_WEIGHT,
    WHALE_SCORE_DRAWDOWN_WEIGHT,
    WHALE_SCORE_RECENCY_WEIGHT,
    WHALE_SCORE_CONSISTENCY_WEIGHT,
    WHALE_MAX_CONSECUTIVE_LOSSES,
    WHALE_CONSISTENCY_LOOKBACK_DAYS,
    WHALE_SCORE_THRESHOLD_TO_TRADE,
)

logger = logging.getLogger(__name__)


class WhaleScorer:
    """Score whales based on multi-factor algorithm"""

    def __init__(self):
        self.cache: Dict[str, Tuple[float, float]] = {}  # {whale: (score, timestamp)}
        self.cache_ttl = 3600  # 1 hour cache

    def score_whale(self, user_id: int, whale_address: str) -> float:
        """
        Score a whale on 0-100 scale.
        Score = win_rate(30%) + profit(20%) + drawdown(25%) + recency(15%) + consistency(10%)
        """
        # Check cache
        cached_score = self._get_cached_score(whale_address)
        if cached_score is not None:
            return cached_score

        # Get whale statistics from database
        stats = db.get_whale_stats(user_id, whale_address)
        if not stats:
            logger.warning(f"No stats found for whale {whale_address[:8]}")
            return 0.0

        # Extract metrics
        trades = stats.get("total_trades", 0)
        wins = stats.get("winning_trades", 0)
        losses = stats.get("losing_trades", 0)
        avg_profit = stats.get("avg_profit_pct", 0)
        max_drawdown = stats.get("max_drawdown_pct", 0)

        # Check for recent trades (recency factor)
        last_trade = stats.get("last_trade_at")
        recency_score = self._calculate_recency_score(last_trade)

        # Calculate sub-scores (0-100 for each)
        win_rate_score = self._score_win_rate(wins, losses)
        profit_score = self._score_profit(avg_profit)
        drawdown_score = self._score_drawdown(max_drawdown)
        consistency_score = self._score_consistency(user_id, whale_address, trades)

        # Weighted combination
        total_score = (
            win_rate_score * WHALE_SCORE_WIN_RATE_WEIGHT
            + profit_score * WHALE_SCORE_PROFIT_WEIGHT
            + drawdown_score * WHALE_SCORE_DRAWDOWN_WEIGHT
            + recency_score * WHALE_SCORE_RECENCY_WEIGHT
            + consistency_score * WHALE_SCORE_CONSISTENCY_WEIGHT
        )

        logger.info(
            f"🐋 Whale {whale_address[:8]}… scored {total_score:.1f}/100 "
            f"[WR={win_rate_score:.0f}, P={profit_score:.0f}, DD={drawdown_score:.0f}, "
            f"REC={recency_score:.0f}, CON={consistency_score:.0f}]"
        )

        # Cache the score
        self._cache_score(whale_address, total_score)
        return total_score

    def _score_win_rate(self, wins: int, losses: int) -> float:
        """Score based on win rate (0-100)"""
        total = wins + losses
        if total == 0:
            return 0.0
        win_rate = wins / total
        # 0% -> 0, 50% -> 50, 100% -> 100
        return min(100, win_rate * 100)

    def _score_profit(self, avg_profit_pct: float) -> float:
        """Score based on average profit (0-100)"""
        # -50% -> 0, 0% -> 20, +5% -> 50, +10% -> 100
        if avg_profit_pct < -50:
            return 0.0
        elif avg_profit_pct < 0:
            return 20.0 + (avg_profit_pct + 50) * 0.4  # -50 to 0 maps to 20-50
        elif avg_profit_pct < 10:
            return 50.0 + avg_profit_pct * 5  # 0-10 maps to 50-100
        else:
            return 100.0

    def _score_drawdown(self, max_drawdown_pct: float) -> float:
        """Score based on max drawdown (0-100)"""
        # 0% -> 100, 10% -> 50, 30% -> 0
        if max_drawdown_pct <= 0:
            return 100.0
        elif max_drawdown_pct >= 30:
            return 0.0
        else:
            return max(0, 100 - (max_drawdown_pct / 30) * 100)

    def _calculate_recency_score(self, last_trade_at: Optional[datetime]) -> float:
        """Score based on how recent the last trade was (0-100)"""
        if not last_trade_at:
            return 0.0  # No recent activity
        
        now = datetime.now()
        days_since = (now - last_trade_at).days
        
        # 0 days -> 100, 7 days -> 70, 30 days -> 10, 60+ days -> 0
        if days_since <= 0:
            return 100.0
        elif days_since >= 60:
            return 0.0
        else:
            return max(0, 100 - (days_since / 60) * 100)

    def _score_consistency(self, user_id: int, whale_address: str, trades: int) -> float:
        """
        Score based on win/loss consistency (low variance = better).
        Get recent trades and calculate std dev of returns.
        """
        if trades < 3:
            return 50.0  # Not enough data

        # Get recent trades (last 14 days)
        lookback_days = WHALE_CONSISTENCY_LOOKBACK_DAYS
        recent_trades = db.get_whale_trades(
            user_id, whale_address, days=lookback_days, limit=100
        )

        if not recent_trades or len(recent_trades) < 3:
            return 50.0

        # Extract profit percentages
        profits = [t.get("profit_pct", 0) for t in recent_trades]
        
        # Standard deviation of profits (high std dev = inconsistent)
        std_dev = float(np.std(profits)) if len(profits) > 1 else 0
        
        # 0 std dev -> 100, 50+ std dev -> 0
        if std_dev >= 50:
            consistency_score = 0.0
        else:
            consistency_score = max(0, 100 - (std_dev / 50) * 100)
        
        return consistency_score

    def _get_cached_score(self, whale_address: str) -> Optional[float]:
        """Get cached score if available and fresh"""
        if whale_address not in self.cache:
            return None
        
        score, timestamp = self.cache[whale_address]
        if datetime.now().timestamp() - timestamp > self.cache_ttl:
            del self.cache[whale_address]
            return None
        
        return score

    def _cache_score(self, whale_address: str, score: float) -> None:
        """Cache whale score with timestamp"""
        self.cache[whale_address] = (score, datetime.now().timestamp())

    def rank_whales(
        self, user_id: int, whale_addresses: List[str]
    ) -> List[Tuple[str, float]]:
        """
        Score and rank multiple whales.
        Returns list of (whale_address, score) sorted by score descending.
        """
        ranked = []
        for whale in whale_addresses:
            score = self.score_whale(user_id, whale)
            if score >= WHALE_SCORE_THRESHOLD_TO_TRADE:
                ranked.append((whale, score))
        
        # Sort by score descending
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def check_consecutive_losses(
        self, user_id: int, whale_address: str
    ) -> Tuple[bool, int]:
        """
        Check if whale has exceeded max consecutive losses.
        Returns (should_trade, consecutive_loss_count)
        """
        stats = db.get_whale_stats(user_id, whale_address)
        if not stats:
            return True, 0
        
        consecutive_losses = stats.get("consecutive_losses", 0)
        should_trade = consecutive_losses < WHALE_MAX_CONSECUTIVE_LOSSES
        
        if not should_trade:
            logger.warning(
                f"⚠️ Whale {whale_address[:8]}… has {consecutive_losses} "
                f"consecutive losses (max: {WHALE_MAX_CONSECUTIVE_LOSSES})"
            )
        
        return should_trade, consecutive_losses


# Global instance
whale_scorer = WhaleScorer()
