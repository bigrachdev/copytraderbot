"""
Find your Telegram Channel ID
Run this script after adding your bot to the channel as admin
"""
import asyncio
from telegram import Bot
from config import TELEGRAM_BOT_TOKEN


async def find_channel_id():
    """Get channel ID from recent updates"""
    
    print("="*60)
    print("🔍 TELEGRAM CHANNEL ID FINDER")
    print("="*60)
    print()
    print("Instructions:")
    print("1. Add your bot to your channel as an ADMIN")
    print("2. Send a test message to the channel")
    print("3. Run this script - it will find the channel ID")
    print()
    print("="*60)
    
    input("\nPress Enter when you've completed steps 1-3...")
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # Get bot info
        me = await bot.get_me()
        print(f"\n✅ Bot connected: @{me.username}")
        
        # Get updates
        updates = await bot.get_updates(limit=50)
        
        if not updates:
            print("\n❌ No updates found!")
            print("\nPossible reasons:")
            print("  - Bot hasn't been added to any channels/groups")
            print("  - No messages sent after bot joined")
            print("  - Bot doesn't have admin permissions")
            print("\nTroubleshooting:")
            print("  1. Go to your channel")
            print("  2. Add @Kopytraderbot as Admin")
            print("  3. Send a message to the channel")
            print("  4. Run this script again")
            return
        
        print(f"\n📨 Found {len(updates)} recent update(s)")
        
        # Look for channel chats
        channels_found = {}
        for update in updates:
            if update.message:
                if update.message.chat:
                    chat = update.message.chat
                    if chat.type in ['channel', 'supergroup']:
                        channels_found[chat.id] = {
                            'title': chat.title,
                            'type': chat.type,
                            'username': chat.username
                        }
        
        if channels_found:
            print("\n" + "="*60)
            print("📢 CHANNELS FOUND:")
            print("="*60)
            for chat_id, info in channels_found.items():
                print(f"\nChannel ID: {chat_id}")
                print(f"  Title: {info['title']}")
                print(f"  Type: {info['type']}")
                if info['username']:
                    print(f"  Username: @{info['username']}")
            
            print("\n" + "="*60)
            print("📝 NEXT STEPS:")
            print("="*60)
            print("\n1. Copy the Channel ID from above (including the minus sign)")
            print("2. Open your .env file")
            print("3. Replace the TELEGRAM_CHANNEL_ID value:")
            print()
            first_id = list(channels_found.keys())[0]
            print(f"   TELEGRAM_CHANNEL_ID={first_id}")
            print()
            print("4. Save the .env file")
            print("5. Restart your bot")
        else:
            print("\n❌ No channels found in recent updates")
            print("\nMake sure:")
            print("  - Bot is admin in the channel")
            print("  - You sent a message AFTER adding the bot")
            print("  - The channel is not private (or bot was invited)")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)


if __name__ == '__main__':
    try:
        asyncio.run(find_channel_id())
    except KeyboardInterrupt:
        print("\n\n🛑 Cancelled")
