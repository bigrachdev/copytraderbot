# Implementation Plan — Three New High-Value Post Types

Ordered by dependency. Each item leaves the codebase in a buildable state.
Files read during exploration: `config.py`, `.env.example`, `data/database.py`,
`trading/telegram_broadcaster.py`, `trading/copy_trader.py`, `trading/whale_scorer.py`.

---

- [ ] 1. **Add new config keys to `config.py`**

  Insert the seven new constants after the `COPY_SIGNAL_SCALE_3_WHALE` line (line 143 in the
  current file, inside the `# ── Copy-trader — whale qualification` section). The insertion
  point is right before the `# ── Smart Trader — position management` comment block.

  **Exact text to insert at that location:**
  ```python
  # Convergence alert settings
  CONVERGENCE_MIN_WHALES          = int(os.getenv('CONVERGENCE_MIN_WHALES', '3'))
  CONVERGENCE_TIME_WINDOW_MINUTES = int(os.getenv('CONVERGENCE_TIME_WINDOW_MINUTES', '30'))
  CONVERGENCE_DEDUP_HOURS         = int(os.getenv('CONVERGENCE_DEDUP_HOURS', '4'))

  # Weekly leaderboard
  LEADERBOARD_DAY_OF_WEEK         = int(os.getenv('LEADERBOARD_DAY_OF_WEEK', '6'))  # 6=Sunday
  LEADERBOARD_HOUR_UTC            = int(os.getenv('LEADERBOARD_HOUR_UTC', '18'))
  LEADERBOARD_MIN_WHALES          = int(os.getenv('LEADERBOARD_MIN_WHALES', '2'))

  # Whale exit alert
  WHALE_EXIT_MAX_HOLD_DAYS        = int(os.getenv('WHALE_EXIT_MAX_HOLD_DAYS', '7'))
  ```

  **Files:** `config.py`

  **Verify:** `python -c "from config import CONVERGENCE_MIN_WHALES, LEADERBOARD_DAY_OF_WEEK, WHALE_EXIT_MAX_HOLD_DAYS; print('OK')"` — prints `OK`.

---

- [ ] 2. **Add new `.env.example` entries**

  Append the following block at the end of `.env.example` (after the last existing line):
  ```
  # Convergence alert — post when N whales buy the same token within the time window
  CONVERGENCE_MIN_WHALES=3
  CONVERGENCE_TIME_WINDOW_MINUTES=30
  CONVERGENCE_DEDUP_HOURS=4        # hours before the same token can trigger another convergence post

  # Weekly leaderboard — day 6=Sunday, hour in UTC
  LEADERBOARD_DAY_OF_WEEK=6
  LEADERBOARD_HOUR_UTC=18
  LEADERBOARD_MIN_WHALES=2         # minimum distinct whales required to post the leaderboard

  # Whale exit alert — only post exits for tokens held shorter than this many days
  WHALE_EXIT_MAX_HOLD_DAYS=7
  ```

  **Files:** `.env.example`

  **Verify:** File opens without errors; `grep -c "CONVERGENCE_MIN_WHALES" .env.example` returns 1.

---

