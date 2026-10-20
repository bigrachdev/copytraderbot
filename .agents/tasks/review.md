# Three new broadcast post types: whale exit, convergence alert, weekly leaderboard

The change adds three new Telegram post types (`post_whale_exit`, `post_convergence_alert`, `post_weekly_leaderboard`) and their supporting infrastructure: a background scheduler loop, a DB query method, new config keys, and integration hooks inside the copy-trading engine. The overall structure is sound — each new post method is properly guarded by the existing rate-limiting machinery, dedup is applied, and HTML escaping via `_safe_text` is used consistently. The primary concern is a non-fatal cosmetic issue (duplicate import) and one minor behavioural gap in the leaderboard timing logic.

**Watch for:** Duplicate `InlineKeyboardMarkup, InlineKeyboardButton` in the import line (non-blocking, Python silently ignores it but it signals a copy-paste edit). Leaderboard scheduler has a subtle timing edge case on the exact fire minute. No blocking correctness issues found.

**Verdict**: APPROVED

---

## High-level view

All four methods are present in `TelegramBroadcaster`. `run_weekly_leaderboard_loop` is started inside `initialize()` via `asyncio.create_task`. `_posted_convergence` is initialised in `TelegramBroadcaster.__init__`; the parallel `_posted_convergence_signals` in `CopyTradingEngine.__init__` is also present and correctly referenced in `_register_signal`.

`get_top_whales_by_pnl` is implemented at line 2131 of `database.py` with correct dual-path SQL for both PostgreSQL (`NOW() - INTERVAL '1 day' * %s`) and SQLite (`datetime('now', '-' || ? || ' days')`). The row-type handling (`isinstance(row, dict)`) handles both `dict_row` (psycopg) and `sqlite3.Row` without issues.

The integration hook for whale exits sits in `_handle_whale_swap`, triggering on `outputMint == WSOL_MINT`. The convergence hook fires inside `_register_signal` when `unique_count >= CONVERGENCE_MIN_WHALES`. Both are correctly placed. Both propagate via `asyncio.create_task` to avoid blocking the swap-processing hot path.

All seven `.env.example` keys are present: `CONVERGENCE_MIN_WHALES`, `CONVERGENCE_TIME_WINDOW_MINUTES`, `CONVERGENCE_DEDUP_HOURS`, `LEADERBOARD_DAY_OF_WEEK`, `LEADERBOARD_HOUR_UTC`, `LEADERBOARD_MIN_WHALES`, `WHALE_EXIT_MAX_HOLD_DAYS`. All seven are also in `config.py` with matching defaults.

---

<details>
<summary>Issues (2)</summary>

1. **Duplicate import** — Line 20 of `telegram_broadcaster.py` imports `InlineKeyboardMarkup, InlineKeyboardButton` twice in the same `from telegram import` statement. Python silently ignores duplicate names in a single import so there is no runtime error, but it should be cleaned up to avoid confusion.

2. **Leaderboard loop timing edge case** — In `run_weekly_leaderboard_loop`, when `days_ahead == 0` the code advances to the following week only if `now.hour > LEADERBOARD_HOUR_UTC or (now.hour == LEADERBOARD_HOUR_UTC and now.minute >= 5)`. The five-minute grace window means a bot restarted at exactly `18:00:00 UTC` on Sunday would attempt to fire immediately. This is non-blocking (the 6-day cooldown in `post_weekly_leaderboard` prevents a second post anyway) but the intent is slightly obscured.

</details>

<details>
<summary>Details</summary>

### Duplicate import in telegram_broadcaster.py

Line 20 reads:

```python
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
```

Both `InlineKeyboardMarkup` and `InlineKeyboardButton` appear twice. Python processes the import successfully — the second occurrence simply rebinds the same name — so there is no `ImportError` or `NameError` at runtime. The syntax check (`py_compile`) passes. Recommend trimming to a single occurrence.

### Leaderboard scheduler timing

