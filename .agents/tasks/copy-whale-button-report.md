# Copy-Whale Button — Implementation Report

## Summary

All four changes applied and verified. Three files compile clean.

---

## Change 1 — `config.py`: `COPY_MIN_SOL_BALANCE` added

```python
DEFAULT_COPY_SCALE   = float(os.getenv('DEFAULT_COPY_SCALE',   '1.0'))

# Minimum SOL balance required to use the Copy This Whale deep-link button
COPY_MIN_SOL_BALANCE = float(os.getenv('COPY_MIN_SOL_BALANCE', '0.05'))
```

Default: `0.05 SOL`. Overridable via env var.

---

## Change 2 — `trading/telegram_broadcaster.py`: inline button on whale alerts

### 2a. Import updated
```python
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
```

### 2b. `_send_message` accepts `reply_markup`
Signature: `async def _send_message(self, message: str, reply_markup=None) -> bool:`

The `await self.bot.send_message(...)` call passes `reply_markup=reply_markup` through.

### 2c. Deep-link button built in `post_whale_trade()`

Button is built after `message = "\n".join(...)` and passed to `_send_message`:

```python
reply_markup = None
if bot_username:
    deep_link = f"https://t.me/{bot_username}?start=copywhale_{whale_address}"
    reply_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🐋 Copy This Whale", url=deep_link)
    ]])
return await self._send_message(message, reply_markup=reply_markup)
```

**Deep-link format:** `https://t.me/<bot_username>?start=copywhale_<whale_address>`

---

## Change 3 — `bot/telegram_bot.py`: deeplink handler

### 3a. Config import
```python
from config import TELEGRAM_BOT_TOKEN, BIRDEYE_API_KEY, BIRDEYE_API_URL,
    JUPITER_QUOTE_TIMEOUT, SOLANA_RPC_URL, COPY_MIN_SOL_BALANCE
```

### 3b. Deeplink check in `start()`
Inserted immediately after `user_id = update.effective_user.id`:
```python
if context.args and context.args[0].startswith('copywhale_'):
    whale_address = context.args[0][len('copywhale_'):]
    await self._handle_copywhale_deeplink(update, context, whale_address)
    return MENU
```

### 3c. `_handle_copywhale_deeplink()` method
Inserted after `add_sol_whale_callback`. Flow:
1. Validate base58 address format (32–44 chars)
2. Check user has a wallet — if not, show Import/Create buttons
3. Check `sol_balance >= COPY_MIN_SOL_BALANCE` — uses config value, **not hardcoded**
4. Check if whale already being watched
5. Call `db.add_watched_wallet(user_id, whale_address)` — **same DB method as `add_sol_whale_callback`**
6. Call `copy_trader.start_monitoring_for_user(user_id)` — same as `add_sol_whale_callback`

---

## Change 4 — `.env.example`: `COPY_MIN_SOL_BALANCE` documented

Added after `MIN_TRADE_AMOUNT=0.01`:
```
# Minimum SOL balance required in the user's wallet to use the Copy This Whale button
# Set to 0.05 SOL (~$7 at $150/SOL) — enough to cover swap fees + position size
COPY_MIN_SOL_BALANCE=0.05
```

---

## Verification

| File | Compile result |
|---|---|
| `config.py` | ✅ exit 0 — no errors |
| `trading/telegram_broadcaster.py` | ✅ exit 0 — no errors |
| `bot/telegram_bot.py` | ✅ exit 0 — no errors |

---

## Checklist

- [x] Button appears on channel posts — deep-link format: `https://t.me/<bot_username>?start=copywhale_<whale_address>`
- [x] `_send_message` accepts and passes through `reply_markup`
- [x] Balance check uses `COPY_MIN_SOL_BALANCE` from config (not a hardcoded value)
- [x] `db.add_watched_wallet(user_id, whale_address)` — same DB method as `add_sol_whale_callback`
- [x] All 3 files compile clean
- [x] `COPY_MIN_SOL_BALANCE` added to both `config.py` and `.env.example`
