"""
Main entry point for the DEX Copy Trading Bot
"""
import logging
import asyncio
import os
import sys
import traceback

# Configure logging FIRST before any other imports
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

logger.info("Starting main.py initialization...")

try:
    from dotenv import load_dotenv
    logger.info("✅ Imported dotenv")
    import threading
    logger.info("✅ Imported threading")
    from bot.telegram_bot import main as start_telegram_bot
    logger.info("✅ Imported telegram_bot")
    from wallet.wallet_monitor import monitor
    logger.info("✅ Imported wallet_monitor")
    from keep_alive import AggressiveKeepAlive
    logger.info("✅ Imported keep_alive")
    from config import LOG_LEVEL
    logger.info("✅ Imported config")
except Exception as e:
    logger.critical(f"Import error: {e}")
    logger.critical(traceback.format_exc())
    sys.exit(1)

logger.info("All imports successful")

load_dotenv()


async def main():
    """Main async entry point"""
    try:
        logger.info("=" * 60)
        logger.info("🚀 ULTIMATE DEX COPY TRADING BOT")
        logger.info("=" * 60)

        # Start keep-alive service
        port = int(os.getenv('PORT', 10000))
        keep_alive = AggressiveKeepAlive(port=port)
        keep_alive_thread = threading.Thread(target=keep_alive.start, daemon=True)
        keep_alive_thread.start()
        logger.info("✅ Keep-Alive service started")

        # Start wallet monitoring in background
        monitor_task = asyncio.create_task(monitor.run())
        logger.info("✅ Wallet monitoring started")

        # Start Telegram bot (blocking)
        logger.info("✅ Starting Telegram bot...")
        try:
            await start_telegram_bot()
        except KeyboardInterrupt:
            logger.info("🛑 Bot interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        logger.critical(traceback.format_exc())
        raise
    finally:
        # Cleanup
        try:
            monitor_task.cancel()
            keep_alive.running = False
        except Exception:
            pass


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
