# Integration Checklist - Step-by-Step

Complete these steps to fully integrate the web UI into your bot.

## ✅ Step 1: Update requirements.txt

Add these two lines to your `requirements.txt`:

```txt
Flask==3.0.0
Flask-CORS==4.0.0
Flask-Session==0.5.0
```

Then run:
```bash
pip install -r requirements.txt
```

---

## ✅ Step 2: Update .env File

Add these environment variables:

```env
# Web UI
WEB_UI_PORT=3000
FLASK_SECRET_KEY=your_random_secret_key_here_at_least_32_chars
FLASK_ENV=development

# For production later:
# FLASK_ENV=production
# WEB_UI_URL=https://your-domain.com
```

Generate a random secret key:
```bash
python -c "import os; print(os.urandom(32).hex())"
```

---

## ✅ Step 3: Update main.py

Find the section that starts the web_dashboard:

```python
# OLD CODE (around line 40-50):
web_port = int(os.getenv('WEB_PORT', 5000))
web_thread = threading.Thread(
    target=lambda: subprocess.run(['python', 'bot/web_dashboard.py']),
    daemon=True
)
web_thread.start()
logger.info(f"✅ Web dashboard started on port {web_port}")
```

Replace it with:

```python
# NEW CODE:
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

---

## ✅ Step 4: Update telegram_bot.py (Optional)

Add web app buttons to your start command. Find your start handler and update it:

```python
# Add this import at the top
from telegram import WebAppInfo

# Add this to your start command handler:
@app.command(CommandHandler("start", start_command))
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command with Web UI buttons"""
    
    # Get Web UI URL from environment
    web_ui_url = os.getenv('WEB_UI_URL', 'http://localhost:3000')
    
    # Create keyboard with buttons
    keyboard = [
        [
            InlineKeyboardButton(
                text="📊 Open Dashboard",
                web_app=WebAppInfo(url=web_ui_url)
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Mini App",
                web_app=WebAppInfo(url=f"{web_ui_url}/mini-app")
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 DEX Copy Trading Bot\n\n"
        "Click the button below to open the dashboard:",
        reply_markup=reply_markup
    )
```

---

## ✅ Step 5: Verify File Structure

Make sure the new files are in place:

```bash
# Check templates
ls -la templates/
# Should show: index.html, mini-app.html

# Check static
ls -la static/js/
# Should show: api.js, auth.js, app.js, mini-app.js

ls -la static/css/
# Should show: style.css, responsive.css, mini-app.css

# Check bot
ls -la bot/web_ui.py
# Should exist
```

---

## ✅ Step 6: Test Locally

Start your bot:
```bash
python main.py
```

You should see output like:
```
✅ Keep-Alive service started
✅ Web UI started on port 3000
   🌐 Access at http://localhost:3000
   📱 Mini App at http://localhost:3000/mini-app
```

Test the URLs:
- **Web UI**: Open http://localhost:3000 in browser
- **Mini App Demo**: Open http://localhost:3000/mini-app in browser
- **API Health**: Open http://localhost:3000/api/auth/status

---

## ✅ Step 7: Test Telegram Mini App (Optional)

1. Open Telegram
2. Find your bot and type `/start`
3. You should see the "Open Dashboard" button
4. Click it - the Web UI should open inside Telegram!

---

## ✅ Step 8: Verify All Features

Test the following in the Web UI:

- [ ] Dashboard loads and shows stats
- [ ] Can see balance, trades, win rate
- [ ] Trading page loads
- [ ] Can analyze tokens
- [ ] Copy Trading shows whales
- [ ] Can view wallet holdings
- [ ] Settings page is accessible
- [ ] Mobile layout looks good on phone
- [ ] Dark theme displays correctly

---

## ✅ Step 9: Check for Errors

If you see errors, check these:

```bash
# View bot logs
tail -f bot.log

# Check web_ui.py started (should see Flask messages)
# Check port 3000 is not in use
lsof -i :3000

# Test Flask directly
python bot/web_ui.py
```

---

## ✅ Step 10: Ready for Deployment

When you're happy with local testing, you're ready for production:

See **WEB_UI_SETUP.md** for deployment options:
- Heroku
- AWS/Azure
- VPS with Nginx
- Docker containers

---

## 📋 Checklist Summary

- [ ] requirements.txt updated
- [ ] .env file has WEB_UI_PORT and FLASK_SECRET_KEY
- [ ] main.py updated with new startup code
- [ ] telegram_bot.py updated with web app buttons (optional)
- [ ] All new files present (templates, static)
- [ ] Bot starts without errors
- [ ] Web UI loads at http://localhost:3000
- [ ] Mini app loads at http://localhost:3000/mini-app
- [ ] All dashboard features work
- [ ] Mobile UI looks good
- [ ] Telegram mini app opens (if configured)

---

## 🆘 Troubleshooting

### Port 3000 already in use
```bash
# Find process using port 3000
lsof -i :3000

# Kill it
kill -9 <PID>

# Or use different port
export WEB_UI_PORT=8000
```

### Module not found error
```bash
# Reinstall requirements
pip install --upgrade -r requirements.txt
```

### Flask not starting
```bash
# Run directly to see error
python bot/web_ui.py

# Should start with "Running on http://..."
```

### Web UI shows "Cannot GET /"
```bash
# Make sure templates/ directory exists
ls -la templates/

# Make sure index.html is there
ls -la templates/index.html
```

### Telegram auth not working
```bash
# Check TELEGRAM_BOT_TOKEN in .env
echo $TELEGRAM_BOT_TOKEN

# Verify bot is running
# Try /start command first
```

---

## ✅ You're Done!

Your bot now has:
- ✅ Professional web UI
- ✅ Telegram mini app support
- ✅ Full trading interface
- ✅ Copy trading features
- ✅ Mobile responsive design

**Start trading! 🚀**

---

## 📞 Need Help?

1. Check **WEB_UI_QUICK_START.md** for quick reference
2. Check **WEB_UI_SETUP.md** for detailed setup
3. Check **WEB_UI_ARCHITECTURE.md** for system design
4. Review **WEB_UI_INTEGRATION_EXAMPLE.py** for code examples

---

## 📝 Notes

- All old endpoints still work (backward compatible)
- Database schema unchanged
- No breaking changes to existing code
- Can run both old and new UIs simultaneously if needed
- Easy to roll back if needed (remove web_ui startup)

---

**Integration Time**: ~15 minutes
**Deployment Time**: ~5 minutes per environment
**Status**: ✅ Ready to use!
