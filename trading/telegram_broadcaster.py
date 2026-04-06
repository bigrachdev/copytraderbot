"""
Telegram Broadcasting Module
Posts trade signals, whale alerts, token launches, news, and self-ads to Telegram channel.

Intervals (configurable via .env):
- Trade signals/whale alerts/token launches: on-demand (rate-limited)
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
        self.channel_id = TELEGRAM_CHANNEL_ID
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
        import os
        self._min_liquidity_usd = int(os.getenv('BROADCAST_MIN_LIQUIDITY_USD', '30000'))
        self._min_news_relevance = float(os.getenv('BROADCAST_MIN_NEWS_RELEVANCE', '60'))
        
        logger.info(f"📊 Broadcast thresholds: liquidity=${self._min_liquidity_usd:,}, news relevance={self._min_news_relevance}")
        
        self._news_sources: List[Dict] = [
            {
                'name': 'Solana Foundation',
                'url': 'https://solana.com/news/rss.xml',
                'weight': 1.30,
            },
            {
                'name': 'CoinDesk',
                'url': 'https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml',
                'weight': 1.15,
            },
            {
                'name': 'The Block',
                'url': 'https://www.theblock.co/rss.xml',
                'weight': 1.15,
            },
            {
                'name': 'Blockworks',
                'url': 'https://blockworks.co/feed',
                'weight': 1.10,
            },
            {
                'name': 'Decrypt',
                'url': 'https://decrypt.co/feed',
                'weight': 1.05,
            },
        ]

    async def initialize(self):
        """Initialize the bot and start background tasks."""
        if not self.bot_token or not self.channel_id:
            logger.warning("⚠️ Telegram broadcast not configured: BOT_TOKEN=%s, CHANNEL_ID=%s", 
                          'SET' if self.bot_token else 'MISSING', 
                          self.channel_id or 'MISSING')
            return

        try:
            self.bot = Bot(token=self.bot_token)
            # Verify bot can access channel
            chat = await self.bot.get_chat(self.channel_id)
            logger.info(f"✅ Telegram broadcaster initialized for channel: {chat.title} (ID: {self.channel_id})")

            # Post self-ad on startup
            logger.info("📢 Posting self-advertisement on startup...")
            await self.post_self_ad()

            # Start background loops
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

        wallet = signal_data['wallet_address']
        wallet_short = f"{wallet[:4]}...{wallet[-4:]}" if len(wallet) > 8 else wallet

        message = f"""
<b>🚨 TRADE SIGNAL</b>

<b>Token:</b> <code>{signal_data['token_name']}</code>
<b>CA:</b> <code>{signal_data['token_address']}</code>

<b>Action:</b> {signal_data['action']}
<b>Size:</b> {signal_data.get('size_sol', 0):.4f} SOL (${signal_data.get('size_usd', 0):.2f})

<b>Source Wallet:</b> <code>{wallet_short}</code>

<b>Entry Price:</b> ${signal_data['entry_price']:.8f}
<b>DexScreener:</b> {signal_data.get('dexscreener_url', 'N/A')}

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

        wallet = alert_data['wallet_label'] or (
            f"{alert_data['wallet_address'][:4]}...{alert_data['wallet_address'][-4:]}"
        )

        solscan_url = f"https://solscan.io/tx/{alert_data['tx_hash']}"

        message = f"""
<b>🐋 WHALE ALERT</b>

<b>Wallet:</b> <code>{wallet}</code>

<b>Action:</b> {alert_data['action']}
<b>Token:</b> <code>{alert_data['token_name']}</code>
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
        risk = launch_data.get('risk_label', 'DEGEN')
        emoji = risk_emoji.get(risk, '⚠️')

        message = f"""
<b>🎉 TOKEN LAUNCH ALERT</b>

<b>Token:</b> {launch_data['token_name']} (${launch_data['symbol']})
<b>CA:</b> <code>{launch_data['contract_address']}</code>

<b>Platform:</b> {launch_data['platform']}
<b>Starting Liquidity:</b> ${launch_data['liquidity_usd']:,.2f}
<b>Pool Age:</b> {launch_data['age_minutes']:.0f} minutes

<b>Risk Label:</b> {emoji} {risk.replace('_', ' ')}

<b>DexScreener:</b> {launch_data.get('dexscreener_url', 'N/A')}

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

        message = f"""
<b>SOLANA NEWS & ALPHA</b>

<b>{html.escape(headline)}</b>

{html.escape(summary)}

Source: <a href="{source_link}">{html.escape(source_name)}</a>

--------------------
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

        fetched.sort(
            key=lambda x: (x.get('relevance_score', 0), x.get('published_ts', 0)),
            reverse=True,
        )

        for idx, item in enumerate(fetched, 1):
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
                        parsed = self._parse_feed_items(body, source_name, source_weight)
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

    def _cleanup_posted_news(self):
        """Remove stale posted-news ids (48h window)."""
        cutoff = time.time() - (48 * 3600)
        self._posted_news = {
            news_id: ts for news_id, ts in self._posted_news.items()
            if ts >= cutoff
        }

    def _parse_feed_items(self, xml_text: str, source_name: str, source_weight: float) -> List[Dict]:
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
                title, summary, link, source_name, source_weight, published
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
                title, summary, link, source_name, source_weight, published
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
    ) -> Optional[Dict]:
        if not title or not link:
            return None

        relevance = self._score_news_relevance(title, summary, source_weight)
        if relevance < 60:
            return None

        news_id = hashlib.md5(f"{title}|{link}".encode('utf-8', errors='ignore')).hexdigest()
        return {
            'headline': title[:220],
            'summary': summary[:500],
            'source_link': link,
            'source_name': source_name,
            'relevance_score': relevance,
            'published_at': published_raw or '',
            'published_ts': self._parse_timestamp(published_raw),
            'news_id': news_id,
        }

    def _score_news_relevance(self, title: str, summary: str, source_weight: float = 1.0) -> float:
        """Simple Solana relevance scoring for news ranking."""
        text = f"{title} {summary}".lower()
        score = 0.0

        if any(k in text for k in ['solana', 'sol ', '$sol', 'sol/']):
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

        score += (source_weight - 1.0) * 20
        return max(0.0, min(100.0, round(score, 2)))

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

