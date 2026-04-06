"""
Migration script to add posted_news and posted_signals tables
Run this once to add persistence for broadcast deduplication on Render
"""
from data.database import db


def migrate():
    """Add posted_news and posted_signals tables for Render deployment persistence"""
    
    print("="*60)
    print("🗄️  BROADCAST PERSISTENCE MIGRATION")
    print("="*60)
    print()
    print("This will add two new tables to your database:")
    print("  • posted_news - Tracks news posted to avoid duplicates across restarts")
    print("  • posted_signals - Tracks signals posted to avoid duplicates across restarts")
    print()
    
    if not db.use_postgres and not db.db_path:
        print("❌ Database not configured")
        return
    
    print(f"📊 Database type: {'PostgreSQL' if db.use_postgres else 'SQLite'}")
    print()
    
    try:
        print("🔧 Tables will be created via init_db() if they don't exist...")
        
        # The tables are already defined in database.py init_db()
        # We just need to re-init to create them
        db.init_db()
        
        print("✅ Migration complete - tables created!")
        print()
        print("="*60)
        print("✅ MIGRATION COMPLETE")
        print("="*60)
        print()
        print("Your database now has persistence for broadcast deduplication.")
        print("This prevents re-posting the same news/signals after Render restarts.")
        print()
        print("Next steps:")
        print("  1. Restart your bot on Render")
        print("  2. Verify news/signals don't duplicate after restart")
        print("  3. Use Admin Panel → Test News/Signal to verify broadcasting")
        print()
        
    except Exception as e:
        print()
        print("="*60)
        print("❌ MIGRATION FAILED")
        print("="*60)
        print()
        print(f"Error: {e}")
        print()
        import traceback
        traceback.print_exc()
        return


if __name__ == '__main__':
    try:
        migrate()
    except KeyboardInterrupt:
        print("\n\n🛑 Migration cancelled")
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
