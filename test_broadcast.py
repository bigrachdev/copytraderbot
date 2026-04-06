"""
Test script to verify Telegram broadcast functionality
Tests signal broadcasting, news fetching, and channel connectivity
"""
import asyncio
import logging
import sys
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


async def test_telegram_connection():
    """Test 1: Verify Telegram bot can connect and access channel"""
    print("\n" + "="*60)
    print("TEST 1: Telegram Connection Test")
    print("="*60)
    
    try:
        from telegram import Bot
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID
        
        print(f"📱 Bot Token: {'✅ SET' if TELEGRAM_BOT_TOKEN else '❌ MISSING'}")
        print(f"📢 Channel ID: {TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID else '❌ MISSING'}")
        
        if not TELEGRAM_BOT_TOKEN:
            print("❌ Cannot proceed without TELEGRAM_BOT_TOKEN")
            return False
            
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        if not TELEGRAM_CHANNEL_ID or TELEGRAM_CHANNEL_ID == 'your_channel_id_here':
            print("⚠️  Channel ID not configured properly")
            print("📝 Please update your .env file with your actual channel ID")
            print("💡 To find your channel ID:")
            print("   1. Add your bot to the channel as admin")
            print("   2. Send a message to the channel")
            print("   3. Visit: https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates")
            print("   4. Look for 'chat' -> 'id' in the response")
            print("   5. Channel IDs are usually negative (e.g., -1001234567890)")
            return False
        
        print(f"\n🔍 Attempting to access channel: {TELEGRAM_CHANNEL_ID}")
        chat = await bot.get_chat(TELEGRAM_CHANNEL_ID)
        print(f"✅ Successfully connected to channel!")
        print(f"   Title: {chat.title}")
        print(f"   Type: {chat.type}")
        print(f"   Members: {chat.get_member_count() if hasattr(chat, 'get_member_count') else 'N/A'}")
        
        return True
        
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_signal_broadcast():
    """Test 2: Test broadcasting a sample signal"""
    print("\n" + "="*60)
    print("TEST 2: Signal Broadcast Test")
    print("="*60)
    
    try:
        from trading.telegram_broadcaster import broadcaster
        
        await broadcaster.initialize()
        
        if not broadcaster.bot:
            print("❌ Broadcaster not initialized")
            return False
        
        print("✅ Broadcaster initialized")
        
        # Test signal with high liquidity (should pass)
        test_signal = {
            'token_name': 'TestToken',
            'token_address': '7vfCXTUXx5WJV5JADk17DUJ4k6au4qp9qzvYFQmrVE7',
            'action': 'BUY',
            'size_sol': 5.0,
            'size_usd': 750.0,
            'wallet_address': 'ExampleWhaleWallet1234567890abcdef',
            'entry_price': 0.00001234,
            'dexscreener_url': 'https://dexscreener.com/solana/7vfCXTUXx5WJV5JADk17DUJ4k6au4qp9qzvYFQmrVE7',
            'confidence': 'HIGH',
            'liquidity_usd': 50000,  # Above threshold
        }
        
        print(f"\n📤 Broadcasting test signal (liquidity: ${test_signal['liquidity_usd']:,})...")
        result = await broadcaster.broadcast_signal(test_signal)
        
        if result:
            print("✅ Signal broadcast successful!")
        else:
            print("❌ Signal broadcast failed - check logs for reason")
            
        return result
        
    except Exception as e:
        print(f"❌ Signal test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_news_fetch():
    """Test 3: Test news fetching from RSS sources"""
    print("\n" + "="*60)
    print("TEST 3: News Fetch Test")
    print("="*60)
    
    try:
        from trading.telegram_broadcaster import broadcaster
        
        await broadcaster.initialize()
        
        if not broadcaster.bot:
            print("❌ Broadcaster not initialized")
            return False
        
        print("✅ Broadcaster initialized")
        print(f"📰 News sources configured: {len(broadcaster._news_sources)}")
        for i, source in enumerate(broadcaster._news_sources, 1):
            print(f"   {i}. {source['name']} (weight: {source['weight']})")
        
        print(f"\n🔍 Fetching news from all sources...")
        news_items = await broadcaster._fetch_from_sources(broadcaster._news_sources)
        
        print(f"\n📊 Results:")
        print(f"   Total items fetched: {len(news_items)}")
        
        if news_items:
            # Show top 5 by relevance
            news_items.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
            print(f"\n📝 Top {min(5, len(news_items))} news items by relevance:")
            for i, item in enumerate(news_items[:5], 1):
                score = item.get('relevance_score', 0)
                title = item.get('headline', 'No title')[:80]
                will_post = "✅ WILL POST" if score >= broadcaster._min_news_relevance else "❌ Below threshold"
                print(f"   {i}. [{score:.1f}] {will_post}")
                print(f"      {title}")
            
            # Count how many will be posted
            qualified = [n for n in news_items if n.get('relevance_score', 0) >= broadcaster._min_news_relevance]
            print(f"\n✅ {len(qualified)} items meet minimum relevance threshold ({broadcaster._min_news_relevance:.0f})")
        else:
            print("⚠️  No news items fetched - check network or RSS feed URLs")
            
        return len(news_items) > 0
        
    except Exception as e:
        print(f"❌ News fetch test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_database_state():
    """Test 4: Check database for users and watched wallets"""
    print("\n" + "="*60)
    print("TEST 4: Database State Check")
    print("="*60)
    
    try:
        from data.database import db
        
        # Get all users
        users = db.get_all_users_list()
        print(f"👥 Total users in database: {len(users)}")
        
        if users:
            for user in users[:5]:  # Show first 5
                telegram_id = user.get('telegram_id')
                wallet = user.get('wallet_address', 'N/A')[:12]
                print(f"   - User ID: {telegram_id}, Wallet: {wallet}...")
        
        # Check watched wallets
        total_wallets = 0
        active_wallets = 0
        for user in users:
            telegram_id = user.get('telegram_id')
            if telegram_id:
                wallets = db.get_watched_wallets(telegram_id)
                total_wallets += len(wallets)
                active_wallets += sum(1 for w in wallets if w.get('is_active'))
        
        print(f"\n👁️  Watched wallets:")
        print(f"   Total: {total_wallets}")
        print(f"   Active: {active_wallets}")
        
        if total_wallets == 0:
            print("⚠️  No watched wallets found!")
            print("💡 Add a whale wallet using /addwallet command in Telegram")
        else:
            print(f"✅ Found {active_wallets} active watched wallets")
            
        return total_wallets > 0
        
    except Exception as e:
        print(f"❌ Database check failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_news_broadcast():
    """Test 5: Try posting a test news item"""
    print("\n" + "="*60)
    print("TEST 5: News Broadcast Test")
    print("="*60)
    
    try:
        from trading.telegram_broadcaster import broadcaster
        
        await broadcaster.initialize()
        
        if not broadcaster.bot:
            print("❌ Broadcaster not initialized")
            return False
        
        # Create test news
        test_news = {
            'headline': 'Test: Solana Network Upgrade Successful',
            'summary': 'This is a test news item to verify broadcast functionality. The Solana network has successfully completed an upgrade.',
            'source_link': 'https://solana.com/news',
            'source_name': 'Solana Foundation (Test)',
            'relevance_score': 85.0,  # Above threshold
        }
        
        print(f"📰 Broadcasting test news (relevance: {test_news['relevance_score']})...")
        result = await broadcaster.broadcast_news(test_news)
        
        if result:
            print("✅ News broadcast successful!")
        else:
            print("❌ News broadcast failed - check logs for reason")
            
        return result
        
    except Exception as e:
        print(f"❌ News broadcast test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("🧪 TELEGRAM BROADCAST DIAGNOSTIC TEST SUITE")
    print("="*60)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {}
    
    # Run tests
    results['Connection'] = await test_telegram_connection()
    results['Signal Broadcast'] = await test_signal_broadcast()
    results['News Fetch'] = await test_news_fetch()
    results['Database State'] = await test_database_state()
    results['News Broadcast'] = await test_news_broadcast()
    
    # Summary
    print("\n" + "="*60)
    print("📊 TEST SUMMARY")
    print("="*60)
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}  {test_name}")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Your broadcast system is working correctly.")
    else:
        print("\n⚠️  Some tests failed. Review the output above for details.")
        print("\n💡 Common fixes:")
        print("   1. Update TELEGRAM_CHANNEL_ID in your .env file")
        print("   2. Add whale wallets using /addwallet command")
        print("   3. Lower BROADCAST_MIN_LIQUIDITY_USD in .env (currently 30000)")
        print("   4. Lower BROADCAST_MIN_NEWS_RELEVANCE in .env (currently 60)")
    
    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60 + "\n")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Tests interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
