"""
Main entry point for the DEX Copy Trading Bot
"""
import logging
import asyncio
import os
from dotenv import load_dotenv
import threading
from bot.telegram_bot import main as start_telegram_bot
from wallet.wallet_monitor import monitor
from keep_alive import AggressiveKeepAlive
from config import LOG_LEVEL
import subprocess

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()


async def main():
    """Main async entry point"""
    logger.info("=" * 60)
    logger.info("🚀 ULTIMATE DEX COPY TRADING BOT")
    logger.info("=" * 60)
    
    # Start keep-alive service
    port = int(os.getenv('PORT', 10000))
    keep_alive = AggressiveKeepAlive(port=port)
    keep_alive_thread = threading.Thread(target=keep_alive.start, daemon=True)
    keep_alive_thread.start()
    logger.info("✅ Keep-Alive service started")
    
    # Start web dashboard in separate thread
    web_port = int(os.getenv('WEB_PORT', 5000))
    web_thread = threading.Thread(
        target=lambda: subprocess.run(['python', 'bot/web_dashboard.py']),
        daemon=True
    )
    web_thread.start()
    logger.info(f"✅ Web dashboard started on port {web_port}")
    logger.info(f"   📊 Access at http://localhost:{web_port}")
    
    # Start wallet monitoring in background
    monitor_task = asyncio.create_task(monitor.run())
    logger.info("✅ Wallet monitoring started")
    
    # Start Telegram bot (blocking)
    logger.info("✅ Starting Telegram bot...")
    try:
        await start_telegram_bot()
    except KeyboardInterrupt:
        logger.info("🛑 Bot interrupted by user")
        monitor_task.cancel()
        keep_alive.running = False


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
