# 🚀 Running the Bot with Web UI

This bot has **two separate servers** that need to run simultaneously:

## Setup

### Terminal 1: Web UI + REST API Server
```bash
python run_web_ui.py
```
**Output:**
```
🌐 Starting Web UI on port 3000...
📍 Access at http://localhost:3000
```

### Terminal 2: Bot Backend Server
```bash
python main.py
```
**Output:**
```
🚀 ULTIMATE DEX COPY TRADING BOT
✅ Keep-Alive service started
✅ Wallet monitoring started
✅ Starting Telegram bot...
```

---

## API Endpoints

The Web UI connects to these REST API endpoints:

### Authentication
- `POST /api/auth/telegram` - Verify Telegram user
- `GET /api/auth/status` - Check auth status
- `POST /api/auth/logout` - Logout user

### Dashboard
- `GET /api/dashboard` - Get dashboard data (balance, stats)

### Trades
- `GET /api/trades` - Get recent trades
- `GET /api/trades/<trade_id>` - Get trade details

### Wallet
- `GET /api/wallet` - Get wallet info
- `GET /api/wallet/tokens` - Get token holdings

### Copy Trading
- `GET /api/copy-trading/whales` - Find whale wallets
- `POST /api/copy-trading/watch` - Watch a wallet
- `GET /api/copy-trading/watched` - Get watched wallets
- `DELETE /api/copy-trading/unwatch/<wallet_id>` - Unwatch wallet

### Trading
- `POST /api/trading/analyze` - Analyze token
- `POST /api/trading/swap` - Execute swap

### Settings
- `GET /api/settings` - Get user settings
- `PUT /api/settings` - Update settings

---

## Architecture Diagram

```
┌─────────────────────────────────────┐
│  Browser User at localhost:3000      │
└────────────┬────────────────────────┘
             │ HTTP Requests
             ▼
┌─────────────────────────────────────┐
│  Web UI Server (port 3000)          │
│  - Flask REST API (/api/*)          │
│  - HTML UI (/, /dashboard, etc)     │
│  - JavaScript frontend              │
│  ⚙️ run_web_ui.py                   │
└────────────┬────────────────────────┘
             │ Imports from
             ▼
┌─────────────────────────────────────┐
│  Bot Backend                        │
│  - Imports database.py              │
│  - Imports wallet modules           │
│  - Imports trading modules          │
│  (Shared business logic)            │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│  Bot Backend Server (main.py)       │
│  - Telegram bot listener            │
│  - Wallet monitoring                │
│  - Keep-alive service               │
└─────────────────────────────────────┘
```

---

## Environment Variables

Make sure `.env` has:
```env
WEB_UI_PORT=3000
FLASK_SECRET_KEY=your_secret_key
TELEGRAM_BOT_TOKEN=your_token
```

---

## Stopping Servers

- **Web UI**: Press `Ctrl+C` in Terminal 1
- **Bot**: Press `Ctrl+C` in Terminal 2

---

## Quick Start

1. Open **two terminal windows**
2. In Terminal 1: `python run_web_ui.py`
3. In Terminal 2: `python main.py`
4. Open browser to `http://localhost:3000`
5. Done! 🎉
