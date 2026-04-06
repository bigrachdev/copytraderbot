# 📡 Signal & News Broadcasting Troubleshooting Guide

## Issues Fixed

This document describes the comprehensive diagnostic and fixes implemented for signal and news broadcasting issues.

## What Was Changed

### 1. ✅ Enhanced Diagnostic Logging

Added detailed logging throughout the broadcast pipeline:

- **Telegram Broadcaster** (`trading/telegram_broadcaster.py`):
  - Logs initialization status with channel ID
  - Logs each signal broadcast attempt with reason for blocking
  - Logs news fetch cycles with item counts per source
  - Logs message sending attempts and success/failure
  - Logs rate limit violations

- **Copy Trader** (`trading/copy_trader.py`):
  - Logs signal broadcast preparation steps
  - Logs token info fetching
  - Logs broadcaster call results
  - Logs whale alert threshold checks

### 2. ✅ Configurable Thresholds

Added environment variables to control broadcast filters:

```env
BROADCAST_MIN_LIQUIDITY_USD=30000    # Minimum token liquidity for signals
BROADCAST_MIN_NEWS_RELEVANCE=60      # Minimum relevance score for news
BROADCAST_NEWS_INTERVAL_MINUTES=30   # How often to fetch news
BROADCAST_SELF_AD_INTERVAL_HOURS=4   # How often to post self-ad
```

**For Testing**, you can lower these:
```env
BROADCAST_MIN_LIQUIDITY_USD=1000     # Very low for testing
BROADCAST_MIN_NEWS_RELEVANCE=40      # More permissive for testing
BROADCAST_NEWS_INTERVAL_MINUTES=5    # Fetch news every 5 min for testing
```

### 3. ✅ Diagnostic Test Script

Created `test_broadcast.py` - a comprehensive diagnostic tool that tests:
1. Telegram bot connection and channel access
2. Signal broadcasting with test data
3. News fetching from all RSS sources
4. Database state (users and watched wallets)
5. News broadcasting with test data

**Usage:**
```bash
python test_broadcast.py
```

## Common Issues & Solutions

### Issue 1: No Signals Being Posted

**Root Causes:**
1. ❌ `TELEGRAM_CHANNEL_ID` not set or using placeholder value
2. ❌ No watched wallets configured in database
3. ❌ Wallet monitoring not started
4. ❌ Token liquidity below `$30,000` threshold
5. ❌ Rate limits (max 1 signal per 2 min, 10 per hour)

**How to Fix:**

1. **Set your channel ID** in `.env`:
   ```env
   TELEGRAM_CHANNEL_ID=-1001234567890  # Replace with actual ID
   ```
   
   **To find your channel ID:**
   - Add your bot to the channel as admin
   - Send a message to the channel
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Look for `chat.id` in the JSON response
   - Channel IDs are usually negative (e.g., `-1001234567890`)

2. **Add a whale wallet** via Telegram:
   ```
   /addwallet <wallet_address>
   ```

3. **Lower liquidity threshold** for testing:
   ```env
   BROADCAST_MIN_LIQUIDITY_USD=1000
   ```

4. **Check logs** when bot is running:
   ```bash
   tail -f bot.log | grep -i "signal\|broadcast"
   ```

### Issue 2: No News Being Posted

**Root Causes:**
1. ❌ News relevance score below 60 threshold
2. ❌ RSS feeds not accessible (network issues)
3. ❌ News already posted within 24 hours (deduplication)
4. ❌ Background news loop not started

**How to Fix:**

1. **Lower relevance threshold** for testing:
   ```env
   BROADCAST_MIN_NEWS_RELEVANCE=40
   ```

2. **Decrease news interval** for faster updates:
   ```env
   BROADCAST_NEWS_INTERVAL_MINUTES=5
   ```

3. **Check news fetch logs**:
   ```bash
   tail -f bot.log | grep -i "news"
   ```

4. **Run diagnostic test**:
   ```bash
   python test_broadcast.py
   ```

### Issue 3: Bot Not Posting Anything

**Root Causes:**
1. ❌ Bot not running
2. ❌ `TELEGRAM_BOT_TOKEN` missing
3. ❌ Bot doesn't have admin permissions in channel
4. ❌ Database connection failed

**How to Fix:**

1. **Verify bot is running**:
   ```bash
   python main.py
   ```

2. **Check environment variables**:
   ```bash
   # Windows
   type .env | findstr TELEGRAM
   
   # Linux/Mac
   grep TELEGRAM .env
   ```

3. **Verify bot has admin rights** in your Telegram channel

4. **Run diagnostic**:
   ```bash
   python test_broadcast.py
   ```

## Diagnostic Checklist

Run this checklist to verify your setup:

- [ ] `TELEGRAM_BOT_TOKEN` is set in `.env`
- [ ] `TELEGRAM_CHANNEL_ID` is set (not placeholder)
- [ ] Bot is admin in the Telegram channel
- [ ] At least one user exists in database
- [ ] At least one active watched wallet configured
- [ ] Bot is running (`python main.py`)
- [ ] `bot.log` file shows activity (not empty)
- [ ] Network connectivity to Telegram API
- [ ] Network connectivity to RSS feeds

