"""
Database extensions — risk state, confidence history, whale metrics.
These methods are mixed into the Database class via monkey-patching in __init__.py
to avoid modifying the large database.py file directly.

ARCHITECTURAL IMPROVEMENT:
- Keeps database.py stable
- Adds new capabilities cleanly
- All risk state persisted (survives restarts)
"""
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def get_risk_state_value(db, user_id: int, key: str, default=None):
    """Get a risk state value from the persistent risk_state table."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT value FROM risk_state WHERE user_id={ph} AND key={ph}",
            (user_id, key)
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return default
        val = row[0] if not isinstance(row, dict) else row['value']
        try:
            return float(val) if '.' in str(val) else int(val)
        except (ValueError, TypeError):
            return val
    except Exception as e:
        logger.error(f"get_risk_state_value error ({key}): {e}")
        return default


def set_risk_state_value(db, user_id: int, key: str, value) -> bool:
    """Persist a risk state value."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        if db.use_postgres:
            conn.execute(
                f"""INSERT INTO risk_state (user_id, key, value, updated_at)
                    VALUES ({ph},{ph},{ph},CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, key) DO UPDATE SET
                        value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                (user_id, key, str(value))
            )
        else:
            conn.execute(
                f"""INSERT INTO risk_state (user_id, key, value, updated_at)
                    VALUES ({ph},{ph},{ph},CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, key) DO UPDATE SET
                        value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                (user_id, key, str(value))
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"set_risk_state_value error ({key}): {e}")
        return False


def get_all_risk_state(db, user_id: int) -> Dict:
    """Get all risk state for a user."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT key, value FROM risk_state WHERE user_id={ph}",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        result = {}
        for row in rows:
            k, v = (row['key'], row['value']) if isinstance(row, dict) else (row[0], row[1])
            try:
                result[k] = float(v) if '.' in str(v) else int(v)
            except (ValueError, TypeError):
                result[k] = v
        return result
    except Exception as e:
        logger.error(f"get_all_risk_state error: {e}")
        return {}


def record_confidence_outcome(db, token_address: str, confidence: float,
                               decision: str, outcome: str, profit_pct: float) -> bool:
    """Record confidence prediction outcome for learning."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        conn.execute(
            f"""INSERT INTO confidence_history
                (token_address, confidence, decision, outcome, profit_pct)
                VALUES ({ph},{ph},{ph},{ph},{ph})""",
            (token_address, confidence, decision, outcome, profit_pct)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"record_confidence_outcome error: {e}")
        return False


def get_confidence_accuracy(db, min_samples: int = 20) -> Dict:
    """
    Get confidence score accuracy statistics for learning.
    Returns accuracy by confidence bucket.
    """
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                CAST(confidence / 10 AS INTEGER) * 10 AS bucket,
                COUNT(*) AS total,
                SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) AS wins,
                AVG(profit_pct) AS avg_profit
            FROM confidence_history
            WHERE outcome IS NOT NULL
            GROUP BY bucket
            HAVING COUNT(*) >= ?
            ORDER BY bucket
        """ if not db.use_postgres else """
            SELECT
                (confidence::int / 10) * 10 AS bucket,
                COUNT(*) AS total,
                SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) AS wins,
                AVG(profit_pct) AS avg_profit
            FROM confidence_history
            WHERE outcome IS NOT NULL
            GROUP BY bucket
            HAVING COUNT(*) >= %s
            ORDER BY bucket
        """, (min_samples,))
        rows = cursor.fetchall()
        conn.close()
        return {
            row[0] if not isinstance(row, dict) else row['bucket']: {
                'total': row[1] if not isinstance(row, dict) else row['total'],
                'win_rate': (row[2] / row[1]) if row[1] > 0 else 0,
                'avg_profit': row[3] if not isinstance(row, dict) else row['avg_profit'],
            }
            for row in rows
        }
    except Exception as e:
        logger.error(f"get_confidence_accuracy error: {e}")
        return {}


def upsert_whale_metrics(db, user_id: int, whale_address: str,
                          win_rate: float, avg_profit: float, sharpe: float,
                          max_drawdown: float, rank_score: float, total_trades: int) -> bool:
    """Persist whale metrics to cache table."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        if db.use_postgres:
            conn.execute(
                f"""INSERT INTO whale_metrics_cache
                    (user_id, whale_address, win_rate, avg_profit, sharpe,
                     max_drawdown, rank_score, total_trades, updated_at)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, whale_address) DO UPDATE SET
                        win_rate=excluded.win_rate, avg_profit=excluded.avg_profit,
                        sharpe=excluded.sharpe, max_drawdown=excluded.max_drawdown,
                        rank_score=excluded.rank_score, total_trades=excluded.total_trades,
                        updated_at=CURRENT_TIMESTAMP""",
                (user_id, whale_address, win_rate, avg_profit, sharpe,
                 max_drawdown, rank_score, total_trades)
            )
        else:
            conn.execute(
                f"""INSERT INTO whale_metrics_cache
                    (user_id, whale_address, win_rate, avg_profit, sharpe,
                     max_drawdown, rank_score, total_trades, updated_at)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, whale_address) DO UPDATE SET
                        win_rate=excluded.win_rate, avg_profit=excluded.avg_profit,
                        sharpe=excluded.sharpe, max_drawdown=excluded.max_drawdown,
                        rank_score=excluded.rank_score, total_trades=excluded.total_trades,
                        updated_at=CURRENT_TIMESTAMP""",
                (user_id, whale_address, win_rate, avg_profit, sharpe,
                 max_drawdown, rank_score, total_trades)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"upsert_whale_metrics error: {e}")
        return False


