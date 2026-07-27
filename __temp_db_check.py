from config import DB_PATH, DATABASE_URL
import sqlite3
print('DB_PATH', DB_PATH)
print('DATABASE_URL', DATABASE_URL)
from data.database import db
print('use_postgres', db.use_postgres)
print('db_path', getattr(db, 'db_path', None))
conn = db.get_connection()
cur = conn.cursor()
print('TABLES:')
for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(row)
try:
    for row in cur.execute('SELECT version, description FROM schema_migrations ORDER BY version'):
        print('MIGRATION', row)
except Exception as e:
    print('schema_migrations error', e)
conn.close()
