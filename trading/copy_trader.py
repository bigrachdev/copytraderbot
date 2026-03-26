"""
Copy trading engine — Solana only.

Features:
  1. WebSocket real-time monitoring  (replaces 10-second polling — ~10-25s latency improvement)
  2. Priority fees                   (transactions land in the next block, not the queue)
  3. Whale qualification gate        (only copy wallets with proven track records)
  4. Token safety filter             (run token_analyzer before every copy trade)
  5. Signal aggregation              (multiple whales buying the same token = stronger signal)
  6. Portfolio-% based sizing        (match the whale's risk level, not just raw SOL amount)
  7. Trailing stop / partial exit    (protect profits, cut losses automatically)
  8. Copy latency tracking           (measure and log how fast we are vs the whale)
  9. pump.fun detection              (detects pump.fun launches, not just DEX swaps)
"""
import logging
import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import aiohttp

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "websockets not installed — falling back to HTTP polling. "
        "Run: pip install websockets"
    )

from config import (
    SOLANA_RPC_URL, SOLANA_WSS_URL,
    COPY_TRADE_CHECK_INTERVAL, MIN_TRADE_AMOUNT,
    DEFAULT_COPY_SCALE, WSOL_MINT,
    WHALE_MIN_TRADES, WHALE_MIN_WIN_RATE, WHALE_MIN_AVG_PROFIT,
    COPY_SIGNAL_WINDOW_SECONDS, COPY_LOSS_CHECK_WINDOW,
    COPY_DEFAULT_PROFIT_TARGET, COPY_DEFAULT_TRAILING_STOP,
    COPY_DEFAULT_MAX_LOSS, COPY_DEFAULT_MAX_HOLD_HOURS,
    COPY_MAX_PRICE_IMPACT_PCT,
    ENABLE_JITO_PROTECTION, JITO_MIN_TRADE_SOL,
)
from data.database import db
from chains.solana.dex_swaps import swapper
from utils.notifications import notification_engine
from trading.enhanced_features import enhanced_features
from trading.mev_protection import mev_protection

logger = logging.getLogger(__name__)

# pump.fun program ID (Solana) — stable on-chain address, not a URL
PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Local aliases from config (kept for readability inside this module)
LOSS_CHECK_WINDOW     = COPY_LOSS_CHECK_WINDOW
SIGNAL_WINDOW_SECONDS = COPY_SIGNAL_WINDOW_SECONDS
DEFAULT_PROFIT_TARGET = COPY_DEFAULT_PROFIT_TARGET
DEFAULT_TRAILING_STOP = COPY_DEFAULT_TRAILING_STOP
DEFAULT_MAX_LOSS      = COPY_DEFAULT_MAX_LOSS
DEFAULT_MAX_HOLD_HOURS= COPY_DEFAULT_MAX_HOLD_HOURS
MAX_PRICE_IMPACT_PCT  = COPY_MAX_PRICE_IMPACT_PCT


