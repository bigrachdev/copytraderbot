"""
Database migration: adds risk state, confidence history, and learning tables.
Run once on startup via db.run_migrations().

PROFITABILITY IMPACT:
- Risk state survives restarts (daily loss limits enforced across reboots)
- Confidence history enables Bayesian weight learning
- Learning metrics improve future trade quality
"""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Each migration is (version, description, sqlite_sql, postgres_sql)
MIGRATIONS = [
    (
        1,
        "Add risk state table",
        # SQLite
        """
        CREATE TABLE IF NOT EXISTS risk_state (
            user_id     INTEGER NOT NULL,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_risk_state_user ON risk_state(user_id);
        """,
        # PostgreSQL
        """
        CREATE TABLE IF NOT EXISTS risk_state (
            user_id     INTEGER NOT NULL,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_risk_state_user ON risk_state(user_id);
        """,
    ),
    (
        2,
        "Add confidence history table",
        """
        CREATE TABLE IF NOT EXISTS confidence_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            token_address   TEXT    NOT NULL,
            confidence      REAL    NOT NULL,
            decision        TEXT    NOT NULL,
            outcome         TEXT,
            profit_pct      REAL,
            recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_conf_token ON confidence_history(token_address);
        CREATE INDEX IF NOT EXISTS idx_conf_recorded ON confidence_history(recorded_at);
        """,
        """
        CREATE TABLE IF NOT EXISTS confidence_history (
            id              SERIAL PRIMARY KEY,
            token_address   TEXT    NOT NULL,
            confidence      REAL    NOT NULL,
            decision        TEXT    NOT NULL,
            outcome         TEXT,
            profit_pct      REAL,
            recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_conf_token ON confidence_history(token_address);
        CREATE INDEX IF NOT EXISTS idx_conf_recorded ON confidence_history(recorded_at);
        """,
    ),
    (
        3,
        "Add whale metrics cache table",
        """
        CREATE TABLE IF NOT EXISTS whale_metrics_cache (
            user_id         INTEGER NOT NULL,
            whale_address   TEXT    NOT NULL,
            win_rate        REAL    DEFAULT 0,
            avg_profit      REAL    DEFAULT 0,
            sharpe          REAL    DEFAULT 0,
            max_drawdown    REAL    DEFAULT 0,
            rank_score      REAL    DEFAULT 0,
            total_trades    INTEGER DEFAULT 0,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, whale_address)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS whale_metrics_cache (
            user_id         INTEGER NOT NULL,
            whale_address   TEXT    NOT NULL,
            win_rate        REAL    DEFAULT 0,
            avg_profit      REAL    DEFAULT 0,
            sharpe          REAL    DEFAULT 0,
            max_drawdown    REAL    DEFAULT 0,
            rank_score      REAL    DEFAULT 0,
            total_trades    INTEGER DEFAULT 0,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, whale_address)
        );
        """,
    ),
    (
        4,
        "Add schema_migrations table",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        5,
        "Add smart_trades confidence column",
        """
        ALTER TABLE smart_trades ADD COLUMN confidence REAL DEFAULT 0;
        """,
        """
        ALTER TABLE smart_trades ADD COLUMN IF NOT EXISTS confidence REAL DEFAULT 0;
        """,
    ),
    (
        6,
        "Add copy_performance sharpe and drawdown columns",
        """
        ALTER TABLE copy_performance ADD COLUMN whale_sharpe REAL DEFAULT 0;
        ALTER TABLE copy_performance ADD COLUMN whale_max_dd REAL DEFAULT 0;
        """,
        """
        ALTER TABLE copy_performance ADD COLUMN IF NOT EXISTS whale_sharpe REAL DEFAULT 0;
        ALTER TABLE copy_performance ADD COLUMN IF NOT EXISTS whale_max_dd REAL DEFAULT 0;
        """,
    ),
]


def run_migrations(db_instance) -> None:
    """
    Apply all pending migrations.
    Safe to call on every startup — skips already-applied versions.
    """
    conn = db_instance.get_connection()
    cursor = conn.cursor()
    ph = "%s" if db_instance.use_postgres else "?"

    # Ensure migrations table exists first
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Get applied versions
    cursor.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in cursor.fetchall()}

    applied_count = 0
    for version, description, sqlite_sql, postgres_sql in MIGRATIONS:
        if version in applied:
            continue

        sql = postgres_sql if db_instance.use_postgres else sqlite_sql

        try:
            # Execute each statement separately
            for statement in sql.strip().split(";"):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)

            # Record migration
            if db_instance.use_postgres:
                cursor.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (%s, %s)",
                    (version, description)
                )
            else:
                cursor.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    (version, description)
                )

            conn.commit()
            applied_count += 1
            logger.info(f"✅ Migration {version} applied: {description}")

        except Exception as e:
            # Some ALTER TABLE statements fail if column already exists — that's OK
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                logger.debug(f"Migration {version} skipped (already applied): {e}")
                # Still record it so we don't retry
                try:
                    if db_instance.use_postgres:
                        cursor.execute(
                            "INSERT INTO schema_migrations (version, description) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (version, description)
                        )
                    else:
                        cursor.execute(
                            "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (?, ?)",
                            (version, description)
                        )
                    conn.commit()
                except Exception:
                    pass
            else:
                logger.error(f"Migration {version} failed: {e}")
                conn.rollback()

    conn.close()

    if applied_count:
        logger.info(f"✅ Applied {applied_count} database migration(s)")
    else:
        logger.debug("Database schema up to date")
