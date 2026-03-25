# Web UI & Telegram Mini App Integration Guide

This guide explains how to set up and deploy the new web UI with Telegram Mini App support.

## Features

✨ **Web UI Features:**
- Modern dashboard with real-time stats
- Token analysis and smart trading
- Copy trading whale tracker
- Portfolio management
- Advanced transaction history
- Responsive design for all devices

📱 **Telegram Mini App Features:**
- Seamless Telegram integration
- Optimized mobile interface
- Quick swap shortcuts
- Real-time push notifications
- Safe area support for notched devices
- Theme matching with Telegram

## Installation

### 1. Update Dependencies

Add required packages to `requirements.txt`:

```bash
Flask==3.0.0
Flask-CORS==4.0.0
Flask-Session==0.5.0
python-telegram-bot==20.7
aiohttp==3.9.1
```

Then install:

```bash
pip install -r requirements.txt
```

### 2. Environment Variables

Add to `.env`:

```env
# Web UI
WEB_UI_PORT=3000
FLASK_SECRET_KEY=your_secure_secret_key_here
FLASK_ENV=production

# Telegram Mini App
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_BOT_USERNAME=your_bot_username
```

### 3. Update Main Bot Entry Point

Modify `main.py` to start the web UI:

```python
# In the main() function, replace the old web_dashboard start with:

web_ui_port = int(os.getenv('WEB_UI_PORT', 3000))
web_ui_thread = threading.Thread(
    target=lambda: subprocess.run(['python', 'bot/web_ui.py']),
    daemon=True
)
web_ui_thread.start()
logger.info(f"✅ Web UI started on port {web_ui_port}")
logger.info(f"   🌐 Access at http://localhost:{web_ui_port}")
logger.info(f"   📱 Mini App at http://localhost:{web_ui_port}/mini-app")
```

## File Structure

```
mbot/
├── bot/
│   ├── web_ui.py           # Flask backend with API endpoints
│   ├── telegram_bot.py      # Telegram bot integration
│   └── ...
├── templates/
│   ├── index.html          # Main web UI template
│   └── mini-app.html       # Telegram mini app template
├── static/
│   ├── js/
│   │   ├── api.js          # API client
│   │   ├── auth.js         # Authentication module
│   │   ├── app.js          # Main app logic
│   │   └── mini-app.js     # Telegram mini app logic
│   └── css/
│       ├── style.css       # Main styles
│       ├── responsive.css  # Responsive design
│       └── mini-app.css    # Mini app specific styles
```

## API Endpoints

### Authentication
- `POST /api/auth/telegram` - Authenticate with Telegram
- `POST /api/auth/logout` - Logout
- `GET /api/auth/status` - Check auth status

### Dashboard
- `GET /api/dashboard` - Get dashboard data

### Trading
- `GET /api/trades` - Get recent trades
- `GET /api/trades/<id>` - Get trade details
- `POST /api/trading/analyze` - Analyze token
- `POST /api/trading/swap` - Execute swap

### Copy Trading
- `GET /api/copy-trading/whales` - Get top traders
- `POST /api/copy-trading/watch` - Watch a trader
- `GET /api/copy-trading/watched` - Get watched traders
- `DELETE /api/copy-trading/unwatch/<id>` - Unwatch trader

### Wallet
- `GET /api/wallet` - Get main wallet
- `GET /api/wallet/tokens` - Get token holdings

### Settings
- `GET /api/settings` - Get user settings
- `PUT /api/settings` - Update settings

## Telegram Mini App Setup

### 1. Create Bot Commands

Add these commands to your Telegram bot via BotFather:

```
startapp - Launch the web app
miniapp - Open mini app
trading - Quick trading interface
dashboard - View dashboard
wallets - Manage wallets
copy - Copy trading settings
```

### 2. Add Web App Buttons to Bot

Update `telegram_bot.py` to include web app buttons:

```python
from telegram import WebAppInfo

# In your command handler:
button = InlineKeyboardButton(
    text="📊 Open Dashboard",
    web_app=WebAppInfo(url="https://your-domain.com")
)
```

### 3. Set Web App Domain

1. Go to BotFather
2. Select your bot
3. Choose "Bot Settings" → "Menu Button"
4. Set the URL to your web UI domain

### 4. Configure Telegram Webhook (Optional)

For production, set up webhook instead of polling:

```python
# In telegram_bot.py
await application.bot.set_webhook(
    url=f"https://your-domain.com/webhook",
    allowed_updates=["message", "callback_query"]
)
```

