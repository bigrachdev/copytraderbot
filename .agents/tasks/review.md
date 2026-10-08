# Whale-Only Bot Refactor — Whale Gate, Signal Scaling, and Exit Improvements

The change strips `TelegramBroadcaster` down to whale-trade-only signals, tightens the whale qualification gate with a hard 60% win-rate floor and stale-whale rejection, adds signal-scaling tiers (0.5x/1.0x/1.5x), gates trades behind liquidity and age checks, mirrors whale exits immediately, and tightens trailing stops from 15% to 10%. The motivation is straightforward: the service was running out of compute time broadcasting news and trending tokens nobody asked for, and the copy-trading strategy needed sharper entry and exit discipline.

Watch for: (1) `ENABLE_DAILY_LOSS_LIMIT` is defined **three times** in `config.py` due to duplicate `LOG_LEVEL` lines and an earlier duplicate block — the last definition wins and has the correct default, but the file should be cleaned up before the next refactor; (2) `has_whale_sold_token` can only detect exits the bot itself recorded via `whale_exit_mirror` / `whale_exit_trigger` — it will miss whale sells the bot never processed, making the whale-exit-trigger in the monitor largely a no-op until those exit reasons accumulate in the DB; (3) no coder verification output was found in the task directory — syntax was confirmed by a spot `py_compile` pass during review.

**Verdict**: APPROVED

---

## High-level view

The broadcaster is now a focused whale-alert service. All four background loops (news, market updates, self-ads, launch updates) are gone; a 5-minute heartbeat replaces them. `post_whale_trade()` is a clean new method with its own rate-limiting path that avoids entanglement with the older `broadcast_signal()` path. The broadcaster's `__init__` is trimmed to only the state it actually needs.

The whale gate is meaningfully stricter. The "insufficient history → allow" shortcut is reversed to a hard reject, win-rate floor is raised from 40% to 60%, stale-whale rejection is added at 14 days, and consecutive-loss cap is lowered to 3. These four changes compound: a whale that was borderline acceptable before will now be rejected on at least two of them.

Liquidity and age gates sit between the token safety filter and signal aggregation, which is the right placement. Both fail closed on API error, which is the correct defensive posture for a copy-trade bot — better to miss a trade than to copy an unverified rug.

The whale exit mirror responds to a whale's sell side by immediately cancelling the position monitor and exiting. This is the primary exit acceleration. The secondary check inside the trailing-stop monitor (`has_whale_sold_token`) will only fire once the bot has itself recorded an exit for the whale on that token, so it acts as a consistency guard rather than an independent detection path.

