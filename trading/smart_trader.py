"""
Smart Trader v2 — Intelligent autonomous trading engine

Features:
  1.  Token analysis & safety scoring        (honeypot, liquidity, holder concentration)
  2.  Trending token discovery               (DexScreener + Birdeye trending feeds)
  3.  Momentum scoring                       (volume spike, price momentum, holder growth, buy/sell pressure)
  4.  Auto Copy Trade mode                   (whale-driven loop — rank by win_rate × avg_profit)
  5.  Auto Smart Trade mode                  (token-scan loop — DexScreener + Birdeye every 30 min)
  6.  Solana-only execution                  (single-chain trading flow)
  7.  Kelly Criterion position sizing        (real historical win_rate/avg_win/avg_loss from DB)
  8.  Portfolio limits                       (max open positions, max % per token)
  9.  Graduated take-profit ladder           (25% at +30%, 50% at +60%, rest +100%)
  10. Trailing stop                          (activates after first TP hit, 15% drawdown from peak)
  11. Auto-rebuy                             (re-enters if momentum still strong after exit)
  12. Suggestion engine                      (push best tokens to user each scan cycle)
  13. Token blacklist / whitelist            (per-user block or force-allow lists)
  14. Birdeye new listings discovery         (replaces deprecated pump.fun Heroku relay)
  15. Multi-timeframe momentum + age filter  (1h/6h/24h price data, penalise very new tokens)
"""
import logging
import asyncio
import aiohttp
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta
import time

from data.database import db
from chains.solana.wallet import SolanaWallet
from chains.solana.dex_swaps import swapper
from utils.notifications import notification_engine
from trading.token_analyzer import token_analyzer
from trading.enhanced_features import enhanced_features
from trading.risk_manager import risk_manager
from config import (
    WSOL_MINT, BIRDEYE_API_KEY,
    SMART_MIN_TRADE_SOL, SMART_MAX_OPEN_POSITIONS, SMART_MAX_PCT_PER_TOKEN,
    POSITION_CHECK_INTERVAL, SMART_MAX_HOLD_HOURS, SMART_HARD_STOP_LOSS,
    SMART_TP_LADDER, SMART_AUTO_TRADE_MIN_SCORE,
    SMART_MIN_VOLUME_USD, SMART_MIN_LIQUIDITY_USD,
    SMART_WHALE_RANK_INTERVAL, SMART_MIN_ACTIVE_SCORE, SMART_MAX_ACTIVE_WHALES,
    SMART_MIN_TRADES_TO_RANK, SMART_WHALE_LOOKBACK_DAYS,
    SMART_SCAN_INTERVAL, SMART_TRAILING_STOP_PCT,
    SMART_REBUY_COOLDOWN, SMART_REBUY_MIN_MOMENTUM, SMART_REBUY_MAX_RISK,
    SMART_DEFAULT_MAX_POSITIONS, SMART_DEFAULT_TRADE_PCT,
    DEXSCREENER_BOOSTED_URL, DEXSCREENER_SEARCH_URL, DEXSCREENER_NEW_URL,
    BIRDEYE_TRENDING_URL, BIRDEYE_NEW_TOKENS_URL,
    ENABLE_TP_LADDER_OPT, TP_LADDER_VOLATILITY_ADJ, TP_BREAKEVEN_AFTER_TP1,
    ENABLE_REBUY_ENHANCED, REBUY_MAX_PER_TOKEN, REBUY_PROFIT_REDUCTION,
    ENABLE_DAILY_LOSS_LIMIT, ENABLE_COOL_OFF_PERIOD, DAILY_LOSS_LIMIT_PCT,
    COOL_OFF_LOSSES, COOL_OFF_MINUTES,
    ENABLE_TOKEN_DISCOVERY_PLUS,
)

logger = logging.getLogger(__name__)

# ── Local aliases (all values now live in config.py / .env) ──────────────────
MIN_TRADE_SOL          = SMART_MIN_TRADE_SOL
MAX_OPEN_POSITIONS     = SMART_MAX_OPEN_POSITIONS
MAX_PCT_PER_TOKEN      = SMART_MAX_PCT_PER_TOKEN
MAX_HOLD_HOURS         = SMART_MAX_HOLD_HOURS
HARD_STOP_LOSS         = SMART_HARD_STOP_LOSS
TP_LADDER              = SMART_TP_LADDER
AUTO_TRADE_MIN_SCORE   = SMART_AUTO_TRADE_MIN_SCORE
MIN_VOLUME_USD         = SMART_MIN_VOLUME_USD
MIN_LIQUIDITY_USD      = SMART_MIN_LIQUIDITY_USD
DEXSCREENER_BOOSTED    = DEXSCREENER_BOOSTED_URL
DEXSCREENER_SEARCH     = DEXSCREENER_SEARCH_URL
DEXSCREENER_NEW        = DEXSCREENER_NEW_URL
BIRDEYE_TRENDING       = BIRDEYE_TRENDING_URL
BIRDEYE_NEW_TOKENS     = BIRDEYE_NEW_TOKENS_URL


