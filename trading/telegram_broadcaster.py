"""
Telegram Broadcasting Module — Whale-Trade Alerts Only
Posts whale trade signals and copy-trade confirmations to the Telegram channel.

Rate limits:
- max 1 signal per 2 minutes
- max 10 signals per hour
- 15-minute dedup window per (token+action)
- 5-minute heartbeat to keep channel alive
"""
import logging
import asyncio
import time
import hashlib
import re
import html
import os
from typing import Dict, Optional, List
from datetime import datetime, timedelta, timezone
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_BOT_USERNAME,
    CONVERGENCE_DEDUP_HOURS,
    LEADERBOARD_DAY_OF_WEEK, LEADERBOARD_HOUR_UTC, LEADERBOARD_MIN_WHALES,
)

logger = logging.getLogger(__name__)


class TelegramBroadcaster:
    """Broadcast whale trade signals and copy-trade confirmations to a Telegram channel."""

    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.channel_id = self._normalize_channel_id(TELEGRAM_CHANNEL_ID)
        self.bot: Optional[Bot] = None
        self._last_post_times: Dict[str, float] = {}  # type -> timestamp

        # Signal rate-limiting state
        self._max_signals_per_hour = 10
        self._max_signals_last_hour: List[float] = []  # timestamps

        # Signal dedup (persisted via DB across restarts)
        try:
            from data.database import db
            self._posted_signals = {
                sig_hash: time.time()
                for sig_hash in db.get_posted_signal_hashes(hours=24)
            }
            logger.info(
                "📊 Loaded %s posted signal hashes from database",
                len(self._posted_signals),
            )
        except Exception as e:
            logger.warning("Failed to load posted signals from database: %s", e)
            self._posted_signals: Dict[str, float] = {}

        self._min_liquidity_usd = int(os.getenv('BROADCAST_MIN_LIQUIDITY_USD', '30000'))

        # Convergence alert dedup: { token_mint: timestamp }
        self._posted_convergence: Dict[str, float] = {}

    # =========================================================================
    # Initialisation
    # =========================================================================

    @staticmethod
    def _normalize_channel_id(raw_channel_id: Optional[str]) -> Optional[str]:
        """Strip inline comments and whitespace from TELEGRAM_CHANNEL_ID."""
        if not raw_channel_id:
            return raw_channel_id
        if isinstance(raw_channel_id, str):
            value = raw_channel_id.split('#', 1)[0].strip()
            return value
        return str(raw_channel_id)

    @staticmethod
    def _is_placeholder_channel_id(channel_id: Optional[str]) -> bool:
        if not channel_id:
            return True
        cid = str(channel_id).strip().lower()
        return (
            'xxxx' in cid
            or 'your_channel_id' in cid
            or cid in ('-100xxxxxxxxxx', 'channel_id_here')
        )

    async def initialize(self):
        """Initialize the bot and start the heartbeat loop."""
        if not self.bot_token or not self.channel_id:
            logger.warning(
                "⚠️ Telegram broadcast not configured: BOT_TOKEN=%s, CHANNEL_ID=%s",
                'SET' if self.bot_token else 'MISSING',
                self.channel_id or 'MISSING',
            )
            return
        if self._is_placeholder_channel_id(self.channel_id):
            logger.error(
                "❌ TELEGRAM_CHANNEL_ID appears to be a placeholder (%s). "
                "Set a real channel id like -1001234567890.",
                self.channel_id,
            )
            return

        try:
            self.bot = Bot(token=self.bot_token)
            chat = await self.bot.get_chat(self.channel_id)
            logger.info(
                "✅ Telegram broadcaster initialized for channel: %s (ID: %s)",
                chat.title,
                self.channel_id,
            )

            # Single background task: 5-minute alive ping
            asyncio.create_task(self._heartbeat_loop())
            asyncio.create_task(self.run_weekly_leaderboard_loop())
            logger.info("✅ Telegram broadcaster initialized — whale-trade signals only")

        except TelegramError as e:
            logger.error("Failed to initialize Telegram broadcaster: %s", e)

    # =========================================================================
    # Background loop
    # =========================================================================

    async def _heartbeat_loop(self):
        """Log-only heartbeat every 5 minutes — no public channel spam."""
        while True:
            try:
                await asyncio.sleep(300)
                now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
                logger.debug("💓 Broadcaster alive — %s", now)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat error: %s", e)
                await asyncio.sleep(60)

    # =========================================================================
    # Public API
    # =========================================================================

    async def post_whale_trade(
        self,
        whale_address: str,
        token_symbol: str,
        token_mint: str,
        action: str,           # 'BUY' or 'SELL'
        sol_amount: float,
        price_usd: float,
        whale_score: float,
        win_rate: float,       # 0.0–1.0
        total_trades: int,
        signal_count: int = 1,
        avg_profit: float = 0.0,
    ) -> bool:
        """Post a whale trade alert to the channel with copy-trade summary."""
        if not self.bot:
            return False

        now = time.time()
        signal_key = f"{token_mint}_{action}"
        signal_hash = hashlib.md5(signal_key.encode()).hexdigest()

        # Dedup: same (token+action) within 15 minutes
        last_seen = self._posted_signals.get(signal_hash, 0)
        if now - last_seen < 15 * 60:
            logger.debug("Duplicate whale signal skipped (15m window): %s", signal_key)
            return False

        # Rate: max 1 signal per 2 minutes
        if now - self._last_post_times.get('signal', 0) < 120:
            logger.debug("Rate limit: max 1 whale signal per 2 minutes")
            return False

        # Rate: max 10 signals per hour
        hour_ago = now - 3600
        self._max_signals_last_hour = [t for t in self._max_signals_last_hour if t > hour_ago]
        if len(self._max_signals_last_hour) >= self._max_signals_per_hour:
            logger.debug("Rate limit: max 10 signals per hour reached")
            return False

        # Commit rate-limit state
        self._last_post_times['signal'] = now
        self._max_signals_last_hour.append(now)
        self._posted_signals[signal_hash] = now

        # Persist to DB for cross-restart dedup
        try:
            from data.database import db
            db.save_posted_signal(signal_hash, token_address=token_mint, action=action)
        except Exception as e:
            logger.warning("Could not persist posted signal to DB: %s", e)

        self._cleanup_posted_signals()

        # Determine signal strength label
        if signal_count >= 3:
            strength = "STRONG"
            strength_emoji = "🔥"
        elif signal_count >= 2:
            strength = "MEDIUM"
            strength_emoji = "⚡"
        else:
            strength = "NORMAL"
            strength_emoji = "📡"

        whale_short = f"{whale_address[:6]}...{whale_address[-4:]}"
        action_label = "BUY 🟢" if action.upper() == "BUY" else "SELL 🔴"
        usd_value = sol_amount * price_usd if price_usd > 0 else 0
        bot_username = TELEGRAM_BOT_USERNAME.lstrip('@')

        # Safe base58 mint for use in URLs (only alphanumeric, no HTML-special chars)
        safe_mint = re.sub(r'[^1-9A-HJ-NP-Za-km-z]', '', token_mint)

        signal_line = (
            f"{strength_emoji} <b>{signal_count} whale{'s' if signal_count > 1 else ''} "
            f"{'buying' if action.upper() == 'BUY' else 'selling'} — {strength} signal</b>"
        )

        avg_profit_line = (
            f"   💵 Avg Profit: +{avg_profit:.0f}%" if avg_profit > 0 else None
        )

        cta_line = (
            f"👉 Start copying: @{self._safe_text(bot_username)}"
            if bot_username else None
        )

        lines = [
            f"🐋 <b>WHALE ALERT — {self._safe_text(action_label)}</b>",
            "─────────────────────",
            f"🪙 Token: <b>${self._safe_text(token_symbol)}</b>  •  "
            f"<a href='https://dexscreener.com/solana/{safe_mint}'>Chart</a>  •  "
            f"<a href='https://solscan.io/token/{safe_mint}'>Solscan</a>",
            f"💰 Size: <b>{sol_amount:.2f} SOL</b>"
            + (f"  (~${usd_value:,.0f})" if usd_value > 0 else ""),
            "",
            f"🐳 Whale <code>{self._safe_text(whale_short)}</code>",
            f"   📈 Win Rate: {int(win_rate * 100)}%  |  {total_trades} trades",
            avg_profit_line,
            f"   ⭐ Score: {whale_score:.1f}/100",
            "",
            signal_line,
            "",
            "📊 Bot is copying this trade automatically.",
            cta_line,
        ]

        message = "\n".join(line for line in lines if line is not None)

        # Inline button — deep link to copy this specific whale in the bot
        reply_markup = None
        if bot_username:
            deep_link = f"https://t.me/{bot_username}?start=copywhale_{whale_address}"
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🐋 Copy This Whale", url=deep_link)
            ]])

        return await self._send_message(message, reply_markup=reply_markup)

    async def post_whale_exit(
        self,
        whale_address: str,
        token_symbol: str,
        token_mint: str,
        sol_amount: float,
        price_usd: float,
        profit_pct: Optional[float],
        hold_duration_hours: float,
        whale_score: float,
        win_rate: float,
        total_trades: int,
        avg_profit: float = 0.0,
    ) -> bool:
        """Post a whale exit alert — smart money is moving out."""
        if not self.bot:
            return False

        now = time.time()
        signal_key  = f"{token_mint}_EXIT"
        signal_hash = hashlib.md5(signal_key.encode()).hexdigest()

        # Dedup: same exit within 15 minutes
        if now - self._posted_signals.get(signal_hash, 0) < 15 * 60:
            logger.debug("Duplicate whale exit signal skipped: %s", signal_key)
            return False

        # Per-type cooldown: max 1 signal per 2 minutes
        if now - self._last_post_times.get('signal', 0) < 120:
            logger.debug("Rate limit: max 1 signal per 2 minutes (whale exit)")
            return False

        # Hourly cap: max 10 signals per hour
        hour_ago = now - 3600
        self._max_signals_last_hour = [t for t in self._max_signals_last_hour if t > hour_ago]
        if len(self._max_signals_last_hour) >= self._max_signals_per_hour:
            logger.debug("Rate limit: max 10 signals per hour reached (whale exit)")
            return False

        # Commit rate-limit state
        self._last_post_times['signal'] = now
        self._max_signals_last_hour.append(now)
        self._posted_signals[signal_hash] = now

        try:
            from data.database import db
            db.save_posted_signal(signal_hash, token_address=token_mint, action='EXIT')
        except Exception as e:
            logger.warning("Could not persist whale exit signal: %s", e)

        self._cleanup_posted_signals()

        if hold_duration_hours >= 48:
            hold_str = f"{hold_duration_hours / 24:.1f} days"
        else:
            hold_str = f"{hold_duration_hours:.1f} hours"

        safe_mint   = re.sub(r'[^1-9A-HJ-NP-Za-km-z]', '', token_mint)
        whale_short = f"{whale_address[:6]}...{whale_address[-4:]}"
        usd_value   = sol_amount * price_usd if price_usd > 0 else 0
        bot_username = TELEGRAM_BOT_USERNAME.lstrip('@') if TELEGRAM_BOT_USERNAME else ''

        profit_line = None
        if profit_pct is not None:
            if profit_pct > 0:
                profit_line = f"💰 Estimated profit: <b>+{profit_pct:.1f}%</b> 🟢"
            elif profit_pct < 0:
                profit_line = f"🔻 Exited at loss: <b>{profit_pct:.1f}%</b> 🔴"

        lines = [
            "🚨 <b>WHALE EXIT ALERT</b>",
            "─────────────────────",
            f"🪙 Token: <b>${self._safe_text(token_symbol)}</b>  •  "
            f"<a href='https://dexscreener.com/solana/{safe_mint}'>Chart</a>  •  "
            f"<a href='https://solscan.io/token/{safe_mint}'>Solscan</a>",
            f"💸 Whale just sold: <b>{sol_amount:.2f} SOL</b>"
            + (f"  (~${usd_value:,.0f})" if usd_value > 0 else ""),
            "",
            f"⏱ Held for: <b>{hold_str}</b>",
            profit_line,
            "",
            f"🐳 Whale <code>{self._safe_text(whale_short)}</code>",
            f"   Win Rate: {int(win_rate * 100)}%  |  Score: {whale_score:.1f}/100"
            + (f"  |  Avg: +{avg_profit:.0f}%" if avg_profit > 0 else ""),
            "",
            "⚠️ Smart money is moving out.",
            "   This whale just took profits — token may have peaked.",
        ]

        message = "\n".join(line for line in lines if line is not None)

        reply_markup = None
        if bot_username:
            deep_link = f"https://t.me/{bot_username}?start=copywhale_{whale_address}"
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🐋 Copy This Whale", url=deep_link)
            ]])

        return await self._send_message(message, reply_markup=reply_markup)

    async def post_convergence_alert(
        self,
        token_symbol: str,
        token_mint: str,
        whale_count: int,
        total_sol: float,
        price_usd: float,
        whale_addresses: list,
        whale_scores: list,
        time_window_minutes: int,
    ) -> bool:
        """Post a multi-whale convergence alert — strongest buy signal."""
        if not self.bot:
            return False

        now = time.time()
        dedup_ttl = CONVERGENCE_DEDUP_HOURS * 3600

        # Convergence-specific dedup
        if now - self._posted_convergence.get(token_mint, 0) < dedup_ttl:
            logger.debug("Duplicate convergence alert skipped: %s", token_mint[:8])
            return False

        # Shared per-type cooldown (2 min)
        if now - self._last_post_times.get('signal', 0) < 120:
            logger.debug("Rate limit: max 1 signal per 2 minutes (convergence)")
            return False

        # Hourly cap
        hour_ago = now - 3600
        self._max_signals_last_hour = [t for t in self._max_signals_last_hour if t > hour_ago]
        if len(self._max_signals_last_hour) >= self._max_signals_per_hour:
            logger.debug("Rate limit: max 10 signals per hour reached (convergence)")
            return False

        # Commit rate-limit state
        self._last_post_times['signal'] = now
        self._max_signals_last_hour.append(now)
        self._posted_convergence[token_mint] = now

        safe_mint    = re.sub(r'[^1-9A-HJ-NP-Za-km-z]', '', token_mint)
        usd_value    = total_sol * price_usd if price_usd > 0 else 0
        bot_username = TELEGRAM_BOT_USERNAME.lstrip('@') if TELEGRAM_BOT_USERNAME else ''

        paired     = sorted(zip(whale_scores, whale_addresses), reverse=True)
        top_whales = paired[:3]
        extra_count = len(paired) - 3

        whale_lines = []
        for score, addr in top_whales:
            short = f"{addr[:6]}...{addr[-4:]}"
            whale_lines.append(f"  🐳 <code>{self._safe_text(short)}</code>  Score: {int(score)}")
        if extra_count > 0:
            whale_lines.append(f"  + {extra_count} more")

        lines = [
            "🔥 <b>MULTI-WHALE CONVERGENCE</b>",
            "─────────────────────",
            f"🪙 Token: <b>${self._safe_text(token_symbol)}</b>  •  "
            f"<a href='https://dexscreener.com/solana/{safe_mint}'>Chart</a>  •  "
            f"<a href='https://solscan.io/token/{safe_mint}'>Solscan</a>",
            "",
            f"<b>{whale_count} whales</b> bought ${self._safe_text(token_symbol)} "
            f"in the last {time_window_minutes} minutes",
            f"💰 Total size: <b>{total_sol:.2f} SOL</b>"
            + (f"  (~${usd_value:,.0f})" if usd_value > 0 else ""),
            "",
            "🏆 Top whales:",
        ] + whale_lines + [
            "",
            "⚡ This is the strongest buy signal we track.",
            "   Independent whales, same token, same window.",
        ]

        message = "\n".join(lines)

        reply_markup = None
        if bot_username and top_whales:
            top_addr  = top_whales[0][1]
            deep_link = f"https://t.me/{bot_username}?start=copywhale_{top_addr}"
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🐋 Copy This Whale", url=deep_link)
            ]])

        return await self._send_message(message, reply_markup=reply_markup)

    async def post_weekly_leaderboard(self) -> bool:
        """Post the weekly whale performance leaderboard. Not counted toward hourly cap."""
        if not self.bot:
            return False

        now = time.time()
        if now - self._last_post_times.get('leaderboard', 0) < 518400:
            logger.debug("Leaderboard cooldown active — skipping")
            return False

        try:
            from data.database import db
            whales = db.get_top_whales_by_pnl(days=7, limit=5)
        except Exception as e:
            logger.error("post_weekly_leaderboard: db error: %s", e)
            return False

        if len(whales) < LEADERBOARD_MIN_WHALES:
            logger.info(
                "post_weekly_leaderboard: only %d whales found, need %d — skipping",
                len(whales), LEADERBOARD_MIN_WHALES,
            )
            return False

        self._last_post_times['leaderboard'] = now

        medals       = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        bot_username = TELEGRAM_BOT_USERNAME.lstrip('@') if TELEGRAM_BOT_USERNAME else ''

        entry_lines = []
        for i, whale in enumerate(whales):
            medal  = medals[i] if i < len(medals) else "•"
            addr   = whale.get('watched_wallet', '')
            short  = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
            wins   = whale.get('wins', 0) or 0
            total  = whale.get('total_trades', 0) or 0
            losses = total - wins
            avg_p  = whale.get('avg_profit', 0) or 0
            best_p = whale.get('best_trade_pct', 0) or 0
            sign   = '+' if avg_p >= 0 else ''
            entry_lines.append(
                f"{medal} <code>{self._safe_text(short)}</code>\n"
                f"   {sign}{avg_p:.0f}% avg profit  |  {wins} wins / {losses} losses\n"
                f"   Best trade: +{best_p:.0f}%"
            )

        lines = [
            "🏆 <b>WEEKLY WHALE LEADERBOARD</b>",
            "─────────────────────",
            "<i>Top 5 whales by avg profit — last 7 days</i>",
            "",
        ] + entry_lines + [
            "",
            "Follow this channel to get every alert.",
        ]
        if bot_username:
            lines.append(f"👉 @{self._safe_text(bot_username)} to start copying")

        message = "\n".join(lines)
        success = await self._send_message(message)
        if not success:
            # Revert cooldown stamp so we can retry
            self._last_post_times['leaderboard'] = 0
        return success

    async def run_weekly_leaderboard_loop(self):
        """Background task: post the weekly leaderboard at the configured time."""
        while True:
            try:
                now        = datetime.now(timezone.utc)
                days_ahead = (LEADERBOARD_DAY_OF_WEEK - now.weekday()) % 7
                if days_ahead == 0 and (
                    now.hour > LEADERBOARD_HOUR_UTC
                    or (now.hour == LEADERBOARD_HOUR_UTC and now.minute >= 5)
                ):
                    days_ahead = 7
                next_post = now.replace(
                    hour=LEADERBOARD_HOUR_UTC, minute=0, second=0, microsecond=0
                )
                next_post    += timedelta(days=days_ahead)
                seconds_until = (next_post - now).total_seconds()
                logger.info(
                    "📅 Leaderboard loop: next post in %.1f hours", seconds_until / 3600
                )
                await asyncio.sleep(seconds_until)
                await self.post_weekly_leaderboard()
                # Sleep ~7 days minus 1 minute so we wake early and recalculate
                await asyncio.sleep(7 * 24 * 3600 - 60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Leaderboard loop error: %s", e)
                await asyncio.sleep(3600)

    async def broadcast_signal(self, signal_data: Dict) -> bool:
        """
        Broadcast a generic trade signal.

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
        logger.info(
            "📢 Attempting to broadcast signal: %s",
            signal_data.get('token_name', 'Unknown'),
        )

        if not self.bot:
            logger.warning("⚠️ Cannot broadcast signal: Telegram bot not initialized")
            return False

        if not await self._check_rate_limits(signal_data):
            logger.warning("⚠️ Signal blocked by rate limits or duplicate detection")
            return False

        liquidity = signal_data.get('liquidity_usd', 0)
        if liquidity < self._min_liquidity_usd:
            logger.warning(
                "⚠️ Signal skipped: Low liquidity $%.0f < $%s threshold",
                liquidity,
                self._min_liquidity_usd,
            )
            return False

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

        message = (
            "<b>🚨 TRADE SIGNAL</b>\n\n"
            f"<b>Token:</b> <code>{token_name}</code>\n"
            f"<b>CA:</b> <code>{token_address}</code>\n\n"
            f"<b>Action:</b> {action}\n"
            f"<b>Size:</b> {signal_data.get('size_sol', 0):.4f} SOL "
            f"(${signal_data.get('size_usd', 0):.2f})\n\n"
            f"<b>Source Wallet:</b> <code>{wallet_short}</code>\n\n"
            f"<b>Entry Price:</b> ${signal_data.get('entry_price', 0):.8f}\n"
            f"<b>DexScreener:</b> {dexscreener_url}\n\n"
            f"<b>Confidence:</b> {emoji} {confidence}"
        )

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

        wallet = str(
            alert_data.get('wallet_label')
            or (
                f"{alert_data.get('wallet_address', 'unknown')[:4]}"
                f"...{alert_data.get('wallet_address', 'unknown')[-4:]}"
            )
        )
        wallet = self._safe_text(wallet)
        action = self._safe_text(alert_data.get('action', 'BUY'))
        token_name = self._safe_text(alert_data.get('token_name', 'Unknown Token'))
        tx_hash = self._safe_text(alert_data.get('tx_hash', 'N/A'))
        solscan_url = f"https://solscan.io/tx/{tx_hash}"

        message = (
            "<b>🐋 WHALE ALERT</b>\n\n"
            f"<b>Wallet:</b> <code>{wallet}</code>\n\n"
            f"<b>Action:</b> {action}\n"
            f"<b>Token:</b> <code>{token_name}</code>\n"
            f"<b>Amount:</b> {alert_data.get('amount', 0):.4f}\n\n"
            f"<b>USD Value:</b> ${alert_data.get('usd_value', 0):,.2f}\n\n"
            f"<b>Tx:</b> {solscan_url}"
        )

        return await self._send_message(message)

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _send_message(self, message: str, reply_markup=None) -> bool:
        """Send message to Telegram channel."""
        if not self.bot or not self.channel_id:
            logger.warning(
                "⚠️ Cannot send message: bot=%s, channel_id=%s",
                'SET' if self.bot else 'NONE',
                self.channel_id or 'NONE',
            )
            return False

        try:
            max_chars = 3900
            if len(message) > max_chars:
                logger.warning(
                    "⚠️ Message truncated from %d to %d chars",
                    len(message),
                    max_chars,
                )
                message = message[: max_chars - 3] + "..."

            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=False,
                reply_markup=reply_markup,
            )
            logger.info("✅ Message sent successfully")
            return True

        except TelegramError as e:
            err = str(e)
            # Fallback: retry as plain text so signals are never dropped silently
            if 'parse entities' in err.lower() or "can't parse" in err.lower():
                logger.warning("⚠️ HTML parse failed, retrying plain text: %s", e)
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
                    logger.error("Fallback plain text send failed: %s", fallback_err)
            logger.error("Failed to send Telegram message: %s", e)
            return False
        except Exception as e:
            logger.error("Unexpected error sending message: %s", e)
            return False

    async def _check_rate_limits(self, signal_data: Dict) -> bool:
        """Check rate limits before posting a generic signal."""
        now = time.time()

        signal_key = f"{signal_data['token_address']}_{signal_data['action']}"
        signal_hash = hashlib.md5(signal_key.encode()).hexdigest()

        last_seen = self._posted_signals.get(signal_hash, 0)
        if now - last_seen < 15 * 60:
            logger.debug("Duplicate signal skipped within 15m window: %s", signal_key)
            return False

        if now - self._last_post_times.get('signal', 0) < 120:
            logger.debug("Rate limit: max 1 signal per 2 minutes")
            return False

        hour_ago = now - 3600
        self._max_signals_last_hour = [
            t for t in self._max_signals_last_hour if t > hour_ago
        ]
        if len(self._max_signals_last_hour) >= self._max_signals_per_hour:
            logger.debug("Rate limit: max 10 signals per hour reached")
            return False

        self._last_post_times['signal'] = now
        self._max_signals_last_hour.append(now)
        self._posted_signals[signal_hash] = now

        try:
            from data.database import db
            db.save_posted_signal(
                signal_hash,
                token_address=signal_data.get('token_address', ''),
                action=signal_data.get('action', ''),
            )
        except Exception as e:
            logger.error("Failed to save posted signal to database: %s", e)

        self._cleanup_posted_signals()
        return True

    def _cleanup_posted_signals(self):
        """Remove posted signal hashes older than 15 minutes."""
        cutoff = time.time() - (15 * 60)
        self._posted_signals = {
            sig_hash: ts
            for sig_hash, ts in self._posted_signals.items()
            if ts >= cutoff
        }

    @staticmethod
    def _safe_text(value: object) -> str:
        """Escape text values for HTML parse mode."""
        return html.escape(str(value or ''))

    def _to_float(self, value: object) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _clean_text(self, value: str) -> str:
        text = html.unescape(value or '')
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text


# Singleton instance
broadcaster = TelegramBroadcaster()
