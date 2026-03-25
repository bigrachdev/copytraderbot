# 🚀 Render Deployment Guide with Neon Database

## ✅ Prerequisites Complete

- ✅ Neon PostgreSQL database configured
- ✅ `DATABASE_URL` in `.env`
- ✅ `render.yaml` created
- ✅ `psycopg2-binary` in requirements.txt

---

## 📋 Deployment Steps

### Step 1: Push to GitHub

```bash
cd c:\Users\user\Desktop\mbot
git init
git add .
git commit -m "Ready for Render deployment with Neon DB"
# Create repo on GitHub, then:
git remote add origin https://github.com/YOUR_USERNAME/mbot.git
git push -u origin main
```

### Step 2: Connect Render to GitHub

1. Go to [render.com](https://render.com)
2. Sign in / Create account
3. Click **"New +"** → **"Blueprint"**
4. Connect your GitHub account
5. Select your `mbot` repository
6. Render will detect `render.yaml`

### Step 3: Configure Environment Variables

In Render dashboard, set these **required variables**:

| Variable | Value |
|----------|-------|
| `TELEGRAM_BOT_TOKEN` | Get from [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | Your Telegram user ID (e.g., `6417609151`) |
| `SOLANA_RPC_URL` | `https://mainnet.helius-rpc.com/?api-key=YOUR_KEY` |
| `SOLANA_WSS_URL` | `wss://mainnet.helius-rpc.com/?api-key=YOUR_KEY` |
| `BIRDEYE_API_KEY` | Your Birdeye API key |
| `DATABASE_URL` | Your Neon PostgreSQL URL ✅ |
| `ENCRYPTION_MASTER_PASSWORD` | Secure 32+ char password |

> ⚠️ **Important**: `DATABASE_URL` is already in your `.env` - Render will sync it if you connect it, or you can manually paste it.

### Step 4: Deploy

1. Click **"Apply"** in Render
2. Wait for build (~2-5 minutes)
3. Both services will start:
   - `mbot-bot` (port 10000) - Main bot
   - `mbot-dashboard` (port 5000) - Web UI

---

## 🔍 Verify Deployment

### Check Logs
```
Render Dashboard → Your Service → Logs
```

Look for:
```
✅ Database initialized
✅ Telegram bot is now listening for messages...
✅ Keep-Alive service started
```

### Test Bot
1. Open Telegram
2. Find your bot
3. Send `/start`
4. Bot should respond!

---

## 🎯 Render Free Tier Limits

| Resource | Limit |
|----------|-------|
| **Web Services** | 2 free services |
| **Bandwidth** | 100GB/month |
| **Build Minutes** | 500 hours/month |
| **Auto-Sleep** | 15 min inactivity (free tier) |

> 💡 **Tip**: Use [UptimeRobot](https://uptimerobot.com) to ping your Render URL every 10 minutes to prevent sleep.

---

## 🔧 Troubleshooting

### Bot Not Starting
```
Check logs for:
- "DATABASE_URL not set" → Add to env vars
- "TELEGRAM_BOT_TOKEN missing" → Add to env vars
- "ModuleNotFoundError" → Check requirements.txt
```

### Database Connection Failed
```
1. Verify DATABASE_URL in Render env vars
2. Check Neon dashboard for connection limits
3. Ensure SSL mode is 'require'
4. Test connection string locally first
```

### Build Fails
```bash
# Test build locally
docker run -it --rm -v $(pwd):/app -w /app python:3.11-slim
pip install -r requirements.txt
python main.py
```

---

## 📊 Monitoring

### Render Dashboard
- Real-time logs
- CPU/Memory usage
- Request metrics
- Auto-deploy on git push

### Neon Dashboard
- Connection count
- Query performance
- Storage usage
- Backup management

---

## 🔄 Updates

To deploy changes:
```bash
git add .
git commit -m "Update feature"
git push
```
Render auto-deploys on push to main branch!

---

## 🎉 You're Live!

Your bot is now:
- ✅ Running 24/7 on Render
- ✅ Using Neon serverless PostgreSQL
- ✅ Ready for production trading

**Next**: Send `/start` to your bot on Telegram! 🚀