class SmartTrader:
    """Intelligent autonomous trading engine with discovery and auto-execution."""

    def __init__(self):
        self.wallet           = SolanaWallet()
        self.min_trade_amount = MIN_TRADE_SOL
        self.profit_target    = SMART_TP_LADDER[0][0]  # first TP threshold
        self.monitoring_active = False

        # Auto-copy trade state per user (whale-driven loop)
        self._auto_copy_tasks: Dict[int, asyncio.Task] = {}
        # Auto-smart trade state per user (token-scan loop)
        self._auto_smart_tasks: Dict[int, asyncio.Task] = {}
        # Active position monitors: (user_id, token_address) -> Task
        self._position_monitors: Dict[tuple, asyncio.Task] = {}
        # Blacklist: user_id -> set of token addresses
        self._blacklist: Dict[int, set] = {}
        # Whitelist: user_id -> set of token addresses (bypass risk check)
        self._whitelist: Dict[int, set] = {}
        # Pending suggestions: user_id -> list of token dicts
        self._pending_suggestions: Dict[int, List[Dict]] = {}

        logger.info("✅ Smart Trader v2 initialized")

    # =========================================================================
    # 1. Analyze & Trade (manual, user-triggered)
    # =========================================================================

    def _get_chain_tools(self, chain: str = 'solana'):
        """Return (wallet_manager, swap_fn, native_token, min_amount) — Solana only."""
        return self.wallet, swapper, WSOL_MINT, MIN_TRADE_SOL

    def _get_user_keypair(self, user_id: int):
        """Decrypt and return the solders Keypair for a user's active trading wallet."""
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
                logger.error(f"No encrypted key for user {user_id}")
                return None
            private_key = encryption.decrypt(enc_key)
            if not private_key:
                logger.error(f"Key decryption failed for user {user_id}")
                return None
            return SolanaWallet().import_keypair(private_key)
        except Exception as e:
            logger.error(f"_get_user_keypair error: {e}")
            return None

    def _passes_runtime_risk_gates(self, user_id: int) -> Tuple[bool, str]:
        """Check enhanced risk gates before opening a new position."""
        can_trade_daily, current_loss = enhanced_features.check_daily_loss_limit(user_id)
        if not can_trade_daily:
            return False, f"Daily loss limit reached ({current_loss:.1f}%)"

        can_trade_cooloff, remaining_min = enhanced_features.check_cool_off_period(user_id)
        if not can_trade_cooloff:
            return False, f"Cool-off active ({remaining_min}m remaining)"

        return True, ""

    async def analyze_and_trade(
        self,
        user_id: int,
        token_address: str,
        user_trade_percent: float = 20.0,
        dex: str = "jupiter",
        chain: str = "solana",
        auto_rebuy: bool = False,
    ) -> Dict:
        """Full analyze-then-trade flow (Solana only)."""
        if chain != 'solana':
            logger.warning(f"Unsupported chain '{chain}' requested; forcing Solana")
            chain = 'solana'
        result = {
            'user_id': user_id,
            'token_address': token_address,
            'chain': 'solana',
            'status': 'PENDING',
            'trade_percent_selected': user_trade_percent,
            'trade_amount_sol': 0,
            'tx_signature': None,
            'risk_assessment': {},
            'momentum_score': 0,
            'timestamp': datetime.now().isoformat()
        }
        try:
            # Resolve chain tools
            wallet_mgr, chain_swapper, native_token, min_amount = self._get_chain_tools(chain)

            # Get wallet address
            user = db.get_user(user_id)
            if not user:
                result.update({'status': 'ERROR', 'error': 'No wallet configured'})
                return result
            wallet_addr = user.get('wallet_address')
            if user.get('use_separate_trading_wallet') and user.get('trading_wallet_address'):
                wallet_addr = user['trading_wallet_address']

            if not wallet_addr:
                result.update({'status': 'ERROR', 'error': 'No wallet address found'})
                return result

            balance = wallet_mgr.get_balance(wallet_addr) or 0.0
            result['wallet_balance'] = balance

            if balance < min_amount:
                result.update({'status': 'INSUFFICIENT_BALANCE',
                               'error': f"Minimum {min_amount} required on Solana"})
                return result

            # Safety check: blacklist
            if token_address in self._get_blacklist(user_id):
                result.update({'status': 'BLACKLISTED', 'error': 'Token is on your blacklist'})
                return result

            # Runtime risk gates
            can_trade, reason = self._passes_runtime_risk_gates(user_id)
            if not can_trade:
                result.update({'status': 'RISK_BLOCKED', 'error': reason})
                logger.warning(f"[SmartRiskGate] user={user_id} blocked: {reason}")
                return result

            # Token analysis — pass chain so EVM uses Honeypot.is instead of Solscan
            analysis = token_analyzer.analyze_token(token_address, chain=chain)
            result['risk_assessment'] = analysis

            # Whitelist bypasses rejection
            whitelisted = token_address in self._get_whitelist(user_id)
            if not whitelisted and analysis['trade_recommendation'] in [
                'REJECT_HONEYPOT', 'REJECT_CONCENTRATED', 'REJECT_TOO_RISKY'
            ]:
                result.update({'status': 'REJECTED', 'reason': analysis['trade_recommendation']})
                await notification_engine.notify_user(
                    user_id,
                    f"🚫 **Token Rejected**\n`{token_address[:12]}…`\n"
                    f"Reason: {analysis['trade_recommendation']}\n"
                    f"Risk: {analysis['risk_score']}/100"
                )
                return result

            # Momentum score
            momentum = await self._score_momentum(token_address)
            result['momentum_score'] = momentum

            trade_amount = self._calculate_trade_amount_kelly(
                balance, user_trade_percent,
                analysis['suggested_trade_percent'],
                analysis['risk_score'], momentum, user_id=user_id
            )
            result['trade_amount_sol'] = trade_amount

            # Get keypair — abort if unavailable
            keypair = self._get_user_keypair(user_id)
            if keypair is None:
                result.update({'status': 'ERROR', 'error': 'Could not load wallet keypair'})
                return result

            # Execute swap via Jupiter (on-chain)
            tx_result = await chain_swapper.execute_swap(
                native_token, token_address, trade_amount, dex, keypair=keypair)

            if tx_result and tx_result.get('status') in ('confirmed', 'quoted'):
                received_amount = tx_result.get('expectedOutput', 0)
                entry_price     = trade_amount / received_amount if received_amount else 0
                result.update({'status': 'SUCCESS',
                               'tx_signature': tx_result.get('signature', '')})

                db.add_pending_trade(user_id, token_address, received_amount,
                                     trade_amount, entry_price,
                                     dex or chain, result['tx_signature'])

                # Track position and notify with sell button
                pos_id = notification_engine.track_position(
                    user_id, token_address, received_amount,
                    entry_price, dex or chain, position_type='smart'
                )
                await notification_engine.notify_trade_opened(
                    user_id, pos_id,
                    f"✅ **Smart Trade Executed**\n"
                    f"Token: `{token_address[:12]}…`\n"
                    f"Spent: {trade_amount:.4f} SOL\n"
                    f"Tokens: {received_amount:.2f}\n"
                    f"Momentum: {momentum}/100  |  Risk: {analysis['risk_score']:.0f}/100\n"
                    f"TP ladder: +30% → +60% → +100%"
                )

                # Store task so manual sell can cancel it
                if risk_manager.is_enabled(user_id):
                    pm_key = (user_id, token_address)
                    if pm_key in self._position_monitors and not self._position_monitors[pm_key].done():
                        self._position_monitors[pm_key].cancel()
                    self._position_monitors[pm_key] = asyncio.create_task(
                        self._monitor_position_graduated(
                            user_id, token_address, entry_price, received_amount,
                            auto_rebuy=auto_rebuy
                        )
                    )
                else:
                    logger.info(
                        f"Risk manager disabled for user {user_id} - skipping smart auto-exit monitor"
                    )
            else:
                result.update({'status': 'SWAP_FAILED',
                               'error': 'Swap returned no result'})
        except Exception as e:
            logger.error(f"analyze_and_trade error: {e}")
            result.update({'status': 'ERROR', 'error': str(e)})
        return result

    # =========================================================================
    # 2. Token Discovery
    # =========================================================================

    async def discover_trending_tokens(self, chain: str = 'solana',
                                        limit: int = 10) -> List[Dict]:
        """
        Pull active/trending tokens from DexScreener (boosted + volume search) + Birdeye.
        Filters out dead tokens (no volume / no liquidity).
        Returns a deduplicated list sorted by momentum score.
        """
        if chain != 'solana':
            logger.warning(f"Unsupported discovery chain '{chain}' requested; forcing Solana")
            chain = 'solana'

        tokens: Dict[str, Dict] = {}
        chain_id = 'solana'

        def _parse_pair(p: Dict, source: str) -> Optional[Dict]:
            """Extract token dict from a DexScreener pair object."""
            if p.get('chainId') != chain_id:
                return None
            addr = (p.get('baseToken') or {}).get('address', '')
            if not addr:
                return None
            volume_24h = float(p.get('volume', {}).get('h24', 0) or 0)
            liquidity  = float((p.get('liquidity') or {}).get('usd', 0) or 0)
            # Skip dead/low-activity tokens
            if volume_24h < MIN_VOLUME_USD or liquidity < MIN_LIQUIDITY_USD:
                return None
            return {
                'address': addr,
                'symbol': (p.get('baseToken') or {}).get('symbol', '?'),
                'price_change_1h': float((p.get('priceChange') or {}).get('h1', 0) or 0),
                'price_change_6h': float((p.get('priceChange') or {}).get('h6', 0) or 0),
                'price_change_24h': float((p.get('priceChange') or {}).get('h24', 0) or 0),
                'volume_24h': volume_24h,
                'liquidity_usd': liquidity,
                'fdv': float(p.get('fdv', 0) or 0),
                'age_hours': ((time.time() * 1000 - (p.get('pairCreatedAt') or 0)) / 3_600_000
                              if p.get('pairCreatedAt') else 999),
                'source': source,
                'chain': chain,
            }

        async with aiohttp.ClientSession() as session:

            # ── Source 1: DexScreener boosted tokens (actively promoted = high activity) ──
            try:
                async with session.get(DEXSCREENER_BOOSTED, timeout=6) as resp:
                    if resp.status == 200:
                        items = await resp.json()
                        items = items if isinstance(items, list) else []
                        addrs = [i.get('tokenAddress', '') for i in items
                                 if i.get('chainId') == chain_id][:30]
                        if addrs:
                            # Batch lookup — up to 30 addresses
                            addr_csv = ','.join(addrs)
                            async with session.get(
                                f"https://api.dexscreener.com/latest/dex/tokens/{addr_csv}",
                                timeout=8
                            ) as resp2:
                                if resp2.status == 200:
                                    d2 = await resp2.json()
                                    for p in (d2.get('pairs') or []):
                                        tok = _parse_pair(p, 'dexscreener_boost')
                                        if tok and tok['address'] not in tokens:
                                            tokens[tok['address']] = tok
            except Exception as e:
                logger.warning(f"DexScreener boosted error: {e}")

            # ── Source 2: DexScreener volume search — top pairs by 24h volume ──
            try:
                async with session.get(
                    DEXSCREENER_SEARCH,
                    params={'q': chain_id},
                    timeout=6
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get('pairs') or []
                        # Sort by 24h volume descending
                        pairs.sort(
                            key=lambda p: float((p.get('volume') or {}).get('h24', 0) or 0),
                            reverse=True
                        )
                        for p in pairs[:40]:
                            tok = _parse_pair(p, 'dexscreener_vol')
                            if tok and tok['address'] not in tokens:
                                tokens[tok['address']] = tok
            except Exception as e:
                logger.warning(f"DexScreener search error: {e}")

        # ── Source 3: Birdeye trending (Solana only) ──────────────────────────
        if chain == 'solana':
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"X-API-KEY": BIRDEYE_API_KEY}
                    async with session.get(
                        BIRDEYE_TRENDING,
                        headers=headers,
                        params={"sort_by": "v24hUSD", "sort_type": "desc", "limit": 20},
                        timeout=6
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # Birdeye returns data.items[] or data.data.items[]
                            items = (data.get('data') or {})
                            items = items.get('items') or items.get('tokens') or []
                            for item in items:
                                addr    = item.get('address', '')
                                vol     = float(item.get('v24hUSD', 0) or 0)
                                liq     = float(item.get('liquidity', 0) or 0)
                                if not addr or vol < MIN_VOLUME_USD or liq < MIN_LIQUIDITY_USD:
                                    continue
                                if addr not in tokens:
                                    tokens[addr] = {
                                        'address': addr,
                                        'symbol': item.get('symbol', '?'),
                                        'price_change_24h': float(item.get('priceChange24hPercent', 0) or 0),
                                        'volume_24h': vol,
                                        'liquidity_usd': liq,
                                        'source': 'birdeye',
                                        'chain': 'solana',
                                    }
            except Exception as e:
                logger.warning(f"Birdeye trending error: {e}")

        # Score and sort
        scored = []
        for tok in tokens.values():
            tok['momentum_score'] = self._quick_momentum_score(tok)
            scored.append(tok)

        scored.sort(key=lambda x: x['momentum_score'], reverse=True)
        return scored[:limit]

    async def discover_new_pump_fun_tokens(self, limit: int = 10) -> List[Dict]:
        """Fetch newly listed Solana tokens from Birdeye (replaces pump.fun Heroku relay)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    BIRDEYE_NEW_TOKENS,
                    params={"limit": limit, "min_liquidity": 10000},
                    headers={"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"},
                    timeout=10,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = (data.get('data') or {}).get('items') or []
                        results = []
                        for item in items:
                            results.append({
                                'address':       item.get('address', ''),
                                'symbol':        item.get('symbol', '?'),
                                'name':          item.get('name', ''),
                                'volume_24h':    float(item.get('v24hUSD', 0) or 0),
                                'liquidity_usd': float(item.get('liquidity', 0) or 0),
                                'age_hours':     0.5,  # new listing, treat as very new
                                'source':        'birdeye_new',
                                'chain':         'solana',
                            })
                        return results
        except Exception as e:
            logger.warning(f"Birdeye new listings error: {e}")
        return []

    # =========================================================================
    # 3. Auto-Trading Mode
    # =========================================================================

    def is_auto_trading(self, user_id: int) -> bool:
        """Return True if auto-copy trading is running for this user."""
        task = self._auto_copy_tasks.get(user_id)
        return task is not None and not task.done()

    async def start_auto_trading(self, user_id: int,
                                  max_trades_per_cycle: int = 2,
                                  trade_percent: float = 10.0):
        """
        Start the autonomous copy-trading loop for a user.
        Each cycle: rank whales by win_rate × avg_profit → activate/pause accordingly.
        """
        if self.is_auto_trading(user_id):
            logger.info(f"Auto-copy trading already running for user {user_id}")
            return

        task = asyncio.create_task(
            self._auto_copy_loop(user_id, max_trades_per_cycle, trade_percent)
        )
        self._auto_copy_tasks[user_id] = task
        db.save_auto_trade_settings(user_id, True, trade_percent, max_trades_per_cycle)
        logger.info(f"🤖 Auto-copy trading started for user {user_id}")

        await notification_engine.notify_user(
            user_id,
            f"🤖 **Auto-Trade Activated! (Solana)**\n\n"
            f"Monitoring watched whales and automatically following\n"
            f"the best performers based on win rate × average profit.\n\n"
            f"• Rankings re-run every 6 hours\n"
            f"• Underperforming whales are paused automatically\n"
            f"• New whales stay active until they build a track record\n\n"
            f"Add whale wallets via **Copy Trade** to get started.\n"
            f"Use **Stop Auto-Trade** to pause at any time."
        )

    def stop_auto_trading(self, user_id: int):
        """Stop the auto-copy trading loop for a user."""
        task = self._auto_copy_tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()
        db.save_auto_trade_settings(user_id, False)
        logger.info(f"🛑 Auto-copy trading stopped for user {user_id}")

    # ── Auto Smart Trade ───────────────────────────────────────────────────────

    def is_auto_smart_trading(self, user_id: int) -> bool:
        task = self._auto_smart_tasks.get(user_id)
        return task is not None and not task.done()

    async def start_auto_smart_trading(self, user_id: int, trade_percent: float = 10.0, max_positions: int = 4):
        if self.is_auto_smart_trading(user_id):
            return
        task = asyncio.create_task(self._auto_smart_loop(user_id, trade_percent, max_positions))
        self._auto_smart_tasks[user_id] = task
        db.save_auto_smart_settings(user_id, True, trade_percent, max_positions)
        logger.info(f"🤖 Auto-Smart trading started for user {user_id}")
        await notification_engine.notify_user(
            user_id,
            f"🤖 **Auto Smart Trade Activated!**\n\n"
            f"Scanning DexScreener + Birdeye every 30 min.\n"
            f"Buying top momentum tokens with Kelly sizing.\n"
            f"TP ladder: +30% → +60% → +100%\n"
            f"Trailing stop after first TP, hard stop -20%.\n"
            f"Re-buys if token still shows strong momentum after exit.\n\n"
            f"Trade size: {trade_percent}% per position | Max positions: {max_positions}"
        )

    def stop_auto_smart_trading(self, user_id: int):
        task = self._auto_smart_tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()
        db.save_auto_smart_settings(user_id, False)
        logger.info(f"🛑 Auto-Smart trading stopped for user {user_id}")

    async def recover_auto_traders(self):
        """
        Called once on bot startup — re-starts auto-trading for any users
        who had it enabled before the last restart.
        """
        # Recover auto-copy traders
        active = db.get_active_auto_traders()
        for row in active:
            uid  = row['user_id']
            pct  = row.get('trade_percent', 20.0)
            mtpc = row.get('max_trades_per_cycle', 2)
            if not self.is_auto_trading(uid):
                task = asyncio.create_task(self._auto_copy_loop(uid, mtpc, pct))
                self._auto_copy_tasks[uid] = task
                logger.info(f"♻️  Auto-copy trading recovered for user {uid}")
        if active:
            logger.info(f"♻️  Recovered auto-copy trading for {len(active)} user(s)")

        # Recover auto-smart traders
        smart_active = db.get_active_auto_smart_traders()
        for row in smart_active:
            uid  = row['user_id']
            pct  = row.get('trade_percent', 10.0)
            mpos = row.get('max_positions', 4)
            if not self.is_auto_smart_trading(uid):
                task = asyncio.create_task(self._auto_smart_loop(uid, pct, mpos))
                self._auto_smart_tasks[uid] = task
                logger.info(f"♻️  Auto-smart trading recovered for user {uid}")
        if smart_active:
            logger.info(f"♻️  Recovered auto-smart trading for {len(smart_active)} user(s)")

    async def _auto_copy_loop(self, user_id: int,
                                max_trades: int, trade_percent: float):
        """
        Whale-driven auto-copy loop.

        Each cycle:
          1. Rank all watched whales by (win_rate × avg_profit) over last 30 days.
          2. Activate copy-monitoring for top performers; pause underperformers.
          3. If a user has no trade history yet (new whales), keep all active.
          4. Every WHALE_RANK_INTERVAL seconds re-rank and rebalance.
        """
        from trading.copy_trader import copy_trader

        WHALE_RANK_INTERVAL = SMART_WHALE_RANK_INTERVAL
        MIN_ACTIVE_SCORE    = SMART_MIN_ACTIVE_SCORE
        MAX_ACTIVE_WHALES   = SMART_MAX_ACTIVE_WHALES
        MIN_TRADES_TO_RANK  = SMART_MIN_TRADES_TO_RANK

        _CHAIN_ENGINE = {
            'solana': copy_trader,
        }

        consecutive_errors = 0

        while True:
            try:
                user = db.get_user(user_id)
                if not user:
                    break

                uid = user['user_id']

                # ── 1. Rank watched whales ────────────────────────────────────
                rankings = db.get_whale_rankings(
                    user_id, min_trades=MIN_TRADES_TO_RANK, lookback_days=SMART_WHALE_LOOKBACK_DAYS
                )
                all_watched = db.get_watched_wallets(user_id)

                # Whales with enough history → rank them
                ranked_addrs  = {r['watched_wallet'] for r in rankings}
                # Whales without enough history → keep active (trial period)
                unranked      = [w for w in all_watched
                                 if w['wallet_address'] not in ranked_addrs]

                # Top N by score
                top_ranked    = [r for r in rankings if r['score'] >= MIN_ACTIVE_SCORE]
                top_ranked    = top_ranked[:MAX_ACTIVE_WHALES - len(unranked)]
                top_addrs     = {r['watched_wallet'] for r in top_ranked}

                # Underperformers: ranked but score too low
                under_addrs   = {r['watched_wallet'] for r in rankings
                                 if r['score'] < MIN_ACTIVE_SCORE}

                # ── 2. Activate / pause accordingly ──────────────────────────
                activated, paused = 0, 0
                for whale in all_watched:
                    addr    = whale['wallet_address']
                    chain   = whale.get('chain', 'solana')
                    engine  = _CHAIN_ENGINE.get(chain)
                    if not engine:
                        continue

                    if addr in under_addrs and not whale.get('is_paused'):
                        # Pause underperformer
                        db.pause_watched_wallet(
                            whale['id'],
                            f"[AutoRank] score below {MIN_ACTIVE_SCORE:.1f} — paused"
                        )
                        engine.stop_monitoring_for_user(user_id)
                        paused += 1
                        logger.info(f"[AutoRank] ⏸ Paused underperformer {addr[:12]}… ({chain})")

                    elif (addr in top_addrs or addr in {w['wallet_address'] for w in unranked}):
                        if whale.get('is_paused'):
                            # Re-activate previously paused whale that improved
                            db.resume_watched_wallet(whale['id'])
                        await engine.start_monitoring_for_user(user_id)
                        activated += 1

                # ── 3. Notify user of ranking results ─────────────────────────
                if rankings:
                    lines = ["🐋 **Auto-Trade: Whale Rankings Updated**\n"]
                    for i, r in enumerate(rankings[:5], 1):
                        status = "✅" if r['watched_wallet'] in top_addrs else "⏸"
                        lines.append(
                            f"{status} #{i} `{r['watched_wallet'][:14]}…` [{r['chain'].upper()}]\n"
                            f"   Win: {r['win_rate']*100:.0f}%  Avg: {r['avg_profit']:+.1f}%  "
                            f"Trades: {r['total_trades']}"
                        )
                    if unranked:
                        lines.append(f"\n🆕 {len(unranked)} new whale(s) on trial (not enough history yet)")
                    if paused:
                        lines.append(f"⏸ {paused} whale(s) paused — poor performance")
                    await notification_engine.notify_user(user_id, "\n".join(lines))
                else:
                    # No history yet — just make sure all watched wallets are monitored
                    for whale in all_watched:
                        chain  = whale.get('chain', 'solana')
                        engine = _CHAIN_ENGINE.get(chain)
                        if engine:
                            await engine.start_monitoring_for_user(user_id)
                    if all_watched:
                        await notification_engine.notify_user(
                            user_id,
                            f"🐋 **Auto-Trade Active**\n\n"
                            f"Monitoring {len(all_watched)} whale(s) — building performance history.\n"
                            f"Rankings will appear after each whale has {MIN_TRADES_TO_RANK}+ closed trades."
                        )
                    else:
                        await notification_engine.notify_user(
                            user_id,
                            "🐋 **Auto-Trade Active**\n\n"
                            "No whale wallets found. Add whales via **Copy Trade** and auto-trade\n"
                            "will automatically follow the best performers."
                        )

                logger.info(
                    f"[AutoRank] cycle done for user {user_id} — "
                    f"active={len(top_addrs)+len(unranked)} paused={paused} "
                    f"next rerank in {WHALE_RANK_INTERVAL//3600}h"
                )
                consecutive_errors = 0
                await asyncio.sleep(WHALE_RANK_INTERVAL)

            except asyncio.CancelledError:
                logger.info(f"[AutoCopy] loop cancelled for user {user_id}")
                break
            except Exception as e:
                consecutive_errors += 1
                backoff = min(60 * consecutive_errors, 600)
                logger.error(f"[AutoCopy] loop error for user {user_id} (#{consecutive_errors}): {e}")
                await asyncio.sleep(backoff)

    async def _auto_smart_loop(self, user_id: int, trade_percent: float, max_positions: int):
        """
        Token-scan autonomous loop:
          1. Every 30 minutes: get_suggestions() to discover top tokens
          2. Filter: not already in position, passes safety, momentum >= 65
          3. Buy top N tokens to fill available position slots
          4. Each position monitored with trailing stop + auto-rebuy
        """
        SCAN_INTERVAL = SMART_SCAN_INTERVAL
        consecutive_errors = 0

        while True:
            try:
                # Reload user settings each cycle so live changes take effect
                s = self._get_user_smart_settings(user_id)
                effective_max = int(s['max_positions'])     # user can override
                MIN_SCORE     = int(s['auto_min_score'])    # user can override

                # How many positions are open?
                open_pos = db.get_all_open_positions(user_id)
                open_count = len(open_pos.get('smart', []))
                open_tokens = {p['token_address'] for p in open_pos.get('smart', [])}

                slots = effective_max - open_count
                if slots > 0:
                    suggestions = await self.get_suggestions(user_id)
                    bought = 0
                    for tok in suggestions:
                        if bought >= slots:
                            break
                        addr = tok.get('address', '')
                        if not addr or addr in open_tokens:
                            continue
                        if tok.get('momentum_score', 0) < MIN_SCORE:
                            continue
                        result = await self.analyze_and_trade(
                            user_id, addr,
                            user_trade_percent=trade_percent,
                            auto_rebuy=True,
                        )
                        if result.get('status') == 'SUCCESS':
                            bought += 1
                            open_tokens.add(addr)
                            logger.info(f"[AutoSmart] Bought {addr[:10]}… ({bought}/{slots} slots)")

                consecutive_errors = 0
                await asyncio.sleep(SCAN_INTERVAL)

            except asyncio.CancelledError:
                logger.info(f"[AutoSmart] loop cancelled for user {user_id}")
                break
            except Exception as e:
                consecutive_errors += 1
                backoff = min(60 * consecutive_errors, 300)
                logger.error(f"[AutoSmart] error for user {user_id}: {e}")
                await asyncio.sleep(backoff)

    # =========================================================================
    # 4. Suggestion Engine (on-demand scan)
    # =========================================================================

    async def get_suggestions(self, user_id: int,
                               chain: str = 'solana') -> List[Dict]:
        """
        Run a fresh discovery scan and return scored token suggestions.
        Safety checks run in a thread pool so they never block the event loop.
        Total timeout: 8 seconds.
        """
        if chain != 'solana':
            logger.warning(f"Unsupported suggestion chain '{chain}' requested; forcing Solana")
            chain = 'solana'

        import concurrent.futures

        user = db.get_user(user_id)
        open_pos = db.get_all_open_positions(user['user_id']) if user else {'smart': []}
        open_tokens = {p['token_address'] for p in open_pos.get('smart', [])}
        blacklist   = self._get_blacklist(user_id)

        # Discovery — already async, cap at 8 s total
        try:
            candidates = await asyncio.wait_for(
                self.discover_trending_tokens(chain, limit=15), timeout=6
            )
        except asyncio.TimeoutError:
            candidates = []

        if chain == 'solana':
            try:
                pump_new = await asyncio.wait_for(
                    self.discover_new_pump_fun_tokens(limit=5), timeout=3
                )
                candidates += pump_new
            except asyncio.TimeoutError:
                pass

        # Pre-filter by momentum before the expensive safety checks
        pre = []
        for tok in candidates:
            addr = tok.get('address', '')
            if not addr or addr in blacklist or addr in open_tokens:
                continue
            momentum = tok.get('momentum_score') or self._quick_momentum_score(tok)
            if momentum >= AUTO_TRADE_MIN_SCORE:
                pre.append({**tok, 'momentum_score': momentum})

        pre.sort(key=lambda x: x['momentum_score'], reverse=True)
        # Only safety-check the top 8 — avoids hitting 20+ APIs sequentially
        to_check = pre[:8]

        def _safe_check(tok):
            addr = tok['address']
            try:
                analysis = token_analyzer.analyze_token(addr, chain=chain)
                return tok, analysis.get('risk_score', 100), analysis.get('trade_recommendation', 'ANALYZE')
            except Exception:
                return tok, 100, 'UNKNOWN'

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            raw_futures = [loop.run_in_executor(pool, _safe_check, t) for t in to_check]
            # 10s per-task timeout so a hung API call never blocks the pool
            done_results = await asyncio.gather(
                *[asyncio.wait_for(f, timeout=10) for f in raw_futures],
                return_exceptions=True
            )

        results = []
        whitelist = self._get_whitelist(user_id)
        for item in done_results:
            if isinstance(item, Exception):
                continue
            tok, risk, rec = item
            if risk > 70 and tok['address'] not in whitelist:
                continue
            results.append({
                **tok,
                'risk_score': risk,
                'recommendation': rec,
                'combined_score': tok.get('momentum_score', 0) * (1 - risk / 200),
            })

        results.sort(key=lambda x: x['combined_score'], reverse=True)
        return results[:10]

    def get_cached_suggestions(self, user_id: int) -> List[Dict]:
        """Return suggestions from the last auto-trade cycle (instant, no API call)."""
        return self._pending_suggestions.get(user_id, [])

    # =========================================================================
    # 5. Blacklist / Whitelist
    # =========================================================================

    def _get_blacklist(self, user_id: int) -> set:
        if user_id not in self._blacklist:
            self._blacklist[user_id] = db.get_token_list(user_id, 'blacklist')
        return self._blacklist[user_id]

    def _get_whitelist(self, user_id: int) -> set:
        if user_id not in self._whitelist:
            self._whitelist[user_id] = db.get_token_list(user_id, 'whitelist')
        return self._whitelist[user_id]

    def blacklist_token(self, user_id: int, token_address: str):
        self._get_blacklist(user_id).add(token_address)
        db.add_to_token_list(user_id, 'blacklist', token_address)
        # Remove from whitelist if present
        self._get_whitelist(user_id).discard(token_address)
        db.remove_from_token_list(user_id, 'whitelist', token_address)
        logger.info(f"Blacklisted {token_address[:10]} for user {user_id}")

    def whitelist_token(self, user_id: int, token_address: str):
        self._get_whitelist(user_id).add(token_address)
        db.add_to_token_list(user_id, 'whitelist', token_address)
        # Remove from blacklist if present
        self._get_blacklist(user_id).discard(token_address)
        db.remove_from_token_list(user_id, 'blacklist', token_address)
        logger.info(f"Whitelisted {token_address[:10]} for user {user_id}")

    def remove_from_blacklist(self, user_id: int, token_address: str):
        self._get_blacklist(user_id).discard(token_address)
        db.remove_from_token_list(user_id, 'blacklist', token_address)

    # =========================================================================
    # 6. Position monitoring — graduated TP ladder
    # =========================================================================

    def _get_user_smart_settings(self, user_id: int) -> dict:
        """Load per-user smart trade preferences, falling back to global config defaults."""
        return {
            'hard_stop_loss':    db.get_user_setting(user_id, 'hard_stop_loss',    HARD_STOP_LOSS),
            'trailing_stop_pct': db.get_user_setting(user_id, 'trailing_stop_pct', SMART_TRAILING_STOP_PCT),
            'max_hold_hours':    db.get_user_setting(user_id, 'max_hold_hours',    MAX_HOLD_HOURS),
            'auto_min_score':    db.get_user_setting(user_id, 'auto_min_score',    AUTO_TRADE_MIN_SCORE),
            'max_positions':     db.get_user_setting(user_id, 'max_positions',     MAX_OPEN_POSITIONS),
            'tp1_threshold':     db.get_user_setting(user_id, 'tp1_threshold',     TP_LADDER[0][0]),
            'tp1_fraction':      db.get_user_setting(user_id, 'tp1_fraction',      TP_LADDER[0][1]),
            'tp2_threshold':     db.get_user_setting(user_id, 'tp2_threshold',     TP_LADDER[1][0]),
            'tp2_fraction':      db.get_user_setting(user_id, 'tp2_fraction',      TP_LADDER[1][1]),
            'tp3_threshold':     db.get_user_setting(user_id, 'tp3_threshold',     TP_LADDER[2][0]),
            'tp3_fraction':      db.get_user_setting(user_id, 'tp3_fraction',      TP_LADDER[2][1]),
        }

    async def _monitor_position_graduated(
        self,
        user_id: int,
        token_address: str,
        entry_price: float,
        token_amount: float,
        trailing_stop_pct: float = None,
        auto_rebuy: bool = False,
    ):
        """
        Monitor with a 3-level take-profit ladder + trailing stop + hard stop + time-decay.
        All thresholds loaded from per-user settings at position open time.
        """
        # Load user-specific settings once at open (consistent during trade lifetime)
        s = self._get_user_smart_settings(user_id)
        hard_stop   = float(s['hard_stop_loss'])
        trail_pct   = float(trailing_stop_pct if trailing_stop_pct is not None else s['trailing_stop_pct'])
        max_hold_h  = float(s['max_hold_hours'])
        tp_ladder   = [
            (float(s['tp1_threshold']), float(s['tp1_fraction'])),
            (float(s['tp2_threshold']), float(s['tp2_fraction'])),
            (float(s['tp3_threshold']), float(s['tp3_fraction'])),
        ]

        remaining      = token_amount
        tp_idx         = 0
        trailing_active = False   # activated after first TP hit
        peak_price     = entry_price or 1e-12
        start_time     = time.time()
        user_trade_percent = 5.0  # used for rebuy sizing

        logger.info(
            f"📊 [Graduated] monitoring {token_address[:10]}…  "
            f"entry={entry_price:.8f}  amount={token_amount:.4f}  "
            f"stop={hard_stop*100:.0f}%  trail={trail_pct*100:.0f}%  "
            f"TP={tp_ladder[0][0]*100:.0f}/{tp_ladder[1][0]*100:.0f}/{tp_ladder[2][0]*100:.0f}%"
        )

        while remaining > 0:
            try:
                if not risk_manager.is_enabled(user_id):
                    logger.info(
                        f"Risk manager disabled for user {user_id} - stopping smart monitor {token_address[:10]}"
                    )
                    return

                elapsed_h = (time.time() - start_time) / 3600
                if elapsed_h >= max_hold_h:
                    await self._exit_smart_position(user_id, token_address, remaining, 'time_decay')
                    if auto_rebuy:
                        asyncio.create_task(self._consider_rebuy(user_id, token_address, entry_price, user_trade_percent=user_trade_percent))
                    return

                current_price = await self._get_token_price(token_address)
                if not current_price or current_price <= 0:
                    await asyncio.sleep(POSITION_CHECK_INTERVAL)
                    continue

                pnl_pct    = (current_price - entry_price) / entry_price if entry_price else 0
                peak_price = max(peak_price, current_price)

                # Hard stop (user-configured)
                if pnl_pct <= hard_stop:
                    logger.info(f"🛑 Stop-loss {pnl_pct*100:.1f}% — exiting {token_address[:10]}")
                    await self._exit_smart_position(user_id, token_address, remaining, 'stop_loss')
                    await notification_engine.notify_user(
                        user_id,
                        f"🛑 **Stop-Loss Hit**\n`{token_address[:12]}…`\n"
                        f"Loss: {pnl_pct*100:.1f}%  (limit: {hard_stop*100:.0f}%)"
                    )
                    if auto_rebuy:
                        asyncio.create_task(self._consider_rebuy(user_id, token_address, entry_price, user_trade_percent=user_trade_percent))
                    return

                # TP ladder (user-configured thresholds)
                if tp_idx < len(tp_ladder):
                    tp_threshold, sell_frac = tp_ladder[tp_idx]
                    if pnl_pct >= tp_threshold:
                        sell_qty       = remaining * sell_frac
                        next_remaining = remaining - sell_qty
                        tp_idx        += 1
                        logger.info(
                            f"💰 TP level {tp_idx}: sell {sell_frac*100:.0f}% "
                            f"at +{pnl_pct*100:.1f}%  remaining={next_remaining:.4f}"
                        )
                        confirmed = await self._partial_exit_smart(
                            user_id, token_address, sell_qty, next_remaining
                        )
                        if confirmed:
                            remaining = next_remaining
                            await notification_engine.notify_user(
                                user_id,
                                f"💰 **Take-Profit Level {tp_idx}!**\n"
                                f"`{token_address[:12]}…`\n"
                                f"Profit: +{pnl_pct*100:.1f}%  |  Sold {sell_frac*100:.0f}%\n"
                                f"Still holding {remaining:.2f} tokens"
                            )
                            if tp_idx >= len(tp_ladder):
                                return  # all sold
                        else:
                            tp_idx -= 1  # retry this level next cycle

                # Activate trailing stop once first TP is hit
                if tp_idx >= 1:
                    trailing_active = True

                # Trailing stop — exit remaining if price drops trail_pct from peak
                if trailing_active and peak_price > 0:
                    drawdown = (peak_price - current_price) / peak_price
                    if drawdown >= trail_pct:
                        logger.info(f"📉 [Smart] Trailing stop {drawdown*100:.1f}% from peak — exiting {token_address[:10]}")
                        await self._exit_smart_position(user_id, token_address, remaining, 'trailing_stop')
                        await notification_engine.notify_user(
                            user_id,
                            f"📉 **Trailing Stop Hit**\n`{token_address[:12]}…`\n"
                            f"Dropped {drawdown*100:.1f}% from peak\n"
                            f"PnL: {pnl_pct*100:+.1f}%"
                        )
                        if auto_rebuy:
                            asyncio.create_task(self._consider_rebuy(user_id, token_address, entry_price, user_trade_percent=user_trade_percent))
                        return

                await asyncio.sleep(POSITION_CHECK_INTERVAL)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Graduated monitor error: {e}")
                await asyncio.sleep(POSITION_CHECK_INTERVAL)

    async def _partial_exit_smart(self, user_id: int, token_address: str,
                                   sell_qty: float, remaining: float) -> bool:
        """Sell part of a smart trade position. Returns True only if swap confirmed."""
        try:
            keypair     = self._get_user_keypair(user_id)
            sell_result = await swapper.execute_swap(
                token_address, WSOL_MINT, sell_qty, 'jupiter', keypair=keypair
            )
            if not sell_result or sell_result.get('status') not in ('confirmed', 'quoted'):
                logger.warning(f"[Smart] Partial exit swap not confirmed — DB not updated")
                return False
            sol_received = sell_result.get('expectedOutput', 0)
            # Update remaining amount in DB only after confirmed swap
            db.update_pending_trade_token_amount(user_id, token_address, remaining)
            logger.info(
                f"[Smart] Partial exit: {sell_qty:.4f} tokens → {sol_received:.4f} SOL  "
                f"sig={sell_result.get('signature','n/a')[:20]}"
            )
            return True
        except Exception as e:
            logger.error(f"Partial exit error: {e}")
            return False

    async def _exit_smart_position(self, user_id: int, token_address: str,
                                    amount: float, reason: str):
        """Full exit of a smart trade position."""
        try:
            open_trade = db.get_pending_trade_by_token(user_id, token_address)
            keypair     = self._get_user_keypair(user_id)
            sell_result = await swapper.execute_swap(
                token_address, WSOL_MINT, amount, 'jupiter', keypair=keypair
            )
            if not sell_result or sell_result.get('status') not in ('confirmed', 'quoted'):
                logger.error(f"[Smart] Full exit swap failed [{reason}]")
                return
            sol_received = sell_result.get('expectedOutput', 0)
            tx_sig       = sell_result.get('signature', 'n/a')
            db.update_pending_trade_closed(user_id, token_address, sol_received, tx_sig)
            if open_trade and open_trade.get('sol_spent'):
                sol_spent = float(open_trade.get('sol_spent') or 0)
                profit_pct = ((sol_received - sol_spent) / sol_spent * 100) if sol_spent > 0 else 0
                enhanced_features.record_daily_loss(user_id, profit_pct)
                enhanced_features.record_trade_result(user_id, profit_pct > 0)
            logger.info(
                f"[Smart] Full exit [{reason}]: {amount:.4f} tokens → {sol_received:.4f} SOL  "
                f"sig={tx_sig[:20]}"
            )
        except Exception as e:
            logger.error(f"Full exit error: {e}")

    async def _consider_rebuy(self, user_id: int, token_address: str,
                               prev_entry_price: float, user_trade_percent: float = 10.0,
                               cooldown_seconds: int = SMART_REBUY_COOLDOWN):
        """
        After a position closes, wait cooldown then re-analyze.
        Re-buy only if momentum is still strong and risk is acceptable.
        """
        await asyncio.sleep(cooldown_seconds)
        try:
            # Don't re-enter if already in position
            existing = db.get_pending_trade_by_token(user_id, token_address)
            if existing:
                return

            momentum = await self._score_momentum(token_address)
            if momentum < SMART_REBUY_MIN_MOMENTUM:
                logger.info(f"[Rebuy] {token_address[:10]} momentum {momentum} < {SMART_REBUY_MIN_MOMENTUM} — skip")
                return

            analysis = await asyncio.to_thread(
                token_analyzer.analyze_token, token_address
            )
            risk = analysis.get('risk_score', 100)
            rec = analysis.get('trade_recommendation', '')
            if risk > SMART_REBUY_MAX_RISK or rec in ('REJECT_HONEYPOT', 'REJECT_CONCENTRATED', 'REJECT_TOO_RISKY'):
                logger.info(f"[Rebuy] {token_address[:10]} risk {risk} too high — skip")
                return

            logger.info(f"[Rebuy] {token_address[:10]} still shows promise (momentum={momentum}, risk={risk}) — re-entering")
            await notification_engine.notify_user(
                user_id,
                f"🔄 **Re-entry Signal**\n`{token_address[:12]}…`\n"
                f"Momentum: {momentum}/100  |  Risk: {risk}/100\n"
                f"Re-buying after position closed."
            )
            await self.analyze_and_trade(
                user_id, token_address,
                user_trade_percent=user_trade_percent,
                auto_rebuy=True,
            )
        except Exception as e:
            logger.error(f"[Rebuy] error for {token_address[:10]}: {e}")

    # =========================================================================
    # 7. Sizing — Kelly Criterion
    # =========================================================================

    def _get_historical_stats(self, user_id: int) -> tuple:
        """
        Returns (win_rate, avg_win_pct, avg_loss_pct) from closed smart_trades.
        Falls back to (0.5, 0.40, 0.20) if insufficient data (<5 trades).
        """
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT profit_percent FROM smart_trades
                WHERE user_id = ? AND is_closed = 1 AND profit_percent IS NOT NULL
                ORDER BY closed_at DESC LIMIT 50
            ''', (user_id,))
            rows = [r[0] for r in cursor.fetchall()]
            conn.close()

            if len(rows) < 5:
                return (0.50, 0.40, 0.20)  # defaults

            wins   = [r for r in rows if r > 0]
            losses = [abs(r) for r in rows if r <= 0]
            win_rate  = len(wins) / len(rows)
            avg_win   = (sum(wins) / len(wins) / 100) if wins else 0.40
            avg_loss  = (sum(losses) / len(losses) / 100) if losses else 0.20
            return (win_rate, max(0.05, avg_win), max(0.02, avg_loss))
        except Exception as e:
            logger.error(f"_get_historical_stats error: {e}")
            return (0.50, 0.40, 0.20)

    def _calculate_trade_amount_kelly(
        self,
        wallet_balance: float,
        user_selected_percent: float,
        analyzer_suggested_percent: float,
        risk_score: float,
        momentum_score: float,
        user_id: int = 0,
    ) -> float:
        """
        Kelly fraction based on real historical win/loss data.
        Capped at user's selected% and analyzer's suggested%.
        """
        try:
            user_pct    = max(5.0, min(MAX_PCT_PER_TOKEN, user_selected_percent))
            recommended = min(user_pct, analyzer_suggested_percent)

            # Use real historical stats when available
            win_prob, avg_win, avg_loss = self._get_historical_stats(user_id)
            loss_prob = 1 - win_prob

            # Adjust by momentum (higher momentum = slight edge boost)
            momentum_boost = (momentum_score - 50) / 1000   # -0.05 to +0.05
            win_prob = max(0.30, min(0.80, win_prob + momentum_boost))
            loss_prob = 1 - win_prob

            # Risk score penalty
            risk_penalty = risk_score / 500   # 0..0.2
            adj_win = max(0.05, avg_win - risk_penalty)

            if avg_loss > 0:
                kelly_frac = (win_prob * adj_win - loss_prob * avg_loss) / adj_win
                kelly_frac = max(0.02, min(kelly_frac, 0.25))
            else:
                kelly_frac = 0.10

            kelly_pct   = kelly_frac * 100
            final_pct   = min(recommended, kelly_pct)
            trade_amount = max(self.min_trade_amount, (wallet_balance * final_pct) / 100.0)

            logger.info(
                f"Kelly sizing: balance={wallet_balance:.4f}  win_rate={win_prob:.0%}  "
                f"avg_win={avg_win:.0%}  avg_loss={avg_loss:.0%}  "
                f"kelly={kelly_pct:.1f}%  final={final_pct:.1f}%  amount={trade_amount:.4f} SOL"
            )
            return round(trade_amount, 4)
        except Exception as e:
            logger.error(f"Kelly sizing error: {e}")
            return round(wallet_balance * 0.10, 4)

    # =========================================================================
    # 8. Momentum & price helpers
    # =========================================================================

    def _quick_momentum_score(self, tok: Dict) -> int:
        """
        0–100 score based on multi-timeframe price data, volume, liquidity, and token age.
        Used for pre-filter before expensive on-chain analysis.
        """
        score = 50
        change_1h   = float(tok.get('price_change_1h',  0) or 0)
        change_6h   = float(tok.get('price_change_6h',  0) or 0)
        change_24h  = float(tok.get('price_change_24h', 0) or 0)
        volume_24h  = float(tok.get('volume_24h', 0) or 0)
        liquidity   = float(tok.get('liquidity_usd', 0) or 0)
        age_hours   = float(tok.get('age_hours', 999) or 999)  # default old = safe

        # Token age penalty — very new tokens are higher risk
        if 0 < age_hours < 6:
            score -= 20   # less than 6h old — very risky
        elif 6 <= age_hours < 24:
            score -= 10   # less than 1 day
        # birdeye_new/pump.fun exception: they graduate fast, age < 24h is normal
        if tok.get('source') in ('pump.fun', 'birdeye_new'):
            score += 10   # new listing discovery bonus (offset age penalty)

        # Multi-timeframe momentum — weight recent moves more heavily
        if change_1h > 20:    score += 20
        elif change_1h > 10:  score += 15
        elif change_1h > 5:   score += 10
        elif change_1h > 0:   score += 5
        elif change_1h < -10: score -= 15

        if change_6h > 50:    score += 10
        elif change_6h > 20:  score += 7
        elif change_6h > 0:   score += 3
        elif change_6h < -20: score -= 10

        if change_24h > 100:   score += 10
        elif change_24h > 50:  score += 7
        elif change_24h > 20:  score += 4
        elif change_24h < -30: score -= 10

        # Volume signal
        if volume_24h > 5_000_000:   score += 15
        elif volume_24h > 1_000_000: score += 10
        elif volume_24h > 100_000:   score += 5

        # Liquidity (safety proxy)
        if liquidity > 500_000:   score += 8
        elif liquidity > 100_000: score += 4
        elif liquidity < 10_000:  score -= 20

        return max(0, min(100, score))

    async def _score_momentum(self, token_address: str) -> int:
        """Fetch live DexScreener + Birdeye data and score momentum with buy/sell pressure."""
        score = 50
        try:
            async with aiohttp.ClientSession() as session:
                # ── DexScreener: multi-timeframe price data ──
                url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                async with session.get(url, timeout=8) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get('pairs') or []
                        if pairs:
                            p = pairs[0]
                            age_ms = p.get('pairCreatedAt', 0) or 0
                            import time as _t
                            age_hours = (_t.time() * 1000 - age_ms) / 3_600_000 if age_ms else 999
                            score = self._quick_momentum_score({
                                'price_change_1h':  float((p.get('priceChange') or {}).get('h1', 0) or 0),
                                'price_change_6h':  float((p.get('priceChange') or {}).get('h6', 0) or 0),
                                'price_change_24h': float((p.get('priceChange') or {}).get('h24', 0) or 0),
                                'volume_24h':       float((p.get('volume') or {}).get('h24', 0) or 0),
                                'liquidity_usd':    float((p.get('liquidity') or {}).get('usd', 0) or 0),
                                'age_hours':        age_hours,
                            })

                # ── Birdeye: buy/sell pressure (trade count ratio) ──
                if BIRDEYE_API_KEY:
                    try:
                        async with session.get(
                            f"https://public-api.birdeye.so/defi/token_overview",
                            params={"address": token_address},
                            headers={"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"},
                            timeout=6,
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                overview = (data.get('data') or {})
                                buy1h  = int(overview.get('buy1h',  0) or 0)
                                sell1h = int(overview.get('sell1h', 0) or 0)
                                total  = buy1h + sell1h
                                if total > 10:
                                    buy_ratio = buy1h / total
                                    if buy_ratio > 0.70:   score += 15  # strong buy pressure
                                    elif buy_ratio > 0.55: score += 8
                                    elif buy_ratio < 0.35: score -= 10  # strong sell pressure
                    except Exception:
                        pass  # Birdeye is optional

        except Exception as e:
            logger.warning(f"Momentum score error for {token_address[:10]}: {e}")
        return max(0, min(100, score))

    async def _get_token_price(self, token_address: str) -> Optional[float]:
        """Get current token price in SOL per token via Jupiter best-price quote."""
        try:
            price_info = await swapper.get_best_price(WSOL_MINT, token_address, 1.0)
            if price_info and price_info.get('price'):
                tokens_per_sol = float(price_info['price'])
                if tokens_per_sol > 0:
                    return 1.0 / tokens_per_sol  # SOL per token
        except Exception as e:
            logger.error(f"Price fetch error: {e}")
        return None

    # ── Legacy compat (used by position monitor in old flow) ──────────────────
    async def monitor_position_for_profit(self, user_id, token_address,
                                           entry_tx, profit_target=0.30):
        """Redirect to graduated monitor (backwards compat)."""
        pos = db.get_pending_trade_by_token(user_id, token_address)
        entry_price  = pos.get('entry_price', 0) if pos else 0
        token_amount = pos.get('token_amount', 0) if pos else 0
        await self._monitor_position_graduated(
            user_id, token_address, entry_price, token_amount
        )


# Singleton
smart_trader = SmartTrader()
