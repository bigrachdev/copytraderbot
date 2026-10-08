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
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

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
            logger.info("✅ Telegram broadcaster initialized — whale-trade signals only")

        except TelegramError as e:
            logger.error("Failed to initialize Telegram broadcaster: %s", e)

    # =========================================================================
    # Background loop
    # =========================================================================

    async def _heartbeat_loop(self):
        """Post a brief alive-ping to the channel every 5 minutes."""
        while True:
            try:
                await asyncio.sleep(300)
                if self.bot:
                    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
                    await self._send_message(f"<i>🤖 Bot active — {now}</i>")
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
        mint_short = f"{token_mint[:6]}...{token_mint[-4:]}"
        action_label = "BUY 🟢" if action.upper() == "BUY" else "SELL 🔴"
        sol_usd = sol_amount * price_usd if price_usd > 0 else 0
        action_verb = "buying" if action.upper() == "BUY" else "selling"

        message = (
            "<b>🐋 Whale Alert</b>\n\n"
            f"<b>Action:</b> {action_label}\n"
            f"<b>Token:</b> <code>${self._safe_text(token_symbol)}</code> "
            f"(<code>{self._safe_text(mint_short)}</code>)\n"
            f"<b>Whale:</b> <code>{self._safe_text(whale_short)}</code>\n"
            f"<b>Score:</b> {whale_score:.1f} | "
            f"<b>Win Rate:</b> {win_rate * 100:.0f}% | "
            f"<b>Trades:</b> {total_trades}\n\n"
            f"<b>Amount:</b> {sol_amount:.2f} SOL"
            + (f" (~${sol_usd:,.0f})" if sol_usd > 0 else "")
            + "\n"
            f"<b>Price:</b> ${price_usd:.6f}\n\n"
            f"{strength_emoji} Signal strength: <b>{strength}</b> "
            f"({signal_count} whale{'s' if signal_count > 1 else ''} {action_verb})\n"
            "📊 Copying automatically"
        )

        return await self._send_message(message)

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

    async def _send_message(self, message: str) -> bool:
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
