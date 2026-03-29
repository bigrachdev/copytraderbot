"""
Migration script to add UNIQUE constraint to watched_wallets table
Run this once to update existing databases
"""
import logging
from data.database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate():
    """Add UNIQUE constraint to watched_wallets(user_id, wallet_address)"""
    logger.info("Starting migration: Adding UNIQUE constraint to watched_wallets...")
    
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        if db.use_postgres:
            # Check if constraint already exists
            cursor.execute("""
                SELECT 1 FROM information_schema.table_constraints 
                WHERE constraint_name = 'unique_user_wallet' 
                AND table_name = 'watched_wallets'
            """)
            if cursor.fetchone():
                logger.info("✅ Constraint already exists")
                conn.close()
                return
            
            # Remove duplicates first (keep the oldest entry)
            cursor.execute("""
                DELETE FROM watched_wallets a USING watched_wallets b
                WHERE a.user_id = b.user_id 
                AND a.wallet_address = b.wallet_address
                AND a.ctid < b.ctid
                AND a.is_active = TRUE AND b.is_active = TRUE
            """)
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"🗑️ Removed {deleted} duplicate entries")
            
            # Add UNIQUE constraint
            cursor.execute("""
                ALTER TABLE watched_wallets 
                ADD CONSTRAINT unique_user_wallet 
                UNIQUE (user_id, wallet_address)
            """)
            logger.info("✅ Added UNIQUE constraint (PostgreSQL)")
            
        else:
            # SQLite: Need to recreate table with UNIQUE constraint
            # First check if table already has the constraint by trying to create a new table
            cursor.execute("PRAGMA table_info(watched_wallets)")
            columns = cursor.fetchall()
            
            # Check if UNIQUE exists in any column definition
            has_unique = any('UNIQUE' in col[1] for col in columns)
            
            if has_unique:
                logger.info("✅ UNIQUE constraint already exists")
                conn.close()
                return
            
            # Remove duplicates (keep oldest)
            cursor.execute("""
                DELETE FROM watched_wallets 
                WHERE id NOT IN (
                    SELECT MIN(id) 
                    FROM watched_wallets 
                    GROUP BY user_id, wallet_address
                )
            """)
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"🗑️ Removed {deleted} duplicate entries")
            
            # Create new table with UNIQUE constraint
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watched_wallets_new (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    wallet_address TEXT NOT NULL,
                    alias TEXT,
                    chain TEXT DEFAULT 'solana',
                    copy_scale REAL DEFAULT 1.0,
                    copy_delay_seconds INTEGER DEFAULT 0,
                    max_loss_percent REAL DEFAULT 20.0,
                    weight REAL DEFAULT 1.0,
                    is_active BOOLEAN DEFAULT 1,
                    is_paused BOOLEAN DEFAULT 0,
                    pause_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, wallet_address),
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            
            # Copy data
            cursor.execute("""
                INSERT INTO watched_wallets_new 
                SELECT * FROM watched_wallets
            """)
            
            # Drop old table
            cursor.execute("DROP TABLE watched_wallets")
            
            # Rename new table
            cursor.execute("ALTER TABLE watched_wallets_new RENAME TO watched_wallets")
            
            logger.info("✅ Added UNIQUE constraint (SQLite)")
        
        conn.commit()
        logger.info("✅ Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Migration failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
