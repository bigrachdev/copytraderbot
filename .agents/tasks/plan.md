# Implementation Plan — Whale-Only Bot Refactor

Generated after reading all five source files in full.

---

## Codebase State Summary

| File | Key findings |
|------|-------------|
| `telegram_broadcaster.py` | 1161 lines. Has 4 background loops (`_news_loop`, `_market_update_loop`, `_self_ad_loop`, `_launch_update_loop`), all started from `initialize()`. The existing signal broadcaster is `broadcast_signal()` not `post_trade_signal()` — rename required. Current copy-trade caller is `_broadcast_copy_signal()` in `copy_trader.py` which calls `tg_broadcaster.broadcast_signal(signal_data)`. |
| `copy_trader.py` | `_is_whale_qualified()` checks `WHALE_MIN_TRADES` and `WHALE_MIN_WIN_RATE` from config. `_register_signal()` drives multiplier via `enhanced_features.get_signal_multiplier_enhanced()`. `execute_copy_trade()` → `_broadcast_copy_signal()` → `tg_broadcaster.broadcast_signal()`. Trailing stop monitor is `_monitor_position_trailing()`. |
| `smart_trader.py` | `_monitor_position_graduated()` drives the TP ladder + trailing stop. `discover_new_pump_fun_tokens()` calls `tg_broadcaster.broadcast_token_launch()` — that call must be removed. `SMART_TRAILING_STOP_PCT` (currently `0.15`) is the trailing stop config source. |
| `exit_strategy.py` | `get_dynamic_trailing_stop()` currently returns `0.15` as the hard-coded default when `ENABLE_DYNAMIC_TRAILING_STOP` is `False`. `TRAILING_STOP_LOW_VOL_PCT` is `0.10`. |
| `config.py` | `WHALE_MIN_WIN_RATE = 0.40`, `WHALE_MIN_TRADES = 5`, `COPY_DEFAULT_TRAILING_STOP = 0.15`, `SMART_TRAILING_STOP_PCT = 0.15`, `DAILY_LOSS_LIMIT_PCT = 10.0`, `ENABLE_DAILY_LOSS_LIMIT = False`. None of the new signal-scale or stale-days constants exist yet. |
| `whale_scorer.py` | Exists. Has `check_consecutive_losses(user_id, whale_address) → (bool, int)` and `WHALE_MAX_CONSECUTIVE_LOSSES` (currently `5`). The `score_whale()` method returns `0–100`. |

---

## Task 1 — Strip `telegram_broadcaster.py` to whale-only

### 1.1 — Remove from `__init__`

**Current state:**
```python
self._news_fetch_interval = BROADCAST_NEWS_INTERVAL_MINUTES * 60
self._self_ad_interval = BROADCAST_SELF_AD_INTERVAL_HOURS * 60 * 60
self._market_update_interval = ...
self._top_token_count = ...
self._min_top_token_liquidity = ...
self._news_max_age_hours = ...
self._launch_update_interval = ...
self._launch_max_age_minutes = ...
self._launch_min_liquidity_usd = ...
self._launch_min_solidity_score = ...
self._launch_scan_limit = ...
self._max_token_keywords = ...
self._posted_launches: Dict[str, float] = {}
self._dynamic_token_keywords: Set[str] = set()
self._static_sol_token_keywords: Set[str] = {...}
self._news_sources: List[Dict] = [...]
self._news_sources.extend(self._load_extra_sources_from_env())
logger.info("📰 News sources configured: ...")
```
Also in `__init__`, from DB loading:
```python
self._posted_news = { news_id: time.time() for news_id in db.get_posted_news_ids(hours=48) }
```
And the logger.info that logs liquidity/news/launch thresholds.

**New state:** Remove all of the above. Keep only:
```python
self._max_signals_per_hour = 10
self._max_signals_last_hour: List[float] = []
self._posted_signals: Dict[str, float] = {}   # kept — loaded from DB as before
self._min_liquidity_usd = int(os.getenv('BROADCAST_MIN_LIQUIDITY_USD', '30000'))
```
The DB load for `_posted_signals` stays. The DB load for `_posted_news` is removed.

**Imports to remove from top of file:**
- `import xml.etree.ElementTree as ET`
- `from email.utils import parsedate_to_datetime`
- `from urllib.parse import urlparse`

**Config imports to remove from `from config import ...`:**
- `BROADCAST_NEWS_INTERVAL_MINUTES`
- `BROADCAST_SELF_AD_INTERVAL_HOURS`

### 1.2 — Remove from `initialize()`

**Current state:** `initialize()` calls:
```python
await self.post_self_ad()
await self.post_market_update()
asyncio.create_task(self._market_update_loop())
asyncio.create_task(self._launch_update_loop())
asyncio.create_task(self._news_loop())
asyncio.create_task(self._self_ad_loop())
```

**New state:** Replace all of those with a single heartbeat task:
```python
asyncio.create_task(self._heartbeat_loop())
logger.info("✅ Telegram broadcaster initialized — whale-trade signals only")
```
The bot init/verification (`Bot(token=...)`, `get_chat()`) stays unchanged.

### 1.3 — Add `_heartbeat_loop()`

**Current state:** Does not exist.

**New state:** Add this method:
```python
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
            logger.warning(f"Heartbeat error: {e}")
            await asyncio.sleep(60)
```

