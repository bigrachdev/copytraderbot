"""
Telegram Broadcasting Module
Posts trade signals, whale alerts, token launches, market updates, news, and self-ads to Telegram channel.

Intervals (configurable via .env):
- Trade signals/whale alerts/token launches: on-demand (rate-limited)
- Market updates (SOL + top token performance): every N minutes
- News posts: every 30 minutes (BROADCAST_NEWS_INTERVAL_MINUTES)
- Self-advertisement: every 4 hours (BROADCAST_SELF_AD_INTERVAL_HOURS)
"""
import logging
import asyncio
import aiohttp
import time
import hashlib
import re
import html
import os
import xml.etree.ElementTree as ET
from typing import Dict, Optional, List, Set
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from telegram import Bot
from telegram.error import TelegramError
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, BROADCAST_NEWS_INTERVAL_MINUTES, BROADCAST_SELF_AD_INTERVAL_HOURS

logger = logging.getLogger(__name__)


class TelegramBroadcaster:
    """Broadcast trading signals and updates to Telegram channel."""

    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.channel_id = self._normalize_channel_id(TELEGRAM_CHANNEL_ID)
        self.bot: Optional[Bot] = None
        self._last_post_times: Dict[str, float] = {}  # type -> timestamp
        
        # Initialize posted tracking with database persistence for Render
        try:
            from data.database import db
            self._posted_news = {
                news_id: time.time()  # Use current time as placeholder
                for news_id in db.get_posted_news_ids(hours=48)
            }
            self._posted_signals = {
                sig_hash: time.time()  # Use current time as placeholder  
                for sig_hash in db.get_posted_signal_hashes(hours=24)
            }
            logger.info(f"📊 Loaded posted tracking from database: {len(self._posted_news)} news, {len(self._posted_signals)} signals")
        except Exception as e:
            logger.warning(f"Failed to load posted tracking from database: {e}")
            self._posted_news: Dict[str, float] = {}  # news_id -> posted timestamp
            self._posted_signals: Dict[str, float] = {}  # hash -> posted timestamp
        
        self._news_fetch_interval = BROADCAST_NEWS_INTERVAL_MINUTES * 60  # configurable (default 30 minutes)
        self._self_ad_interval = BROADCAST_SELF_AD_INTERVAL_HOURS * 60 * 60  # configurable (default 4 hours)
        self._max_signals_per_hour = 10
        self._max_signals_last_hour: List[float] = []  # timestamps
        
        # Configurable thresholds - can be overridden via environment variables
        self._min_liquidity_usd = int(os.getenv('BROADCAST_MIN_LIQUIDITY_USD', '30000'))
        self._min_news_relevance = float(os.getenv('BROADCAST_MIN_NEWS_RELEVANCE', '60'))
        self._market_update_interval = int(os.getenv('BROADCAST_MARKET_UPDATE_INTERVAL_MINUTES', '30')) * 60
        self._top_token_count = int(os.getenv('BROADCAST_TOP_TOKEN_COUNT', '5'))
        self._min_top_token_liquidity = float(os.getenv('BROADCAST_TOP_TOKEN_MIN_LIQUIDITY_USD', '25000'))
        self._news_max_age_hours = int(os.getenv('BROADCAST_NEWS_MAX_AGE_HOURS', '24'))
        self._launch_update_interval = int(os.getenv('BROADCAST_LAUNCH_UPDATE_INTERVAL_MINUTES', '20')) * 60
        self._launch_max_age_minutes = int(os.getenv('BROADCAST_LAUNCH_MAX_AGE_MINUTES', '180'))
        self._launch_min_liquidity_usd = float(os.getenv('BROADCAST_LAUNCH_MIN_LIQUIDITY_USD', '10000'))
        self._launch_scan_limit = int(os.getenv('BROADCAST_LAUNCH_SCAN_LIMIT', '15'))
        self._max_token_keywords = int(os.getenv('BROADCAST_MAX_TOKEN_NEWS_KEYWORDS', '80'))
        self._posted_launches: Dict[str, float] = {}
        self._dynamic_token_keywords: Set[str] = set()
        self._static_sol_token_keywords: Set[str] = {
            'jupiter', 'jup', 'raydium', 'ray', 'orca', 'bonk', 'wif', 'pyth',
            'jito', 'jto', 'drift', 'render', 'rndr', 'wen', 'popcat', 'kamino',
            'meteora', 'tensor', 'hivemapper', 'helium', 'hnt',
        }
        
        logger.info(
            "📊 Broadcast thresholds: liquidity=$%s, news relevance=%.1f, news max age=%sh, top token liquidity=$%s, launch min liquidity=$%s",
            f"{self._min_liquidity_usd:,}",
            self._min_news_relevance,
            self._news_max_age_hours,
            f"{int(self._min_top_token_liquidity):,}",
            f"{int(self._launch_min_liquidity_usd):,}",
        )
        
        self._news_sources: List[Dict] = [
            {
                'name': 'Solana Foundation',
                'url': 'https://solana.com/news/rss.xml',
                'weight': 1.30,
                'type': 'official',
            },
            {
                'name': 'CoinDesk',
                'url': 'https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml',
                'weight': 1.15,
                'type': 'media',
            },
            {
                'name': 'The Block',
                'url': 'https://www.theblock.co/rss.xml',
                'weight': 1.15,
                'type': 'media',
            },
            {
                'name': 'Blockworks',
                'url': 'https://blockworks.co/feed',
                'weight': 1.10,
                'type': 'media',
            },
            {
                'name': 'Decrypt',
                'url': 'https://decrypt.co/feed',
                'weight': 1.05,
                'type': 'media',
            },
        ]
        self._news_sources.extend(self._load_extra_sources_from_env())
        logger.info("📰 News sources configured: %s", len(self._news_sources))

    @staticmethod
    def _normalize_channel_id(raw_channel_id: Optional[str]) -> Optional[str]:
        """Normalize TELEGRAM_CHANNEL_ID and strip accidental inline comments."""
        if not raw_channel_id:
            return raw_channel_id
        if isinstance(raw_channel_id, str):
            # Accept values like: -1001234567890  # main channel
            value = raw_channel_id.split('#', 1)[0].strip()
            return value
        return str(raw_channel_id)

    @staticmethod
    def _is_placeholder_channel_id(channel_id: Optional[str]) -> bool:
        """Detect common placeholder channel ids that should not be used in production."""
        if not channel_id:
            return True
        cid = str(channel_id).strip().lower()
        return (
            'xxxx' in cid
            or 'your_channel_id' in cid
            or cid in ('-100xxxxxxxxxx', 'channel_id_here')
        )

    @staticmethod
    def _safe_text(value: object) -> str:
        """Escape text values for HTML parse mode."""
        return html.escape(str(value or ''))

    async def initialize(self):
        """Initialize the bot and start background tasks."""
        if not self.bot_token or not self.channel_id:
            logger.warning("⚠️ Telegram broadcast not configured: BOT_TOKEN=%s, CHANNEL_ID=%s", 
                          'SET' if self.bot_token else 'MISSING', 
                          self.channel_id or 'MISSING')
            return
        if self._is_placeholder_channel_id(self.channel_id):
            logger.error(
                "❌ TELEGRAM_CHANNEL_ID appears to be a placeholder (%s). "
                "Set a real channel id like -1001234567890.",
                self.channel_id
            )
            return

        try:
            self.bot = Bot(token=self.bot_token)
            # Verify bot can access channel
            chat = await self.bot.get_chat(self.channel_id)
            logger.info(f"✅ Telegram broadcaster initialized for channel: {chat.title} (ID: {self.channel_id})")

            # Post self-ad on startup
            logger.info("📢 Posting self-advertisement on startup...")
            await self.post_self_ad()
            await self.post_market_update()

            # Start background loops
            logger.info(
                "📈 Starting market update loop (interval: %s minutes)",
                int(self._market_update_interval / 60),
            )
            asyncio.create_task(self._market_update_loop())
            logger.info(
                "🆕 Starting token launch loop (interval: %s minutes)",
                int(self._launch_update_interval / 60),
            )
            asyncio.create_task(self._launch_update_loop())
            logger.info(f"📰 Starting news loop (interval: {BROADCAST_NEWS_INTERVAL_MINUTES} minutes)")
            asyncio.create_task(self._news_loop())
            logger.info(f"📢 Starting self-ad loop (interval: {BROADCAST_SELF_AD_INTERVAL_HOURS} hours)")
            asyncio.create_task(self._self_ad_loop())
            logger.info("✅ All background broadcast tasks started")

        except TelegramError as e:
            logger.error(f"Failed to initialize Telegram broadcaster: {e}")

    # =========================================================================
    # Public API - Call these from your trading flow
    # =========================================================================

    async def broadcast_signal(self, signal_data: Dict) -> bool:
        """
        Broadcast a trade signal when a tracked wallet makes a move.

        Args:
            signal_data: {
                'token_name': str,
                'token_address': str,
                'action': 'BUY' or 'SELL',
                'size_sol': float,
                'size_usd': float,
                'wallet_address': str,
                'entry_price': float,
                'dexscreener_url': str,
                'confidence': 'HIGH' | 'MEDIUM' | 'LOW',
                'liquidity_usd': float,
            }
        """
        logger.info(f"📢 Attempting to broadcast signal: {signal_data.get('token_name', 'Unknown')}")
        
        if not self.bot:
            logger.warning("⚠️ Cannot broadcast signal: Telegram bot not initialized")
            return False

        # Safety checks
        if not await self._check_rate_limits(signal_data):
            logger.warning("⚠️ Signal blocked by rate limits or duplicate detection")
            return False

        # Skip tokens with low liquidity
        liquidity = signal_data.get('liquidity_usd', 0)
        if liquidity < self._min_liquidity_usd:
            logger.warning(f"⚠️ Signal skipped: Low liquidity ${liquidity:.0f} < ${self._min_liquidity_usd:,} threshold")
            return False
        else:
            logger.info(f"✅ Liquidity check passed: ${liquidity:.0f} >= ${self._min_liquidity_usd:,}")

        # Build message
        confidence_emoji = {'HIGH': '🔥', 'MEDIUM': '⚡', 'LOW': '👀'}
        confidence = signal_data.get('confidence', 'MEDIUM')
        emoji = confidence_emoji.get(confidence, '⚡')

        token_name = self._safe_text(signal_data.get('token_name', 'Unknown Token'))
        token_address = self._safe_text(signal_data.get('token_address', 'N/A'))
        wallet = str(signal_data.get('wallet_address', 'unknown'))
        wallet_short = f"{wallet[:4]}...{wallet[-4:]}" if len(wallet) > 8 else wallet
        wallet_short = self._safe_text(wallet_short)
        action = self._safe_text(signal_data.get('action', 'BUY'))
        dexscreener_url = self._safe_text(signal_data.get('dexscreener_url', 'N/A'))

        message = f"""
<b>🚨 TRADE SIGNAL</b>

<b>Token:</b> <code>{token_name}</code>
<b>CA:</b> <code>{token_address}</code>

<b>Action:</b> {action}
<b>Size:</b> {signal_data.get('size_sol', 0):.4f} SOL (${signal_data.get('size_usd', 0):.2f})

<b>Source Wallet:</b> <code>{wallet_short}</code>

<b>Entry Price:</b> ${signal_data['entry_price']:.8f}
<b>DexScreener:</b> {dexscreener_url}

<b>Confidence:</b> {emoji} {confidence}

━━━━━━━━━━━━━━━━━━
<i>Not financial advice. DYOR.</i>
"""

        return await self._send_message(message)

    async def broadcast_whale_alert(self, alert_data: Dict) -> bool:
        """
        Broadcast when a wallet moves > $10k USD equivalent.
        
        Args:
            alert_data: {
                'wallet_label': str,
                'wallet_address': str,
                'token_name': str,
                'token_address': str,
                'action': 'BUY' or 'SELL',
                'amount': float,
                'usd_value': float,
                'tx_hash': str,
            }
        """
        if not self.bot:
            return False

        wallet = str(alert_data.get('wallet_label') or (
            f"{alert_data.get('wallet_address', 'unknown')[:4]}...{alert_data.get('wallet_address', 'unknown')[-4:]}"
        ))
        wallet = self._safe_text(wallet)
        action = self._safe_text(alert_data.get('action', 'BUY'))
        token_name = self._safe_text(alert_data.get('token_name', 'Unknown Token'))
        tx_hash = self._safe_text(alert_data.get('tx_hash', 'N/A'))

        solscan_url = f"https://solscan.io/tx/{tx_hash}"

        message = f"""
<b>🐋 WHALE ALERT</b>

<b>Wallet:</b> <code>{wallet}</code>

<b>Action:</b> {action}
<b>Token:</b> <code>{token_name}</code>
<b>Amount:</b> {alert_data['amount']:.4f}

<b>USD Value:</b> ${alert_data['usd_value']:,.2f}

<b>Tx:</b> {solscan_url}

━━━━━━━━━━━━━━━━━━
<i>Not financial advice. DYOR.</i>
"""

        return await self._send_message(message)

    async def broadcast_token_launch(self, launch_data: Dict) -> bool:
        """
        Broadcast new Raydium/pump.fun listing.
        
        Args:
            launch_data: {
                'token_name': str,
                'symbol': str,
                'contract_address': str,
                'platform': 'pump.fun' | 'Raydium',
                'liquidity_usd': float,
                'age_minutes': float,
                'risk_label': 'DEGEN' | 'OKAY' | 'RUG_RISK',
                'dexscreener_url': str,
            }
        """
        if not self.bot:
            return False

        risk_emoji = {'DEGEN': '⚠️', 'OKAY': '✅', 'RUG_RISK': '🚨'}
        risk = str(launch_data.get('risk_label', 'DEGEN'))
        emoji = risk_emoji.get(risk, '⚠️')
        token_name = self._safe_text(launch_data.get('token_name', 'Unknown Token'))
        symbol = self._safe_text(launch_data.get('symbol', '?'))
        contract_address = self._safe_text(launch_data.get('contract_address', 'N/A'))
        platform = self._safe_text(launch_data.get('platform', 'N/A'))
        dexscreener_url = self._safe_text(launch_data.get('dexscreener_url', 'N/A'))
        risk_label = self._safe_text(risk.replace('_', ' '))

        message = f"""
<b>🎉 TOKEN LAUNCH ALERT</b>

<b>Token:</b> {token_name} (${symbol})
<b>CA:</b> <code>{contract_address}</code>

<b>Platform:</b> {platform}
<b>Starting Liquidity:</b> ${float(launch_data.get('liquidity_usd', 0) or 0):,.2f}
<b>Pool Age:</b> {float(launch_data.get('age_minutes', 0) or 0):.0f} minutes

<b>Risk Label:</b> {emoji} {risk_label}

<b>DexScreener:</b> {dexscreener_url}

━━━━━━━━━━━━━━━━━━
<i>Not financial advice. DYOR.</i>
"""

        return await self._send_message(message)

    async def broadcast_news(self, news_data: Dict) -> bool:
        """
        Broadcast Solana-related news from verified sources.
        
        Args:
            news_data: {
                'headline': str,
                'summary': str,
                'source_link': str,
                'relevance_score': float,
            }
        """
        if not self.bot:
            return False

        # Only post if relevance score meets minimum threshold
        relevance = news_data.get('relevance_score', 0)
        if relevance < self._min_news_relevance:
            logger.debug(f"Skipping low-relevance news (score: {relevance:.1f} < {self._min_news_relevance:.1f})")
            return False
        else:
            logger.info(f"✅ News relevance check passed: {relevance:.1f} >= {self._min_news_relevance:.1f}")

        headline = self._clean_text(news_data.get('headline', ''))
        summary = self._clean_text(news_data.get('summary', ''))
        source_link = news_data.get('source_link', '')
        source_name = self._clean_text(news_data.get('source_name', 'Source'))
        source_type = self._clean_text(news_data.get('source_type', 'media')).upper()
        published_at = self._clean_text(news_data.get('published_at', ''))
        published_line = f"Published: {html.escape(published_at)}\n" if published_at else ""
        brief = summary[:350] + ("..." if len(summary) > 350 else "")

        message = f"""
<b>🗞 SOLANA INTEL BRIEF</b>

<b>{html.escape(headline)}</b>

{html.escape(brief)}

<b>Source:</b> <a href="{source_link}">{html.escape(source_name)}</a>
<b>Type:</b> {source_type}
{published_line}<b>Relevance:</b> {relevance:.1f}/100

━━━━━━━━━━━━━━━━━━━━
<i>Not financial advice. DYOR.</i>
"""

        return await self._send_message(message)

    async def post_self_ad(self) -> bool:
        """
        Post self-advertisement introducing the bot.
        Posted on startup and every 4 hours (customized from original 24h).
        """
        if not self.bot:
            return False

        message = """
<b>🤖 KOPYTRADER BOT </b>
<b> YOUR SOLANA TRADING EDGE</b>
<b>🐋 Copy Trade Whale Wallets</b>
• Add any Solana wallet to watch & auto-copy their trades
• Real-time monitoring via WebSocket (1-2s latency)
• Live Birdeye leaderboard to discover top traders
• Auto-pause underperforming whales

<b>⚡ Smart Autonomous Trading</b>
• AI-powered token discovery & momentum scoring
• Auto-buy tokens meeting your risk/reward criteria
• Kelly Criterion position sizing
• Graduated take-profit ladder (25%/50%/100%)

<b>🛡️ Advanced Risk Management</b>
• Trailing stops & hard stop-loss protection
• Token safety scoring (honeypot, liquidity, holders)
• Daily loss limits & cool-off periods
• RugCheck integration to avoid scam tokens

<b>💼 Full Portfolio Control</b>
• View all holdings with real-time USD values
• Track open positions with live ROI
• One-tap sell from notifications
• Detailed performance analytics

<b>🎨 Custom Vanity Wallets</b>
• Generate Solana wallets with custom prefixes
• Branded addresses (1-6 character difficulty)
• Separate trading wallet support

<b>📊 Jupiter DEX Swapping</b>
• Instant SOL ↔ Token swaps via Jupiter V6
• Best price routing across all Solana DEXs
• Priority fee optimization for fast execution
• MEV protection via Jito private pools

━━━━━━━━━━━━━━━━━━
<b>💬 Send /start to @Kopytraderbot</b>
<i>Work smart, not hard. 🚀</i>
"""

        result = await self._send_message(message)

        if result:
            logger.info("Self-ad posted to channel")
        return result

    async def post_market_update(self) -> bool:
        """Post SOL market snapshot with top token performance."""
        if not self.bot:
            return False

        try:
            async with aiohttp.ClientSession(
                headers={'User-Agent': 'KopytraderBot/1.0 (+https://t.me/Kopytraderbot)'}
            ) as session:
                sol_data = await self._fetch_sol_market_data(session)
                top_tokens = await self._fetch_top_token_performance(session, self._top_token_count)
                self._register_dynamic_token_keywords(top_tokens)

            if not sol_data and not top_tokens:
                logger.warning("⚠️ Market update skipped: no SOL data or top token data available")
                return False

            now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
            sol_lines = []
            if sol_data:
                sol_lines = [
                    f"<b>Price:</b> ${sol_data.get('price_usd', 0):.4f}",
                    (
                        f"<b>Change:</b> 1h {self._fmt_pct(sol_data.get('change_1h'))} "
                        f"| 24h {self._fmt_pct(sol_data.get('change_24h'))}"
                    ),
                    f"<b>24h Volume:</b> ${sol_data.get('volume_24h', 0):,.0f}",
                    f"<b>Liquidity:</b> ${sol_data.get('liquidity_usd', 0):,.0f}",
                ]
            else:
                sol_lines = ["Data unavailable right now."]

            top_lines = []
            if top_tokens:
                for idx, token in enumerate(top_tokens, 1):
                    symbol = self._safe_text(token.get('symbol') or '?')
                    change_24h = self._fmt_pct(token.get('change_24h'))
                    vol_24h = token.get('volume_24h', 0.0)
                    liquidity = token.get('liquidity_usd', 0.0)
                    top_lines.append(
                        f"{idx}. <b>{symbol}</b> {change_24h} | Vol ${vol_24h:,.0f} | Liq ${liquidity:,.0f}"
                    )
            else:
                top_lines = ["No qualifying tokens found in this cycle."]

            message = (
                "<b>📈 SOL MARKET UPDATE</b>\n\n"
                + "\n".join(sol_lines)
                + "\n\n"
                + "<b>🏆 TOP TOKEN PERFORMANCE (24H)</b>\n"
                + "\n".join(top_lines)
                + f"\n\n<i>Updated: {now_utc}</i>\n"
                + "<i>Not financial advice. DYOR.</i>"
            )

            return await self._send_message(message)
        except Exception as e:
            logger.error(f"Market update post failed: {e}")
            return False

    # =========================================================================
    # Background Loops
    # =========================================================================

    async def _news_loop(self):
        """Fetch and post Solana news every 30 minutes."""
        if not self.bot:
            return

        logger.info("📰 Starting news fetch loop (every 30 minutes)")

        while True:
            try:
                await self._fetch_and_post_news()
                await asyncio.sleep(self._news_fetch_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"News loop error: {e}")
                await asyncio.sleep(60)  # Wait before retry

    async def _market_update_loop(self):
        """Post SOL market + top token performance on a schedule."""
        if not self.bot:
            return

        logger.info(
            "📈 Starting market update loop (every %s minutes)",
            int(self._market_update_interval / 60),
        )
        while True:
            try:
                await asyncio.sleep(self._market_update_interval)
                await self.post_market_update()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Market update loop error: {e}")
                await asyncio.sleep(60)

    async def _self_ad_loop(self):
        """Post self-advertisement every 4 hours (customized from original 24h)."""
        if not self.bot:
            return

        logger.info("📢 Starting self-ad loop (every 4 hours)")

        while True:
            try:
                await asyncio.sleep(self._self_ad_interval)
                await self.post_self_ad()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Self-ad loop error: {e}")
                await asyncio.sleep(60)  # Wait before retry

    async def _launch_update_loop(self):
        """Fetch and post newly launched Solana tokens on a schedule."""
        if not self.bot:
            return

        logger.info(
            "🆕 Starting launch update loop (every %s minutes)",
            int(self._launch_update_interval / 60),
        )
        while True:
            try:
                await self._fetch_and_post_launch_updates()
                await asyncio.sleep(self._launch_update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Launch update loop error: {e}")
                await asyncio.sleep(60)

    # =========================================================================
    # Internal Methods
    # =========================================================================

    async def _send_message(self, message: str) -> bool:
        """Send message to Telegram channel."""
        if not self.bot or not self.channel_id:
            logger.warning(f"⚠️ Cannot send message: bot={'SET' if self.bot else 'NONE'}, channel_id={self.channel_id or 'NONE'}")
            return False

        try:
            # Telegram supports much longer messages (~4096 chars).
            # Keep a safe buffer for formatting and future edits.
            max_chars = 3900
            if len(message) > max_chars:
                logger.warning(f"⚠️ Message truncated from {len(message)} to {max_chars} chars")
                message = message[:max_chars - 3] + "..."

            logger.info(f"📤 Sending message to channel {self.channel_id} ({len(message)} chars)")
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=False,
            )
            logger.info("✅ Message sent successfully")
            return True

        except TelegramError as e:
            err = str(e)
            # Fallback: if HTML entity parsing fails, retry plain text so alerts are not dropped.
            if 'parse entities' in err.lower() or "can't parse" in err.lower():
                logger.warning(f"⚠️ HTML parse failed, retrying plain text: {e}")
                plain = re.sub(r'<[^>]+>', '', message)
                try:
                    await self.bot.send_message(
                        chat_id=self.channel_id,
                        text=plain,
                        disable_web_page_preview=False,
                    )
                    logger.info("✅ Message sent successfully (plain text fallback)")
                    return True
                except Exception as fallback_err:
                    logger.error(f"Fallback plain text send failed: {fallback_err}")
            logger.error(f"Failed to send Telegram message: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending message: {e}")
            return False

    async def _check_rate_limits(self, signal_data: Dict) -> bool:
        """Check rate limits before posting signal."""
        now = time.time()

        # Check duplicate signals (same CA + action within 15 minutes)
        signal_key = f"{signal_data['token_address']}_{signal_data['action']}"
        signal_hash = hashlib.md5(signal_key.encode()).hexdigest()

        last_seen = self._posted_signals.get(signal_hash, 0)
        if now - last_seen < 15 * 60:
            logger.debug(f"Duplicate signal skipped within 15m window: {signal_key}")
            return False

        # Max 1 signal per 2 minutes
        if now - self._last_post_times.get('signal', 0) < 120:
            logger.debug("Rate limit: max 1 signal per 2 minutes")
            return False

        # Max 10 signals per hour
        hour_ago = now - 3600
        self._max_signals_last_hour = [
            t for t in self._max_signals_last_hour if t > hour_ago
        ]
        if len(self._max_signals_last_hour) >= self._max_signals_per_hour:
            logger.debug("Rate limit: max 10 signals per hour reached")
            return False

        # Track this signal
        self._last_post_times['signal'] = now
        self._max_signals_last_hour.append(now)
        self._posted_signals[signal_hash] = now

        # Save to database for persistence across Render restarts
        try:
            from data.database import db
            db.save_posted_signal(
                signal_hash,
                token_address=signal_data.get('token_address', ''),
                action=signal_data.get('action', '')
            )
        except Exception as e:
            logger.error(f"Failed to save posted signal to database: {e}")

        # Clean old posted signals (keep last 15 minutes)
        self._cleanup_posted_signals()

        return True

    def _cleanup_posted_signals(self):
        """Remove old signal hashes from tracking dict."""
        cutoff = time.time() - (15 * 60)
        self._posted_signals = {
            sig_hash: ts for sig_hash, ts in self._posted_signals.items()
            if ts >= cutoff
        }

    async def _fetch_and_post_news(self):
        """Fetch Solana news from verified sources and post relevant items."""
        logger.info("📰 ====== Starting news fetch cycle ======")
        fetched = await self._fetch_from_sources(self._news_sources)
        if not fetched:
            logger.warning("⚠️ No news items fetched from configured sources - check RSS feeds or network connectivity")
            logger.warning("⚠️ Sources configured: {[s['name'] for s in self._news_sources]}")
            return

        logger.info(f"📰 Fetched {len(fetched)} total news items from all sources, filtering by relevance...")

        now = time.time()
        posted_count = 0
        max_posts_per_cycle = 3

        fresh_items = [item for item in fetched if self._is_news_recent(item, now)]
        if not fresh_items:
            logger.warning(
                "⚠️ News cycle had items, but none were recent enough (max age %sh)",
                self._news_max_age_hours,
            )
            return

        logger.info(
            "🕒 News freshness filter: %s/%s items within %sh window",
            len(fresh_items),
            len(fetched),
            self._news_max_age_hours,
        )

        fresh_items.sort(
            key=lambda x: (x.get('published_ts', 0), x.get('relevance_score', 0)),
            reverse=True,
        )

        for idx, item in enumerate(fresh_items, 1):
            if posted_count >= max_posts_per_cycle:
                logger.info(f"⏹️ Max posts per cycle ({max_posts_per_cycle}) reached")
                break
                
            news_id = item.get('news_id')
            if not news_id:
                logger.debug(f"News item #{idx} skipped: no news_id")
                continue

            # Check if already posted in last 24 hours
            if now - self._posted_news.get(news_id, 0) < 24 * 3600:
                logger.debug(f"News item #{idx} already posted within 24h: {item.get('headline', '')[:60]}")
                continue

            # Log attempt
            logger.info(f"📝 Attempting to post news #{idx}: score={item.get('relevance_score', 0)}, title={item.get('headline', '')[:60]}")
            
            if await self.broadcast_news(item):
                self._posted_news[news_id] = now
                posted_count += 1
                logger.info(f"✅ News posted #{posted_count}: {item.get('headline', '')[:60]}")
                
                # Save to database for persistence across Render restarts
                try:
                    from data.database import db
                    db.save_posted_news(
                        news_id, 
                        headline=item.get('headline', ''), 
                        source_name=item.get('source_name', '')
                    )
                except Exception as e:
                    logger.error(f"Failed to save posted news to database: {e}")
                
                await asyncio.sleep(1.0)
            else:
                logger.warning(f"⚠️ Failed to post news #{idx}")

        self._cleanup_posted_news()
        
        # Also clean old entries from database
        try:
            from data.database import db
            db.cleanup_old_posted_news(days=7)
        except Exception as e:
            logger.error(f"Failed to cleanup old news in database: {e}")
        
        logger.info(f"✅ News cycle complete: fetched={len(fetched)} posted={posted_count}")

    async def _fetch_and_post_launch_updates(self):
        """Fetch newly launched tokens and post qualified launch alerts."""
        logger.info("🆕 ====== Starting launch update cycle ======")
        launches = await self._fetch_new_launch_candidates(limit=self._launch_scan_limit)
        if not launches:
            logger.info("🆕 No new launch candidates found this cycle")
            self._cleanup_posted_launches()
            return
        self._register_dynamic_token_keywords(launches)

        now = time.time()
        posted = 0
        max_posts_per_cycle = 3

        launches.sort(key=lambda x: x.get('created_ts', 0), reverse=True)
        for item in launches:
            if posted >= max_posts_per_cycle:
                break

            launch_id = item.get('launch_id')
            if not launch_id:
                continue
            if now - self._posted_launches.get(launch_id, 0) < 24 * 3600:
                continue
            if float(item.get('liquidity_usd', 0) or 0) < self._launch_min_liquidity_usd:
                continue

            if await self.broadcast_token_launch(item):
                self._posted_launches[launch_id] = now
                posted += 1
                logger.info(
                    "✅ Launch posted #%s: %s (%s)",
                    posted,
                    item.get('symbol', '?'),
                    item.get('contract_address', 'n/a')[:12],
                )
                await asyncio.sleep(1.0)

        self._cleanup_posted_launches()
        logger.info("✅ Launch cycle complete: candidates=%s posted=%s", len(launches), posted)

    async def _fetch_from_sources(self, sources: List[Dict]) -> List[Dict]:
        """Fetch and parse RSS/Atom news from configured sources."""
        news_items: List[Dict] = []
        seen_ids: Set[str] = set()
        headers = {'User-Agent': 'KopytraderBot/1.0 (+https://t.me/Kopytraderbot)'}

        async with aiohttp.ClientSession(headers=headers) as session:
            for source in sources:
                source_url = source.get('url')
                source_name = source.get('name', 'Unknown Source')
                source_weight = float(source.get('weight', 1.0))
                source_type = str(source.get('type', 'media') or 'media')
                if not source_url:
                    logger.warning(f"⚠️ Source {source_name} has no URL, skipping")
                    continue

                try:
                    logger.debug(f"📰 Fetching from {source_name}: {source_url}")
                    async with session.get(source_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            logger.warning(f"⚠️ Failed to fetch from {source_name}: HTTP {resp.status}")
                            continue
                        body = await resp.text()
                        logger.debug(f"📄 {source_name}: Received {len(body)} bytes")
                        parsed = self._parse_feed_items(body, source_name, source_weight, source_type)
                        logger.info(f"✅ {source_name}: parsed {len(parsed)} items")
                        for item in parsed:
                            news_id = item.get('news_id')
                            if news_id and news_id not in seen_ids:
                                seen_ids.add(news_id)
                                news_items.append(item)
                except asyncio.TimeoutError:
                    logger.error(f"⏱️ Timeout fetching news from {source_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to fetch news from {source_url}: {e}")

        logger.info(f"📊 Total unique news items collected: {len(news_items)}")
        return news_items

    async def _fetch_sol_market_data(self, session: aiohttp.ClientSession) -> Optional[Dict]:
        """Fetch SOL metrics from DexScreener using WSOL token."""
        try:
            url = "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning(f"⚠️ SOL market fetch failed: HTTP {resp.status}")
                    return None
                payload = await resp.json(content_type=None)
        except Exception as e:
            logger.warning(f"⚠️ SOL market fetch error: {e}")
            return None

        pairs = payload.get('pairs') or []
        pair = self._pick_best_solana_pair(pairs)
        if not pair:
            return None

        return {
            'price_usd': self._to_float(pair.get('priceUsd')),
            'change_1h': self._to_float((pair.get('priceChange') or {}).get('h1')),
            'change_24h': self._to_float((pair.get('priceChange') or {}).get('h24')),
            'volume_24h': self._to_float((pair.get('volume') or {}).get('h24')),
            'liquidity_usd': self._to_float((pair.get('liquidity') or {}).get('usd')),
        }

    async def _fetch_top_token_performance(
        self,
        session: aiohttp.ClientSession,
        limit: int = 5,
    ) -> List[Dict]:
        """Fetch top performing Solana tokens by 24h % change from DexScreener boosted feed."""
        addresses: List[str] = []
        try:
            boosted_url = "https://api.dexscreener.com/token-boosts/top/v1"
            async with session.get(boosted_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    payload = await resp.json(content_type=None)
                    if isinstance(payload, list):
                        for item in payload:
                            if str(item.get('chainId', '')).lower() != 'solana':
                                continue
                            token_address = str(item.get('tokenAddress', '')).strip()
                            if token_address and token_address not in addresses:
                                addresses.append(token_address)
                            if len(addresses) >= max(12, limit * 3):
                                break
        except Exception as e:
            logger.warning(f"⚠️ Top token boosted fetch failed: {e}")

        if not addresses:
            return []

        tasks = [
            self._fetch_token_pair_snapshot(session, address)
            for address in addresses
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        tokens: List[Dict] = []
        for r in results:
            if isinstance(r, Exception) or not r:
                continue
            if r['liquidity_usd'] < self._min_top_token_liquidity:
                continue
            tokens.append(r)

        tokens.sort(key=lambda t: t.get('change_24h', -99999), reverse=True)
        return tokens[:max(1, limit)]

    async def _fetch_new_launch_candidates(self, limit: int = 15) -> List[Dict]:
        """Fetch newly created Solana tokens from DexScreener profiles and enrich with pair stats."""
        addresses: List[str] = []
        headers = {'User-Agent': 'KopytraderBot/1.0 (+https://t.me/Kopytraderbot)'}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                profiles_url = "https://api.dexscreener.com/token-profiles/latest/v1"
                async with session.get(profiles_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning(f"⚠️ Launch profiles fetch failed: HTTP {resp.status}")
                        return []
                    payload = await resp.json(content_type=None)

                if not isinstance(payload, list):
                    return []

                for entry in payload:
                    if str(entry.get('chainId', '')).lower() != 'solana':
                        continue
                    addr = str(entry.get('tokenAddress', '')).strip()
                    if addr and addr not in addresses:
                        addresses.append(addr)
                    if len(addresses) >= max(limit * 2, 10):
                        break

                tasks = [self._fetch_token_launch_snapshot(session, address) for address in addresses]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                items = []
                for r in results:
                    if isinstance(r, Exception) or not r:
                        continue
                    age_minutes = float(r.get('age_minutes', 999999) or 999999)
                    if age_minutes > self._launch_max_age_minutes:
                        continue
                    items.append(r)
                return items
        except Exception as e:
            logger.warning(f"⚠️ Launch candidate fetch error: {e}")
            return []

    async def _fetch_token_launch_snapshot(
        self,
        session: aiohttp.ClientSession,
        token_address: str,
    ) -> Optional[Dict]:
        """Fetch launch details for one token and normalize for token-launch broadcast."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json(content_type=None)
        except Exception:
            return None

        pair = self._pick_best_solana_pair(payload.get('pairs') or [])
        if not pair:
            return None

        created_ts = self._pair_created_ts(pair)
        if created_ts <= 0:
            return None

        now = time.time()
        age_minutes = max(0.0, (now - created_ts) / 60.0)
        base = pair.get('baseToken') or {}
        liquidity = self._to_float((pair.get('liquidity') or {}).get('usd'))
        dex_id = str(pair.get('dexId', '')).lower()
        platform = 'pump.fun' if 'pump' in dex_id else (pair.get('dexId') or 'DEX')
        risk_label = self._classify_launch_risk(liquidity)

        return {
            'launch_id': hashlib.md5(f"launch|{token_address}".encode('utf-8')).hexdigest(),
            'token_name': base.get('name') or 'Unknown Token',
            'symbol': base.get('symbol') or '?',
            'contract_address': token_address,
            'platform': platform,
            'liquidity_usd': liquidity,
            'age_minutes': age_minutes,
            'risk_label': risk_label,
            'dexscreener_url': pair.get('url') or f"https://dexscreener.com/solana/{token_address}",
            'created_ts': created_ts,
        }

    async def _fetch_token_pair_snapshot(
        self,
        session: aiohttp.ClientSession,
        token_address: str,
    ) -> Optional[Dict]:
        """Fetch token metrics from DexScreener and select the best Solana pair."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json(content_type=None)
        except Exception:
            return None

        pair = self._pick_best_solana_pair(payload.get('pairs') or [])
        if not pair:
            return None

        base = pair.get('baseToken') or {}
        change_24h = self._to_float((pair.get('priceChange') or {}).get('h24'))
        return {
            'address': token_address,
            'symbol': base.get('symbol') or '?',
            'name': base.get('name') or 'Unknown',
            'change_24h': change_24h,
            'volume_24h': self._to_float((pair.get('volume') or {}).get('h24')),
            'liquidity_usd': self._to_float((pair.get('liquidity') or {}).get('usd')),
        }

    def _pick_best_solana_pair(self, pairs: List[Dict]) -> Optional[Dict]:
        """Pick the highest-liquidity Solana pair with sane metrics."""
        sol_pairs = [p for p in pairs if str(p.get('chainId', '')).lower() == 'solana']
        if not sol_pairs:
            return None

        def pair_key(p: Dict) -> float:
            return self._to_float((p.get('liquidity') or {}).get('usd'))

        sol_pairs.sort(key=pair_key, reverse=True)
        for pair in sol_pairs:
            if pair_key(pair) > 0:
                return pair
        return sol_pairs[0]

    def _pair_created_ts(self, pair: Dict) -> float:
        """Return pair creation timestamp in seconds."""
        raw = pair.get('pairCreatedAt')
        value = self._to_float(raw)
        if value <= 0:
            return 0.0
        # DexScreener commonly returns milliseconds.
        if value > 10_000_000_000:
            return value / 1000.0
        return value

    def _classify_launch_risk(self, liquidity_usd: float) -> str:
        """Simple risk label for launch alerts."""
        if liquidity_usd < 5000:
            return 'RUG_RISK'
        if liquidity_usd >= 50000:
            return 'OKAY'
        return 'DEGEN'

    def _to_float(self, value: object) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _fmt_pct(self, value: object) -> str:
        number = self._to_float(value)
        sign = "+" if number >= 0 else ""
        return f"{sign}{number:.2f}%"

    def _is_news_recent(self, item: Dict, now_ts: float) -> bool:
        """Accept only news with a valid timestamp inside freshness window."""
        published_ts = self._to_float(item.get('published_ts'))
        if published_ts <= 0:
            return False
        max_age_seconds = max(1, self._news_max_age_hours) * 3600
        if published_ts > now_ts + 6 * 3600:
            return False
        return (now_ts - published_ts) <= max_age_seconds

    def _cleanup_posted_news(self):
        """Remove stale posted-news ids (48h window)."""
        cutoff = time.time() - (48 * 3600)
        self._posted_news = {
            news_id: ts for news_id, ts in self._posted_news.items()
            if ts >= cutoff
        }

    def _cleanup_posted_launches(self):
        """Remove stale launch ids (48h window)."""
        cutoff = time.time() - (48 * 3600)
        self._posted_launches = {
            launch_id: ts for launch_id, ts in self._posted_launches.items()
            if ts >= cutoff
        }

    def _parse_feed_items(
        self,
        xml_text: str,
        source_name: str,
        source_weight: float,
        source_type: str = 'media',
    ) -> List[Dict]:
        """Parse RSS/Atom XML into normalized news items."""
        items: List[Dict] = []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return items

        for entry in root.findall('.//channel/item')[:25]:
            title = self._clean_text(self._xml_text(entry.find('title')))
            link = self._clean_link(self._xml_text(entry.find('link')))
            summary = self._clean_text(
                self._xml_text(entry.find('description')) or self._xml_text(entry.find('content'))
            )
            published = self._xml_text(entry.find('pubDate'))
            normalized = self._build_news_item(
                title, summary, link, source_name, source_weight, published, source_type
            )
            if normalized:
                items.append(normalized)

        atom_ns = '{http://www.w3.org/2005/Atom}'
        for entry in root.findall(f'.//{atom_ns}entry')[:25]:
            title = self._clean_text(self._xml_text(entry.find(f'{atom_ns}title')))
            summary = self._clean_text(
                self._xml_text(entry.find(f'{atom_ns}summary'))
                or self._xml_text(entry.find(f'{atom_ns}content'))
            )
            link_node = entry.find(f'{atom_ns}link')
            link = self._clean_link(link_node.attrib.get('href', '')) if link_node is not None else ''
            published = (
                self._xml_text(entry.find(f'{atom_ns}updated'))
                or self._xml_text(entry.find(f'{atom_ns}published'))
            )
            normalized = self._build_news_item(
                title, summary, link, source_name, source_weight, published, source_type
            )
            if normalized:
                items.append(normalized)

        return items

    def _build_news_item(
        self,
        title: str,
        summary: str,
        link: str,
        source_name: str,
        source_weight: float,
        published_raw: str,
        source_type: str = 'media',
    ) -> Optional[Dict]:
        if not title or not link:
            return None

        relevance = self._score_news_relevance(title, summary, source_weight)
        if relevance < self._min_news_relevance:
            return None

        news_id = hashlib.md5(f"{title}|{link}".encode('utf-8', errors='ignore')).hexdigest()
        return {
            'headline': title[:220],
            'summary': summary[:500],
            'source_link': link,
            'source_name': source_name,
            'source_type': source_type,
            'relevance_score': relevance,
            'published_at': published_raw or '',
            'published_ts': self._parse_timestamp(published_raw),
            'news_id': news_id,
        }

    def _load_extra_sources_from_env(self) -> List[Dict]:
        """
        Load extra news sources from env:
        - BROADCAST_EXTRA_NEWS_SOURCES
        - BROADCAST_SOCIAL_NEWS_SOURCES
        Format:
            "Name|https://url|1.1;Another Source|https://url2|1.0"
        """
        sources: List[Dict] = []
        sources.extend(self._parse_source_list_env('BROADCAST_EXTRA_NEWS_SOURCES', default_type='media'))
        sources.extend(self._parse_source_list_env('BROADCAST_SOCIAL_NEWS_SOURCES', default_type='social'))
        return sources

    def _parse_source_list_env(self, env_key: str, default_type: str) -> List[Dict]:
        raw = os.getenv(env_key, '').strip()
        if not raw:
            return []

        parsed_sources: List[Dict] = []
        chunks = [c.strip() for c in raw.split(';') if c.strip()]
        for chunk in chunks:
            parts = [p.strip() for p in chunk.split('|')]
            if len(parts) < 2:
                logger.warning("⚠️ Invalid source format in %s: %s", env_key, chunk)
                continue

            name = parts[0]
            url = parts[1]
            weight = 1.0
            if len(parts) >= 3:
                try:
                    weight = float(parts[2])
                except ValueError:
                    logger.warning("⚠️ Invalid weight in %s source %s; defaulting to 1.0", env_key, name)
                    weight = 1.0

            clean_url = self._clean_link(url)
            if not clean_url:
                logger.warning("⚠️ Invalid URL in %s source %s: %s", env_key, name, url)
                continue

            parsed_sources.append({
                'name': name,
                'url': clean_url,
                'weight': weight,
                'type': default_type,
            })
        logger.info("📰 Loaded %s extra %s news source(s) from %s", len(parsed_sources), default_type, env_key)
        return parsed_sources

    def _score_news_relevance(self, title: str, summary: str, source_weight: float = 1.0) -> float:
        """Simple Solana relevance scoring for news ranking."""
        text = f"{title} {summary}".lower()
        score = 0.0

        if any(k in text for k in ['solana', 'sol ', '$sol', 'sol/', 'spl', 'on solana', 'solana-based', 'sol ecosystem']):
            score += 45

        ecosystem_keywords = [
            'jupiter', 'raydium', 'phantom', 'drift', 'helius', 'pyth',
            'jito', 'firedancer', 'validator', 'rpc', 'pump.fun', 'meteora'
        ]
        score += 8 * sum(1 for k in ecosystem_keywords if k in text)

        high_impact = [
            'sec', 'etf', 'lawsuit', 'settlement', 'hack', 'exploit', 'outage',
            'mainnet', 'upgrade', 'partnership', 'funding', 'integration'
        ]
        score += 6 * sum(1 for k in high_impact if k in text)

        if len(summary) > 80:
            score += 5
        if 'opinion' in text or 'sponsored' in text:
            score -= 15

        token_hits = self._count_token_keyword_hits(text)
        if token_hits:
            score += min(36, token_hits * 9)
            if any(k in text for k in ['solana', 'on solana', 'spl', 'solana-based']):
                score += 10

        score += (source_weight - 1.0) * 20
        return max(0.0, min(100.0, round(score, 2)))

    def _count_token_keyword_hits(self, text: str) -> int:
        """Count unique Solana-token keyword hits in text."""
        keywords = self._all_token_keywords()
        hits = 0
        for kw in keywords:
            if not kw:
                continue
            # For symbols/words use a word boundary to avoid many false positives.
            if re.search(rf'\b{re.escape(kw)}\b', text):
                hits += 1
        return hits

    def _all_token_keywords(self) -> Set[str]:
        """Combined token keywords used for token-specific Solana news detection."""
        return self._static_sol_token_keywords | self._dynamic_token_keywords

    def _register_dynamic_token_keywords(self, token_items: List[Dict]):
        """Add symbols/names from live token feeds so their news qualifies as Solana updates."""
        if not token_items:
            return
        for token in token_items:
            symbol = str(token.get('symbol') or '').strip().lower()
            name = str(token.get('name') or token.get('token_name') or '').strip().lower()
            if symbol and 2 <= len(symbol) <= 12 and symbol.isascii():
                self._dynamic_token_keywords.add(symbol)
            if name:
                for word in re.findall(r'[a-z0-9]{3,20}', name):
                    self._dynamic_token_keywords.add(word)

        if len(self._dynamic_token_keywords) > self._max_token_keywords:
            sorted_keys = sorted(self._dynamic_token_keywords)
            self._dynamic_token_keywords = set(sorted_keys[-self._max_token_keywords:])

    def _parse_timestamp(self, value: str) -> float:
        if not value:
            return 0.0
        try:
            dt = parsedate_to_datetime(value)
            return dt.timestamp() if dt else 0.0
        except Exception:
            pass
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except Exception:
            return 0.0

    def _xml_text(self, node) -> str:
        if node is None:
            return ''
        return (node.text or '').strip()

    def _clean_link(self, link: str) -> str:
        link = (link or '').strip()
        parsed = urlparse(link)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            return link
        return ''

    def _clean_text(self, value: str) -> str:
        text = html.unescape(value or '')
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\\s+', ' ', text).strip()
        return text


# Singleton instance
broadcaster = TelegramBroadcaster()