- [ ] 3. **Add `get_top_whales_by_pnl()` to `data/database.py`**

  Insert the new method immediately before the `get_whale_rankings()` method (which starts at
  line 2130 of the current file — the first line of the `get_whale_rankings` definition).
  The method belongs to the `Database` class.

  **Full method to insert:**
  ```python
  def get_top_whales_by_pnl(self, days: int = 7, limit: int = 5) -> list:
      """
      Return top whales by average user_profit_percent for the last `days` days.
      Each returned dict has keys:
        watched_wallet, avg_profit, total_trades, wins, best_trade_pct, best_trade_token
      Returns [] on any error.
      """
      try:
          conn = self.get_connection()
          cursor = conn.cursor()

          if self.use_postgres:
              # Main aggregation query
              cursor.execute('''
                  SELECT
                      watched_wallet,
                      AVG(user_profit_percent)                                  AS avg_profit,
                      COUNT(*)                                                  AS total_trades,
                      SUM(CASE WHEN user_profit_percent > 0 THEN 1 ELSE 0 END) AS wins,
                      MAX(user_profit_percent)                                  AS best_trade_pct
                  FROM copy_performance
                  WHERE status = 'closed'
                    AND user_profit_percent IS NOT NULL
                    AND closed_at >= NOW() - INTERVAL '%s days'
                  GROUP BY watched_wallet
                  ORDER BY AVG(user_profit_percent) DESC
                  LIMIT %s
              ''', (days, limit))
          else:
              cursor.execute('''
                  SELECT
                      watched_wallet,
                      AVG(user_profit_percent)                                  AS avg_profit,
                      COUNT(*)                                                  AS total_trades,
                      SUM(CASE WHEN user_profit_percent > 0 THEN 1 ELSE 0 END) AS wins,
                      MAX(user_profit_percent)                                  AS best_trade_pct
                  FROM copy_performance
                  WHERE status = 'closed'
                    AND user_profit_percent IS NOT NULL
                    AND closed_at >= datetime('now', '-' || ? || ' days')
                  GROUP BY watched_wallet
                  ORDER BY AVG(user_profit_percent) DESC
                  LIMIT ?
              ''', (days, limit))

          rows = cursor.fetchall()
          results = []

          for row in rows:
              wallet       = row['watched_wallet']
              best_pct     = row['best_trade_pct']

              # Second query: find the token_address for the best trade
              best_token = ''
              try:
                  if self.use_postgres:
                      cursor.execute('''
                          SELECT token_address FROM copy_performance
                          WHERE watched_wallet = %s
                            AND status = 'closed'
                            AND user_profit_percent IS NOT NULL
                            AND closed_at >= NOW() - INTERVAL '%s days'
                          ORDER BY user_profit_percent DESC
                          LIMIT 1
                      ''', (wallet, days))
                  else:
                      cursor.execute('''
                          SELECT token_address FROM copy_performance
                          WHERE watched_wallet = ?
                            AND status = 'closed'
                            AND user_profit_percent IS NOT NULL
                            AND closed_at >= datetime('now', '-' || ? || ' days')
                          ORDER BY user_profit_percent DESC
                          LIMIT 1
                      ''', (wallet, days))
                  best_row = cursor.fetchone()
                  if best_row:
                      best_token = best_row['token_address'] if isinstance(best_row, dict) else best_row[0]
              except Exception:
                  pass

              results.append({
                  'watched_wallet':  wallet,
                  'avg_profit':      round(float(row['avg_profit'] or 0), 2),
                  'total_trades':    int(row['total_trades'] or 0),
                  'wins':            int(row['wins'] or 0),
                  'best_trade_pct':  round(float(best_pct or 0), 2),
                  'best_trade_token': best_token,
              })

          conn.close()
          return results
      except Exception as e:
          logger.error(f'get_top_whales_by_pnl error: {e}')
          return []
  ```

  **Files:** `data/database.py`

  **Verify:**
  ```
  python -c "from data.database import db; r = db.get_top_whales_by_pnl(days=7, limit=5); print(type(r), 'OK')"
  ```
  Returns `<class 'list'> OK` without raising.

---

- [ ] 4. **Update imports in `trading/telegram_broadcaster.py`**

  At the top of the file, find the existing config import line:
  ```python
  from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_BOT_USERNAME
  ```
  Replace it with:
  ```python
  from config import (
      TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_BOT_USERNAME,
      CONVERGENCE_DEDUP_HOURS,
      LEADERBOARD_DAY_OF_WEEK, LEADERBOARD_HOUR_UTC, LEADERBOARD_MIN_WHALES,
  )
  ```
  Also ensure `timedelta` is imported from `datetime`. The current import at the top is:
  ```python
  from datetime import datetime
  ```
  Change it to:
  ```python
  from datetime import datetime, timedelta
  ```

  **Files:** `trading/telegram_broadcaster.py`

  **Verify:** `python -c "from trading.telegram_broadcaster import broadcaster; print('OK')"` — prints `OK`.

---

- [ ] 5. **Add `_posted_convergence` dict to `TelegramBroadcaster.__init__()`**

  In `TelegramBroadcaster.__init__()`, after the line:
  ```python
  self._min_liquidity_usd = int(os.getenv('BROADCAST_MIN_LIQUIDITY_USD', '30000'))
  ```
  Add:
  ```python
  # Convergence alert dedup: { token_mint: timestamp }
  self._posted_convergence: Dict[str, float] = {}
  ```

  **Files:** `trading/telegram_broadcaster.py`

  **Verify:** `python -c "from trading.telegram_broadcaster import broadcaster; print(hasattr(broadcaster, '_posted_convergence'))"` — prints `True`.

