# QUICK SETUP & DEPLOYMENT CHECKLIST

## Pre-Deployment Verification ✅

### 1. Environment Variables Required
- [ ] `TELEGRAM_BOT_TOKEN` - Set
- [ ] `TELEGRAM_CHANNEL_ID` - Set  
- [ ] `ENCRYPTION_MASTER_PASSWORD` - Set
- [ ] `SOLANA_RPC_URL` - Set (or using default)
- [ ] `BIRDEYE_API_KEY` - Set (for full functionality)
- [ ] `DATABASE_URL` - Set for PostgreSQL (or using SQLite fallback)

### 2. Keep-Alive Configuration (CRITICAL for Render)
- [ ] Deployed to Render at least once
- [ ] `RENDER_EXTERNAL_URL` - Set in Render dashboard 
  - Go to: https://dashboard.render.com → Your Service → Environment
  - Copy the URL from your service (e.g., `https://dex-trading-bot.onrender.com`)
  - Add variable: `RENDER_EXTERNAL_URL` = Your full URL

### 3. Port Configuration  
- [ ] `PORT` environment variable set (default: 10000)
- [ ] Port 10000 is exposed in Render (should be automatic)
- [ ] Firewall allows connections to port 10000

### 4. External Monitoring (Recommended)
Choose at least ONE:

**Option A: UptimeRobot (Easiest)**
- [ ] Account created at https://uptimerobot.com
- [ ] Monitor created for your Render URL
- [ ] Check interval set to 5 minutes
- [ ] (Optional) Add `UPTIMEROBOT_URLS` to Render env

**Option B: Cron-Job.org (Very Reliable)**
- [ ] Account created at https://cron-job.org
- [ ] Cron job created with your Render URL
- [ ] Schedule: `*/5 * * * *` (every 5 minutes)
- [ ] (Optional) Add `CRON_JOB_URLS` to Render env

**Option C: Better Uptime (Enterprise)**
- [ ] Account created at https://betteruptime.com
- [ ] Monitor created for your Render URL
- [ ] Check frequency: 3-5 minutes
- [ ] (Optional) Add `BETTERUPTIME_URLS` to Render env

### 5. Rate Limiting & Caching
Default settings should work, but optional optimization:
- [ ] Review .env for cache TTL settings
- [ ] Adjust if getting rate limited:
  ```
  DEX_SCREENER_CACHE_TTL=180
  BIRDEYE_CACHE_TTL=300
  API_MAX_RETRIES=5
  ```

## Deployment Steps

### Step 1: Push Latest Code
```bash
git add .
git commit -m "Deploy bot reliability fixes"
git push origin main
```

Render will auto-deploy if connected.

### Step 2: Set RENDER_EXTERNAL_URL (Post-Deployment)
1. Go to Render dashboard → Your service
2. Navigate to Environment
3. Add new environment variable:
   - Key: `RENDER_EXTERNAL_URL`
   - Value: `https://your-service-name.onrender.com`
4. Save and redeploy

### Step 3: Verify Services Running
1. Check health endpoint:
   ```
   GET https://YOUR_RENDER_URL:10000/
   ```
   Should return JSON with status "ALIVE"

2. Check logs in Render dashboard for:
   ```
   ✅ Enhanced Keep-Alive v2 initialized
   ✅ HTTP server thread started
   ✅ Self-ping thread started (AGGRESSIVE)
   ✅ Heartbeat monitor started
   ✅ Auto-restart watchdog started
   ```

### Step 4: Setup External Monitoring
If using UptimeRobot or equivalent:
1. Create monitor for `https://YOUR_RENDER_URL`
2. Test manually to ensure it works
3. Set up alerts/notifications

## Post-Deployment Verification ✅

### First 30 Minutes
Look for these SUCCESS logs:
```
✅ Enhanced Keep-Alive v2 initialized
✅ HTTP server started on port 10000
✅ Self-ping thread started
✅ Heartbeat monitor started
💚 HEARTBEAT - Uptime: 0:05:00 | Pings: 2
```

### First 2 Hours
Verify:
- [ ] No "Ping failed" errors (should be "Ping successful")
- [ ] Heartbeat logs appearing every 3 minutes
- [ ] No crash/restart logs unless testing

### Health Check URL
```bash
# Test from terminal
curl https://YOUR_RENDER_URL:10000/

# Expected response:
{
  "status": "ALIVE",
  "service": "dex_copy_trading_bot",
  "uptime": "0:02:30",
  "health_score": 95,
  "render_status": "NO SLEEP",
  "bot_status": "RUNNING",
  "ping_count": 2
}
```