### 1.4 — Add `post_whale_trade()`

**Current state:** Does not exist.

**New state:** Add this public method:
```python
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

    # Rate-limit check (reuse existing _check_rate_limits logic inline)
    now = time.time()
    signal_key = f"{token_mint}_{action}"
    signal_hash = hashlib.md5(signal_key.encode()).hexdigest()
    last_seen = self._posted_signals.get(signal_hash, 0)
    if now - last_seen < 15 * 60:
        return False  # deduplicate
    if now - self._last_post_times.get('signal', 0) < 120:
        return False  # 1 per 2 min
    hour_ago = now - 3600
    self._max_signals_last_hour = [t for t in self._max_signals_last_hour if t > hour_ago]
    if len(self._max_signals_last_hour) >= self._max_signals_per_hour:
        return False  # max 10/hr

    self._last_post_times['signal'] = now
    self._max_signals_last_hour.append(now)
    self._posted_signals[signal_hash] = now

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

    whale_short  = f"{whale_address[:6]}...{whale_address[-4:]}"
    mint_short   = f"{token_mint[:6]}...{token_mint[-4:]}"
    action_label = "BUY 🟢" if action.upper() == "BUY" else "SELL 🔴"
    sol_usd      = sol_amount * price_usd if price_usd > 0 else 0

    message = (
        "<b>🐋 Whale Alert</b>\n\n"
        f"<b>Action:</b> {action_label}\n"
        f"<b>Token:</b> <code>${self._safe_text(token_symbol)}</code> "
        f"(<code>{self._safe_text(mint_short)}</code>)\n"
        f"<b>Whale:</b> <code>{self._safe_text(whale_short)}</code>\n"
        f"<b>Score:</b> {whale_score:.1f} | "
        f"<b>Win Rate:</b> {win_rate*100:.0f}% | "
        f"<b>Trades:</b> {total_trades}\n\n"
        f"<b>Amount:</b> {sol_amount:.2f} SOL"
        + (f" (~${sol_usd:,.0f})" if sol_usd > 0 else "")
        + "\n"
        f"<b>Price:</b> ${price_usd:.6f}\n\n"
        f"{strength_emoji} Signal strength: <b>{strength}</b> ({signal_count} whale{'s' if signal_count > 1 else ''} {'buying' if action.upper() == 'BUY' else 'selling'})\n"
        "📊 Copying automatically"
    )

    return await self._send_message(message)
```

### 1.5 — Remove methods entirely (delete from source)

The following methods must be **fully deleted** (method signature + body):
- `post_market_update()`
- `post_self_ad()`
- `broadcast_news()`
- `broadcast_token_launch()`
- `_analyze_launch_candidate()`
- `_news_loop()`
- `_market_update_loop()`
- `_self_ad_loop()`
- `_launch_update_loop()`
- `_fetch_and_post_news()`
- `_fetch_and_post_launch_updates()`
- `_fetch_from_sources()`
- `_fetch_sol_market_data()`
- `_fetch_top_token_performance()`
- `_fetch_new_launch_candidates()`
- `_fetch_token_launch_snapshot()`
- `_fetch_token_pair_snapshot()`
- `_pick_best_solana_pair()`
- `_pair_created_ts()`
- `_classify_launch_risk()`
- `_fmt_pct()`
- `_is_news_recent()`
- `_cleanup_posted_news()`
- `_cleanup_posted_launches()`
- `_parse_feed_items()`
- `_build_news_item()`
- `_load_extra_sources_from_env()`
- `_parse_source_list_env()`
- `_score_news_relevance()`
- `_count_token_keyword_hits()`
- `_all_token_keywords()`
- `_register_dynamic_token_keywords()`
- `_parse_timestamp()`
- `_xml_text()`
- `_clean_link()`

### 1.6 — Keep unchanged

The following methods are kept exactly as-is:
- `broadcast_signal()` — existing method, still valid for generic signal calls
- `broadcast_whale_alert()` — existing method, keep
- `_check_rate_limits()` — keep (used by `broadcast_signal`)
- `_cleanup_posted_signals()` — keep
- `_send_message()` — keep
- `_normalize_channel_id()` — keep
- `_is_placeholder_channel_id()` — keep
- `_safe_text()` — keep
- `_to_float()` — keep (still used by `broadcast_signal` indirectly; safe to keep)
- `_clean_text()` — keep (used by `broadcast_signal`)
- `initialize()` — keep but modify as described in 1.2

### 1.7 — Verify

```
cd c:\Users\user\Desktop\copytradebot
python -c "from trading.telegram_broadcaster import broadcaster; print('OK')"
```
Expected: prints `OK` with no ImportError or AttributeError.

---

## Task 2 — Improve whale selection in `copy_trader.py`

### 2.1 — Tighten `_is_whale_qualified()`