---

- [ ] 6. **Add `post_whale_exit()` method to `TelegramBroadcaster`**

  Insert as a new public method in `TelegramBroadcaster`, placed directly after the
  `post_whale_trade()` method (after its closing `return await self._send_message(...)` line
  and before `broadcast_signal()`).

  **Full method:**
  ```python
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
      win_rate: float,          # 0.0–1.0
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

      # Persist for cross-restart dedup
      try:
          from data.database import db
          db.save_posted_signal(signal_hash, token_address=token_mint, action='EXIT')
      except Exception as e:
          logger.warning("Could not persist whale exit signal: %s", e)

      self._cleanup_posted_signals()

      # Format hold duration
      if hold_duration_hours >= 48:
          hold_str = f"{hold_duration_hours / 24:.1f} days"
      else:
          hold_str = f"{hold_duration_hours:.1f} hours"

      safe_mint    = re.sub(r'[^1-9A-HJ-NP-Za-km-z]', '', token_mint)
      whale_short  = f"{whale_address[:6]}...{whale_address[-4:]}"
      usd_value    = sol_amount * price_usd if price_usd > 0 else 0
      bot_username = TELEGRAM_BOT_USERNAME.lstrip('@') if TELEGRAM_BOT_USERNAME else ''

      # Profit/loss line — only shown when profit_pct is available
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
  ```

  **Files:** `trading/telegram_broadcaster.py`

  **Verify:** `python -c "import inspect; from trading.telegram_broadcaster import TelegramBroadcaster; print('post_whale_exit' in dir(TelegramBroadcaster()))"` — prints `True`.

---

- [ ] 7. **Add `post_convergence_alert()` method to `TelegramBroadcaster`**

  Insert directly after the `post_whale_exit()` method (before `broadcast_signal()`).

  **Full method:**
  ```python
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

      # Convergence-specific dedup: same token within CONVERGENCE_DEDUP_HOURS
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

      safe_mint   = re.sub(r'[^1-9A-HJ-NP-Za-km-z]', '', token_mint)
      usd_value   = total_sol * price_usd if price_usd > 0 else 0
      bot_username = TELEGRAM_BOT_USERNAME.lstrip('@') if TELEGRAM_BOT_USERNAME else ''

      # Sort whale_addresses by score descending, show top 3
      paired = sorted(zip(whale_scores, whale_addresses), reverse=True)
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
          top_addr = top_whales[0][1]  # highest-scoring whale address
          deep_link = f"https://t.me/{bot_username}?start=copywhale_{top_addr}"
          reply_markup = InlineKeyboardMarkup([[
              InlineKeyboardButton("🐋 Copy This Whale", url=deep_link)
          ]])

      return await self._send_message(message, reply_markup=reply_markup)
  ```

  **Files:** `trading/telegram_broadcaster.py`

  **Verify:** `python -c "from trading.telegram_broadcaster import TelegramBroadcaster; print('post_convergence_alert' in dir(TelegramBroadcaster()))"` — prints `True`.

---

- [ ] 8. **Add `post_weekly_leaderboard()` method to `TelegramBroadcaster`**

  Insert directly after `post_convergence_alert()` (before `broadcast_signal()`).

  **Full method:**
  ```python
  async def post_weekly_leaderboard(self) -> bool:
      """Post the weekly whale performance leaderboard. Not counted toward hourly cap."""
      if not self.bot:
          return False

      # 6-day cooldown to prevent duplicate posts on restart
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
              len(whales), LEADERBOARD_MIN_WHALES
          )
          return False

      self._last_post_times['leaderboard'] = now

      medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
      bot_username = TELEGRAM_BOT_USERNAME.lstrip('@') if TELEGRAM_BOT_USERNAME else ''

      entry_lines = []
      for i, whale in enumerate(whales):
          medal    = medals[i] if i < len(medals) else "•"
          addr     = whale['watched_wallet']
          short    = f"{addr[:6]}...{addr[-4:]}"
          wins     = whale['wins']
          losses   = whale['total_trades'] - wins
          avg_p    = whale['avg_profit']
          best_p   = whale['best_trade_pct']
          entry_lines.append(
              f"{medal} <code>{self._safe_text(short)}</code>\n"
              f"   +{avg_p:.0f}% avg profit  |  {wins} wins / {losses} losses\n"
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
      return await self._send_message(message)
  ```

  **Files:** `trading/telegram_broadcaster.py`

  **Verify:** `python -c "from trading.telegram_broadcaster import TelegramBroadcaster; print('post_weekly_leaderboard' in dir(TelegramBroadcaster()))"` — prints `True`.

