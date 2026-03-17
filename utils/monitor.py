import os
import time
import asyncio
from datetime import datetime, timedelta
from telegram import Bot
from dotenv import load_dotenv
import threading
import logging
import aiohttp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

from keep_alive import AggressiveKeepAlive


class WhaleWalletTracker:
    """Track whale wallets using ONLY DexScreener"""
    
    def __init__(self):
        self.dexscreener_url = "https://api.dexscreener.com/latest/dex"
        self.whale_wallets = [
            "HUpPyLU8KWisCAr3mzWy2FKT6uuxQ2qGgJQxyTpDoes5",
            "8Nty9vLxN3ZtT4DQjJ5uFrKtvan28rySiGVJ5dPzu81u", 
            "9iXbBirB8F8z1L2QRgRMm8SNWvcM5gDiebmT3TiYhyNT",
        ]
    
    def parse_dexscreener_data(self, data, wallet_address):
        """Parse DexScreener wallet data for active tokens"""
        trades = []
        try:
            pairs = data.get('pairs', [])
            
            for pair in pairs[:5]:  # Check top 5 pairs
                # Only include tokens with decent liquidity
                liquidity = float(pair.get('liquidity', {}).get('usd', 0))
                if liquidity > 1000:  # $1k+ liquidity
                    token_data = {
                        'token_address': pair.get('baseToken', {}).get('address'),
                        'symbol': pair.get('baseToken', {}).get('symbol', 'Unknown'),
                        'price': float(pair.get('priceUsd', 0)),
                        'liquidity': liquidity,
                        'volume_24h': float(pair.get('volume', {}).get('h24', 0)),
                        'price_change_24h': float(pair.get('priceChange', {}).get('h24', 0)),
                        'timestamp': datetime.now(),
                        'wallet': wallet_address,
                        'dex_url': pair.get('url', '')
                    }
                    trades.append(token_data)
                    logger.info(f"   📈 Found {token_data['symbol']} (${token_data['liquidity']:,.0f})")
            
            return trades
            
        except Exception as e:
            logger.error(f"Error parsing DexScreener data: {e}")
            return []
    
    async def get_whale_activity(self):
        """Get whale activity using ONLY DexScreener"""
        whale_tokens = {}
        
        for wallet in self.whale_wallets:
            try:
                logger.info(f"🐋 Checking whale: {wallet[:8]}...")
                
                # Get wallet data from DexScreener
                url = f"{self.dexscreener_url}/search"
                params = {"q": wallet}
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params, timeout=15) as response:
                        if response.status == 200:
                            data = await response.json()
                            trades = self.parse_dexscreener_data(data, wallet)
                            
                            for trade in trades:
                                token_address = trade['token_address']
                                if token_address not in whale_tokens:
                                    whale_tokens[token_address] = {
                                        'token_data': trade,
                                        'whales': [wallet]
                                    }
                                else:
                                    whale_tokens[token_address]['whales'].append(wallet)
                        else:
                            logger.warning(f"DexScreener API error: {response.status}")
                
                # CRITICAL: Wait 10 seconds between API calls
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"Error checking whale {wallet}: {e}")
                continue
        
        logger.info(f"📊 Found {len(whale_tokens)} tokens with whale activity")
        return whale_tokens


class SimpleAnalyzer:
    """Simple analysis without complexity"""
    
    def analyze_token(self, token_data, whale_count):
        """Basic token analysis"""
        try:
            score = 0
            
            # Liquidity check (50 points)
            liquidity = token_data.get('liquidity', 0)
            if liquidity > 50000:
                score += 50
            elif liquidity > 10000:
                score += 40
            elif liquidity > 1000:
                score += 30
            
            # Whale activity (50 points)
            if whale_count >= 2:
                score += 50
            elif whale_count >= 1:
                score += 40
            
            verdict = "STRONG_BUY" if score >= 80 else "BUY" if score >= 60 else "NEUTRAL"
            
            return {
                'score': score,
                'verdict': verdict,
                'whale_count': whale_count
            }
            
        except Exception as e:
            logger.error(f"Analysis error: {e}")
            return {'score': 50, 'verdict': 'NEUTRAL', 'whale_count': whale_count}