### Monitoring Cache Hits
After 1 hour, look for cache statistics in logs (if debug logging enabled):
```
💾 Cache HIT: dexscreener:abc123    # Good - cache is working
💾 Cached result: token_analysis    # New data cached
```

## Troubleshooting During Deployment

### Issue: "RENDER_EXTERNAL_URL not set"
**Solution**: 
1. Check Render dashboard → Environment variables
2. Ensure `RENDER_EXTERNAL_URL` is set exactly (case-sensitive)
3. Redeploy after adding

### Issue: "Health check server failed to start"
**Solution**:
1. Check if port 10000 is available
2. Try port 8080 instead:
   ```
   PORT=8080
   ```
3. Ensure firewall allows the port

### Issue: "Ping failed - Connection refused"
**Solution**:
1. Verify RENDER_EXTERNAL_URL is correct (should be https://, not http://)
2. Test the URL manually in browser
3. Should see JSON response, not an error

### Issue: "Bot keeps restarting"
**Solution**:
1. Check actual error message after restart
2. Check all required env vars are set
3. Check database connection if using PostgreSQL
4. View full logs in Render dashboard

## Performance Targets

After successful deployment, you should see:

| Metric | Target | What to Check |
|--------|--------|---------------|
| Bot Uptime | 99.5%+ | Render dashboard |
| Sleep Incidents | 0 | Check logs daily |
| Keep-Alive Pings | Every 2min | Look for "Ping successful" |
| Cache Hit Rate | 60-80% | Monitor logs |
| API Rate Limits | 10x reduction | Compare before/after |
| Crash Recovery | <1 min | Auto-restart when crash occurs |

## Daily Monitoring

### Quick Health Check (30 seconds)
```bash
# 1. Test health endpoint
curl https://YOUR_RENDER_URL:10000/

# 2. Check uptimerobot/monitoring service

# 3. Check Render logs for errors
```

### Weekly Review (5 minutes)
1. Open Render dashboard
2. Check service logs for:
   - Any ERROR or CRITICAL messages
   - Unusual restart patterns
   - Rate limit errors (should be rare)
3. Review monitoring service uptime chart

### Monthly Optimization (15 minutes)
1. Check cache hit rate statistics
2. Adjust cache TTLs if needed:
   - Too many rate limits → increase TTL
   - Stale data issues → decrease TTL
3. Check for any new error patterns

## Rollback Plan

If deployment causes issues:

### Quick Rollback (2 minutes)
1. Go to Render dashboard
2. Find the previous deployment
3. Click "Re-deploy"
4. Wait 30 seconds for restart

### Code Rollback (5 minutes)
```bash
# If code changes broke it
git revert HEAD
git push origin main

# Render will auto-deploy old version
```

## Success Indicators ✅

Your deployment is successful when:

1. ✅ Health endpoint returns "ALIVE" status
2. ✅ Keep-alive successfully pinging every 2 minutes
3. ✅ No crashes for 1+ hour
4. ✅ Telegram bot responding to commands
5. ✅ External monitoring shows green (100% uptime)

## Questions or Issues?

1. **Check logs**: Render dashboard → Logs tab (most informative)
2. **Check endpoint**: `GET https://YOUR_RENDER_URL:10000/`
3. **Manual test**: Verify external monitoring can reach URL
4. **Compare**: Check if fresh deployment vs. previous version

---

## Summary of Changes Made

### Files Modified:
1. ✅ `keep_alive.py` - Enhanced v2 (2-min pings, watchdog, etc.)
2. ✅ `main.py` - Auto-recovery loops for crashes
3. ✅ `config.py` - Added rate limit configuration
4. ✅ `utils/rate_limit_handler.py` - NEW intelligent caching system

### Files Created:
1. ✅ `BOT_RELIABILITY_FIX.md` - Detailed deployment guide
2. ✅ `RATE_LIMIT_INTEGRATION.md` - Integration examples
3. ✅ `QUICK_SETUP_CHECKLIST.md` - This file

### Key Improvements:
- 🔥 **No more Render sleep** - 2-minute aggressive pings
- 📦 **Rate limit protection** - Intelligent caching + backoff
- 🤖 **Never crashes** - Auto-restart with exponential backoff  
- 💚 **Better monitoring** - Health checks + external service integration
- 📊 **Cache statistics** - Monitor cache performance

Good luck! 🚀