## Testing Workflow

### Step 1: Run Diagnostic Test
```bash
python test_broadcast.py
```

This will tell you exactly what's working and what's not.

### Step 2: Fix Identified Issues

Based on test results:
- **Connection failed** → Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHANNEL_ID`
- **Signal failed** → Check liquidity threshold or rate limits
- **News fetch failed** → Check network/RSS feed accessibility
- **Database empty** → Add users and watched wallets via Telegram bot

### Step 3: Lower Thresholds for Testing

Temporarily modify `.env`:
```env
BROADCAST_MIN_LIQUIDITY_USD=1000
BROADCAST_MIN_NEWS_RELEVANCE=40
BROADCAST_NEWS_INTERVAL_MINUTES=5
```

Restart the bot after changes.

### Step 4: Monitor Logs

Watch the logs in real-time:
```bash
# Windows PowerShell
Get-Content bot.log -Wait | Select-String "signal|news|broadcast"

# Windows CMD
type bot.log | findstr /i "signal news broadcast"

# Linux/Mac
tail -f bot.log | grep -i "signal\|news\|broadcast"
```

### Step 5: Trigger a Test Signal

Add an active whale wallet that makes frequent trades, or use the test script:
```bash
python test_broadcast.py
```

## Log Message Guide

### Signal Broadcasting Logs

```
📢 Attempting to broadcast signal: TokenName
✅ Liquidity check passed: $50000
📊 Signal confidence: HIGH (3 whales)
📤 Calling broadcaster.broadcast_signal...
📢 Successfully broadcasted copy signal for TokenName
```

**If blocked:**
```
⚠️ Signal blocked by rate limits or duplicate detection
⚠️ Signal skipped: Low liquidity $5000 < $30000 threshold
```

### News Broadcasting Logs

```
📰 ====== Starting news fetch cycle ======
✅ Solana Foundation: parsed 12 items
✅ CoinDesk: parsed 8 items
📊 Total unique news items collected: 18
📝 Attempting to post news #1: score=85.0, title=Solana Network Upgrade...
✅ News posted #1: Solana Network Upgrade...
✅ News cycle complete: fetched=18 posted=2
```

**If blocked:**
```
⚠️ No news items fetched from configured sources
❌ Failed to fetch news from https://...: Connection error
```

## Advanced Troubleshooting

### Manually Test RSS Feeds

```python
import aiohttp
import asyncio

async def test_rss():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://solana.com/news/rss.xml') as resp:
            print(f"Status: {resp.status}")
            body = await resp.text()
            print(f"Content length: {len(body)} bytes")
            print(body[:500])

asyncio.run(test_rss())
```

### Check Database State Manually

```python
from data.database import db

# Get all users
users = db.get_all_users_list()
print(f"Users: {len(users)}")

# Get watched wallets for a user
if users:
    telegram_id = users[0]['telegram_id']
    wallets = db.get_watched_wallets(telegram_id)
    print(f"Watched wallets: {len(wallets)}")
    for w in wallets:
        print(f"  - {w['wallet_address'][:12]}... active={w['is_active']}")
```

### Force News Fetch

```python
import asyncio
from trading.telegram_broadcaster import broadcaster

async def force_news():
    await broadcaster.initialize()
    await broadcaster._fetch_and_post_news()

asyncio.run(force_news())
```

## Configuration Reference

| Variable | Default | Description | Testing Value |
|----------|---------|-------------|---------------|
| `BROADCAST_MIN_LIQUIDITY_USD` | 30000 | Min liquidity for signals | 1000 |
| `BROADCAST_MIN_NEWS_RELEVANCE` | 60 | Min relevance for news | 40 |
| `BROADCAST_NEWS_INTERVAL_MINUTES` | 30 | News fetch interval | 5 |
| `BROADCAST_SELF_AD_INTERVAL_HOURS` | 4 | Self-ad interval | 1 |

## Getting Help

If issues persist:

1. Run `python test_broadcast.py` and share the output
2. Check `bot.log` for error messages
3. Verify all environment variables are set correctly
4. Ensure bot has admin permissions in the channel
5. Test network connectivity to Telegram and RSS feeds

## Recent Changes Summary

### Files Modified:
1. ✅ `trading/telegram_broadcaster.py` - Added diagnostic logging and configurable thresholds
2. ✅ `trading/copy_trader.py` - Added signal broadcast logging
3. ✅ `config.py` - Added new broadcast configuration variables
4. ✅ `.env.example` - Added documentation for broadcast variables
5. ✅ `test_broadcast.py` - Created comprehensive diagnostic test script
6. ✅ `BROADCAST_TROUBLESHOOTING.md` - This troubleshooting guide

### New Features:
- 🎛️ Configurable liquidity threshold via `BROADCAST_MIN_LIQUIDITY_USD`
- 🎛️ Configurable news relevance via `BROADCAST_MIN_NEWS_RELEVANCE`
- 📊 Detailed logging at every broadcast pipeline stage
- 🧪 Diagnostic test script for quick troubleshooting
