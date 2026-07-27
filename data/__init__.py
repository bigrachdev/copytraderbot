"""
Data layer initialisation.
Runs migrations and patches extensions onto the db singleton at import time.
"""
from data.database import db
from data.migrations import run_migrations
from data.db_extensions import patch_database

# Apply schema migrations (idempotent — safe to run every startup)
try:
    run_migrations(db)
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"Migration error: {_e}")

# Attach new methods to the existing singleton
patch_database(db)
