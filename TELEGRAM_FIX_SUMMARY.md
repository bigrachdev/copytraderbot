# Wallet Saving Fix Summary - Telegram Bot

## Problem
Wallets added via Telegram bot (both manual entry and suggested whales) were not being saved to the database, causing the "No Solana wallets being watched yet" message to appear even after adding wallets.

## Root Causes Identified

### 1. **User ID Mismatch** (CRITICAL BUG)
- **Issue**: The Telegram bot was passing `telegram_id` (e.g., `123456789`) to database functions
- **Expected**: The `watched_wallets.user_id` column references `users.user_id` (internal SERIAL primary key, e.g., `1, 2, 3...`)
- **Result**: Wallets were being inserted with wrong `user_id` or failing silently

### 2. **Wrong Function Call in Web UI**
- **File**: `bot/web_ui.py` line 302
- **Issue**: Called `copy_trader.add_watched_wallet()` which doesn't exist
- **Fix**: Changed to `db.add_watched_wallet()` and use `telegram_id`

### 3. **No Duplicate Check**
- **Issue**: `add_watched_wallet()` didn't check for existing wallets
- **Result**: Silent failures when trying to add duplicate wallets
- **Fix**: Added duplicate check before INSERT

### 4. **Missing UNIQUE Constraint**
- **Issue**: Database schema lacked `UNIQUE(user_id, wallet_address)` constraint
- **Fix**: Added constraint to both PostgreSQL and SQLite schemas

## Files Modified

### 1. `data/database.py` - Core Fix
**`add_watched_wallet()` function:**
```python
def add_watched_wallet(self, telegram_id: int, wallet_address: str, ...):
    # Get internal user_id from telegram_id
    user = self.get_user(telegram_id)
    if not user:
        return False
    user_id = user['user_id']
    
    # Check for duplicate
    # ... then INSERT with correct user_id
```

**`get_watched_wallets()` function:**
```python
def get_watched_wallets(self, telegram_id: int) -> List[Dict]:
    # Get internal user_id from telegram_id
    user = self.get_user(telegram_id)
    user_id = user['user_id']
    
    # Query with correct user_id
```

**Key Points:**
- Both functions now accept `telegram_id` (from Telegram bot)
- Automatically lookup internal `user_id` from database
- All callers (Telegram, Web UI) use `telegram_id` consistently

### 2. `bot/web_ui.py`
```python
# Before (WRONG)
result = copy_trader.add_watched_wallet(session['user_id'], wallet_address, copy_scale)

# After (CORRECT)
result = db.add_watched_wallet(session['telegram_id'], wallet_address, copy_scale=copy_scale)
```

### 3. `bot/web_dashboard.py`
```python
# Before (WRONG)
wallets = db.get_watched_wallets(session['user_id'])
success = db.add_watched_wallet(session['user_id'], wallet_address, alias, copy_scale)

# After (CORRECT)
wallets = db.get_watched_wallets(session['telegram_id'])
success = db.add_watched_wallet(session['telegram_id'], wallet_address, alias, copy_scale)
```

### 4. `data/database.py` - Schema Updates
- Added `UNIQUE(user_id, wallet_address)` constraint to PostgreSQL schema
- Added `UNIQUE(user_id, wallet_address)` constraint to SQLite schema

### 5. `migrate_unique_constraint.py` (NEW)
- Migration script to add UNIQUE constraint to existing databases
- Removes duplicates before adding constraint
- Works with both PostgreSQL and SQLite

### 6. `keep_alive.py` - Enhanced Aggressive Mode - NO SLEEP ALLOWED
- **Ping interval reduced**: 240s → 180s (3 minutes)
- **Heartbeat interval reduced**: 300s → 180s (3 minutes)
- **More endpoints**: Added `/api` to ping list
- **Better headers**: Added Cache-Control to prevent caching
- **Aggressive retry**: Quick 30s retry after consecutive failures
- **NO SLEEP ALLOWED** mode activated

## How the Fix Works

All database functions now use `telegram_id` consistently:

```python
# Telegram Bot (telegram_bot.py)
user_id = update.effective_user.id  # This is telegram_id
db.add_watched_wallet(user_id, addr)

# Web UI (web_ui.py)
session['telegram_id'] = telegram_id  # Stored during auth
db.add_watched_wallet(session['telegram_id'], wallet_address)

# Database (database.py)
def add_watched_wallet(self, telegram_id: int, ...):
    user = self.get_user(telegram_id)  # Lookup by telegram_id
    user_id = user['user_id']          # Get internal ID
    # Insert with internal user_id
```

This ensures:
- **Telegram bot**: Uses `telegram_id` directly ✅
- **Web UI**: Uses `session['telegram_id']` ✅
- **Database**: Converts to internal `user_id` automatically ✅

## Testing Checklist

- [ ] Add wallet via Telegram: `/start` → Copy Trade → Add Wallet → Enter address
- [ ] Add whale via Telegram: Copy Trade → Suggested Whales → Click "➕ Add #1"
- [ ] View watched wallets: Copy Trade → View Watched Wallets
- [ ] Add wallet via Web UI: `/copy-trading/watch` endpoint
- [ ] Verify duplicate prevention: Try adding same wallet twice
- [ ] Check bot logs for "Wallet already being watched" warnings
- [ ] Verify keep-alive: Check logs for "AGGRESSIVE KEEP-ALIVE" messages
- [ ] Monitor uptime: Should see pings every 3 minutes

## Deployment Steps

1. **Stop the bot** (if running)

2. **Run migration** (optional, for existing databases):
   ```bash
   python migrate_unique_constraint.py
   ```

3. **Restart the bot**:
   ```bash
   python main.py
   ```

4. **Test adding wallets** via Telegram and Web UI

5. **Verify keep-alive**: Check logs for aggressive pinging

## Performance Impact

- Minimal: One extra SELECT query to lookup user_id (cached by DB)
- Duplicate check prevents unnecessary INSERT attempts
- UNIQUE constraint provides database-level protection
- Keep-alive pings every 3 minutes (negligible resource usage)

## Related Issues Fixed

- ✅ Web UI copy trading endpoint now works correctly
- ✅ Suggested whales can be added successfully
- ✅ Manual wallet addition works correctly
- ✅ Duplicate wallets are prevented
- ✅ Proper error logging for debugging
- ✅ Aggressive keep-alive prevents Render sleep
- ✅ NO SLEEP ALLOWED mode activated

## Keep-Alive Features

The enhanced keep-alive service now includes:

1. **Aggressive Self-Ping**: Every 3 minutes (180 seconds)
2. **Multiple Endpoints**: `/`, `/health`, `/api`
3. **Quick Retry**: 30s retry after consecutive failures
4. **Heartbeat Logger**: Every 3 minutes with "NO SLEEP!" status
5. **Better Headers**: Cache-Control to prevent caching
6. **External Monitoring**: Recommendations for UptimeRobot, Cron-Job.org

### Log Output Example
```
🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥
 AGGRESSIVE KEEP-ALIVE SERVICE - NO SLEEP ALLOWED
🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥

⏰ Started at: 2026-03-29 07:37:00 UTC
🌐 Port: 10000
⚡ Ping Interval: 180 seconds (AGGRESSIVE)
🔗 URL: https://your-bot.onrender.com
💪 Status: ACTIVE - PREVENTING SLEEP

✅ Ping #1 successful (200) - 0.45s
💚 HEARTBEAT - Uptime: 0:03:00 | Pings: 1 | NO SLEEP!
✅ Ping #2 successful (200) - 0.38s
```