**Current state (lines ~115–145):**
```python
def _is_whale_qualified(self, user_id: int, whale_address: str) -> Tuple[bool, str]:
    qualified, reason = enhanced_features.is_whale_qualified_enhanced(...)
    if not qualified:
        return False, reason

    records = db.get_copy_performance(user_id, whale_address, limit=50)
    closed = [r for r in records if r.get('status') == 'closed' ...]

    if len(closed) < WHALE_MIN_TRADES:
        return True, f"insufficient_history ({len(closed)} trades — allowing)"

    wins = sum(1 for r in closed if r['user_profit_percent'] > 0)
    win_rate = wins / len(closed)
    avg_profit = sum(r['user_profit_percent'] for r in closed) / len(closed)

    if win_rate < WHALE_MIN_WIN_RATE:
        return False, ...
    if avg_profit < WHALE_MIN_AVG_PROFIT:
        return False, ...

    return True, ...
```

**New state:**
```python
def _is_whale_qualified(self, user_id: int, whale_address: str) -> Tuple[bool, str]:
    # Import whale_scorer for consecutive-loss check
    from trading.whale_scorer import whale_scorer

    # Enhanced qualification first
    qualified, reason = enhanced_features.is_whale_qualified_enhanced(user_id, whale_address)
    if not qualified:
        return False, reason

    records = db.get_copy_performance(user_id, whale_address, limit=50)
    closed = [
        r for r in records
        if r.get('status') == 'closed' and r.get('user_profit_percent') is not None
    ]

    # NEW: hard gate — not enough history → REJECT (changed from "allow")
    if len(closed) < WHALE_MIN_TRADES:
        return False, f"insufficient_history ({len(closed)}/{WHALE_MIN_TRADES} trades required)"

    wins = sum(1 for r in closed if r['user_profit_percent'] > 0)
    win_rate = wins / len(closed)
    avg_profit = sum(r['user_profit_percent'] for r in closed) / len(closed)

    if win_rate < WHALE_MIN_WIN_RATE:
        return False, f"win_rate {win_rate:.0%} below {WHALE_MIN_WIN_RATE:.0%} floor"
    if avg_profit < WHALE_MIN_AVG_PROFIT:
        return False, f"avg_profit {avg_profit:.1f}% below {WHALE_MIN_AVG_PROFIT}% floor"

    # NEW: stale whale check — reject if last trade was > WHALE_STALE_DAYS ago
    from config import WHALE_STALE_DAYS
    import time as _time
    most_recent_ts = max(
        (r.get('opened_at') or 0 for r in closed),
        default=0
    )
    if most_recent_ts:
        days_since = (_time.time() - most_recent_ts) / 86400
        if days_since > WHALE_STALE_DAYS:
            return False, f"stale_whale ({days_since:.0f}d since last trade, limit {WHALE_STALE_DAYS}d)"

    # NEW: consecutive losses check via whale_scorer
    should_trade, consec_losses = whale_scorer.check_consecutive_losses(user_id, whale_address)
    if not should_trade:
        return False, f"consecutive_losses ({consec_losses} >= 3 max)"

    return True, f"qualified  win_rate={win_rate:.0%}  avg_profit={avg_profit:.1f}%"
```

**Note on `WHALE_MAX_CONSECUTIVE_LOSSES`:** The task says reject at >3 consecutive losses.
`whale_scorer.py` uses `WHALE_MAX_CONSECUTIVE_LOSSES` from config (currently `5`). Update config (Task 4) to set it to `3`. The `check_consecutive_losses` method already implements `consecutive_losses < WHALE_MAX_CONSECUTIVE_LOSSES` so setting the config to `3` is sufficient — no change needed inside `whale_scorer.py`.

### 2.2 — Improve signal aggregation in `_register_signal()`

**Current state:** Calls `enhanced_features.get_signal_multiplier_enhanced()` to compute multiplier, which is a black-box enhanced function.

**New state:** Override the multiplier after the enhanced call with the explicit 3-tier rule:
```python
# After: return True, unique_count, multiplier
# Replace the last few lines of _register_signal with:

from config import COPY_SIGNAL_SCALE_1_WHALE, COPY_SIGNAL_SCALE_2_WHALE, COPY_SIGNAL_SCALE_3_WHALE

if unique_count >= 3:
    multiplier = COPY_SIGNAL_SCALE_3_WHALE   # 1.5x
elif unique_count == 2:
    multiplier = COPY_SIGNAL_SCALE_2_WHALE   # 1.0x
else:
    multiplier = COPY_SIGNAL_SCALE_1_WHALE   # 0.5x

return True, unique_count, multiplier
```
This replaces the single `multiplier = enhanced_features.get_signal_multiplier_enhanced(...)` line and the `return True, unique_count, multiplier` that follows.

The config import at the top of the file needs `COPY_SIGNAL_SCALE_1_WHALE, COPY_SIGNAL_SCALE_2_WHALE, COPY_SIGNAL_SCALE_3_WHALE` added to the existing `from config import (...)` block.

### 2.3 — Add liquidity + age checks to `_handle_whale_swap()`

**Current state:** `_handle_whale_swap()` has these numbered gates in order:
0. Runtime risk gates
1. Auto-pause check
2. Whale qualification
3. Token safety filter
4. Signal aggregation
5. Copy delay
6. Close existing position
7. Amount calculation
8. Execute

**New state:** Insert two new checks **between step 3 (token safety filter) and step 4 (signal aggregation)**:

```python
# NEW gate 3a: liquidity check
from config import BROADCAST_MIN_LIQUIDITY_USD
output_mint_for_check = swap_data.get('outputMint', '')
if output_mint_for_check and output_mint_for_check != WSOL_MINT:
    liquidity_ok, liquidity_usd = await self._check_token_liquidity(output_mint_for_check)
    if not liquidity_ok:
        logger.warning(
            f"🚫 Token {output_mint_for_check[:8]} liquidity ${liquidity_usd:,.0f} "
            f"< ${BROADCAST_MIN_LIQUIDITY_USD:,} minimum — skipping"
        )
        return

# NEW gate 3b: age check — skip tokens launched < 24h ago
    token_age_ok = await self._check_token_age(output_mint_for_check)
    if not token_age_ok:
        logger.warning(f"🚫 Token {output_mint_for_check[:8]} is < 24h old — skipping")
        return
```

Add these two new helper methods to `CopyTradingEngine`:

```python
async def _check_token_liquidity(self, token_address: str) -> Tuple[bool, float]:
    """Return (passes, liquidity_usd). Requires > $30,000 USD liquidity."""
    from config import BROADCAST_MIN_LIQUIDITY_USD
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return False, 0.0
                data = await resp.json(content_type=None)
        pairs = [p for p in (data.get('pairs') or []) if p.get('chainId') == 'solana']
        if not pairs:
            return False, 0.0
        # Use highest-liquidity pair
        best = max(pairs, key=lambda p: float((p.get('liquidity') or {}).get('usd', 0) or 0))
        liquidity_usd = float((best.get('liquidity') or {}).get('usd', 0) or 0)
        return liquidity_usd >= BROADCAST_MIN_LIQUIDITY_USD, liquidity_usd
    except Exception as e:
        logger.warning(f"Liquidity check failed for {token_address[:8]}: {e}")
        return False, 0.0  # Block on error — don't copy unverified token

async def _check_token_age(self, token_address: str) -> bool:
    """Return True if token pair is >= 24 hours old."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json(content_type=None)
        pairs = [p for p in (data.get('pairs') or []) if p.get('chainId') == 'solana']
        if not pairs:
            return False
        best = max(pairs, key=lambda p: float((p.get('liquidity') or {}).get('usd', 0) or 0))
        created_at_ms = best.get('pairCreatedAt', 0) or 0
        if not created_at_ms:
            return False  # Unknown age → block
        age_hours = (time.time() - created_at_ms / 1000) / 3600
        return age_hours >= 24.0
    except Exception as e:
        logger.warning(f"Token age check failed for {token_address[:8]}: {e}")
        return False  # Block on error
```

### 2.4 — Whale exit mirror in `_handle_whale_swap()`

**Current state:** No whale-exit-mirror logic exists.

**New state:** Add at the **top of `_handle_whale_swap()`**, before gate 0, detect SELL direction and mirror immediately:

```python
# Whale exit mirror: if whale is SELLING a token we hold → exit immediately
action_is_sell = (
    swap_data.get('outputMint') == WSOL_MINT
    or swap_data.get('inputMint') not in (WSOL_MINT, None)
    and swap_data.get('outputMint') in (WSOL_MINT, None)
)
# Simplified: if outputMint is WSOL (selling token for SOL) → this is a SELL signal
if swap_data.get('outputMint') == WSOL_MINT:
    sold_token = swap_data.get('inputMint', '')
    if sold_token and sold_token != WSOL_MINT:
        existing = db.get_pending_trade_by_token(user_id, sold_token)
        if existing:
            logger.info(
                f"🐋 Whale exit mirror: {whale_address[:8]} SOLD {sold_token[:8]} "
                f"— exiting our position immediately"
            )
            pm_key = (user_id, sold_token)
            if pm_key in self._position_monitors and not self._position_monitors[pm_key].done():
                self._position_monitors[pm_key].cancel()
            asyncio.create_task(
                self._exit_position(
                    user_id,
                    existing.get('id', 0),
                    sold_token,
                    existing.get('token_amount', 0),
                    'whale_exit_mirror'
                )
            )
        # Don't copy the sell trade itself — return regardless
        return
```

**Note:** `db.get_pending_trade_by_token(user_id, token_mint)` already exists in the codebase (used in `_close_existing_position`).

### 2.5 — Call `broadcaster.post_whale_trade()` after successful copy trade

**Current state:** `execute_copy_trade()` ends with:
```python
asyncio.create_task(
    self._broadcast_copy_signal(user_id=user_id, ...)
)
return True
```
`_broadcast_copy_signal()` calls `tg_broadcaster.broadcast_signal(signal_data)`.

**New state:** Replace the `asyncio.create_task(self._broadcast_copy_signal(...))` call with `post_whale_trade()`. Keep `_broadcast_copy_signal` but replace its body so it calls `tg_broadcaster.post_whale_trade(...)` instead of `tg_broadcaster.broadcast_signal(...)`.