`run_weekly_leaderboard_loop` calculates `days_ahead` correctly for the standard case. The only subtlety: if the process starts within five minutes of the fire time on the target day (`minute < 5` and `hour == LEADERBOARD_HOUR_UTC`), `days_ahead` stays 0 and the sleep is very short (or negative if `seconds_until` rounds oddly). In that scenario the loop proceeds to call `post_weekly_leaderboard()`, which is gated by its own 6-day cooldown timestamp, so double-posting is impossible. The worst outcome is an unnecessary log line and a no-op post attempt. Non-blocking.

### Rate limiting and dedup coverage — confirmed correct

`post_whale_exit` and `post_convergence_alert` both share the `_last_post_times['signal']` 2-minute cooldown and the `_max_signals_last_hour` 10/hour cap, consistent with `post_whale_trade`. `post_weekly_leaderboard` uses its own `_last_post_times['leaderboard']` key with a 518,400-second (6-day) cooldown and is explicitly excluded from the hourly cap — both decisions are intentional and correct.

`_posted_convergence` in `TelegramBroadcaster.__init__` and `_posted_convergence_signals` in `CopyTradingEngine.__init__` are both initialised as empty `Dict[str, float]`. The two dicts serve parallel dedup roles at different layers (broadcaster dedup vs engine dedup) and are both correctly used.

### HTML escaping coverage — confirmed correct

All user-supplied strings rendered into HTML messages pass through `_safe_text(html.escape(...))`. Numeric values (win rates, profit pcts, SOL amounts) are formatted via f-string format specs and never contain HTML-special characters. Token mints pass through `re.sub(r'[^1-9A-HJ-NP-Za-km-z]', '', ...)` before use in URLs, which strips any HTML-injectable character.

### Integration hooks in copy_trader.py — confirmed correct

The whale exit hook fires unconditionally when `outputMint == WSOL_MINT` and the hold-duration filter (`hold_duration_hours <= WHALE_EXIT_MAX_HOLD_DAYS * 24`) is applied before the `asyncio.create_task` call. A zero `hold_duration_hours` (entry record not found) bypasses the filter correctly — the fallback posts the alert without a hold-duration constraint, which is the right conservative behaviour.

The convergence hook in `_register_signal` fires before the `already_executed` early-return, so it is evaluated on every new whale registration regardless of whether the trade was already copied. The engine-level dedup (`_posted_convergence_signals`) prevents a second alert within `CONVERGENCE_DEDUP_HOURS`. The broadcaster has its own separate dedup via `_posted_convergence`, so even if `_register_signal` fires twice in a race condition, the broadcaster will only post once.

### get_top_whales_by_pnl — confirmed correct

The method handles both backends. The SQLite path uses `datetime('now', '-' || ? || ' days')` which is valid SQLite date arithmetic. The PostgreSQL path uses `INTERVAL '1 day' * %s` which is valid in PostgreSQL. Row access uses the `isinstance(row, dict)` guard that is consistent with how every other dual-path method in this file handles `dict_row` vs `sqlite3.Row`. The inner query for `best_trade_token` is wrapped in a try/except so a failure to find the best token degrades gracefully to an empty string.

### Config and .env.example — confirmed correct

Seven new keys are present in `config.py` with defaults, and all seven appear in `.env.example` with comments. The `LEADERBOARD_DAY_OF_WEEK` default of `6` matches Python's `weekday()` convention where Monday=0 and Sunday=6.

### Syntax — confirmed clean

All five files pass `python -m py_compile` with no errors.

</details>

---

## File map

<details>
<summary>Changed files</summary>

- `config.py` — Added 7 new config keys: convergence settings (3), leaderboard settings (3), whale exit max hold days (1)
- `.env.example` — Added corresponding 7 env var stubs with comments
- `data/database.py` — Added `get_top_whales_by_pnl(days, limit)` with dual PostgreSQL/SQLite implementation
- `trading/telegram_broadcaster.py` — Added `post_whale_exit`, `post_convergence_alert`, `post_weekly_leaderboard`, `run_weekly_leaderboard_loop`; started leaderboard loop in `initialize()`; added `_posted_convergence` dict in `__init__`
- `trading/copy_trader.py` — Added `_posted_convergence_signals` dict in `__init__`; added convergence alert hook in `_register_signal`; added whale exit alert hook in `_handle_whale_swap`; imported new config keys

</details>