def get_whale_metrics(db, user_id: int, whale_address: str) -> Optional[Dict]:
    """Get cached whale metrics."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM whale_metrics_cache WHERE user_id={ph} AND whale_address={ph}",
            (user_id, whale_address)
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_whale_metrics error: {e}")
        return None


def get_whale_stats(db, user_id: int, whale_address: str) -> Optional[Dict]:
    """Get whale stats from copy_performance (used by whale_scorer)."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT
                COUNT(*) AS total_trades,
                SUM(CASE WHEN user_profit_percent > 0 THEN 1 ELSE 0 END) AS winning_trades,
                SUM(CASE WHEN user_profit_percent <= 0 THEN 1 ELSE 0 END) AS losing_trades,
                AVG(user_profit_percent) AS avg_profit_pct,
                MAX(opened_at) AS last_trade_at
            FROM copy_performance
            WHERE user_id={ph} AND watched_wallet={ph} AND status='closed'
        """, (user_id, whale_address))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        if isinstance(row, dict):
            return row
        return {
            'total_trades': row[0],
            'winning_trades': row[1],
            'losing_trades': row[2],
            'avg_profit_pct': row[3],
            'last_trade_at': row[4],
            'max_drawdown_pct': 0,
            'consecutive_losses': 0,
        }
    except Exception as e:
        logger.error(f"get_whale_stats error: {e}")
        return None


def get_whale_trades(db, user_id: int, whale_address: str,
                     days: int = 14, limit: int = 100) -> List[Dict]:
    """Get recent whale trades for consistency scoring."""
    try:
        conn = db.get_connection()
        ph = "%s" if db.use_postgres else "?"
        cursor = conn.cursor()
        if db.use_postgres:
            cursor.execute(f"""
                SELECT user_profit_percent AS profit_pct, opened_at, closed_at
                FROM copy_performance
                WHERE user_id={ph} AND watched_wallet={ph} AND status='closed'
                  AND opened_at >= CURRENT_TIMESTAMP - INTERVAL '{days} days'
                ORDER BY opened_at DESC LIMIT {ph}
            """, (user_id, whale_address, limit))
        else:
            cursor.execute(f"""
                SELECT user_profit_percent AS profit_pct, opened_at, closed_at
                FROM copy_performance
                WHERE user_id={ph} AND watched_wallet={ph} AND status='closed'
                  AND opened_at >= datetime('now', '-{days} days')
                ORDER BY opened_at DESC LIMIT {ph}
            """, (user_id, whale_address, limit))
        rows = [dict(r) if not isinstance(r, dict) else r for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"get_whale_trades error: {e}")
        return []


def patch_database(db_instance) -> None:
    """
    Monkey-patch new methods onto the Database singleton.
    Called once from data/__init__.py after db is created.
    """
    import types

    db_instance.get_risk_state_value = types.MethodType(get_risk_state_value, db_instance)
    db_instance.set_risk_state_value = types.MethodType(set_risk_state_value, db_instance)
    db_instance.get_all_risk_state = types.MethodType(get_all_risk_state, db_instance)
    db_instance.record_confidence_outcome = types.MethodType(record_confidence_outcome, db_instance)
    db_instance.get_confidence_accuracy = types.MethodType(get_confidence_accuracy, db_instance)
    db_instance.upsert_whale_metrics = types.MethodType(upsert_whale_metrics, db_instance)
    db_instance.get_whale_metrics = types.MethodType(get_whale_metrics, db_instance)
    db_instance.get_whale_stats = types.MethodType(get_whale_stats, db_instance)
    db_instance.get_whale_trades = types.MethodType(get_whale_trades, db_instance)

    logger.info("✅ Database extensions patched")