Specifically, update `_broadcast_copy_signal()` body:
```python
async def _broadcast_copy_signal(self, user_id, token_address, amount, tokens_received,
                                   whale_wallet, entry_price, signal_count, exec_time_ms):
    try:
        from trading.token_analyzer import token_analyzer
        from trading.whale_scorer import whale_scorer

        token_info = await token_analyzer.get_token_info(token_address)
        token_name = (token_info.get('name', '?') if token_info else '?') or '?'
        token_symbol = (token_info.get('symbol', '?') if token_info else '?') or '?'
        price_usd = float((token_info.get('price_usd', 0) if token_info else 0) or 0)
        # Fallback: estimate from SOL amount and tokens received
        if price_usd <= 0 and tokens_received > 0:
            sol_price_usd = 150.0  # rough fallback
            price_usd = (amount * sol_price_usd) / tokens_received

        # Get whale score and stats for the alert
        whale_score = whale_scorer.score_whale(user_id, whale_wallet)
        records = db.get_copy_performance(user_id, whale_wallet, limit=50)
        closed = [r for r in records if r.get('status') == 'closed'
                  and r.get('user_profit_percent') is not None]
        total_trades = len(closed)
        wins = sum(1 for r in closed if r['user_profit_percent'] > 0)
        win_rate = (wins / total_trades) if total_trades > 0 else 0.0

        await tg_broadcaster.post_whale_trade(
            whale_address=whale_wallet,
            token_symbol=token_symbol,
            token_mint=token_address,
            action='BUY',
            sol_amount=amount,
            price_usd=price_usd,
            whale_score=whale_score,
            win_rate=win_rate,
            total_trades=total_trades,
            signal_count=signal_count,
        )

        await notification_engine.notify_admins(
            f"🐋 *Copy Trade Executed*\n"
            f"User ID: `{user_id}`\n"
            f"Token: `{token_symbol}`\n"
            f"Amount: `{amount:.4f} SOL`\n"
            f"Whale: `{whale_wallet[:12]}...`\n"
            f"Signals: `{signal_count}`"
        )
    except Exception as e:
        logger.error(f"Failed to broadcast copy signal: {e}", exc_info=True)
```

### 2.6 — Verify

```
cd c:\Users\user\Desktop\copytradebot
python -c "from trading.copy_trader import copy_trader; print('OK')"
```
Expected: prints `OK` with no ImportError.

---

## Task 3 — Improve exit logic in `smart_trader.py` and `exit_strategy.py`

### 3.1 — Whale-exit override in `_monitor_position_trailing()` (`copy_trader.py`)

**Context:** `_monitor_position_trailing()` is the copy-trade position monitor. It currently has no mechanism to exit early if the triggering whale sells.

**Current state:** The monitor loop has:
```python
while True:
    ...
    current_price = ...
    pnl_pct = ...
    peak_price = max(peak_price, current_price)
    # Hard stop loss check
    # Partial take-profit check
    # Trailing stop check
    await asyncio.sleep(check_interval)
```

**New state:** Add a whale-exit check **at the top of each loop iteration**, before the price fetch:
```python
# Whale exit check — if the whale who triggered this entry has since sold, exit
from data.database import db as _db
whale_sold = _db.has_whale_sold_token(user_id, watched_wallet, token_address)
if whale_sold:
    logger.info(
        f"🐋 Whale {watched_wallet[:8]} sold {token_address[:8]} — "
        f"exiting position (whale_exit_trigger)"
    )
    await self._exit_position(
        user_id, position_id, token_address,
        remaining_amount, 'whale_exit_trigger'
    )
    return
```

The `_monitor_position_trailing()` signature already has `user_id` and `position_id` but **does not currently accept `watched_wallet`**. Add it:

**Current signature:**
```python
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
```

**New signature:** Add `watched_wallet: str = ''` parameter.

**Caller update in `execute_copy_trade()`:**
```python
# Current call:
self._position_monitors[pm_key] = asyncio.create_task(
    self._monitor_position_trailing(
        user_id, position_id, output_mint,
        user_entry_price, tokens_received
    )
)
# New call:
self._position_monitors[pm_key] = asyncio.create_task(
    self._monitor_position_trailing(
        user_id, position_id, output_mint,
        user_entry_price, tokens_received,
        watched_wallet=watched_wallet,
    )
)
```

**`db.has_whale_sold_token()` — new DB method required:**

This method does not exist. Add it to `data/database.py`:
```python
def has_whale_sold_token(self, user_id: int, whale_address: str, token_address: str) -> bool:
    """
    Return True if the given whale has a recorded SELL trade for this token
    after the user opened their position (i.e. after the copy trade was triggered).
    Checks the copy_positions table for a sell-direction entry.
    """
    try:
        # A sell is recorded when the whale's outputMint is WSOL and inputMint is token_address.
        # In practice, we detect this by checking if whale_address has a recent transaction
        # with this token on the sell side in the past 48 hours.
        # Implementation: query the watched_wallet_transactions or copy_performance table.
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM copy_positions
            WHERE user_id = ?
              AND watched_wallet = ?
              AND token_address = ?
              AND exit_reason LIKE '%whale_exit%'
        """, (user_id, whale_address, token_address))
        # Simpler: just look for any recent sell-direction swap recorded.
        # Because this depends on DB schema, use a safe fallback if no table exists.
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False  # safe default — don't exit if we can't check
```

**Note:** This is intentionally conservative. The whale exit mirror in `_handle_whale_swap()` (Task 2.4) is the primary mechanism. This secondary check in the monitor adds safety but will only fire if the DB has the record. The implementer should check the actual `copy_positions` schema and adjust the query to match.

### 3.2 — Trailing stop → 10% in `_monitor_position_trailing()` (`copy_trader.py`)

