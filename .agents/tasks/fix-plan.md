# Implementation Plan — Bot Fixes & Channel Improvements
**Generated from:** full-audit.md + live file inspection  
**Files in scope:** `bot/telegram_bot.py`, `trading/telegram_broadcaster.py`, `config.py`, `.env.example`

---

## CRITICAL FIXES

- [ ] 1. **C1 — Fix `copy_stats_callback` NameError (`smart_trades` undefined)**

  `db.get_user_stats()` returns a dict; `smart_trades` is never assigned before use at line 3167.
  Replace the bare name with a `.get()` call so the function never raises `NameError`.

  **File:** `bot/telegram_bot.py`

  **Change:**
  - Line 3167 — replace:
    ```python
            f"  - Smart Trades: {smart_trades}\n"
    ```
    with:
    ```python
            f"  - Smart Trades: {stats.get('total_smart_trades', 0)}\n"
    ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 2. **C2 — Add missing `SOLANA_RPC_URL` import**

  `_monitor_transaction` (line 157) uses `SOLANA_RPC_URL` at runtime but it is not in the
  `from config import …` line at line 13. `config.py` line 41 confirms the symbol exists.

  **File:** `bot/telegram_bot.py`

  **Change:**
  - Line 13 — replace:
    ```python
    from config import TELEGRAM_BOT_TOKEN, BIRDEYE_API_KEY, BIRDEYE_API_URL, JUPITER_QUOTE_TIMEOUT
    ```
    with:
    ```python
    from config import TELEGRAM_BOT_TOKEN, BIRDEYE_API_KEY, BIRDEYE_API_URL, JUPITER_QUOTE_TIMEOUT, SOLANA_RPC_URL
    ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 3. **C3 — Add sell button handlers to `HOLDINGS_SELL_CONFIRM` state + implement handler methods**

  The `holdings_sell_callback` (line ~3016) renders four buttons with callback data
  `hold_sell_pct_25`, `hold_sell_pct_50`, `hold_sell_pct_100`, `hold_sell_custom`.
  None of these appear in the `HOLDINGS_SELL_CONFIRM` state (lines 4530–4533).
  There are also no handler methods for these patterns in the class.

  **Step A — Implement two handler methods** inside `TelegramBot`, directly after
  `holdings_send_callback` ends (insert before `copy_stats_callback` at line 3142):

  ```python
  async def hold_sell_pct_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Execute sell at 25 / 50 / 100 % of held balance."""
      await update.callback_query.answer()
      user_id = update.effective_user.id
      data = update.callback_query.data  # hold_sell_pct_25 / _50 / _100

      pct = int(data.split("_")[-1]) / 100.0
      mint   = context.user_data.get('hold_sell_mint', '')
      balance = context.user_data.get('hold_sell_balance', 0)
      symbol  = context.user_data.get('hold_sell_symbol', 'TOKEN')

      if not mint or balance <= 0:
          await update.callback_query.edit_message_text("❌ No token data. Please go back and retry.")
          return MENU

      sell_amount = balance * pct
      user = db.get_user(user_id)
      if not user:
          await update.callback_query.answer("⚠️ No wallet found.", show_alert=True)
          return MENU

      await update.callback_query.edit_message_text(
          f"⏳ Selling {pct*100:.0f}% of {symbol}…\n"
          f"Amount: `{sell_amount:.6f} {symbol}`\n\n"
          f"Fetching quote from Jupiter…",
          parse_mode='Markdown'
      )
      try:
          from config import WSOL_MINT
          keypair = None
          raw_key = decrypt_private_key(user['encrypted_private_key'])
          if raw_key:
              keypair = self.wallet_manager.import_keypair(raw_key)
          if not keypair:
              await update.callback_query.edit_message_text("❌ Could not load wallet key.")
              return MENU
          result = await swapper.execute_swap(mint, WSOL_MINT, sell_amount, 'jupiter', keypair=keypair)
          if result and result.get('status') == 'confirmed':
              sol_out = result.get('expectedOutput', 0)
              tx = result.get('tx_hash', result.get('signature', 'pending'))
              await update.callback_query.edit_message_text(
                  f"✅ **Sold {pct*100:.0f}% of {symbol}!**\n\n"
                  f"Sold: `{sell_amount:.6f} {symbol}`\n"
                  f"Received: ~`{sol_out:.6f} SOL`\n"
                  f"TX: `{str(tx)[:40]}…`",
                  parse_mode='Markdown'
              )
          else:
              await update.callback_query.edit_message_text("❌ Sell failed. Check balance and try again.")
      except Exception as e:
          logger.error(f"hold_sell_pct_callback error: {e}")
          await update.callback_query.edit_message_text(f"❌ Error: {e}")
      await self.show_main_menu(update, context)
      return MENU

  async def hold_sell_custom_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Ask user for a custom sell amount."""
      await update.callback_query.answer()
      mint   = context.user_data.get('hold_sell_mint', '')
      symbol  = context.user_data.get('hold_sell_symbol', 'TOKEN')
      balance = context.user_data.get('hold_sell_balance', 0)
      keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="my_holdings")]]
      await update.callback_query.edit_message_text(
          f"📝 **Custom Sell Amount**\n\n"
          f"Token: **{symbol}**\n"
          f"Balance: `{balance:.6f}`\n\n"
          f"Enter the amount to sell (e.g. `500`):",
          reply_markup=InlineKeyboardMarkup(keyboard),
          parse_mode='Markdown'
      )
      context.user_data['hold_sell_step'] = 'custom_amount'
      return HOLDINGS_SELL_CONFIRM

  async def handle_hold_sell_custom_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Handle the typed custom sell amount."""
      user_id = update.effective_user.id
      mint    = context.user_data.get('hold_sell_mint', '')
      symbol  = context.user_data.get('hold_sell_symbol', 'TOKEN')
      balance = context.user_data.get('hold_sell_balance', 0)
      try:
          sell_amount = float(update.message.text.strip())
          if sell_amount <= 0 or sell_amount > balance:
              raise ValueError("out of range")
      except ValueError:
          keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="my_holdings")]]
          await update.message.reply_text(
              f"❌ Invalid amount. Must be between 0 and `{balance:.6f}`.",
              reply_markup=InlineKeyboardMarkup(keyboard),
              parse_mode='Markdown'
          )
          return HOLDINGS_SELL_CONFIRM

      user = db.get_user(user_id)
      if not user:
          await update.message.reply_text("⚠️ No wallet found.")
          return MENU

      await update.message.reply_text(f"⏳ Selling `{sell_amount:.6f} {symbol}`…", parse_mode='Markdown')
      try:
          from config import WSOL_MINT
          keypair = None
          raw_key = decrypt_private_key(user['encrypted_private_key'])
          if raw_key:
              keypair = self.wallet_manager.import_keypair(raw_key)
          if not keypair:
              await update.message.reply_text("❌ Could not load wallet key.")
              return MENU
          result = await swapper.execute_swap(mint, WSOL_MINT, sell_amount, 'jupiter', keypair=keypair)
          if result and result.get('status') == 'confirmed':
              sol_out = result.get('expectedOutput', 0)
              tx = result.get('tx_hash', result.get('signature', 'pending'))
              await update.message.reply_text(
                  f"✅ **Sold `{sell_amount:.6f} {symbol}`!**\n\n"
                  f"Received: ~`{sol_out:.6f} SOL`\n"
                  f"TX: `{str(tx)[:40]}…`",
                  parse_mode='Markdown'
              )
          else:
              await update.message.reply_text("❌ Sell failed. Check balance and try again.")
      except Exception as e:
          logger.error(f"handle_hold_sell_custom_amount error: {e}")
          await update.message.reply_text(f"❌ Error: {e}")
      await self.show_main_menu(update, context)
      return MENU
  ```

  **Step B — Register handlers in `HOLDINGS_SELL_CONFIRM` state** (lines 4530–4533).
  Replace:
  ```python
          HOLDINGS_SELL_CONFIRM: [
              CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
              CommandHandler("start", bot.start),
          ],
  ```
  with:
  ```python
          HOLDINGS_SELL_CONFIRM: [
              CallbackQueryHandler(bot.hold_sell_pct_callback,    pattern="^hold_sell_pct_"),
              CallbackQueryHandler(bot.hold_sell_custom_callback,  pattern="^hold_sell_custom$"),
              MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_hold_sell_custom_amount),
              CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
              CallbackQueryHandler(bot.my_holdings_callback, pattern="^my_holdings$"),
              CommandHandler("start", bot.start),
          ],
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 4. **C4 — Replace dead `admin_test_news_callback` with working `admin_test_whale_alert_callback`**

  `admin_test_news_callback` (lines 4042–4116) calls `broadcaster._fetch_from_sources`,
  `broadcaster._news_sources`, and `broadcaster._min_news_relevance` — none of which exist
  on the current `TelegramBroadcaster`. It always crashes with `AttributeError`.

  **Step A — Replace the entire `admin_test_news_callback` method body** (lines 4042–4116)
  with a new method that calls `broadcaster.post_whale_trade()` with realistic dummy data:

  ```python
  async def admin_test_news_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Test whale alert broadcast — replaces defunct news test."""
      await update.callback_query.answer()
      await update.callback_query.edit_message_text(
          "📡 Sending test whale alert to channel…",
          reply_markup=InlineKeyboardMarkup([[
              InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
          ]])
      )
      try:
          from trading.telegram_broadcaster import broadcaster
          result = await broadcaster.post_whale_trade(
              whale_address="7vfCXTUXx5WJV5JADk17DUJ4k6au4qp9qzvYFQmrVE7",
              token_symbol="BONK",
              token_mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
              action="BUY",
              sol_amount=45.2,
              price_usd=149.5,
              whale_score=82.5,
              win_rate=0.73,
              total_trades=247,
              signal_count=3,
              avg_profit=38.0,
          )
          if result:
              await update.callback_query.edit_message_text(
                  "✅ **Test Whale Alert Sent!**\n\n"
                  "Check your Telegram channel to see the alert.\n\n"
                  "If you don't see it, verify:\n"
                  "• TELEGRAM_CHANNEL_ID is set correctly in .env\n"
                  "• Bot has admin permissions in the channel\n"
                  "• Not a duplicate within the 15-minute dedup window",
                  reply_markup=InlineKeyboardMarkup([
                      [InlineKeyboardButton("🔄 Send Again", callback_data="admin_test_news")],
                      [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
                  ]),
                  parse_mode='Markdown'
              )
          else:
              await update.callback_query.edit_message_text(
                  "❌ **Test Alert Not Sent**\n\n"
                  "Possible reasons:\n"
                  "• TELEGRAM_CHANNEL_ID not set or incorrect\n"
                  "• Bot not admin in channel\n"
                  "• Rate limit (max 1 per 2 min)\n"
                  "• Duplicate signal within 15-minute window\n\n"
                  "Check bot.log for detailed errors.",
                  reply_markup=InlineKeyboardMarkup([
                      [InlineKeyboardButton("🔄 Try Again", callback_data="admin_test_news")],
                      [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
                  ]),
                  parse_mode='Markdown'
              )
      except Exception as e:
          logger.error(f"Admin test whale alert failed: {e}")
          await update.callback_query.edit_message_text(
              f"❌ **Error**\n\n```\n{str(e)[:400]}\n```",
              reply_markup=InlineKeyboardMarkup([[
                  InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
              ]]),
              parse_mode='Markdown'
          )
      return ADMIN_MENU
  ```

  **Step B — Update the admin panel keyboard** (line 3798) to reflect the new purpose.
  Replace:
  ```python
              [InlineKeyboardButton("📡 Test News Broadcast", callback_data="admin_test_news")],
  ```
  with:
  ```python
              [InlineKeyboardButton("📡 Test Whale Alert", callback_data="admin_test_news")],
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 5. **C5 — Add global error handler**

  No `application.add_error_handler()` is registered anywhere. Unhandled exceptions in
  callbacks are silently swallowed by python-telegram-bot.

  **File:** `bot/telegram_bot.py`

  **Change:** After `application.add_handler(conv_handler)` at line 4661, insert:
  ```python
  async def _error_handler(update, context):
      logger.error("Unhandled exception in handler", exc_info=context.error)
      if update and update.callback_query:
          try:
              await update.callback_query.answer(
                  "⚠️ Something went wrong. Please try again.", show_alert=True
              )
          except Exception:
              pass
      elif update and update.message:
          try:
              await update.message.reply_text("⚠️ An error occurred. Please try /start.")
          except Exception:
              pass

  application.add_error_handler(_error_handler)
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

## MEDIUM FIXES — Missing `query.answer()` calls

- [ ] 6. **M1–M7 — Add `await update.callback_query.answer()` as first executable line**

  Seven callbacks edit the message without first answering the callback query, leaving
  Telegram's loading spinner permanently stuck.

  **File:** `bot/telegram_bot.py`

  **Changes (one per function, add as first line of body after the docstring):**

  | # | Function | Line | Insert after line |
  |---|---|---|---|
  | M1 | `swap_amount_prompt` | 546 | Line 547 (after `direction = …`) — insert `await update.callback_query.answer()` before `direction = update.callback_query.data` |
  | M2 | `st_manual_callback` | 1254 | Line 1255 (docstring) — add as first executable line (before `user_id = …`) |
  | M3 | `handle_trade_percent` (second, live one at 1671) | 1671 | After docstring at 1672 — add before `user_id = …` at line 1673 |
  | M4 | `add_watch_wallet_callback` | 3356 | After docstring at 3357 — add before `keyboard = [` at line 3358 |
  | M5 | `view_trades_callback` | 3392 | After docstring at 3393 — add before `user_id = …` at line 3394 |
  | M6 | `daily_report_callback` | 3529 | After docstring at 3530 — add before `user_id = …` at line 3531 |
  | M7 | `handle_vanity_generation` | 3637 | After docstring at 3638 — add before `difficulty = …` at line 3639 |

  For `swap_amount_prompt` specifically, the first executable line is already
  `direction = update.callback_query.data` — insert `await update.callback_query.answer()`
  before it (i.e. make it the new first line of the body after the docstring would be, but
  since there is no docstring, insert it as the very first line of the function body at line 547).

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 7. **M8 — Delete dead first `handle_trade_percent` definition + add `return MENU`**

  `handle_trade_percent` is defined twice: line 1281 (dead, never called) and line 1671
  (live). Python silently discards the first. Also, `copy_trade_callback` (line 1802) never
  returns a state.

  **File:** `bot/telegram_bot.py`

  **Change A — Delete lines 1281–1300** (the entire first `handle_trade_percent` definition,
  from `async def handle_trade_percent` through `return SMART_TOKEN_INPUT`). Exact span:
  lines 1281–1300. Confirm by checking: first line is
  `    async def handle_trade_percent(self, update …` and last line is
  `        return SMART_TOKEN_INPUT`.

  **Change B — Add `return MENU` to `copy_trade_callback`.**
  After line 1817 (the `edit_message_text` closing paren `)`), add:
  ```python
      return MENU
  ```
  That makes the full function end:
  ```python
          await update.callback_query.edit_message_text(
              "🐋 **Copy Trading — Solana**\n\nWhat would you like to do?",
              reply_markup=reply_markup,
              parse_mode='Markdown'
          )
          return MENU
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 8. **M9–M12 — Fix conversation state dead ends (missing back handlers)**

  Four states trap users who change their mind, because no `back_menu` handler is registered
  and/or no Back button is shown.

  **File:** `bot/telegram_bot.py`

  **Change A — `VANITY_PREFIX` state (line 4575).** Replace:
  ```python
          VANITY_PREFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_vanity_prefix)],
  ```
  with:
  ```python
          VANITY_PREFIX: [
              MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_vanity_prefix),
              CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
          ],
  ```
  Also update `create_vanity_callback` (line ~3566) to add a Back button to the prompt keyboard:
  ```python
          keyboard = [
              [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
          ]
  ```
  *(Already has this — confirm it is `back_menu` not `tools`. If it says `tools`, change to `back_menu`.)*

  **Change B — `ADD_WALLET` state (line 4521).** Replace:
  ```python
          ADD_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_watch_wallet)],
  ```
  with:
  ```python
          ADD_WALLET: [
              MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_watch_wallet),
              CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
          ],
  ```

  **Change C — `BLACKLIST_TOKEN_INPUT` state (line 4564).** Replace:
  ```python
          BLACKLIST_TOKEN_INPUT: [
              MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_blacklist_input),
          ],
  ```
  with:
  ```python
          BLACKLIST_TOKEN_INPUT: [
              MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_blacklist_input),
              CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
              CallbackQueryHandler(bot.smart_trade_callback, pattern="^smart_trade$"),
          ],
  ```

  **Change D — `ADMIN_MASTER_PASSWORD` state (line 4599) — add Back handler and show Back button.**
  Replace:
  ```python
          ADMIN_MASTER_PASSWORD: [
              MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_admin_decrypt),
          ],
  ```
  with:
  ```python
          ADMIN_MASTER_PASSWORD: [
              MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_admin_decrypt),
              CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
          ],
  ```
  Also update `admin_decrypt_callback` body to include a Back button in the keyboard shown to the user:
  ```python
      async def admin_decrypt_callback(self, update, context):
          await update.callback_query.answer()
          keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
          await update.callback_query.edit_message_text(
              "🔐 Enter master password to decrypt keys:\n"
              "(Type password or /cancel to exit)",
              reply_markup=InlineKeyboardMarkup(keyboard)
          )
          return ADMIN_MASTER_PASSWORD
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 9. **M13 — Add `/help` and `/status` command handlers**

  Only `/start` and `/cancel` are registered. Users have no `/help` or `/status`.

  **File:** `bot/telegram_bot.py`

  **Change A — Add two methods to `TelegramBot` class** (insert after `cancel` method near
  line 3775):
  ```python
  async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      await update.message.reply_text(
          "📖 *Available Commands*\n\n"
          "/start — open main menu\n"
          "/status — show copy-trade & auto-trade status\n"
          "/help — show this message\n\n"
          "Use the inline buttons for everything else.",
          parse_mode='Markdown'
      )

  async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      user_id = update.effective_user.id
      is_copy = copy_trader.is_monitoring(user_id) if hasattr(copy_trader, 'is_monitoring') else False
      is_smart = smart_trader.is_auto_trading(user_id)
      await update.message.reply_text(
          f"📊 *Bot Status*\n\n"
          f"🐋 Copy Trading: {'🟢 Active' if is_copy else '🔴 Off'}\n"
          f"🤖 Auto Trader: {'🟢 Active' if is_smart else '🔴 Off'}\n\n"
          "Use /start to manage settings.",
          parse_mode='Markdown'
      )
  ```

  **Change B — Register the handlers in `main()`** after `application.add_handler(conv_handler)`
  at line 4661 (after the error handler added in step 5):
  ```python
  application.add_handler(CommandHandler("help",   bot._help_command))
  application.add_handler(CommandHandler("status", bot._status_command))
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

## LOW FIXES — UX Polish

- [ ] 10. **L1 — Replace generic welcome message**

  New-user welcome message is in `start` handler in two places (callback_query path at
  lines 258–267 and message path at lines 269–278). Both say "Solana DEX Copy Trader" with
  generic bullet points.

  **File:** `bot/telegram_bot.py`

  **Change — Replace both welcome text blocks** (callback_query path lines 259–264, message
  path lines 270–275) with identical new text:
  ```python
  "👋 *Welcome to Whale Copy Trader*\n\n"
  "The bot tracks profitable Solana whale wallets in real-time and copies their "
  "trades automatically — so you follow the money, not the hype.\n\n"
  "✅ Live whale alerts posted to our channel\n"
  "✅ Auto-copy trades from top-performing wallets\n"
  "✅ Manual swap, send & portfolio tools\n\n"
  "Connect a Solana wallet to get started:"
  ```
  Keep `reply_markup=reply_markup` and `parse_mode='Markdown'` unchanged.

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

- [ ] 11. **L2 — Update main menu button labels**

  `show_main_menu` keyboard (lines 315–325). Rename four buttons — callback_data unchanged,
  only the label string changes.

  **File:** `bot/telegram_bot.py`

  **Changes at lines 319, 320, 322, 325:**

  - Line 319: `"🤖 Smart Analyzer"` → `"🤖 Auto Trader"`
  - Line 320: `"📊 My Holdings"` → `"💼 Holdings"`
  - Line 322: `"⚠️ Risk"` → `"🛡️ Risk Mgmt"`
  - Line 325: `"⚙️"` → `"⚙️ Settings"`

  Full updated keyboard block (lines 315–325) for reference:
  ```python
          keyboard = [
              [InlineKeyboardButton("💱 Swap", callback_data="swap"),
               InlineKeyboardButton("📤 Send", callback_data="send_tokens"),
               InlineKeyboardButton("📥 Receive", callback_data="receive")],
              [InlineKeyboardButton("🐋 Copy Trade", callback_data="copy_trade"),
               InlineKeyboardButton("🤖 Auto Trader", callback_data="smart_trade")],
              [InlineKeyboardButton("💼 Holdings", callback_data="my_holdings"),
               InlineKeyboardButton("📂 Positions", callback_data="active_positions")],
              [InlineKeyboardButton("🛡️ Risk Mgmt", callback_data="risk_mgmt")],
              [InlineKeyboardButton("📊 Analytics", callback_data="analytics"),
               InlineKeyboardButton("🔧 Tools", callback_data="tools"),
               InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
          ]
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile bot/telegram_bot.py` — no output = pass.

---

## CHANNEL POST IMPROVEMENTS

- [ ] 12. **L3 — Upgrade `post_whale_trade()` message format in `telegram_broadcaster.py`**

  The current format (lines ~161–185 in `telegram_broadcaster.py`) is text-only with no
  clickable links, no avg profit, no CTA. Upgrade to a rich HTML format.

  **File:** `trading/telegram_broadcaster.py`

  **Change A — Add `avg_profit: float = 0.0` parameter** to `post_whale_trade` signature.
  Current signature (line ~117):
  ```python
  async def post_whale_trade(
      self,
      whale_address: str,
      token_symbol: str,
      token_mint: str,
      action: str,
      sol_amount: float,
      price_usd: float,
      whale_score: float,
      win_rate: float,
      total_trades: int,
      signal_count: int = 1,
  ) -> bool:
  ```
  New signature:
  ```python
  async def post_whale_trade(
      self,
      whale_address: str,
      token_symbol: str,
      token_mint: str,
      action: str,
      sol_amount: float,
      price_usd: float,
      whale_score: float,
      win_rate: float,
      total_trades: int,
      signal_count: int = 1,
      avg_profit: float = 0.0,
  ) -> bool:
  ```

  **Change B — Replace the `message = (…)` block** (approximately lines 161–185) with the
  upgraded format. Find the block that starts with `message = (` and `"<b>🐋 Whale Alert</b>"`,
  and replace it entirely with:
  ```python
          usd_value = sol_amount * price_usd if price_usd > 0 else 0
          action_label = "BUY 🟢" if action.upper() == "BUY" else "SELL 🔴"
          whale_short = f"{whale_address[:6]}...{whale_address[-4:]}"
          bot_username = os.getenv('TELEGRAM_BOT_USERNAME', '')

          if signal_count >= 3:
              strength_emoji = "🔥"
              strength = "STRONG"
          elif signal_count >= 2:
              strength_emoji = "⚡"
              strength = "MEDIUM"
          else:
              strength_emoji = "📡"
              strength = "NORMAL"

          signal_line = (
              f"{strength_emoji} <b>{signal_count} whale{'s' if signal_count > 1 else ''} "
              f"{'buying' if action.upper() == 'BUY' else 'selling'} — {strength} signal</b>"
          )

          avg_profit_line = (
              f"   💵 Avg Profit: +{avg_profit:.0f}%\n" if avg_profit > 0 else ""
          )

          cta_line = (
              f"👉 Start copying: @{self._safe_text(bot_username)}\n"
              if bot_username else ""
          )

          lines = [
              f"🐋 <b>WHALE ALERT — {self._safe_text(action_label)}</b>",
              "─────────────────────",
              f"🪙 Token: <b>${self._safe_text(token_symbol)}</b>  •  "
              f"<a href='https://dexscreener.com/solana/{token_mint}'>Chart</a>  •  "
              f"<a href='https://solscan.io/token/{token_mint}'>Solscan</a>",
              f"💰 Size: <b>{sol_amount:.2f} SOL</b>"
              + (f"  (~${usd_value:,.0f})" if usd_value > 0 else ""),
              "",
              f"🐳 Whale <code>{self._safe_text(whale_short)}</code>",
              f"   📈 Win Rate: {int(win_rate * 100)}%  |  {total_trades} trades",
              avg_profit_line.rstrip('\n') if avg_profit_line else None,
              f"   ⭐ Score: {whale_score:.1f}/100",
              "",
              signal_line,
              "",
              "📊 Bot is copying this trade automatically.",
              cta_line.rstrip('\n') if cta_line else None,
          ]

          message = "\n".join(line for line in lines if line is not None)
  ```

  **Verify:** `c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile trading/telegram_broadcaster.py` — no output = pass.

---

- [ ] 13. **L4 — Add `TELEGRAM_BOT_USERNAME` to `config.py` and `.env.example`**

  `post_whale_trade` now reads `TELEGRAM_BOT_USERNAME` from env. Add it to config and example.

  **File A: `config.py`**
  After `TELEGRAM_CHANNEL_ID` line (line 10), add:
  ```python
  TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '')
  ```

  **File B: `.env.example`**
  After the `TELEGRAM_CHANNEL_ID=` line, add:
  ```
  TELEGRAM_BOT_USERNAME=
  ```
  (empty placeholder — user fills in their bot's @username without the `@`)

  **Verify:**
  ```
  c:\Users\user\Desktop\copytradebot\.venv\Scripts\python.exe -m py_compile config.py
  ```
  No output = pass.

---

## FINAL VERIFICATION

- [ ] 14. **Run compile checks on all three modified files**

  ```
  cd c:\Users\user\Desktop\copytradebot

  .venv\Scripts\python.exe -m py_compile bot/telegram_bot.py
  .venv\Scripts\python.exe -m py_compile trading/telegram_broadcaster.py
  .venv\Scripts\python.exe -m py_compile config.py
  ```

  All three must produce zero output (no errors). If any file fails, fix the reported line
  before marking the plan complete.

---

## Quick reference: exact line numbers confirmed from live files

| Symbol | File | Line |
|---|---|---|
| `from config import …` (to extend) | telegram_bot.py | 13 |
| `_monitor_transaction` (uses SOLANA_RPC_URL) | telegram_bot.py | 157 |
| `start` — callback_query welcome text block | telegram_bot.py | 258–267 |
| `start` — message welcome text block | telegram_bot.py | 269–278 |
| `show_main_menu` keyboard block | telegram_bot.py | 315–325 |
| `swap_amount_prompt` def | telegram_bot.py | 546 |
| `st_manual_callback` def | telegram_bot.py | 1254 |
| `handle_trade_percent` (dead, first) | telegram_bot.py | 1281 |
| `handle_trade_percent` (live, second) | telegram_bot.py | 1671 |
| `copy_trade_callback` def | telegram_bot.py | 1802 |
| `copy_trade_callback` last line before missing `return` | telegram_bot.py | 1817 |
| `copy_stats_callback` def | telegram_bot.py | 3142 |
| `copy_stats_callback` — `smart_trades` NameError | telegram_bot.py | 3167 |
| `add_watch_wallet_callback` def | telegram_bot.py | 3356 |
| `view_trades_callback` def | telegram_bot.py | 3392 |
| `daily_report_callback` def | telegram_bot.py | 3529 |
| `handle_vanity_generation` def | telegram_bot.py | 3637 |
| `admin_panel_callback` keyboard — "Test News Broadcast" button | telegram_bot.py | 3798 |
| `admin_test_news_callback` def start | telegram_bot.py | 4042 |
| `admin_test_news_callback` def end | telegram_bot.py | 4116 |
| `application.add_handler(conv_handler)` | telegram_bot.py | 4661 |
| `HOLDINGS_SELL_CONFIRM` state block | telegram_bot.py | 4530–4533 |
| `ADD_WALLET` state block | telegram_bot.py | 4521 |
| `VANITY_PREFIX` state block | telegram_bot.py | 4575 |
| `BLACKLIST_TOKEN_INPUT` state block | telegram_bot.py | 4564 |
| `ADMIN_MASTER_PASSWORD` state block | telegram_bot.py | 4599–4601 |
| `TELEGRAM_CHANNEL_ID` line | config.py | 10 |
| `SOLANA_RPC_URL` line | config.py | 41 |
| `post_whale_trade` signature | telegram_broadcaster.py | ~117 |
| `message = (…)` whale alert block | telegram_broadcaster.py | ~161–185 |