class CopyTradingEngine:
    """Enhanced copy trading engine with real-time monitoring and smart exits."""

    def __init__(self):
        self.rpc_url = SOLANA_RPC_URL
        self.check_interval = COPY_TRADE_CHECK_INTERVAL
        self.min_trade_amount = MIN_TRADE_AMOUNT

        # { (user_id, whale_address): asyncio.Task }
        self._monitor_tasks: Dict[tuple, asyncio.Task] = {}
        # Dedup: signatures already processed  { whale_address: set }
        self._seen_signatures: Dict[str, set] = {}
        # Signal aggregation: { (user_id, output_mint): List[Dict] }
        self._pending_signals: Dict[tuple, List[Dict]] = {}
        # Trailing stop monitors: { (user_id, token_address): asyncio.Task }
        self._position_monitors: Dict[tuple, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_monitoring_for_user(self, user_id: int):
        """Start monitoring all active Solana watched wallets for a user.
        Uses WebSocket if available, falls back to HTTP polling."""
        watched_wallets = db.get_watched_wallets(user_id)
        if not watched_wallets:
            logger.info(f"No watched wallets for user {user_id}")
            return

        for wallet in watched_wallets:
            if wallet.get('chain', 'solana') != 'solana':
                continue
            if wallet.get('is_paused'):
                logger.info(f"⏸️ Skipping paused whale {wallet['wallet_address']}")
                continue
            key = (user_id, wallet['wallet_address'])
            if key not in self._monitor_tasks or self._monitor_tasks[key].done():
                if WS_AVAILABLE:
                    task = asyncio.create_task(
                        self.monitor_wallet_ws(wallet['wallet_address'], user_id)
                    )
                    mode = "WebSocket"
                else:
                    task = asyncio.create_task(
                        self.monitor_wallet(wallet['wallet_address'], user_id)
                    )
                    mode = "HTTP polling"
                self._monitor_tasks[key] = task
                logger.info(f"👁️ Monitoring {wallet['wallet_address'][:8]}… via {mode}")

    def stop_monitoring_for_user(self, user_id: int):
        """Cancel all monitoring and trailing-stop tasks for a user."""
        for key in [k for k in self._monitor_tasks if k[0] == user_id]:
            task = self._monitor_tasks.pop(key)
            if not task.done():
                task.cancel()

        for key in [k for k in self._position_monitors if k[0] == user_id]:
            task = self._position_monitors.pop(key)
            if not task.done():
                task.cancel()

        logger.info(f"🛑 Stopped all monitoring for user {user_id}")

    # ------------------------------------------------------------------
    # WebSocket monitoring  (primary — real-time, ~1-2s latency)
    # ------------------------------------------------------------------

    async def monitor_wallet_ws(self, wallet_address: str, user_id: int):
        """Subscribe to Solana logsSubscribe for instant swap notifications.
        Reconnects with exponential back-off + jitter. Gives up after MAX_WS_RETRIES
        consecutive failures and falls back to HTTP polling."""
        MAX_WS_RETRIES = 10
        sig_cache = self._seen_signatures.setdefault(wallet_address, {})
        reconnect_delay = 2
        retries = 0

        while retries < MAX_WS_RETRIES:
            try:
                async with websockets.connect(
                    SOLANA_WSS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [wallet_address]},
                            {"commitment": "processed"}
                        ]
                    }))

                    # Subscription confirmation
                    raw_conf = await asyncio.wait_for(ws.recv(), timeout=10)
                    sub_id = json.loads(raw_conf).get('result')
                    logger.info(f"🔌 WS subscribed {wallet_address[:8]}… (sub={sub_id})")
                    reconnect_delay = 2  # reset on successful connect
                    retries = 0

                    async for raw in ws:
                        wallet_config = self._get_wallet_config(user_id, wallet_address)
                        if not wallet_config:
                            logger.warning(f"Config gone for {wallet_address} — closing WS")
                            return
                        if wallet_config.get('is_paused'):
                            continue

                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        if 'params' not in msg:
                            continue  # subscription confirmation or heartbeat

                        value = msg['params'].get('result', {}).get('value', {})
                        signature = value.get('signature')
                        err = value.get('err')

                        if not signature or err:
                            continue

                        now = time.time()
                        # Prune signatures older than 1 hour to prevent stale dedup
                        if len(sig_cache) > 200:
                            cutoff = now - 3600
                            sig_cache = {s: t for s, t in sig_cache.items() if t > cutoff}
                            self._seen_signatures[wallet_address] = sig_cache

                        if signature in sig_cache:
                            continue
                        sig_cache[signature] = now

                        # Fire-and-forget so WS reads are never blocked
                        asyncio.create_task(
                            self._process_signature(
                                signature, user_id, wallet_address, wallet_config
                            )
                        )

            except asyncio.CancelledError:
                logger.info(f"🛑 WS monitor cancelled for {wallet_address}")
                return
            except Exception as e:
                retries += 1
                import random
                jitter = random.uniform(0, reconnect_delay * 0.3)
                wait = reconnect_delay + jitter
                logger.warning(
                    f"⚠️ WS error {wallet_address[:8]}: {e} "
                    f"— retry {retries}/{MAX_WS_RETRIES} in {wait:.1f}s"
                )
                await asyncio.sleep(wait)
                reconnect_delay = min(reconnect_delay * 2, 60)

        logger.error(
            f"❌ WS gave up after {MAX_WS_RETRIES} retries for {wallet_address[:8]}…"
            f" — falling back to HTTP polling"
        )
        # Fall back to HTTP polling so monitoring doesn't silently stop
        await self.monitor_wallet(wallet_address, user_id)

    async def _process_signature(self, signature: str, user_id: int,
                                  wallet_address: str, wallet_config: Dict):
        """Fetch full tx from the signature emitted by the WS feed and process it."""
        try:
            tx_data = {'signature': signature}
            swap_data = await self.extract_swap_data(tx_data)
            if swap_data:
                logger.info(
                    f"⚡ [{wallet_address[:8]}] "
                    f"{swap_data['inputMint'][:8]}→{swap_data['outputMint'][:8]} "
                    f"{swap_data['inputAmount']:.4f} SOL  dex={swap_data['dex']}"
                )
                await self._handle_whale_swap(user_id, wallet_address, wallet_config, swap_data)
        except Exception as e:
            logger.error(f"Error processing signature {signature[:10]}: {e}")

    # ------------------------------------------------------------------
    # HTTP polling  (fallback when websockets is not installed)
    # ------------------------------------------------------------------

    async def monitor_wallet(self, wallet_address: str, user_id: int):
        """Poll getSignaturesForAddress every COPY_TRADE_CHECK_INTERVAL seconds."""
        logger.info(f"👁️ [HTTP] Monitoring whale: {wallet_address}")
        sig_cache = self._seen_signatures.setdefault(wallet_address, {})

        while True:
            try:
                wallet_config = self._get_wallet_config(user_id, wallet_address)
                if not wallet_config:
                    logger.warning(f"Wallet config gone for {wallet_address} — stopping")
                    break
                if wallet_config.get('is_paused'):
                    await asyncio.sleep(self.check_interval * 2)
                    continue

                now = time.time()
                transactions = await self.get_wallet_transactions(wallet_address)
                for tx in transactions:
                    sig = tx.get('signature', '')
                    if not sig or sig in sig_cache:
                        continue
                    sig_cache[sig] = now
                    if await self.is_swap_transaction(tx):
                        swap_data = await self.extract_swap_data(tx)
                        if swap_data:
                            await self._handle_whale_swap(
                                user_id, wallet_address, wallet_config, swap_data
                            )

                # Prune signatures older than 1 hour
                if len(sig_cache) > 200:
                    cutoff = now - 3600
                    sig_cache = {s: t for s, t in sig_cache.items() if t > cutoff}
                    self._seen_signatures[wallet_address] = sig_cache

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                logger.info(f"🛑 Monitor cancelled for {wallet_address}")
                break
            except Exception as e:
                logger.error(f"Error monitoring wallet {wallet_address}: {e}")
                await asyncio.sleep(self.check_interval)

    # ------------------------------------------------------------------
    # Whale qualification gate
    # ------------------------------------------------------------------

    def _is_whale_qualified(self, user_id: int, whale_address: str) -> Tuple[bool, str]:
        """Check historical copy-performance to decide if this whale is worth copying.

        Uses enhanced qualification if enabled (Sharpe ratio, drawdown checks).
        Wallets with fewer than WHALE_MIN_TRADES closed positions pass through
        with a warning — we can't disqualify on insufficient data.
        """
        # Try enhanced qualification first
        qualified, reason = enhanced_features.is_whale_qualified_enhanced(user_id, whale_address)
        if not qualified:
            logger.warning(f"⛔ Whale {whale_address[:8]} not qualified (enhanced): {reason}")
            return False, reason

        # Fall back to basic qualification if enhanced is disabled or passes
        records = db.get_copy_performance(user_id, whale_address, limit=50)
        closed = [
            r for r in records
            if r.get('status') == 'closed' and r.get('user_profit_percent') is not None
        ]

        if len(closed) < WHALE_MIN_TRADES:
            return True, f"insufficient_history ({len(closed)} trades — allowing)"

        wins = sum(1 for r in closed if r['user_profit_percent'] > 0)
        win_rate = wins / len(closed)
        avg_profit = sum(r['user_profit_percent'] for r in closed) / len(closed)

        if win_rate < WHALE_MIN_WIN_RATE:
            return False, f"win_rate {win_rate:.0%} below {WHALE_MIN_WIN_RATE:.0%} floor"
        if avg_profit < WHALE_MIN_AVG_PROFIT:
            return False, f"avg_profit {avg_profit:.1f}% below {WHALE_MIN_AVG_PROFIT}% floor"

        return True, f"qualified  win_rate={win_rate:.0%}  avg_profit={avg_profit:.1f}%"

    # ------------------------------------------------------------------
    # Token safety filter
    # ------------------------------------------------------------------

    async def _passes_token_filter(self, token_address: str) -> Tuple[bool, str]:
        """Run the token_analyzer safety checks before copying a trade.

        Includes RugCheck integration if enabled.
        On API error we block the trade — never copy an unverified token.
        """
        try:
            from trading.token_analyzer import token_analyzer
            analysis = token_analyzer.analyze_token(token_address)
            recommendation = analysis.get('trade_recommendation', 'ANALYZE')
            risk_score = analysis.get('risk_score', 50)

            if recommendation in ('REJECT_HONEYPOT', 'REJECT_CONCENTRATED'):
                return False, recommendation
            if risk_score > 85:
                return False, f"risk_score={risk_score:.0f}"

            # Additional RugCheck filter if enabled
            passes_rugcheck, rugcheck_score = await enhanced_features.check_rugcheck_score(token_address)
            if not passes_rugcheck:
                return False, f"rugcheck_score={rugcheck_score}"

            return True, f"ok  risk={risk_score:.0f}  rugcheck={rugcheck_score}"
        except Exception as e:
            logger.warning(f"Token filter unavailable — blocking trade for safety: {e}")
            return False, "filter_unavailable"

    # ------------------------------------------------------------------
    # Signal aggregation
    # ------------------------------------------------------------------

    def _register_signal(
        self, user_id: int, swap_data: Dict, whale_address: str
    ) -> Tuple[bool, int, float]:
        """Register a buy signal and return (should_execute, unique_whales, size_multiplier).

        Enhanced version uses performance-weighted multipliers and requires min whales for risky tokens.
        """
        now = time.time()
        token = swap_data['outputMint']
        key = (user_id, token)

        # Prune expired signals
        existing = [
            s for s in self._pending_signals.get(key, [])
            if now - s['ts'] < SIGNAL_WINDOW_SECONDS
        ]

        seen_whales = {s['whale'] for s in existing}
        is_new_whale = whale_address not in seen_whales

        if is_new_whale:
            existing.append({'ts': now, 'whale': whale_address,
                             'amount': swap_data['inputAmount'], 'executed': False})

        self._pending_signals[key] = existing

        unique_count = len({s['whale'] for s in existing})
        already_executed = any(s.get('executed') for s in existing)

        if already_executed:
            # Additional whale confirmed — log but don't re-open
            if is_new_whale:
                logger.info(f"📊 Additional signal #{unique_count} for {token[:8]}… (already executed)")
            return False, unique_count, 1.0

        # First execution: mark all signals
        for s in existing:
            s['executed'] = True

        # Enhanced signal aggregation with performance weighting
        multiplier = enhanced_features.get_signal_multiplier_enhanced(
            user_id, token, unique_count, whale_ranks=[]
        )

        return True, unique_count, multiplier

    # ------------------------------------------------------------------
    # Portfolio-% based sizing
    # ------------------------------------------------------------------

    async def _get_whale_portfolio_pct(self, whale_address: str,
                                       trade_amount: float) -> float:
        """Estimate what fraction of the whale's SOL balance this trade represents.
        Falls back to 2% if the RPC call fails."""
        try:
            from chains.solana.wallet import SolanaWallet
            balance = SolanaWallet().get_balance(whale_address)
            if balance and balance > 0:
                pct = trade_amount / balance
                # Clamp to sensible range — outliers are likely bad data
                return max(0.005, min(0.30, pct))
        except Exception as e:
            logger.warning(f"Could not fetch whale balance for sizing: {e}")
        return 0.02  # default: assume 2% of portfolio

    async def _get_user_sol_balance(self, user_id: int) -> float:
        """Get the SOL balance of the user's active (or trading) wallet."""
        try:
            user = db.get_user(user_id)
            if not user:
                return 0.0
            addr = user.get('wallet_address')
            if user.get('use_separate_trading_wallet') and user.get('trading_wallet_address'):
                addr = user['trading_wallet_address']
            if not addr:
                return 0.0
            from chains.solana.wallet import SolanaWallet
            return SolanaWallet().get_balance(addr) or 0.0
        except Exception as e:
            logger.error(f"Error getting user SOL balance: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Main swap handler — orchestrates every feature
    # ------------------------------------------------------------------

    async def _handle_whale_swap(self, user_id: int, whale_address: str,
                                  wallet_config: Dict, swap_data: Dict):
        """Route a detected whale swap through all feature gates, then execute."""

        # 1. Auto-pause check
        if self._should_auto_pause(user_id, whale_address, wallet_config):
            return

        # 2. Whale qualification
        qualified, qual_reason = self._is_whale_qualified(user_id, whale_address)
        if not qualified:
            logger.warning(f"⛔ Whale {whale_address[:8]} not qualified: {qual_reason}")
            return
        logger.debug(f"✅ Whale qualified: {qual_reason}")

        # 3. Token safety filter
        output_mint = swap_data.get('outputMint', '')
        if output_mint and output_mint != WSOL_MINT:
            ok, filter_reason = await self._passes_token_filter(output_mint)
            if not ok:
                logger.warning(f"🚫 Token {output_mint[:8]} blocked: {filter_reason}")
                return
            logger.debug(f"✅ Token filter: {filter_reason}")

        # 4. Signal aggregation
        should_execute, signal_count, size_multiplier = self._register_signal(
            user_id, swap_data, whale_address
        )
        if not should_execute:
            return

        if signal_count > 1:
            logger.info(
                f"🔥 {signal_count} whales confirmed {output_mint[:8]}… "
                f"— size boost {size_multiplier}x"
            )

        # 5. Configurable copy delay
        delay = int(wallet_config.get('copy_delay_seconds', 0))
        if delay > 0:
            logger.info(f"⏳ Waiting {delay}s before copying")
            await asyncio.sleep(delay)

        # 6. Close existing open position in the same token
        await self._close_existing_position(user_id, output_mint)

        # 7. Portfolio-% based amount calculation with dynamic scaling
        whale_pct = await self._get_whale_portfolio_pct(
            whale_address, swap_data['inputAmount']
        )
        user_balance = await self._get_user_sol_balance(user_id)
        base_scale = float(wallet_config.get('copy_scale', DEFAULT_COPY_SCALE))
        
        # Apply dynamic copy scale based on whale performance
        scale = enhanced_features.get_dynamic_copy_scale(user_id, whale_address, base_scale)
        
        weight = float(wallet_config.get('weight', 1.0))
        amount = user_balance * whale_pct * scale * weight * size_multiplier

        # Fallback to flat scaling when balance is unavailable
        if amount <= 0:
            amount = self._weighted_amount(swap_data['inputAmount'], wallet_config) * size_multiplier

        if amount < self.min_trade_amount:
            logger.info(f"Amount {amount:.4f} SOL below minimum — skipping")
            return

        # 8. Execute
        await self.execute_copy_trade(
            user_id=user_id,
            input_mint=swap_data['inputMint'],
            output_mint=output_mint,
            amount=amount,
            dex=swap_data.get('dex', 'jupiter'),
            watched_wallet=whale_address,
            whale_entry_price=swap_data.get('whaleEntryPrice', 0.0),
            copy_scale=scale,
            whale_block_time=swap_data.get('blockTime', 0),
            signal_count=signal_count,
        )

    # ------------------------------------------------------------------
    # Keypair helper
    # ------------------------------------------------------------------

    def _get_user_keypair(self, user_id: int):
        """Decrypt and return the solders Keypair for a user's active trading wallet.
        Returns None on any error so callers can abort gracefully."""
        try:
            from wallet.encryption import encryption
            from chains.solana.wallet import SolanaWallet
            user = db.get_user(user_id)
            if not user:
                return None
            if user.get('use_separate_trading_wallet') and user.get('encrypted_trading_key'):
                enc_key = user['encrypted_trading_key']
            else:
                enc_key = user.get('encrypted_private_key')
            if not enc_key:
                logger.error(f"No encrypted key stored for user {user_id}")
                return None
            private_key = encryption.decrypt(enc_key)
            if not private_key:
                logger.error(f"Failed to decrypt key for user {user_id}")
                return None
            return SolanaWallet().import_keypair(private_key)
        except Exception as e:
            logger.error(f"_get_user_keypair error for user {user_id}: {e}")
            return None

    def _should_auto_pause(self, user_id: int, whale_address: str,
                            wallet_config: Dict) -> bool:
        """Pause this whale if their rolling avg loss exceeds the user-configured limit."""
        max_loss = float(wallet_config.get('max_loss_percent', 20.0))
        avg_return = db.get_whale_recent_loss(user_id, whale_address, LOSS_CHECK_WINDOW)

        if avg_return < -abs(max_loss):
            wallet_id = wallet_config['id']
            reason = (f"Auto-paused: avg {LOSS_CHECK_WINDOW}-trade return "
                      f"{avg_return:.1f}% breached -{max_loss}% limit")
            db.pause_watched_wallet(wallet_id, reason)
            logger.warning(f"⛔ {reason} for whale {whale_address}")
            return True
        return False

    async def _close_existing_position(self, user_id: int, token_mint: str):
        """Exit any open position in token_mint before entering a new one."""
        existing = db.get_pending_trade_by_token(user_id, token_mint)
        if not existing:
            return
        logger.info(f"📤 Closing existing position in {token_mint[:8]}…")
        try:
            keypair = self._get_user_keypair(user_id)
            close_result = await swapper.execute_swap(
                token_mint, WSOL_MINT, existing.get('token_amount', 0), 'jupiter',
                keypair=keypair
            )
            if close_result and close_result.get('status') in ('confirmed', 'quoted'):
                sol_received = close_result.get('expectedOutput', 0)
                db.update_pending_trade_closed(
                    user_id, token_mint, sol_received,
                    close_result.get('signature', 'close')
                )
                logger.info(f"✅ Closed existing position, received {sol_received:.4f} SOL")
        except Exception as e:
            logger.error(f"Error closing existing position: {e}")

    def _weighted_amount(self, whale_amount: float, wallet_config: Dict) -> float:
        """Flat copy_scale × weight fallback for when user balance is unavailable."""
        scale  = float(wallet_config.get('copy_scale', DEFAULT_COPY_SCALE))
        weight = float(wallet_config.get('weight', 1.0))
        return whale_amount * scale * weight

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------

    async def execute_copy_trade(
        self,
        user_id: int,
        input_mint: str,
        output_mint: str,
        amount: float,
        dex: str,
        watched_wallet: str,
        whale_entry_price: float = 0.0,
        copy_scale: float = 1.0,
        whale_block_time: int = 0,
        signal_count: int = 1,
    ) -> bool:
        """Execute a copy trade, record performance, and launch the trailing-stop monitor."""
        try:
            user = db.get_user(user_id)
            if not user:
                logger.error(f"User {user_id} not found")
                return False

            # Get best price quote with latency-optimized slippage
            base_slippage = float(db.get_user_setting(user_id, 'slippage_tolerance', 2.0))
            adjusted_slippage = enhanced_features.get_latency_adjusted_slippage(
                base_slippage, exec_time_ms
            )
            
            swap_data = await swapper.get_best_price(input_mint, output_mint, amount, slippage_bps=int(adjusted_slippage * 100))
            if not swap_data:
                logger.error("No price quote available")
                return False

            output_amount  = swap_data.get('price', 0)
            price_impact   = float(swap_data.get('priceImpact', 0))

            # Refuse trades with excessive price impact (protects against thin liquidity)
            if price_impact > MAX_PRICE_IMPACT_PCT:
                logger.warning(
                    f"⚠️ Price impact {price_impact:.1f}% > {MAX_PRICE_IMPACT_PCT}% — skipping"
                )
                return False

            # SOL per token (needed for correct PnL: higher = token more valuable)
            user_entry_price = amount / output_amount if output_amount > 0 else 0

            # 8. Latency tracking
            exec_time_ms = 0
            if whale_block_time:
                exec_time_ms = int((time.time() - whale_block_time) * 1000)
                logger.info(f"⏱️ Copy latency: {exec_time_ms}ms")

            # Priority fee (already fetched by execute_jupiter_swap, but log it here)
            priority_fee = await swapper.get_recent_priority_fee()

            # Get keypair for signing — abort early if unavailable
            keypair = self._get_user_keypair(user_id)
            if keypair is None:
                logger.error(f"Cannot execute copy trade — no keypair for user {user_id}")
                return False

            # Check if should use Jito for MEV protection
            use_jito = await enhanced_features.should_use_jito(amount)

            # Open performance record
            position_id = db.open_copy_position(
                user_id=user_id,
                watched_wallet=watched_wallet,
                token_address=output_mint,
                whale_entry_price=whale_entry_price,
                user_entry_price=user_entry_price,
                copy_scale=copy_scale,
                sol_spent=amount,
                whale_block_time=whale_block_time,
                copy_latency_ms=exec_time_ms,
                signal_count=signal_count,
            )

            # Execute swap with optional Jito protection
            if use_jito:
                logger.info(f"🔒 Using Jito private pool for MEV protection")
                swap_result = await swapper.execute_swap(
                    input_mint, output_mint, amount, dex, keypair=keypair,
                    use_private_tx=True
                )
            else:
                swap_result = await swapper.execute_swap(
                    input_mint, output_mint, amount, dex, keypair=keypair
                )

            if swap_result and swap_result.get('status') in ('confirmed', 'quoted'):
                tokens_received = swap_result.get('expectedOutput', output_amount)

                db.add_trade(
                    user_id=user_id,
                    input_mint=input_mint,
                    output_mint=output_mint,
                    input_amount=amount,
                    output_amount=tokens_received,
                    dex=dex,
                    price=user_entry_price,
                    slippage=price_impact,
                    tx_hash=swap_result.get('signature', 'pending'),
                    watched_wallet=watched_wallet,
                    is_copy=True,
                )

                # Store token amount on the position record (needed for trailing stop sells)
                db.update_copy_position_token_amount(position_id, tokens_received)

                logger.info(
                    f"✅ Copy trade:  {amount:.4f} SOL → {tokens_received:.6f} tokens "
                    f"| whale_entry={whale_entry_price:.6f}  our_entry={user_entry_price:.6f} "
                    f"| scale={copy_scale}x  signals={signal_count} "
                    f"| latency={exec_time_ms}ms  priority={priority_fee}μL"
                )

                # Track position in notification engine so sell buttons work
                pos_id = notification_engine.track_position(
                    user_id, output_mint, tokens_received,
                    user_entry_price, dex, position_type='copy',
                    db_position_id=position_id
                )
                await notification_engine.notify_trade_opened(
                    user_id, pos_id,
                    f"🐋 **Copy Trade Executed**\n"
                    f"Whale: `{watched_wallet[:8]}…`\n"
                    f"Token: `{output_mint[:12]}…`\n"
                    f"Spent: {amount:.4f} SOL  |  Tokens: {tokens_received:.6f}\n"
                    f"Signals: {signal_count}  |  Latency: {exec_time_ms}ms\n"
                    f"TP: +30%  |  Trailing stop: -15%"
                )

                # Launch trailing-stop monitor for this position
                pm_key = (user_id, output_mint)
                if pm_key in self._position_monitors and not self._position_monitors[pm_key].done():
                    self._position_monitors[pm_key].cancel()

                self._position_monitors[pm_key] = asyncio.create_task(
                    self._monitor_position_trailing(
                        user_id, position_id, output_mint,
                        user_entry_price, tokens_received
                    )
                )
                return True

            # Swap failed — remove dangling open record
            if position_id > 0:
                db.close_copy_position(position_id, 0, 0, 0, exit_reason='swap_failed')
            return False

        except Exception as e:
            logger.error(f"Error executing copy trade: {e}")
            return False

    # ------------------------------------------------------------------
    # Trailing stop / partial take-profit / time-decay exit
    # ------------------------------------------------------------------

    async def _monitor_position_trailing(
        self,
        user_id: int,
        position_id: int,
        token_address: str,
        entry_price: float,
        token_amount: float,
        profit_target: float   = DEFAULT_PROFIT_TARGET,
        trailing_stop_pct: float = DEFAULT_TRAILING_STOP,
        max_loss_pct: float    = DEFAULT_MAX_LOSS,
        max_hours: float       = DEFAULT_MAX_HOLD_HOURS,
    ):
        """
        Monitor a position with four exit triggers:
          • Hard stop loss       — exit 100% if PnL ≤ -max_loss_pct
          • Partial take-profit  — sell 50% when PnL ≥ +profit_target
          • Trailing stop        — exit remaining if price drops trailing_stop_pct from peak
          • Time-decay exit      — exit after max_hours regardless of PnL
        """
        peak_price        = entry_price or 1e-12
        partial_taken     = False
        remaining_amount  = token_amount
        start_time        = time.time()
        check_interval    = 30  # seconds

        logger.info(
            f"📊 Trailing monitor: {token_address[:8]}…  "
            f"entry={entry_price:.8f}  amount={token_amount:.4f}"
        )

        while True:
            try:
                elapsed_hours = (time.time() - start_time) / 3600

                # Time-decay exit
                if elapsed_hours >= max_hours:
                    logger.info(
                        f"⏰ Time-decay exit {token_address[:8]}…  ({elapsed_hours:.1f}h held)"
                    )
                    await self._exit_position(
                        user_id, position_id, token_address,
                        remaining_amount, 'time_decay'
                    )
                    return

                # Current price — convert Jupiter's tokens/SOL to SOL/token so PnL is intuitive
                price_info      = await swapper.get_best_price(WSOL_MINT, token_address, 1.0)
                _tokens_per_sol = price_info.get('price') if price_info else None
                current_price   = (1.0 / _tokens_per_sol) if _tokens_per_sol and _tokens_per_sol > 0 else None
                if not current_price or current_price <= 0:
                    await asyncio.sleep(check_interval)
                    continue

                pnl_pct            = (current_price - entry_price) / entry_price if entry_price else 0
                peak_price         = max(peak_price, current_price)
                drawdown_from_peak = (peak_price - current_price) / peak_price

                logger.debug(
                    f"  {token_address[:8]}: pnl={pnl_pct*100:+.1f}%  "
                    f"peak={peak_price:.8f}  dd={drawdown_from_peak*100:.1f}%"
                )

                # Hard stop loss
                if pnl_pct <= -max_loss_pct:
                    logger.info(
                        f"🛑 Stop loss {pnl_pct*100:.1f}%  → exiting {token_address[:8]}…"
                    )
                    await self._exit_position(
                        user_id, position_id, token_address,
                        remaining_amount, 'stop_loss'
                    )
                    return

                # Partial take-profit: sell 50% at first profit target
                if pnl_pct >= profit_target and not partial_taken:
                    sell_half         = remaining_amount * 0.5
                    remaining_amount -= sell_half
                    partial_taken     = True
                    logger.info(
                        f"💰 Partial exit 50% at +{pnl_pct*100:.1f}%  "
                        f"{token_address[:8]}…  remaining={remaining_amount:.4f}"
                    )
                    await self._execute_exit_swap(
                        user_id, token_address, sell_half, position_id, 'partial_profit'
                    )
                    db.update_copy_position_token_amount(position_id, remaining_amount)

                # Trailing stop — activated once we're in profit territory or down >10%
                if (peak_price > entry_price or pnl_pct < -0.10) \
                        and drawdown_from_peak >= trailing_stop_pct:
                    logger.info(
                        f"📉 Trailing stop  {drawdown_from_peak*100:.1f}% from peak  "
                        f"→ exiting {token_address[:8]}…"
                    )
                    await self._exit_position(
                        user_id, position_id, token_address,
                        remaining_amount, 'trailing_stop'
                    )
                    return

                await asyncio.sleep(check_interval)

            except asyncio.CancelledError:
                logger.info(f"🛑 Trailing monitor cancelled: {token_address[:8]}…")
                return
            except Exception as e:
                logger.error(f"Trailing monitor error: {e}")
                await asyncio.sleep(check_interval)

    async def _exit_position(self, user_id: int, position_id: int,
                              token_address: str, amount: float, reason: str):
        """Full exit of remaining position amount."""
        await self._execute_exit_swap(user_id, token_address, amount, position_id, reason)

    async def _execute_exit_swap(self, user_id: int, token_address: str,
                                  amount: float, position_id: int, reason: str):
        """Swap tokens → SOL and close / update the DB record."""
        try:
            keypair     = self._get_user_keypair(user_id)
            sell_result = await swapper.execute_swap(
                token_address, WSOL_MINT, amount, 'jupiter', keypair=keypair
            )
            if not sell_result or sell_result.get('status') not in ('confirmed', 'quoted'):
                logger.error(f"Exit swap failed for position {position_id} [{reason}]")
                return

            sol_received   = sell_result.get('expectedOutput', 0)
            price_info     = await swapper.get_best_price(WSOL_MINT, token_address, 1.0)
            _tps           = price_info.get('price', 0) if price_info else 0
            exit_price     = (1.0 / _tps) if _tps > 0 else 0  # SOL per token

            db.close_copy_position(
                position_id, 0, exit_price, sol_received, exit_reason=reason
            )
            logger.info(
                f"✅ Exit [{reason}]: {amount:.4f} tokens → {sol_received:.4f} SOL  "
                f"sig={sell_result.get('signature', 'n/a')[:20]}"
            )
        except Exception as e:
            logger.error(f"Error executing exit swap ({reason}): {e}")

    # ------------------------------------------------------------------
    # Performance summary (includes latency stats)
    # ------------------------------------------------------------------

    def get_copy_performance_summary(self, user_id: int,
                                      watched_wallet: str = None) -> Dict:
        """Whale vs user performance summary, including average copy latency."""
        records = db.get_copy_performance(user_id, watched_wallet, limit=50)
        closed = [
            r for r in records
            if r['status'] == 'closed'
            and r.get('whale_profit_percent') is not None
            and r.get('user_profit_percent') is not None
        ]

        if not closed:
            return {'trades': 0, 'whale_avg': 0.0, 'user_avg': 0.0,
                    'delta': 0.0, 'avg_latency_ms': 0}

        whale_avg  = sum(r['whale_profit_percent'] for r in closed) / len(closed)
        user_avg   = sum(r['user_profit_percent']  for r in closed) / len(closed)
        latencies  = [r['copy_latency_ms'] for r in closed if r.get('copy_latency_ms')]
        avg_lat    = int(sum(latencies) / len(latencies)) if latencies else 0

        return {
            'trades':          len(closed),
            'whale_avg':       round(whale_avg, 2),
            'user_avg':        round(user_avg, 2),
            'delta':           round(user_avg - whale_avg, 2),
            'avg_latency_ms':  avg_lat,
        }

    # ------------------------------------------------------------------
    # Blockchain helpers
    # ------------------------------------------------------------------

    def _get_wallet_config(self, user_id: int, wallet_address: str) -> Optional[Dict]:
        for w in db.get_watched_wallets(user_id):
            if w['wallet_address'] == wallet_address:
                return w
        return None

    async def get_wallet_transactions(self, wallet_address: str) -> List[Dict]:
        """HTTP polling fallback: fetch recent signatures via RPC."""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [wallet_address, {"limit": 10}]
                }
                async with session.post(self.rpc_url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('result', [])
        except Exception as e:
            logger.error(f"Error getting wallet transactions: {e}")
        return []

    async def is_swap_transaction(self, tx_data: Dict) -> bool:
        """Return True if the transaction has a valid signature and did not fail."""
        return bool(tx_data.get('signature')) and not tx_data.get('err')

    async def extract_swap_data(self, tx_data: Dict) -> Optional[Dict]:
        """
        Fetch the full transaction via getTransaction and parse token balance deltas.

        Detection:
          - Pre/post SPL token balances  → identifies input/output tokens
          - Native SOL balance fallback  → catches SOL→token and token→SOL swaps
          - Log messages                 → detects Jupiter / Raydium / Orca
          - Account keys                 → detects pump.fun program involvement
        """
        signature = tx_data.get('signature')
        if not signature or tx_data.get('err'):
            return None

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                    ]
                }
                async with session.post(self.rpc_url, json=payload, timeout=15) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            tx = data.get('result')
            if not tx:
                return None

            meta = tx.get('meta', {})
            if meta.get('err'):
                return None

            block_time = tx.get('blockTime', 0)

            # Parse SPL token balance changes
            def _bal_map(balances):
                m = {}
                for b in balances:
                    mint   = b.get('mint')
                    amount = float(b.get('uiTokenAmount', {}).get('uiAmount') or 0)
                    idx    = b.get('accountIndex', mint)
                    m[(idx, mint)] = amount
                return m

            pre_map  = _bal_map(meta.get('preTokenBalances',  []))
            post_map = _bal_map(meta.get('postTokenBalances', []))
            all_keys = set(pre_map) | set(post_map)

            input_mint = output_mint = None
            input_amount = output_amount = 0.0

            for key in all_keys:
                pre   = pre_map.get(key, 0)
                post  = post_map.get(key, 0)
                delta = post - pre
                _, mint = key
                if delta < -1e-9 and abs(delta) > input_amount:
                    input_mint   = mint
                    input_amount = abs(delta)
                elif delta > 1e-9 and delta > output_amount:
                    output_mint   = mint
                    output_amount = delta

            # SOL balance fallback for account[0] (the fee-payer / whale)
            pre_sol_list  = meta.get('preBalances',  [])
            post_sol_list = meta.get('postBalances', [])
            if pre_sol_list and post_sol_list:
                sol_delta = (post_sol_list[0] - pre_sol_list[0]) / 1e9
                if sol_delta < -1e-6 and input_mint is None:
                    input_mint   = WSOL_MINT
                    input_amount = abs(sol_delta)
                elif sol_delta > 1e-6 and output_mint is None:
                    output_mint   = WSOL_MINT
                    output_amount = sol_delta

            if not input_mint or not output_mint:
                return None

            # DEX detection from log messages + pump.fun account key check
            dex  = 'unknown'
            msg  = tx.get('transaction', {}).get('message', {})
            for acc in msg.get('accountKeys', []):
                pubkey = acc.get('pubkey', '') if isinstance(acc, dict) else str(acc)
                if pubkey == PUMP_FUN_PROGRAM:
                    dex = 'pump_fun'
                    break

            if dex == 'unknown':
                for log in (meta.get('logMessages', []) or []):
                    low = log.lower()
                    if 'jupiter' in low or 'jup4' in low:
                        dex = 'jupiter';  break
                    elif 'raydium' in low:
                        dex = 'raydium';  break
                    elif 'orca' in low:
                        dex = 'orca';     break

            entry_price = input_amount / output_amount if output_amount > 0 else 0.0

            return {
                'signature':      signature,
                'inputMint':      input_mint,
                'outputMint':     output_mint,
                'inputAmount':    input_amount,
                'outputAmount':   output_amount,
                'dex':            dex,
                'whaleEntryPrice': entry_price,
                'blockTime':      block_time,
            }

        except Exception as e:
            logger.error(f"Error extracting swap data for {signature[:10]}: {e}")
            return None


# Singleton instance
copy_trader = CopyTradingEngine()

