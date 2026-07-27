import traceback

from config import DB_PATH, DATABASE_URL
import sqlite3
print('DB_PATH', DB_PATH)
print('DATABASE_URL', DATABASE_URL)
try:
    from data.database import db
    print('Imported data.database successfully')
    print('use_postgres', db.use_postgres)
    print('db_path', getattr(db, 'db_path', None))
    try:
        conn = db.get_connection()
        print('Connection OK')
        conn.close()
    except Exception as inner:
        print('Connection failed:', inner)
except Exception as e:
    print('Import failed:', e)
    traceback.print_exc()