---

- [ ] 9. **Add `run_weekly_leaderboard_loop()` and wire it into `initialize()`**

  Insert the loop method directly after `post_weekly_leaderboard()` (before `broadcast_signal()`).

  **Full method:**
  ```python
  async def run_weekly_leaderboard_loop(self):
      """Background task: post the weekly leaderboard at the configured time."""
      while True:
          try:
              now = datetime.now(timezone.utc)
              days_ahead = (LEADERBOARD_DAY_OF_WEEK - now.weekday()) % 7
              if days_ahead == 0 and (
                  now.hour > LEADERBOARD_HOUR_UTC
                  or (now.hour == LEADERBOARD_HOUR_UTC and now.minute >= 5)
              ):
                  days_ahead = 7
              next_post = now.replace(
                  hour=LEADERBOARD_HOUR_UTC, minute=0, second=0, microsecond=0
              )
              next_post += timedelta(days=days_ahead)
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
              await asyncio.sleep(3600)  # retry in 1 hour on unexpected error
  ```

  Also add `timezone` to the `datetime` import at the top. Change:
  ```python
  from datetime import datetime, timedelta
  ```
  to:
  ```python
  from datetime import datetime, timedelta, timezone
  ```

  Also update `initialize()` — after the existing:
  ```python
  asyncio.create_task(self._heartbeat_loop())
  ```
  add:
  ```python
  asyncio.create_task(self.run_weekly_leaderboard_loop())
  ```

  **Files:** `trading/telegram_broadcaster.py`

  **Verify:**
  ```
  python -c "from trading.telegram_broadcaster import TelegramBroadcaster; print('run_weekly_leaderboard_loop' in dir(TelegramBroadcaster()))"
  ```
  — prints `True`.

---

- [ ] 10. **Update imports in `trading/copy_trader.py`**

  Find the existing `from config import (...)` block (lines 28–47 in the current file).
  Add these four names to the import tuple:
  ```python
  CONVERGENCE_MIN_WHALES, CONVERGENCE_TIME_WINDOW_MINUTES, CONVERGENCE_DEDUP_HOURS,
  WHALE_EXIT_MAX_HOLD_DAYS,
  ```
  The full updated import should read (add these after `ENABLE_DAILY_LOSS_LIMIT,`):
  ```python
  from config import (
      SOLANA_RPC_URL, SOLANA_WSS_URL,
      COPY_TRADE_CHECK_INTERVAL, MIN_TRADE_AMOUNT,
      DEFAULT_COPY_SCALE, WSOL_MINT,
      WHALE_MIN_TRADES, WHALE_MIN_WIN_RATE, WHALE_MIN_AVG_PROFIT,
      WHALE_STALE_DAYS,
      COPY_SIGNAL_WINDOW_SECONDS, COPY_LOSS_CHECK_WINDOW,
      COPY_DEFAULT_PROFIT_TARGET, COPY_DEFAULT_TRAILING_STOP,
      COPY_DEFAULT_MAX_LOSS, COPY_DEFAULT_MAX_HOLD_HOURS,
      COPY_MAX_PRICE_IMPACT_PCT,
      COPY_SIGNAL_SCALE_1_WHALE, COPY_SIGNAL_SCALE_2_WHALE, COPY_SIGNAL_SCALE_3_WHALE,
      BROADCAST_MIN_LIQUIDITY_USD,
      ENABLE_JITO_PROTECTION, JITO_MIN_TRADE_SOL,
      ENABLE_DAILY_LOSS_LIMIT,
      CONVERGENCE_MIN_WHALES, CONVERGENCE_TIME_WINDOW_MINUTES, CONVERGENCE_DEDUP_HOURS,
      WHALE_EXIT_MAX_HOLD_DAYS,
  )
  ```

  **Files:** `trading/copy_trader.py`

  **Verify:** `python -c "from trading.copy_trader import CopyTradingEngine; print('OK')"` — prints `OK`.

