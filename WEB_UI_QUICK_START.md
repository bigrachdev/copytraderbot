# Web UI Quick Start Guide

Get your new web UI up and running in 5 minutes!

## What You Get

✨ **Standalone Web UI** - Full-featured dashboard accessible via browser
📱 **Telegram Mini App** - Same interface works seamlessly in Telegram
💱 **Trading Interface** - Execute swaps and manage positions
🐋 **Whale Tracker** - Copy top traders automatically
💼 **Portfolio** - View all holdings and trading stats

## Quick Setup (5 minutes)

### Step 1: Update Requirements

```bash
# Open your requirements.txt and add:
pip install Flask==3.0.0 Flask-CORS==4.0.0 Flask-Session==0.5.0
```

### Step 2: Add Environment Variables

Add to your `.env` file:

```env
WEB_UI_PORT=3000
FLASK_SECRET_KEY=change_me_to_random_string_32_chars_long
FLASK_ENV=development
```

Generate a random secret key:
```bash
python -c "import os; print(os.urandom(32).hex())"
```

### Step 3: Start the Bot

```bash
python main.py
```

You'll see:
```
✅ Web UI started on port 3000
   🌐 Access at http://localhost:3000
   📱 Mini App at http://localhost:3000/mini-app
```

### Step 4: Access the UI

- **Web UI**: Open http://localhost:3000 in your browser
- **Mini App**: Open http://localhost:3000/mini-app (will show login)

## Features Overview

### Dashboard
- Real-time SOL balance
- Win rate and total profit stats
- Recent trades history
- Quick action buttons

### Trading
- **Token Analysis**: Analyze tokens for risk/opportunity
- **Quick Swap**: Execute trades with one click
- **Smart Slippage**: Automatic slippage detection

### Copy Trading
- **Whale Tracker**: See top traders
- **1-Click Watch**: Start copying a whale's trades
- **Watch List**: Manage all your watched traders

### Wallet
- **Holdings**: View all tokens and their value
- **Balance**: Real-time SOL balance
- **Addresses**: See your wallet address

### Settings
- User preferences
- Notification settings
- Account management

## Using the Telegram Mini App

### Setup Telegram Bot

1. Open Telegram and find your bot
2. Type `/start` to initialize
3. The bot will show you menu options
4. Click "🌐 Open Dashboard" button
5. The Web UI will open in Telegram Mini App!

### First-Time Setup

When you open the mini app for the first time:
1. You'll be authenticated via Telegram
2. Your wallet will be auto-loaded
3. You can start trading immediately

## API Endpoints

All API calls go to `/api/*`:

```javascript
// Example: Get dashboard
fetch('/api/dashboard')
  .then(r => r.json())
  .then(data => console.log(data))

// Example: Execute swap
fetch('/api/trading/swap', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    input_mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', // USDC
    output_mint: 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', // BONK
    amount: 0.1,
    slippage: 2.0
  })
})
```

## Customization

### Change Colors

Edit `static/css/style.css`:

```css
:root {
    --primary-color: #your-color;
    --secondary-color: #another-color;
    /* Etc */
}
```

### Add New Pages

Add route in `bot/web_ui.py`:

```python
@app.route('/api/your-endpoint', methods=['GET'])
@require_auth
def your_endpoint():
    # Your logic here
    return jsonify({'data': 'value'})
```

Then add in `app.js`:

```javascript
async loadYourPage() {
    const data = await window.api.request('GET', '/api/your-endpoint');
    // Update UI
}
```

### Modify Components

Files are organized as:
- **Backend Logic**: `bot/web_ui.py`
- **Frontend Pages**: `templates/index.html`, `templates/mini-app.html`
- **Styling**: `static/css/*.css`
- **Client Logic**: `static/js/*.js`

## Troubleshooting

### Web UI Not Loading

**Problem**: "Cannot GET /" when accessing http://localhost:3000

**Solution**:
```bash
# Check Flask is running
# Look for error in console
# Restart with: python main.py
```

### Authentication Failed

**Problem**: "Unauthorized" when using mini app

**Solution**:
1. Clear browser cookies
2. Re-open the mini app link in Telegram
3. Check TELEGRAM_BOT_TOKEN in .env

### Slow API Responses

**Problem**: Dashboard takes >5 seconds to load