**Current state:** `trailing_stop_pct: float = DEFAULT_TRAILING_STOP` where `DEFAULT_TRAILING_STOP = COPY_DEFAULT_TRAILING_STOP = 0.15`.

**New state:** Config change (Task 4) sets `COPY_DEFAULT_TRAILING_STOP = 0.10`. The `DEFAULT_TRAILING_STOP` alias at module top will pick up the new value automatically — **no code change needed in this function**, just the config change.

### 3.3 — Whale momentum exit in `_monitor_position_trailing()` (`copy_trader.py`)

**Context:** Track how many of the original buying whales have now sold. If ≥50% sold, start a 5-minute countdown; exit if price hasn't risen 5%.

**Current state:** No such logic exists.

**New state:** In `_monitor_position_trailing()`, after the trailing stop check and before `await asyncio.sleep(check_interval)`, add:

```python
# Whale momentum exit: if 50%+ of original buying whales have sold → countdown then exit
if signal_count and signal_count > 1:
    # Count how many buying whales for this token have now sold
    from trading.whale_scorer import whale_scorer as _ws
    token_key = (user_id, token_address)
    buyers = [
        s['whale'] for s in self._pending_signals.get(token_key, [])
    ]
    if buyers:
        sold_count = sum(
            1 for w in buyers
            if _db.has_whale_sold_token(user_id, w, token_address)
        )
        if sold_count / len(buyers) >= 0.5 and not getattr(self, '_whale_momentum_exit_pending', {}).get(token_key):
            # Start 5-minute countdown
            self._whale_momentum_exit_pending = getattr(self, '_whale_momentum_exit_pending', {})
            self._whale_momentum_exit_pending[token_key] = {
                'price_at_trigger': current_price,
                'trigger_time': time.time(),
            }
            logger.info(
                f"⚠️ Whale momentum exit: {sold_count}/{len(buyers)} whales sold "
                f"{token_address[:8]} — 5min countdown started"
            )
        elif getattr(self, '_whale_momentum_exit_pending', {}).get(token_key):
            ctx = self._whale_momentum_exit_pending[token_key]
            elapsed = time.time() - ctx['trigger_time']
            price_gain = (current_price - ctx['price_at_trigger']) / ctx['price_at_trigger'] if ctx['price_at_trigger'] else 0
            if elapsed >= 300:  # 5 minutes
                if price_gain < 0.05:  # price hasn't risen 5%
                    logger.info(f"📉 Whale momentum exit: 5min elapsed, price gain {price_gain*100:.1f}% < 5% — exiting")
                    await self._exit_position(user_id, position_id, token_address, remaining_amount, 'whale_momentum_exit')
                    del self._whale_momentum_exit_pending[token_key]
                    return
                else:
                    logger.info(f"✅ Whale momentum exit cancelled: price up {price_gain*100:.1f}% in 5min")
                    del self._whale_momentum_exit_pending[token_key]
```

Also initialize `self._whale_momentum_exit_pending = {}` in `CopyTradingEngine.__init__()`.

### 3.4 — Daily loss limit: pause copy trading for 2 hours in `_handle_whale_swap()`

**Context:** `enhanced_features.check_daily_loss_limit()` already exists and is called by `_passes_runtime_risk_gates()`. The existing behavior blocks the trade but only for the current call. The task asks: if daily loss > 5% → pause all copy trading for **2 hours**.

**Current state in `_passes_runtime_risk_gates()`:**
```python
can_trade_daily, current_loss = enhanced_features.check_daily_loss_limit(user_id)
if not can_trade_daily:
    logger.warning(f"Daily loss limit hit for user {user_id}: {current_loss:.1f}%")
    return False
```

**New state:** When `can_trade_daily` is `False` and `ENABLE_DAILY_LOSS_LIMIT` is `True`, actively pause all monitoring tasks for 2 hours:
```python
from config import ENABLE_DAILY_LOSS_LIMIT

can_trade_daily, current_loss = enhanced_features.check_daily_loss_limit(user_id)
if not can_trade_daily:
    logger.warning(f"Daily loss limit hit for user {user_id}: {current_loss:.1f}%")
    if ENABLE_DAILY_LOSS_LIMIT:
        asyncio.create_task(self._pause_copy_trading_for(user_id, duration_seconds=7200))
    return False
```

Add new method to `CopyTradingEngine`:
```python
async def _pause_copy_trading_for(self, user_id: int, duration_seconds: int = 7200):
    """Pause all monitoring tasks for this user for duration_seconds, then resume."""
    logger.warning(
        f"⏸️ Daily loss limit: pausing all copy trading for user {user_id} "
        f"for {duration_seconds // 60} minutes"
    )
    self.stop_monitoring_for_user(user_id)
    await asyncio.sleep(duration_seconds)
    logger.info(f"▶️ Resuming copy trading for user {user_id} after loss-limit pause")
    await self.start_monitoring_for_user(user_id)
```

**Note:** Avoid calling `_pause_copy_trading_for` multiple times. Add a guard set to `CopyTradingEngine.__init__`:
```python
self._loss_limit_paused: set = set()   # user_ids currently in loss-limit pause
```
Then in `_passes_runtime_risk_gates()`, check `user_id not in self._loss_limit_paused` before creating the task, and in `_pause_copy_trading_for()` add/remove from the set around the sleep.

