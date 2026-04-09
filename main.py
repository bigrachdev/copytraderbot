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
    from trading.telegram_broadcaster import broadcaster
    logger.info("✅ Imported telegram_broadcaster")
except Exception as e:
    logger.critical(f"Import error: {e}")
    logger.critical(traceback.format_exc())
    sys.exit(1)

logger.info("All imports successful")

load_dotenv()


async def run_telegram_bot_with_recovery():
    """Run Telegram bot with automatic error recovery - NEVER DIE"""
    restart_count = 0
    max_restart_attempts = 1000  # Nearly infinite
    restart_delay = 5  # Start with 5 second delay
    
    while restart_count < max_restart_attempts:
        try:
            logger.info(f"🚀 Telegram bot startup (attempt {restart_count + 1})")
            await start_telegram_bot()
            logger.info("✅ Telegram bot exited normally")
            break
        
        except KeyboardInterrupt:
            logger.info("🛑 Bot interrupted by user")
            break
        
        except asyncio.CancelledError:
            logger.warning("⚠️ Telegram bot task cancelled")
            break
        
        except Exception as e:
            restart_count += 1
            logger.error(f"❌ Telegram bot crashed: {e}")
            logger.error(traceback.format_exc())
            
            # Exponential backoff with max limit
            restart_delay = min(restart_delay * 1.5, 60)  # Max 60 seconds
            
            logger.critical(f"🔄 AUTO-RESTART #{restart_count}/{max_restart_attempts}")
            logger.critical(f"⏳ Waiting {restart_delay:.1f}s before restart...")
            
            # Notify admins of crash
            try:
                from utils.notifications import notification_engine
                await notification_engine.notify_admins(
                    f"🚨 **BOT CRASH & AUTO-RESTART**\n"
                    f"Error: `{str(e)[:100]}`\n"
                    f"Restart Attempt: `{restart_count}`\n"
                    f"Waiting: `{restart_delay:.1f}s`"
                )
            except Exception as notify_err:
                logger.warning(f"Could not notify admins: {notify_err}")
            
            await asyncio.sleep(restart_delay)
    
    if restart_count >= max_restart_attempts:
        logger.critical("🚨 MAX AUTO-RESTART ATTEMPTS REACHED - MANUAL INTERVENTION REQUIRED")


async def main():
    """Main async entry point with robust error recovery"""
    monitor_task = None
    keep_alive_thread = None
    
    try:
        logger.info("=" * 60)
        logger.info("🚀 ULTIMATE DEX COPY TRADING BOT - INDESTRUCTIBLE MODE")
        logger.info("=" * 60)

        # Start keep-alive service
        port = int(os.getenv('PORT', 10000))
        keep_alive = AggressiveKeepAlive(port=port)
        keep_alive_thread = threading.Thread(target=keep_alive.start, daemon=True, name="KeepAlive")
        keep_alive_thread.start()
        logger.info("✅ Keep-Alive service started (Render sleep prevention)")

        # Start wallet monitoring in background with error recovery
        try:
            monitor_task = asyncio.create_task(run_with_recovery(
                monitor.run(),
                service_name="Wallet Monitor"
            ))
            logger.info("✅ Wallet monitoring started")
        except Exception as e:
            logger.error(f"⚠️ Wallet monitoring failed to start: {e}")

        # Initialize Telegram broadcaster with error handling
        try:
            await broadcaster.initialize()
            logger.info("✅ Telegram broadcaster initialized")
        except Exception as e:
            logger.error(f"⚠️ Telegram broadcaster initialization failed: {e}")
            logger.error("Continuing without broadcaster...")

        # Start Telegram bot with auto-recovery (main loop)
        logger.info("🚀 Starting Telegram bot with auto-recovery...")
        await run_telegram_bot_with_recovery()
        
    except Exception as e:
        logger.critical(f"🚨 FATAL ERROR: {e}")
        logger.critical(traceback.format_exc())
        
        # Attempt to notify admins
        try:
            from utils.notifications import notification_engine
            await notification_engine.notify_admins(
                f"🚨 **FATAL BOT ERROR**\n"
                f"Error: `{str(e)[:100]}`\n"
                f"Check logs for details"
            )
        except:
            pass
    
    finally:
        # Graceful cleanup
        logger.info("🧹 Starting graceful shutdown...")
        try:
            if monitor_task and not monitor_task.done():
                monitor_task.cancel()
                await asyncio.sleep(1)
        except Exception:
            pass
        
        try:
            if keep_alive_thread:
                # Signal keep-alive to stop
                pass
        except Exception:
            pass
        
        logger.info("✅ Shutdown complete")


async def run_with_recovery(coro, service_name: str, max_retries: int = 100):
    """Run async coroutine with automatic recovery"""
    retry_count = 0
    retry_delay = 5
    
    while retry_count < max_retries:
        try:
            await coro
            logger.info(f"✅ {service_name} completed normally")
            break
        except asyncio.CancelledError:
            logger.info(f"⚠️ {service_name} cancelled")
            break
        except Exception as e:
            retry_count += 1
            retry_delay = min(retry_delay * 1.5, 60)
            
            logger.error(f"❌ {service_name} failed: {e}")
            logger.warning(f"🔄 Restarting {service_name} (attempt {retry_count}/{max_retries}) in {retry_delay:.1f}s...")
            
            await asyncio.sleep(retry_delay)
    
    if retry_count >= max_retries:
        logger.critical(f"🚨 {service_name} exceeded max retries - giving up")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot interrupted by user - graceful shutdown")
    except Exception as e:
        logger.critical(f"🚨 Unhandled exception in main: {e}")
        logger.critical(traceback.format_exc())
        sys.exit(1)