class SmartWhaleMonitor:
    def __init__(self):
        self.bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
        self.channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
        self.whale_tracker = WhaleWalletTracker()
        self.analyzer = SimpleAnalyzer()
        self.alert_messages = {}
        self.stats = {
            'alerts_sent': 0,
            'scans_completed': 0,
            'start_time': datetime.now()
        }
    
    async def send_telegram_message(self, text):
        """PROPER async method"""
        try:
            message = await self.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            
            message_id = message.message_id
            self.alert_messages[message_id] = datetime.now()
            logger.info(f"✅ Alert sent: {message_id}")
            
            return message_id
            
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return None
    
    async def delete_old_alerts(self):
        """Delete alerts older than 5 hours"""
        try:
            current_time = datetime.now()
            messages_to_delete = []
            
            for msg_id, sent_time in self.alert_messages.items():
                if (current_time - sent_time).total_seconds() > 5 * 3600:
                    messages_to_delete.append(msg_id)
            
            for msg_id in messages_to_delete:
                try:
                    await self.bot.delete_message(chat_id=self.channel_id, message_id=msg_id)
                    del self.alert_messages[msg_id]
                    logger.info(f"🗑️ Deleted old alert: {msg_id}")
                except Exception as e:
                    logger.error(f"Failed to delete message {msg_id}: {e}")
                    del self.alert_messages[msg_id]
                    
        except Exception as e:
            logger.error(f"Error in delete_old_alerts: {e}")
    
    def format_alert(self, token_data, whale_count, token_address):
        """Format clean alert message"""
        symbol = token_data.get('symbol', 'Unknown')
        price = token_data.get('price', 0)
        liquidity = token_data.get('liquidity', 0)
        
        # Simple analysis
        analysis = self.analyzer.analyze_token(token_data, whale_count)
        
        message = f"🐋 **WHALE ALERT** 🐋\n\n"
        message += f"**Token:** {symbol}\n"
        message += f"**Price:** ${price:.8f}\n"
        message += f"**Liquidity:** ${liquidity:,.0f}\n"
        message += f"**Whales Holding:** {whale_count}\n"
        message += f"**Score:** {analysis['score']}/100\n"
        message += f"**Verdict:** {analysis['verdict']}\n\n"
        
        message += f"**🔗 Trade Links:**\n"
        message += f"• [DexScreener](https://dexscreener.com/solana/{token_address})\n"
        message += f"• [Jupiter](https://jup.ag/swap/SOL-{token_address})\n\n"
        
        message += f"**⚠️ Auto-deletes in 5 hours**\n"
        message += f"⏰ {datetime.now().strftime('%H:%M UTC')}"
        
        return message
    
    async def run_monitoring_cycle(self):
        """Run one monitoring cycle"""
        try:
            # Delete old alerts first
            await self.delete_old_alerts()
            
            # Get whale activity
            whale_tokens = await self.whale_tracker.get_whale_activity()
            
            # Send alerts for tokens with whale activity
            alerts_sent = 0
            for token_address, data in whale_tokens.items():
                whale_count = len(data['whales'])
                if whale_count >= 1:  # At least 1 whale holding
                    alert_message = self.format_alert(data['token_data'], whale_count, token_address)
                    
                    message_id = await self.send_telegram_message(alert_message)
                    if message_id:
                        alerts_sent += 1
                        self.stats['alerts_sent'] += 1
                    
                    # Wait between sending alerts
                    await asyncio.sleep(3)
            
            self.stats['scans_completed'] += 1
            logger.info(f"📊 Cycle complete. Sent {alerts_sent} alerts.")
            
        except Exception as e:
            logger.error(f"Monitoring cycle error: {e}")
    
    async def run_continuous(self):
        """Main async loop"""
        logger.info("=" * 50)
        logger.info("🤖 SOLANA WHALE TRACKER")
        logger.info("=" * 50)
        logger.info(f"🐋 Tracking: {len(self.whale_tracker.whale_wallets)} whales")
        logger.info("⚡ Status: ACTIVE")
        logger.info("=" * 50)
        
        # Send startup message
        try:
            await self.send_telegram_message(
                f"🤖 **Whale Tracker Started!**\n\n"
                f"✅ Tracking {len(self.whale_tracker.whale_wallets)} whales\n"
                f"🔍 Monitoring wallet holdings\n"
                f"🗑️ Auto-delete after 5 hours\n"
                f"⏰ Scans every 10 minutes"
            )
        except Exception as e:
            logger.error(f"Startup message failed: {e}")
        
        # Main loop
        while True:
            try:
                await self.run_monitoring_cycle()
                logger.info("💤 Waiting 10 minutes for next scan...")
                await asyncio.sleep(600)  # 10 minutes
                
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error


def main():
    """Main entry point"""
    port = int(os.getenv('PORT', 10000))
    keep_alive = AggressiveKeepAlive(port=port)
    
    keep_alive_thread = threading.Thread(target=keep_alive.start, daemon=True)
    keep_alive_thread.start()
    
    logger.info("✅ Keep-Alive started")
    logger.info("🚀 Starting Whale Tracker...")
    
    # Create and run monitor
    monitor = SmartWhaleMonitor()
    
    # Run in event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(monitor.run_continuous())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    finally:
        loop.close()


if __name__ == "__main__":
    main()