---

- [ ] 11. **Add `_posted_convergence_signals` dict to `CopyTradingEngine.__init__()`**

  In `CopyTradingEngine.__init__()`, after the existing line:
  ```python
  self._whale_momentum_exit_pending: Dict[tuple, Dict] = {}
  ```
  Add:
  ```python
  # Convergence signal dedup: { token_mint: timestamp }
  self._posted_convergence_signals: Dict[str, float] = {}
  ```

  **Files:** `trading/copy_trader.py`

  **Verify:** `python -c "from trading.copy_trader import CopyTradingEngine; e = CopyTradingEngine(); print(hasattr(e, '_posted_convergence_signals'))"` — prints `True`.

---

- [ ] 12. **Add whale exit alert hook in `_handle_whale_swap()`**

  Locate the SELL detection block at the top of `_handle_whale_swap()`. The current block is:
  ```python
  if swap_data.get('outputMint') == WSOL_MINT:
      sold_token = swap_data.get('inputMint', '')
      if sold_token and sold_token != WSOL_MINT:
          existing = db.get_pending_trade_by_token(user_id, sold_token)
          if existing:
              ...
              asyncio.create_task(
                  self._exit_position(...)
              )
          # Don't copy the sell trade itself — return regardless
          return
  ```

  Insert the exit-alert block **between** the `if existing:` block's close and the `# Don't copy the sell trade itself — return regardless` comment. The new code fires after mirroring the exit (whether `existing` was found or not), before the `return`:

  ```python
          # Post whale exit alert to channel
          try:
              from trading.whale_scorer import whale_scorer as _ws
              whale_stats = db.get_whale_stats(user_id, whale_address)
              recent_buys = db.get_copy_performance(user_id, whale_address, limit=50)
              entry_record = None
              for rec in recent_buys:
                  if (rec.get('token_address') == sold_token
                          and rec.get('status') == 'open'
                          and rec.get('whale_entry_price')):
                      entry_record = rec
                      break
              profit_pct = None
              hold_duration_hours = 0.0
              if entry_record:
                  entry_price = entry_record.get('whale_entry_price', 0)
                  exit_price  = swap_data.get('whaleEntryPrice', 0)
                  if entry_price and exit_price:
                      profit_pct = ((exit_price - entry_price) / entry_price) * 100
                  opened_at = entry_record.get('opened_at')
                  if opened_at:
                      import time as _time
                      opened_ts = (
                          opened_at.timestamp()
                          if hasattr(opened_at, 'timestamp')
                          else float(opened_at)
                      )
                      hold_duration_hours = (_time.time() - opened_ts) / 3600
              # Only post if hold time is within WHALE_EXIT_MAX_HOLD_DAYS
              if hold_duration_hours <= WHALE_EXIT_MAX_HOLD_DAYS * 24 or hold_duration_hours == 0.0:
                  whale_score_val = _ws.score_whale(user_id, whale_address) if whale_stats else 0.0
                  win_rate_val    = 0.0
                  total_trades_val = 0
                  avg_profit_val  = 0.0
                  if whale_stats:
                      total = whale_stats.get('total_trades', 0)
                      wins  = whale_stats.get('winning_trades', 0)
                      win_rate_val     = wins / total if total > 0 else 0.0
                      total_trades_val = total
                      avg_profit_val   = whale_stats.get('avg_profit_pct', 0.0)
                  asyncio.create_task(tg_broadcaster.post_whale_exit(
                      whale_address=whale_address,
                      token_symbol=sold_token[:6],
                      token_mint=sold_token,
                      sol_amount=float(swap_data.get('inputAmount', 0.0)),
                      price_usd=0.0,
                      profit_pct=profit_pct,
                      hold_duration_hours=hold_duration_hours,
                      whale_score=whale_score_val,
                      win_rate=win_rate_val,
                      total_trades=total_trades_val,
                      avg_profit=avg_profit_val,
                  ))
          except Exception as _e:
              logger.warning('Whale exit alert error: %s', _e)
  ```

  The `return` statement at the end of the outer `if sold_token and sold_token != WSOL_MINT:` block is **NOT** moved or changed.

  **Files:** `trading/copy_trader.py`

  **Verify:** `python -c "from trading.copy_trader import CopyTradingEngine; print('OK')"` — prints `OK` (import succeeds).

---

- [ ] 13. **Add convergence check in `_register_signal()` — move before `already_executed` early return**

  The current `_register_signal()` method (around lines 342–376 in the file) has this structure:
  ```python
  unique_count = len({s['whale'] for s in existing})
  already_executed = any(s.get('executed') for s in existing)

  if already_executed:
      if is_new_whale:
          logger.info(...)
      return False, unique_count, 1.0
  ```

  **Insert** the convergence check block **before** the `if already_executed:` block — so it runs
  every time the count reaches the threshold, regardless of whether a trade was already executed:

  ```python
  # ── Convergence alert ────────────────────────────────────────────────
  if unique_count >= CONVERGENCE_MIN_WHALES:
      now_c    = time.time()
      dedup_ttl = CONVERGENCE_DEDUP_HOURS * 3600
      last_conv = self._posted_convergence_signals.get(token, 0)
      if now_c - last_conv >= dedup_ttl:
          self._posted_convergence_signals[token] = now_c
          whale_list = list({s['whale'] for s in existing})
          try:
              from trading.whale_scorer import whale_scorer as _ws2
              scores = [_ws2.score_whale(user_id, w) for w in whale_list]
          except Exception:
              scores = [0.0] * len(whale_list)
          total_sol_conv  = sum(s['amount'] for s in existing)
          oldest_ts       = min(s['ts'] for s in existing)
          time_window_min = int((now_c - oldest_ts) / 60)
          asyncio.create_task(tg_broadcaster.post_convergence_alert(
              token_symbol=token[:6],
              token_mint=token,
              whale_count=unique_count,
              total_sol=total_sol_conv,
              price_usd=0.0,
              whale_addresses=whale_list,
              whale_scores=scores,
              time_window_minutes=time_window_min,
          ))
  # ─────────────────────────────────────────────────────────────────────

  if already_executed:
      ...
  ```

  The insertion is **before** `if already_executed:` and **after** `already_executed = any(...)`.

  **Files:** `trading/copy_trader.py`

  **Verify:** `python -c "from trading.copy_trader import CopyTradingEngine; print('OK')"` — prints `OK` (no import or syntax error).

---

## Dependency Order Summary

| Step | Depends on |
|------|-----------|
| 1 config.py keys | — |
| 2 .env.example   | — |
| 3 db method       | 1 (config import inside db) |
| 4 broadcaster imports | 1 |
| 5 broadcaster __init__ | 4 |
| 6 post_whale_exit | 4, 5 |
| 7 post_convergence_alert | 4, 5 |
| 8 post_weekly_leaderboard | 3, 4, 5 |
| 9 leaderboard loop + initialize wire | 8 |
| 10 copy_trader imports | 1 |
| 11 copy_trader __init__ | 10 |
| 12 _handle_whale_swap hook | 6, 10, 11 |
| 13 _register_signal hook | 7, 10, 11 |

All steps after step 1 are safe to sequence 2→3→4→5→6→7→8→9→10→11→12→13.

## Final Integration Smoke Test

After all steps are complete, run:
```
python -c "
from config import CONVERGENCE_MIN_WHALES, LEADERBOARD_DAY_OF_WEEK, WHALE_EXIT_MAX_HOLD_DAYS
from data.database import db
from trading.telegram_broadcaster import broadcaster
from trading.copy_trader import CopyTradingEngine
r = db.get_top_whales_by_pnl(days=7, limit=5)
e = CopyTradingEngine()
print('config OK, db OK, broadcaster OK, copy_trader OK')
print('_posted_convergence:', hasattr(broadcaster, '_posted_convergence'))
print('_posted_convergence_signals:', hasattr(e, '_posted_convergence_signals'))
print('post_whale_exit:', hasattr(broadcaster, 'post_whale_exit'))
print('post_convergence_alert:', hasattr(broadcaster, 'post_convergence_alert'))
print('post_weekly_leaderboard:', hasattr(broadcaster, 'post_weekly_leaderboard'))
print('get_top_whales_by_pnl result type:', type(r))
"
```
All lines should print `True` or the expected type/string.