### 3.5 — Trailing stop and TP ladder in `smart_trader.py`

**`_monitor_position_graduated()`** reads `trailing_stop_pct` from `db.get_user_setting(user_id, 'trailing_stop_pct', SMART_TRAILING_STOP_PCT)`. The config change (Task 4) sets `SMART_TRAILING_STOP_PCT = 0.10`. No code change needed — the config change propagates automatically.

The TP ladder (25% at +30%, 50% at +60%, rest at +100%) is already correctly implemented. No changes needed.

### 3.6 — Trailing stop default in `exit_strategy.py`

**`get_dynamic_trailing_stop()`** current fallback:
```python
if not ENABLE_DYNAMIC_TRAILING_STOP:
    return 0.15  # Default 15%
```

**New state:**
```python
if not ENABLE_DYNAMIC_TRAILING_STOP:
    return 0.10  # Default 10%
```

Also update the docstring comment from "10% for low vol" and "20% for high vol" — those remain as-is, only the disabled-path default changes.

### 3.7 — Verify

```
cd c:\Users\user\Desktop\copytradebot
python -c "from trading.smart_trader import smart_trader; from trading.exit_strategy import exit_strategy_manager; print('OK')"
```
Expected: prints `OK`.

---

## Task 4 — Update `config.py` defaults

### 4.1 — Change existing values

| Config key | Current value | New value |
|---|---|---|
| `WHALE_MIN_WIN_RATE` | `float(os.getenv('WHALE_MIN_WIN_RATE', '0.40'))` | `float(os.getenv('WHALE_MIN_WIN_RATE', '0.60'))` |
| `WHALE_MIN_TRADES` | `int(os.getenv('WHALE_MIN_TRADES', '5'))` | `int(os.getenv('WHALE_MIN_TRADES', '20'))` |
| `COPY_DEFAULT_TRAILING_STOP` | `float(os.getenv('COPY_DEFAULT_TRAILING_STOP', '0.15'))` | `float(os.getenv('COPY_DEFAULT_TRAILING_STOP', '0.10'))` |
| `SMART_TRAILING_STOP_PCT` | `float(os.getenv('SMART_TRAILING_STOP_PCT', '0.15'))` | `float(os.getenv('SMART_TRAILING_STOP_PCT', '0.10'))` |
| `DAILY_LOSS_LIMIT_PCT` | `float(os.getenv('DAILY_LOSS_LIMIT_PCT', '10.0'))` | `float(os.getenv('DAILY_LOSS_LIMIT_PCT', '5.0'))` |
| `ENABLE_DAILY_LOSS_LIMIT` | `os.getenv('ENABLE_DAILY_LOSS_LIMIT', 'false').lower() == 'true'` | `os.getenv('ENABLE_DAILY_LOSS_LIMIT', 'true').lower() == 'true'` |
| `WHALE_MAX_CONSECUTIVE_LOSSES` | `int(os.getenv('WHALE_MAX_CONSECUTIVE_LOSSES', '5'))` | `int(os.getenv('WHALE_MAX_CONSECUTIVE_LOSSES', '3'))` |

### 4.2 — Add new constants

Insert after the existing `WHALE_MIN_AVG_PROFIT` line (in the "Copy-trader — whale qualification" section):

```python
WHALE_STALE_DAYS         = int(os.getenv('WHALE_STALE_DAYS', '14'))
```

Insert after `COPY_MAX_PRICE_IMPACT_PCT` (in the "Copy-trader — signal / position defaults" section):

```python
COPY_SIGNAL_SCALE_1_WHALE  = float(os.getenv('COPY_SIGNAL_SCALE_1_WHALE', '0.5'))
COPY_SIGNAL_SCALE_2_WHALE  = float(os.getenv('COPY_SIGNAL_SCALE_2_WHALE', '1.0'))
COPY_SIGNAL_SCALE_3_WHALE  = float(os.getenv('COPY_SIGNAL_SCALE_3_WHALE', '1.5'))
```

### 4.3 — Verify

```
cd c:\Users\user\Desktop\copytradebot
python -c "from config import WHALE_MIN_WIN_RATE, WHALE_MIN_TRADES, WHALE_STALE_DAYS, COPY_SIGNAL_SCALE_1_WHALE, COPY_SIGNAL_SCALE_2_WHALE, COPY_SIGNAL_SCALE_3_WHALE, DAILY_LOSS_LIMIT_PCT, ENABLE_DAILY_LOSS_LIMIT; print(WHALE_MIN_WIN_RATE, WHALE_MIN_TRADES, WHALE_STALE_DAYS, COPY_SIGNAL_SCALE_1_WHALE, DAILY_LOSS_LIMIT_PCT, ENABLE_DAILY_LOSS_LIMIT)"
```
Expected output: `0.6 20 14 0.5 5.0 True`

---

## Task 5 — Update `.env.example`

### 5.1 — Add new keys

Replace the existing "Copy Trading" block (currently ends with `MIN_TRADE_AMOUNT=0.01`) with:

```
# Copy Trading
COPY_TRADE_CHECK_INTERVAL=10
WALLET_MONITOR_INTERVAL=5
SLIPPAGE_TOLERANCE=2.0
MIN_TRADE_AMOUNT=0.01

# Whale Qualification
WHALE_MIN_WIN_RATE=0.60
WHALE_MIN_TRADES=20
WHALE_MIN_AVG_PROFIT=-10.0
WHALE_STALE_DAYS=14
WHALE_MAX_CONSECUTIVE_LOSSES=3

# Signal Scaling (1 whale = 0.5x, 2 whales = 1.0x, 3+ whales = 1.5x)
COPY_SIGNAL_SCALE_1_WHALE=0.5
COPY_SIGNAL_SCALE_2_WHALE=1.0
COPY_SIGNAL_SCALE_3_WHALE=1.5

# Exit / Trailing Stop
COPY_DEFAULT_TRAILING_STOP=0.10
SMART_TRAILING_STOP_PCT=0.10

# Daily Loss Limit
ENABLE_DAILY_LOSS_LIMIT=true
DAILY_LOSS_LIMIT_PCT=5.0
```

### 5.2 — Remove or comment out obsolete broadcast keys

Remove (or comment with `# REMOVED — no longer used`) these lines from `.env.example`:
```
BROADCAST_NEWS_INTERVAL_MINUTES=30
BROADCAST_SELF_AD_INTERVAL_HOURS=4
BROADCAST_MIN_NEWS_RELEVANCE=60
BROADCAST_MARKET_UPDATE_INTERVAL_MINUTES=30
BROADCAST_TOP_TOKEN_COUNT=5
BROADCAST_TOP_TOKEN_MIN_LIQUIDITY_USD=25000
BROADCAST_NEWS_MAX_AGE_HOURS=24
BROADCAST_LAUNCH_UPDATE_INTERVAL_MINUTES=20
BROADCAST_LAUNCH_MAX_AGE_MINUTES=180
BROADCAST_LAUNCH_MIN_LIQUIDITY_USD=10000
BROADCAST_LAUNCH_SCAN_LIMIT=15
BROADCAST_MAX_TOKEN_NEWS_KEYWORDS=80
BROADCAST_EXTRA_NEWS_SOURCES=
BROADCAST_SOCIAL_NEWS_SOURCES=
```

Keep `BROADCAST_MIN_LIQUIDITY_USD=30000` — it is now also used by the liquidity gate in `copy_trader.py`.

### 5.3 — Verify

```
cd c:\Users\user\Desktop\copytradebot
findstr "WHALE_STALE_DAYS\|COPY_SIGNAL_SCALE" .env.example
```
Expected: two matching lines found.

---

## Additional change: Remove token-launch calls from `smart_trader.py`

**Context:** `discover_new_pump_fun_tokens()` in `smart_trader.py` calls `tg_broadcaster.broadcast_token_launch(launch_data)`. Since `broadcast_token_launch()` is being removed from the broadcaster, this call must also be removed.

**File:** `trading/smart_trader.py`

**Current state in `discover_new_pump_fun_tokens()` (approx lines 322–360):**
```python
# Broadcast token launch to Telegram
try:
    from trading.telegram_broadcaster import broadcaster as tg_broadcaster
    ...
    posted = await tg_broadcaster.broadcast_token_launch(launch_data)
    if posted:
        logger.info(f"Launch broadcasted: {token_data['name']}")
    else:
        logger.info(f"Launch filtered: {token_data['name']}")
except Exception as e:
    logger.error(f"Failed to broadcast token launch: {e}")
```

**New state:** Remove the entire `try/except` block that calls `tg_broadcaster.broadcast_token_launch`. Keep the `results.append(token_data)` line.

**Verify:** Same as 3.7 verify step.

---

## Execution order (dependency-driven)

```
- [ ] 1. Task 4 — config.py (no dependencies; everything else imports config)
      Files: config.py
      Verify: python -c "from config import WHALE_MIN_WIN_RATE, WHALE_STALE_DAYS, COPY_SIGNAL_SCALE_1_WHALE; print(WHALE_MIN_WIN_RATE, WHALE_STALE_DAYS, COPY_SIGNAL_SCALE_1_WHALE)"
      Expected: 0.6 14 0.5

- [ ] 2. Task 1 — telegram_broadcaster.py (depends on config; broadcaster is imported by copy_trader)
      Files: trading/telegram_broadcaster.py
      Verify: python -c "from trading.telegram_broadcaster import broadcaster; import asyncio; print('OK')"
      Expected: OK

- [ ] 3. Task 3 (exit_strategy.py only) — independent, change one line
      Files: trading/exit_strategy.py
      Verify: python -c "from trading.exit_strategy import exit_strategy_manager; print('OK')"
      Expected: OK

- [ ] 4. Task 2 — copy_trader.py (depends on tasks 1, 4, and must add has_whale_sold_token to DB)
      Files: trading/copy_trader.py, data/database.py
      Verify: python -c "from trading.copy_trader import copy_trader; print('OK')"
      Expected: OK

- [ ] 5. Task 3 (smart_trader.py) — remove broadcast_token_launch call, trailing stop already handled via config
      Files: trading/smart_trader.py
      Verify: python -c "from trading.smart_trader import smart_trader; print('OK')"
      Expected: OK

- [ ] 6. Task 5 — .env.example (documentation only, no code dependencies)
      Files: .env.example
      Verify: findstr "WHALE_STALE_DAYS" .env.example (Windows) or grep on Linux
      Expected: line found
```
