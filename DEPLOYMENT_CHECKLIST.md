# 📋 Render Deployment Checklist

## Pre-Deployment

- [ ] All changes committed to Git
- [ ] TELEGRAM_CHANNEL_ID obtained (not the placeholder)
- [ ] Bot added to Telegram channel as **Admin**

## On Render Dashboard

### Environment Variables to Set/Verify:
- [ ] `TELEGRAM_BOT_TOKEN` - ✅ Already set
- [ ] `TELEGRAM_CHANNEL_ID` - ⚠️ **MUST UPDATE** with actual channel ID
- [ ] `DATABASE_URL` - ✅ Already set (Neon PostgreSQL)
- [ ] `ENCRYPTION_MASTER_PASSWORD` - ✅ Already set
- [ ] `ADMIN_IDS` - ✅ Already set (6417609151)

### Optional (for testing):
```env
BROADCAST_MIN_NEWS_RELEVANCE=40      # Lower for more news
BROADCAST_MIN_LIQUIDITY_USD=10000    # Lower for more signals
BROADCAST_NEWS_INTERVAL_MINUTES=15   # Faster news updates
```

## After Deployment

### Step 1: Verify Bot Starts
- [ ] Check Render logs for: `✅ Database initialized`
- [ ] Check for: `✅ Telegram broadcaster initialized for channel:`
- [ ] Check for: `📊 Loaded posted tracking from database: X news, Y signals`
- [ ] Self-ad should appear in your Telegram channel

### Step 2: Test via Admin Panel
1. [ ] Open Telegram bot → `/start`
2. [ ] Go to **Admin Panel** (bottom of main menu)
3. [ ] Click **📡 Test News Broadcast**
   - [ ] Should show fetched news count
   - [ ] Should show qualified news count
   - [ ] Should list top news items
4. [ ] Click **📢 Test Signal Broadcast**
   - [ ] Should say "Test Signal Broadcast Successful"
   - [ ] Check your Telegram channel - test signal should appear

### Step 3: Monitor for 1 Hour
- [ ] News posts appear (every 30 min by default)
- [ ] No duplicate news after any restarts
- [ ] Self-ad appears (on startup + every 4 hours)
- [ ] Check Render logs periodically for errors

### Step 4: Add Whale Wallet (for signals)
- [ ] Use `/addwallet <whale_address>` to add a watched wallet
- [ ] Wait for whale to make a trade
- [ ] Verify signal posts to channel

## If Issues Occur

### No News Posting?
1. Check logs for: `📰 ====== Starting news fetch cycle ======`
2. Look for: `✅ News cycle complete: fetched=X posted=Y`
3. If posted=0, lower threshold: `BROADCAST_MIN_NEWS_RELEVANCE=40`

### Duplicate News After Restart?
1. Check logs for: `📊 Loaded posted tracking from database: X news`
2. If X=0, database load failed - check DATABASE_URL
3. Verify tables exist in Neon database

### Self-Ad Not Posting?
1. Check logs for: `📢 Posting self-advertisement on startup...`
2. Verify TELEGRAM_CHANNEL_ID is correct (not placeholder)
3. Ensure bot is admin in channel

### Test Buttons Not Working?
1. Verify you're an admin (check users table)
2. Look for errors in logs when clicking buttons
3. Check TELEGRAM_CHANNEL_ID is set

## Success Criteria

✅ Bot starts without errors
✅ Self-ad posts to channel on startup
✅ News posts every 30 minutes (no duplicates)
✅ Admin test tools work correctly
✅ No duplicate posts after manual restart
✅ Signals post when whales trade (if wallets configured)

---

## Quick Reference

**Find Channel ID:**
```bash
python find_channel_id.py
```

**Test Locally:**
```bash
python test_broadcast.py
```

**Check Database:**
```bash
python migrate_broadcast_persistence.py
```

**View Render Logs:**
- Dashboard → Your Service → Logs tab
- Filter for: "broadcast", "news", "signal"

---

**Status**: Ready to deploy! Just set TELEGRAM_CHANNEL_ID and push.
