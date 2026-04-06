"""
Quick test to verify broadcast persistence is working
"""
import asyncio
from data.database import db
from trading.telegram_broadcaster import broadcaster


async def test():
    print("="*60)
    print("🧪 BROADCAST PERSISTENCE TEST")
    print("="*60)
    
    # Test 1: Database tables exist
    print("\n1️⃣ Testing database tables...")
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        if db.use_postgres:
            cursor.execute("SELECT COUNT(*) as count FROM posted_news")
            news_count = cursor.fetchone()['count']
            cursor.execute("SELECT COUNT(*) as count FROM posted_signals")
            signals_count = cursor.fetchone()['count']
        else:
            cursor.execute("SELECT COUNT(*) as count FROM posted_news")
            news_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) as count FROM posted_signals")
            signals_count = cursor.fetchone()[0]
        conn.close()
        print(f"   ✅ posted_news: {news_count} entries")
        print(f"   ✅ posted_signals: {signals_count} entries")
    except Exception as e:
        print(f"   ❌ Database test failed: {e}")
        return
    
    # Test 2: Load posted tracking
    print("\n2️⃣ Testing database load...")
    try:
        news_ids = db.get_posted_news_ids(hours=48)
        signal_hashes = db.get_posted_signal_hashes(hours=24)
        print(f"   ✅ Loaded {len(news_ids)} news IDs")
        print(f"   ✅ Loaded {len(signal_hashes)} signal hashes")
    except Exception as e:
        print(f"   ❌ Load test failed: {e}")
        return
    
    # Test 3: Broadcaster initialization
    print("\n3️⃣ Testing broadcaster initialization...")
    try:
        await broadcaster.initialize()
        if broadcaster.bot:
            print(f"   ✅ Broadcaster initialized")
            print(f"   ✅ Channel: {broadcaster.channel_id}")
        else:
            print(f"   ⚠️  Broadcaster not initialized (check TELEGRAM_CHANNEL_ID)")
    except Exception as e:
        print(f"   ❌ Broadcaster test failed: {e}")
        return
    
    # Test 4: News fetch
    print("\n4️⃣ Testing news fetch...")
    try:
        fetched = await broadcaster._fetch_from_sources(broadcaster._news_sources)
        qualified = [n for n in fetched if n.get('relevance_score', 0) >= broadcaster._min_news_relevance]
        print(f"   ✅ Fetched {len(fetched)} news items")
        print(f"   ✅ {len(qualified)} meet threshold ({broadcaster._min_news_relevance:.0f})")
        
        if qualified:
            print(f"\n   Top 3 news:")
            for i, item in enumerate(qualified[:3], 1):
                print(f"   {i}. [{item.get('relevance_score', 0):.1f}] {item.get('headline', '')[:60]}")
    except Exception as e:
        print(f"   ❌ News fetch failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)
    print("✅ ALL TESTS COMPLETED")
    print("="*60)
    print("\nNext steps:")
    print("  1. Set TELEGRAM_CHANNEL_ID in .env (if not set)")
    print("  2. Deploy to Render")
    print("  3. Use Admin Panel → Test News/Signal to verify")


if __name__ == '__main__':
    try:
        asyncio.run(test())
    except KeyboardInterrupt:
        print("\n\n🛑 Test interrupted")
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
