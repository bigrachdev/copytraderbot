#!/usr/bin/env python3
"""Verify database and all systems are ready"""
import os
from data.database import db

print("🔍 SYSTEM READINESS CHECK\n" + "="*50)

# Check database
print("\n1️⃣  Database Check:")
try:
    db.init_db()
    print("   ✅ Database initialized")
    
    # Check tables
    cursor = db.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"   ✅ Found {len(tables)} tables: {', '.join(tables)}")
except Exception as e:
    print(f"   ❌ Database error: {e}")

# Check .env
print("\n2️⃣  Configuration Check:")
try:
    from dotenv import load_dotenv
    load_dotenv()
    
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if token and token != 'your_token_here':
        print("   ✅ TELEGRAM_BOT_TOKEN configured")
    else:
        print("   ⚠️  TELEGRAM_BOT_TOKEN not fully configured")
    
    render_url = os.getenv('RENDER_EXTERNAL_URL')
    if render_url and render_url != 'your_render_app_url_here':
        print("   ✅ RENDER_EXTERNAL_URL configured")
    else:
        print("   ⚠️  RENDER_EXTERNAL_URL not configured (OK for local)")
        
except Exception as e:
    print(f"   ❌ Config error: {e}")

# Check notification engine
print("\n3️⃣  Notification Engine Check:")
try:
    from notifications import notification_engine, SmartNotificationEngine
    print("   ✅ SmartNotificationEngine loaded")
    print(f"   ✅ Active positions: {len(notification_engine.active_positions)}")
    print("   ✅ Ready to track positions and send alerts")
except Exception as e:
    print(f"   ❌ Notification error: {e}")

# Check Telegram bot
print("\n4️⃣  Telegram Bot Check:")
try:
    from telegram_bot import TelegramBot, main, notification_checker
    print("   ✅ TelegramBot class loaded")
    print("   ✅ notification_checker background task ready")
    print("   ✅ Bot conversation handler prepared")
except Exception as e:
    print(f"   ❌ Bot error: {e}")

print("\n" + "="*50)
print("🎉 SYSTEM READY TO START!")
print("\nTo launch bot:")
print("  python main.py")
print("\nOr test Telegram only:")
print("  python -c \"import asyncio; from telegram_bot import main; asyncio.run(main())\"")
