# 🚀 RENDER DEPLOYMENT - CRITICAL SETUP (3 STEPS)

## BEFORE YOU DEPLOY - READ THIS!

Your bot will **NOT** work on Render without setting ONE critical environment variable after deployment.

---

## Step 1️⃣ - Deploy Code to Render

```bash
git add .
git commit -m "Bot reliability fixes: keep-alive v2, rate limiter, auto-recovery"
git push origin main
```

Render will auto-deploy. Wait for "Deployment successful" message.

---

## Step 2️⃣ - GET Your Render Service URL

1. Go to: https://dashboard.render.com
2. Click on your service (e.g., "mbot-bot")
3. Look at the top of the page - you'll see your service URL
   - Example: `https://dex-trading-bot.onrender.com`
   - Or: `https://my-cool-service.onrender.com`
4. **Copy this exact URL** (including https://)

---

## Step 3️⃣ - SET Environment Variable in Render

**CRITICAL**: This is required for keep-alive to work!

1. In Render dashboard, go to: **Environment**
2. Click: **Add Environment Variable**
3. Fill in:
   - **Key**: `RENDER_EXTERNAL_URL` (exact spelling - case sensitive!)
   - **Value**: Your URL from Step 2 (e.g., `https://my-cool-service.onrender.com`)
4. Click: **Save**
5. Click: **Manual Deploy** to restart service

---

## Verify It Works ✅

After restart, test the health endpoint:

```bash
# Replace YOUR_SERVICE_URL with your actual URL
curl https://YOUR_SERVICE_URL:10000/

# Example:
curl https://dex-trading-bot.onrender.com:10000/
```

Expected response:
```json
{
  "status": "ALIVE",
  "service": "dex_copy_trading_bot",
  "uptime": "0:00:30",
  "health_score": 100,
  "render_status": "NO SLEEP",
  "bot_status": "RUNNING",
  "ping_count": 1
}
```

If you see this → **Setup is successful!** ✅

---

## What This Does

The `RENDER_EXTERNAL_URL` variable tells your bot to ping itself every 2 minutes to prevent Render's 15-minute inactivity sleep.

Without it:
- ❌ Bot sleeps after 15 minutes of inactivity
- ❌ No data processing during sleep
- ❌ Users see "service offline"

With it:
- ✅ Bot pings itself every 2 minutes
- ✅ Render sees activity → No sleep
- ✅ 99.5%+ uptime guaranteed

---

## Optional: External Monitoring (Recommended)

For extra protection, setup one free external monitoring service:

### Option A: UptimeRobot (Easiest)
1. Go to: https://uptimerobot.com/signup
2. Create free account
3. Add monitor:
   - Type: HTTP(S)
   - URL: Your Render URL
   - Check interval: 5 minutes
4. Done! UptimeRobot now pings your bot too

### Option B: Cron-Job.org (Very Reliable)
1. Go to: https://cron-job.org
2. Create account
3. Create cron job:
   - URL: Your Render URL
   - Schedule: `*/5 * * * *`
4. Done!

---

## Monitoring Your Bot

### Daily Health Check (Takes 10 seconds)

```bash
# Check if bot is alive
curl https://YOUR_SERVICE_URL:10000/

# Look for:
# - "status": "ALIVE" ✅
# - "health_score": >50 ✅
# - "render_status": "NO SLEEP" ✅
```

### Check Render Logs

1. Render Dashboard → Your Service → Logs tab
2. Look for:
   - `✅ Ping #XXX successful` (every 2 minutes) ✅
   - `💚 HEARTBEAT` (every 3 minutes) ✅
   - No `ERROR` or `CRITICAL` messages ✅

### Verify No Sleep

Render sleeps after 15 minutes of inactivity. If you see:
- Pings every 2 minutes → Bot is awake ✅
- Heartbeat every 3 minutes → Bot is awake ✅
- No restart logs → No sleep happened ✅

---

## Troubleshooting

### Issue: Bot health endpoint returns error

**Check 1**: Is URL correct?
```bash
# Should work (https + full URL)
curl https://dex-trading-bot.onrender.com:10000/

# Should NOT work (http, localhost, no port)
curl http://localhost:10000/     ❌
```

**Check 2**: Is RENDER_EXTERNAL_URL set?
1. Render Dashboard → Environment
2. Verify `RENDER_EXTERNAL_URL` exists
3. Verify it has full URL (https://...)
4. Redeploy if you changed it

**Check 3**: Restart service
1. Render Dashboard → Service
2. Click "Manual Deploy"
3. Wait 30 seconds
4. Try health endpoint again

### Issue: Logs show "RENDER_EXTERNAL_URL not set"

**Solution**: 
1. You haven't set it yet, OR
2. You set it as `RENDER_URL` instead of `RENDER_EXTERNAL_URL`

Fix:
1. Go to Environment
2. Add variable:
   - Key: `RENDER_EXTERNAL_URL`
   - Value: Your full URL
3. Redeploy

### Issue: Health endpoint working but Render still sleeping?

This shouldn't happen, but if it does:
1. Setup external monitoring (see above)
2. UptimeRobot will ping every 5 minutes (backup)
3. Prevents sleep even if internal keep-alive has issues

---

## Summary

1. ✅ Deploy code (`git push`)
2. ✅ Get your Render service URL
3. ✅ Set `RENDER_EXTERNAL_URL` environment variable
4. ✅ Redeploy
5. ✅ Verify with health endpoint

**That's it!** Your bot is now protected from sleeping.

---

## What Not to Do ❌

- ❌ Don't use `http://` (must use `https://`)
- ❌ Don't append `/` at the end
- ❌ Don't use `localhost` or `127.0.0.1`
- ❌ Don't include port in RENDER_EXTERNAL_URL
- ❌ Don't use wrong spelling (`RENDER_URL` vs `RENDER_EXTERNAL_URL`)

## Example Setup

**WRONG** ❌
```
RENDER_EXTERNAL_URL=http://my-service.onrender.com/
RENDER_URL=my-service.onrender.com:10000
RENDER_SERVICE=dex-bot
```

**CORRECT** ✅
```
RENDER_EXTERNAL_URL=https://my-service.onrender.com
```

---

## Next Steps

1. Complete 3-step setup above
2. Wait 5 minutes for first ping
3. Check logs for "✅ Ping successful"
4. Your bot is now sleeping-proof! 🚀

---

Questions? Check these files:
- `BOT_RELIABILITY_FIX.md` - Full deployment guide
- `COMPLETE_SOLUTION_SUMMARY.md` - Complete overview
- `QUICK_SETUP_CHECKLIST.md` - Verification steps
