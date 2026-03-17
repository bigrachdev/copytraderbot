# 🚀 QUICK START - DEX Copy Trading Bot

## In 3 Steps:

### 1️⃣ Start the Bot
```bash
cd c:\Users\user\Desktop\mbot
python main.py
```

### 2️⃣ Open Telegram
Send `/start` to your bot

### 3️⃣ Start Trading!
- 📈 **Swap** - Buy/sell tokens
- 🐋 **Copy** - Track whale wallets
- 📊 **Analytics** - View profits
- 🛑 **Risk** - Set stop-loss/TP
- 🛠️ **Tools** - Vanity wallets

---

## 🎯 Smart Notifications Work Like This:

```
You buy token
    ↓
Price goes up
    ↓
Bot sends alert every 10%, 25%, 50%, 100%, 250%, 500% profit
    ↓
You click "Sell Now" button
    ↓
Instant swap back to SOL
    ↓
Profit locked in! 🎉
```

---

## ✅ Already Tested & Working

- ✅ All 12 modules loaded
- ✅ Database ready
- ✅ Notifications configured
- ✅ DEX APIs connected
- ✅ Telegram bot ready

---

## 🔧 First Time Setup

### 1. Check .env has token:
```bash
type .env
```
Look for: `TELEGRAM_BOT_TOKEN=8032530249:AA...`

### 2. Start bot:
```bash
python main.py
```

### 3. Test on Telegram:
- Send `/start`
- Click "📈 Swap"
- You'll be guided through each step

---

## 💡 Test with Small Amount First

1. Send `/start` → "📈 Swap"
2. Import your wallet (or create new)
3. Swap **0.1 SOL** for any token
4. Wait for profit alert notifications
5. Click "💸 Sell Now" when alert arrives

---

## 📊 Monitor Performance

Send `/analytics` to see:
- Total trades
- Win rate
- Profits/losses
- Daily reports

---

## ⚠️ If Bot Doesn't Start

```bash
# Run test
python test_imports.py

# Should see: ✅ ALL MODULES IMPORTED SUCCESSFULLY!

# If error, re-install:
pip install --upgrade -r requirements.txt
```

---

## 🎮 Available Commands

On Telegram:

```
/start              → Show main menu
📈 Swap            → Trade tokens manually
🐋 Copy Trading    → Follow whale wallets
🛑 Risk Management → Set stop-loss/take-profit
📊 Analytics       → View performance stats
🛠️ Tools           → Generate vanity wallets
💾 View Trades     → See trade history
/cancel            → Exit current action
```

---

## 📈 Example Session

```
You: /start
Bot: "Welcome! Choose an action:"

You: Click "📈 Swap"
Bot: "Enter your private key..."

You: Enter key
Bot: "✅ Wallet imported. Select DEX..."

You: Click "Jupiter"
Bot: "How much SOL to swap?"

You: Type "0.1"
Bot: "You'll swap 0.1 SOL for ~130 USDC"

You: Click "✅ Confirm"
Bot: "🔄 Executing swap..."
Bot: "✅ Trade complete!"

⏰ 2 minutes later...
Bot: "🎉 MILESTONE ALERT - 10% profit (ROI +$13)"
     Buttons: [💸 Sell] [🚀 Hold] [Set TP]

You: Click "💸 Sell"
Bot: "✅ Sold! Profit locked: +$13 ✨"
```

---

## 🔐 Private Key Safety

- 🔒 Encrypted with AES-128
- 🔑 Never stored in plaintext
- 🛡️ Only decrypted when needed
- 👁️ Only visible to you
- ✅ Backed up in database (encrypted)

---

## 🌐 Works On

- 📱 Telegram Mobile App
- 💻 Telegram Desktop
- 🖥️ Telegram Web
- 🔌 Any VPS/Cloud Server

---

## 📞 Issues?

Check the error messages in `bot.log`:
```bash
tail bot.log
```

Most common fixes:
1. Bot token wrong → Check .env
2. Database locked → Restart bot
3. Network error → Check internet connection
4. RPC limit → Wait 5 mins, try again

---

**You're ready! Type:** `python main.py`

**Then send Telegram bot:** `/start` 

**Happy trading! 🚀**