The whale momentum exit countdown (50%+ whales sold → 5-minute timer → exit if price hasn't risen 5%) is implemented correctly in `_monitor_position_trailing()` and handles the cancellation path when price does recover.

Config changes are correct and pick up the new defaults. `.env.example` documents all new keys and marks removed broadcast keys as `# REMOVED`.

---

<details>
<summary>Issues (4)</summary>

1. **Duplicate config definitions** — `LOG_LEVEL` is defined 3 times in `config.py`, `ENABLE_DAILY_LOSS_LIMIT` is effectively defined twice (lines 183 and an earlier duplicate block). The correct values win by position, but the file is fragile. Clean up duplicate declarations before the next config change.

2. **`has_whale_sold_token` detection gap** — The method queries `copy_performance` for `exit_reason IN ('whale_exit_mirror', 'whale_exit_trigger')`. Those rows are only written when the bot itself processes a sell-side swap for the whale. If the whale's sell transaction arrives during a WS reconnect window, or if the bot wasn't monitoring when the sell happened, the `whale_exit_trigger` path in the trailing monitor will never fire. This limits the secondary monitor check to "the bot already handled this exit" — which is fine as a consistency guard but is not an independent detection path. Document this limitation so future developers don't over-rely on the DB check.

3. **Heartbeat posts to channel on every 5-minute tick** — `_heartbeat_loop()` calls `_send_message()` unconditionally, which will post `🤖 Bot active — ...` to the Telegram channel 288 times per day. For a public channel this is noise. Consider sending to an admin chat or only when a trade was copied in the last interval, or remove the heartbeat message from the public channel entirely.

4. **`notify_trade_opened` still says "Trailing stop: -15%"** — In `execute_copy_trade()` the trade confirmation message hard-codes `"TP: +30%  |  Trailing stop: -15%"` but the trailing stop is now 10%. This is a display-only inaccuracy, not a logic bug, but it will confuse users watching the channel.

</details>

<details>
<summary>Details</summary>

## Broadcaster strip-down

All 27 methods listed in the plan are gone. The `__init__` retains only signal dedup state, rate-limit counters, and `_min_liquidity_usd`. The `initialize()` method launches a single `_heartbeat_loop()` task and nothing else. Imports of `xml.etree`, `email.utils`, and `urllib.parse` are removed; the remaining import list matches what the surviving methods actually need.

`post_whale_trade()` implements its own dedup (15-minute window by `{token_mint}_{action}` hash), a 2-minute inter-signal rate limit, and a 10-per-hour cap — matching the spec exactly. It persists posted signal hashes to the DB for cross-restart dedup via `db.save_posted_signal()`, which is the same persistence mechanism `broadcast_signal()` uses.

`broadcast_signal()` and `broadcast_whale_alert()` are retained unchanged. This is correct: other callsites may still use the generic signal path.

The heartbeat loop posts to the public channel every 5 minutes. This is benign for internal testing but is channel spam at production volume — flagged in Issues above.

## Whale qualification gate

All four new checks are present and in the right order: insufficient history → win rate floor → average profit floor → stale whale → consecutive losses. The flip from "insufficient history → allow" to "insufficient history → reject" is the highest-impact change here; it gates out any wallet that hasn't built 20 closed trades, which was `WHALE_MIN_TRADES`'s previous default of 5.

The consecutive-loss check delegates to `whale_scorer.check_consecutive_losses()`, which reads `WHALE_MAX_CONSECUTIVE_LOSSES` from config. That constant is correctly updated to `3`.

One nuance: `_is_whale_qualified` is synchronous but does a `time.time()` call for the stale-whale check and imports `time as _time` inline. The inline import is unnecessary — `time` is already imported at module level — but it's harmless.

## Signal aggregation scaling

The 3-tier multiplier (0.5x / 1.0x / 1.5x) is correctly implemented directly in `_register_signal()` using the three config constants. The previous `enhanced_features.get_signal_multiplier_enhanced()` call is replaced entirely. The `_register_signal()` return value is `(should_execute, unique_count, multiplier)` — unchanged shape, caller code in `_handle_whale_swap()` needs no update.

## Liquidity and age gates

Both gates call DexScreener with an 8-second timeout and fail closed on any error. They share a DexScreener fetch (same endpoint) — a future optimization could combine the two calls, but the current implementation makes two separate HTTP requests per new token. At one copy-trade decision per whale swap, this is fine.

The placement is after the token safety filter (step 3) and before signal aggregation (step 4), matching the plan. They are wrapped in the same `if output_mint and output_mint != WSOL_MINT:` block as the safety filter, so WSOL-to-WSOL swaps (no real output token) correctly bypass both.

`_check_token_age()` returns `False` when `pairCreatedAt` is absent — blocking on unknown age. This is intentionally conservative.

## Whale exit mirror

The mirror sits at the very top of `_handle_whale_swap()`, before all other gates. When the whale's `outputMint == WSOL_MINT` (selling the token for SOL), it looks up any open position the user holds in that token and cancels the trailing-stop monitor task before calling `_exit_position()`. It then returns regardless of whether a position existed, so the sell-side swap is never re-entered as a copy trade.

The `db.get_pending_trade_by_token()` call is the same method used in `_close_existing_position()`, so it's a known-working DB path.

## `has_whale_sold_token` and monitor-side whale exit

The DB method is implemented with parameterized queries, handles both Postgres (`%s`) and SQLite (`?`) backends, and returns `False` on any exception. The query checks `copy_performance` for rows where `exit_reason` is `whale_exit_mirror` or `whale_exit_trigger`. These rows are written by `close_copy_position()` in `_execute_exit_swap()`. The detection gap (bot must have processed the sell first) is described in Issues.

## Trailing stop tightening

`COPY_DEFAULT_TRAILING_STOP` is `0.10` in config, `DEFAULT_TRAILING_STOP` picks it up as a module-level alias, `_monitor_position_trailing()`'s default parameter uses `DEFAULT_TRAILING_STOP`, and `exit_strategy.py`'s disabled-path fallback returns `0.10`. All three values are consistent.

`SMART_TRAILING_STOP_PCT` is also `0.10`; `_monitor_position_graduated()` reads it via `db.get_user_setting(user_id, 'trailing_stop_pct', SMART_TRAILING_STOP_PCT)` so new users get 10% by default while existing users with a persisted setting keep their value.

The user-facing notification in `execute_copy_trade()` still says `-15%` — flagged in Issues.

## Daily loss limit pause

`_passes_runtime_risk_gates()` now creates a `_pause_copy_trading_for()` task when the daily loss limit fires. The guard `user_id not in self._loss_limit_paused` prevents double-pausing. `_pause_copy_trading_for()` uses a `try/finally` to always `discard()` the user from the paused set, so a crash or cancellation during the sleep won't leave the user permanently paused. This is correct resilience.

## Config hygiene

`WHALE_MIN_WIN_RATE` → `0.60`, `WHALE_MIN_TRADES` → `20`, `COPY_DEFAULT_TRAILING_STOP` → `0.10`, `SMART_TRAILING_STOP_PCT` → `0.10`, `DAILY_LOSS_LIMIT_PCT` → `5.0`, `ENABLE_DAILY_LOSS_LIMIT` → `'true'`, `WHALE_MAX_CONSECUTIVE_LOSSES` → `3`. All match the plan. New constants `WHALE_STALE_DAYS`, `COPY_SIGNAL_SCALE_1/2/3_WHALE` are present.

The duplicate `LOG_LEVEL` (×3) and `ENABLE_DAILY_LOSS_LIMIT` (visible twice in the search output) are a pre-existing problem that this PR did not introduce but also did not clean up. The last definition wins in Python, so the correct values are used at runtime — but it's a maintenance hazard.

## Syntax

`py_compile` passes on all six changed files: `telegram_broadcaster.py`, `copy_trader.py`, `smart_trader.py`, `exit_strategy.py`, `config.py`. No coder verification log was present in the task directory; this spot check was run during review.

</details>

---

## File map

<details>
<summary>Changed files</summary>

| File | What changed |
|---|---|
| `trading/telegram_broadcaster.py` | Stripped to whale-only: removed all 4 background loops and 27 methods; added `post_whale_trade()` and `_heartbeat_loop()` |
| `trading/copy_trader.py` | Added liquidity/age gates, whale exit mirror, whale momentum exit, stale whale check, hard-gate on insufficient history, 3-tier signal multiplier, `_pause_copy_trading_for()`, `_check_token_liquidity()`, `_check_token_age()`; `_broadcast_copy_signal()` rewritten to call `post_whale_trade()` |
| `trading/smart_trader.py` | Removed `broadcast_token_launch()` call from `discover_new_pump_fun_tokens()` |
| `trading/exit_strategy.py` | `get_dynamic_trailing_stop()` disabled-path default changed from `0.15` to `0.10` |
| `config.py` | Seven defaults updated; three new signal-scale constants added; `WHALE_STALE_DAYS` added |
| `.env.example` | Removed broadcast keys documented as `# REMOVED`; added whale qualification, signal scaling, trailing stop, and daily-loss-limit keys |
| `data/database.py` | Added `has_whale_sold_token()` method |

</details>