## Deployment

### Local Testing

1. Start the bot:
```bash
python main.py
```

2. Access web UI:
- Web: `http://localhost:3000`
- Mini App: `http://localhost:3000/mini-app`

### Production Deployment

#### Option 1: Heroku

1. Create `Procfile`:
```
web: python main.py
```

2. Create `runtime.txt`:
```
python-3.11.0
```

3. Deploy:
```bash
heroku create your-app-name
git push heroku main
heroku config:set TELEGRAM_BOT_TOKEN=your_token
```

#### Option 2: AWS/GCP/Azure

1. Use Docker:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV PORT=3000
EXPOSE 3000

CMD ["python", "main.py"]
```

2. Deploy container to your platform

#### Option 3: VPS

1. Install Python and dependencies
2. Use systemd service file:

```ini
[Unit]
Description=DEX Trading Bot
After=network.target

[Service]
Type=simple
User=bot
WorkingDirectory=/home/bot/mbot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### HTTPS/SSL Setup

For production with Telegram Mini App, you need HTTPS:

1. **Use Nginx Reverse Proxy:**

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

2. **Use Let's Encrypt for free SSL:**

```bash
sudo certbot certonly --nginx -d your-domain.com
```

## Configuration

### Customizing the Theme

Edit `static/css/style.css` to change colors:

```css
:root {
    --primary-color: #6366f1;      /* Main accent color */
    --secondary-color: #8b5cf6;    /* Secondary accent */
    --success-color: #10b981;      /* Success states */
    --danger-color: #ef4444;       /* Warning/error states */
    
    --bg-primary: #0f172a;         /* Main background */
    --bg-secondary: #1a1f3a;       /* Secondary background */
    --text-primary: #ffffff;       /* Main text */
}
```

### Customizing API Behavior

All API logic is in `bot/web_ui.py`. Modify endpoints to:
- Add authentication checks
- Implement rate limiting
- Add analytics tracking
- Custom business logic

## Security Best Practices

1. **CSRF Protection**: Enabled via Flask-Session
2. **Authentication**: Telegram WebApp verification via HMAC-SHA256
3. **Input Validation**: All user inputs validated
4. **Rate Limiting**: Implement per-user API rate limits
5. **Secrets**: Store sensitive data in environment variables

### Example Rate Limiter:

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address)

@app.route('/api/trading/swap', methods=['POST'])
@limiter.limit("5 per minute")
@require_auth
def create_swap():
    # ...
```

## Troubleshooting

### Mini App Not Loading
- Check browser console for errors (F12)
- Verify Telegram WebApp JS is loaded
- Ensure domain is HTTPS
- Check bot token in .env

### API 401 Unauthorized
- Clear browser cache and cookies
- Re-authenticate via Telegram
- Check session configuration

### Slow Performance
- Check Solana RPC rate limits
- Implement API caching
- Optimize database queries
- Use CDN for static assets

### HTTPS Issues
- Verify certificate validity
- Check certificate chain
- Test with `curl -I https://your-domain.com`

## Monitoring

### Check Service Status

```bash
# Check if processes are running
ps aux | grep python

# View logs
tail -f bot.log

# Monitor resource usage
top
```

### Set Up Alerts

1. **Uptime Monitoring**: Use UptimeRobot
2. **Error Tracking**: Integrate Sentry
3. **Performance**: Use New Relic

## Advanced Features

### Push Notifications

Use Telegram for real-time updates:

```python
async def send_trade_alert(user_telegram_id, message):
    await application.bot.send_message(
        chat_id=user_telegram_id,
        text=message
    )
```

### Analytics

Track user behavior:

```python
# Submit event to analytics
await analytics.track_event(user_id, 'swap_executed', {
    'token': token_address,
    'amount': amount
})
```

### WebSocket Real-Time Updates

For live price updates, add WebSocket support:

```javascript
// In app.js
const ws = new WebSocket('wss://your-domain.com/ws');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    updatePriceDisplay(data);
};
```

## Support & Resources

- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram Mini Apps: https://core.telegram.org/bots/webapps
- Flask Documentation: https://flask.palletsprojects.com/
- Solana Web3.py: https://github.com/michaelhly/solders

## Next Steps

1. ✅ Deploy to production
2. ✅ Set up Telegram Mini App menu button
3. ✅ Configure webhook for Telegram
4. ✅ Set up monitoring and alerts
5. ✅ Add user analytics tracking
6. ✅ Implement push notifications
7. ✅ Create admin dashboard