**Solution**:
1. Check Solana RPC connection
2. Look at `bot.log` for errors
3. Verify internet connection
4. Check rate limits with API provider

### Mini App Styles Look Wrong

**Problem**: Colors and layout look different in Telegram

**Solution**:
1. Clear Telegram cache (long-press chat → Clear Cache)
2. Close and reopen Telegram
3. Verify HTTPS connection (required for mini apps)

## Production Deployment

When you're ready for production:

### 1. Set HTTPS/SSL

Mini apps **require** HTTPS! Use:
- Heroku (automatic SSL)
- Netlify (automatic SSL)
- Cloudflare (free SSL)
- Let's Encrypt + Nginx

### 2. Update Bot Settings

In BotFather, set Web App domain to your HTTPS URL:

```
Bot Settings → Menu Button → Enable Web App
URL: https://your-domain.com
```

### 3. Environment Variables

```env
FLASK_ENV=production
WEB_UI_PORT=443  # or use reverse proxy
# Use stronger secret key
FLASK_SECRET_KEY=your_very_long_random_string_here
```

### 4. Deploy

Choose your platform:

**Heroku**:
```bash
git push heroku main
```

**Docker**:
```bash
docker build -t dex-bot .
docker run -p 3000:3000 dex-bot
```

**VPS**:
```bash
ssh user@your-vps.com
# Copy code and run
python main.py
```

## Monitor & Maintain

### Check Logs

```bash
# View live logs
tail -f bot.log

# View last 100 lines
tail -n 100 bot.log

# Search for errors
grep "ERROR" bot.log
```

### Monitor Resources

```bash
# Check CPU/Memory usage
top -p $(pgrep -f "python main.py")

# Check port usage
lsof -i :3000
```

### Health Check

```bash
# Test API is working
curl http://localhost:3000/api/auth/status

# Check Telegram connection
curl -X POST https://api.telegram.org/botYOUR_TOKEN/getMe
```

## Performance Tips

1. **Enable Caching**: Add Redis for session caching
2. **Optimize Queries**: Add database indexes
3. **CDN**: Serve static assets from CDN
4. **Compress**: Enable gzip compression
5. **Monitor**: Set up error tracking with Sentry

## Common Customizations

### Add Custom Trading Strategy

```python
# In trading/smart_trader.py
async def my_custom_analysis(token_address):
    # Your analysis logic
    return {
        'recommendation': 'buy',
        'risk_score': 3,
        'potential_return': '50%'
    }
```

### Add Push Notifications

```python
# In bot/web_ui.py
@app.route('/api/subscribe-notifications', methods=['POST'])
@require_auth
def subscribe_notifications():
    data = request.json
    # Save subscription
    # Send via Telegram when trades happen
    return jsonify({'success': True})
```

### Add Admin Dashboard

```python
# In bot/web_ui.py
@app.route('/admin/dashboard')
@require_admin
def admin_dashboard():
    return render_template('admin.html')
```

## Next Steps

1. ✅ **Test Locally**: Try all features in development
2. ✅ **Customize**: Make it match your brand
3. ✅ **Deploy**: Get it on a live server with HTTPS
4. ✅ **Monitor**: Set up alerts and logging
5. ✅ **Grow**: Add more features based on user feedback

## Support Resources

- 📖 [Telegram Mini App Docs](https://core.telegram.org/bots/webapps)
- 🐍 [Flask Docs](https://flask.palletsprojects.com/)
- ⚡ [Solana Docs](https://docs.solana.com/)
- 🚀 [Web3.py](https://web3-py.readthedocs.io/)

## FAQ

**Q: Can I use the web UI without Telegram?**
A: Yes! Just visit http://localhost:3000 directly in your browser.

**Q: How do I add more tokens to the swap list?**
A: Add them to `POPULAR_SOL_TOKENS` in `bot/telegram_bot.py`

**Q: Can I change the refresh rate?**
A: Yes, modify the intervals in `config.py`

**Q: How do I reset my session?**
A: Clear browser cookies or logout from the settings menu.

**Q: Is my data stored on servers?**
A: Only wallet addresses and trade history. Seed phrases are never stored.

## You're All Set! 🚀

Your Web UI with Telegram Mini App is ready to use!

- Share the mini app link with users
- They can trade directly from Telegram
- Track performance in real-time
- No app installation needed!

Happy trading! 📈
