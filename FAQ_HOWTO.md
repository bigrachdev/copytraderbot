# 🤖 Solana DEX Copy Trading Bot - FAQ & How-To-Use Guide

## 📋 Table of Contents

1. [Getting Started](#getting-started)
2. [Copy Trading FAQ](#copy-trading-faq)
3. [Smart Trading FAQ](#smart-trading-faq)
4. [Enhanced Features Guide](#enhanced-features-guide)
5. [Risk Management](#risk-management)
6. [Troubleshooting](#troubleshooting)

---

## 🚀 Getting Started

### What is this bot?
This is an automated Solana DEX copy trading bot that lets you:
- **Copy trades** from successful whale wallets automatically
- **Smart trade** with AI-powered token discovery and analysis
- **Swap tokens** on Solana via Jupiter with optimal routing

### How do I set up the bot?
1. **Create/Import Wallet**: Use the main menu to create a new Solana wallet or import an existing one
2. **Fund Your Wallet**: Transfer SOL to your trading wallet address
3. **Add Whale Wallets**: In Copy Trade menu, add whale addresses you want to follow
4. **Configure Settings**: Adjust trade sizes, risk parameters, and enable enhanced features
5. **Start Trading**: Enable auto-trade or manually execute trades

---

## 🐋 Copy Trading FAQ

### What is copy trading?
Copy trading automatically replicates trades from experienced traders ("whales"). When a whale buys a token, the bot automatically buys the same token for you with your configured position size.

### How do I find good whale wallets?
- Use the **"Find Whales"** feature in the Copy Trade menu to discover top traders
- Look for whales with:
  - **Win rate > 50%** (consistently profitable)
  - **10+ trades** (proven track record)
  - **Positive avg profit** (good risk/reward)
  - **Low max drawdown** (manages risk well)

### What copy scale should I use?
- **0.5x**: Copy at half the whale's position size (conservative)
- **1.0x**: Copy at same size (proportional to your portfolio)
- **2.0x**: Copy at double size (aggressive)

**Recommendation**: Start with 0.5x-1.0x until you understand the whale's style.

### What is copy delay?
Copy delay waits X seconds before copying a whale's trade. This helps:
- Avoid copying potential sandwich attacks
- Let initial price impact settle
- Confirm the trade is legitimate

**Recommendation**: 0-30 seconds for most whales.

### Why did a trade get rejected?
Trades can be rejected if:
- Token fails safety checks (honeypot, high tax, concentrated holders)
- Risk score is too high (>85/100)
- Insufficient balance in your wallet
- Price impact exceeds limit (>5%)

### How does trailing stop work?
After you're in profit, the trailing stop follows the price up. If price drops 15% from the peak, it sells to lock in profits. This lets you capture big gains while protecting against reversals.

---

## 🧠 Smart Trading FAQ

### What is smart trading?
Smart trading uses AI analysis to discover and trade trending tokens automatically. The bot scans DexScreener, Birdeye, and on-chain data to find high-momentum tokens with acceptable risk.

### How does token discovery work?
The bot scans:
1. **DexScreener boosted tokens** (actively promoted)
2. **DexScreener top volume** (most traded)
3. **Birdeye trending** (social momentum)
4. **Birdeye new listings** (early opportunities)

Each token is scored 0-100 based on:
- Price momentum (1h, 6h, 24h)
- Volume spikes
- Liquidity depth
- Token age (newer = riskier)
- Social presence

### What momentum score is safe?
- **80-100**: Very high momentum (higher risk/reward)
- **65-79**: Good momentum (balanced)
- **50-64**: Moderate momentum (safer, slower moves)
- **<50**: Low momentum (avoid for auto-trade)

**Default minimum**: 65 for auto-trade

### What is Kelly Criterion sizing?
Kelly Criterion mathematically optimizes position size based on your historical win rate and average win/loss. It maximizes growth while minimizing ruin risk.

**Formula**: `Kelly % = (Win% × Win/Loss Ratio) - Loss%`

The bot uses **fractional Kelly (50%)** to reduce variance.

### How does the take-profit ladder work?
Default ladder:
- **TP1**: Sell 25% at +30% profit
- **TP2**: Sell 50% at +60% profit
- **TP3**: Sell 100% at +100% profit

This locks in profits while keeping exposure for moonshots.

### What is auto-rebuy?
After exiting a position, auto-rebuy monitors the token. If momentum remains strong after a cooldown period, it re-enters to capture continued pumps.

**Limits**: Max 2 rebuys per token to avoid revenge trading.

---

## ⚡ Enhanced Features Guide

### Overview
The bot includes 12 advanced enhancements. The first 4 are **ON by default**, others are **OFF** and can be enabled in settings.

### Copy Trade Enhancements (Default: ON)

#### 1. Dynamic Copy Scaling ✅
**What it does**: Automatically adjusts your copy scale based on whale performance.
- Winners (high win rate) → larger positions
- Losers (low win rate) → smaller positions

**Formula**: `Scale = Base × (1 + 0.5 × (WinRate - 0.5))`

**Example**: If whale win rate is 60%:
- Base scale: 1.0x
- Dynamic scale: 1.05x (5% boost)

**Toggle**: `/settings` → `Enhanced Features` → `Dynamic Copy Scale`

#### 2. Enhanced Whale Qualification ✅
**What it does**: Stricter whale screening with Sharpe ratio and drawdown checks.

**Requirements**:
- Win rate ≥ 45% (vs 40% basic)
- Avg profit ≥ -5% (vs -10% basic)
- Sharpe ratio ≥ 0.3 (consistency check)
- Max drawdown < 30% (risk management)

**Benefit**: Avoids volatile whales who luck into wins then give back profits.

**Toggle**: `/settings` → `Enhanced Features` → `Enhanced Whale Qual`

#### 3. Latency Optimization ✅
**What it does**: Adjusts slippage tolerance based on copy latency.

**How it works**:
- Latency < 30s → normal slippage (2%)
- Latency ≥ 30s → increased slippage (2.5%)

**Benefit**: Ensures transactions land even with delayed signals.

**Toggle**: `/settings` → `Enhanced Features` → `Latency Optimization`

#### 4. Signal Aggregation ✅
**What it does**: Boosts position size when multiple whales buy the same token.

**Multipliers**:
- 1 whale → 1.0x
- 2 whales → 1.25x
- 3+ whales → 1.5x

**Risk filter**: Tokens with risk score >50 require 2+ whales.

**Toggle**: `/settings` → `Enhanced Features` → `Signal Aggregation`

### Smart Trade Enhancements (Default: OFF)

#### 5. Kelly Criterion for Copy Trades
**What it does**: Uses Kelly formula for copy trade sizing instead of flat %.

**Best for**: Users with 10+ closed trades (reliable stats).

**Recommendation**: Enable after building trade history.

**Toggle**: `/settings` → `Enhanced Features` → `Kelly Copy Trades`

#### 6. Token Discovery Plus
**What it does**: Adds social sentiment and smart money flow analysis.

**Features**:
- Twitter/X mention tracking
- Telegram sentiment analysis
- Smart wallet flow tracking

**Toggle**: `/settings` → `Enhanced Features` → `Token Discovery+`

#### 7. TP Ladder Optimization
**What it does**: Adjusts take-profit levels based on token volatility.

**High volatility tokens** (>100% daily):
- TP1: +20% (instead of +30%)
- TP2: +40% (instead of +60%)
- TP3: +80% (instead of +100%)

**Benefit**: Locks profits faster in volatile conditions.

**Toggle**: `/settings` → `Enhanced Features` → `TP Ladder Opt`

#### 8. Enhanced Auto-Rebuy
**What it does**: Smarter rebuy logic with profit-based cooldown reduction.

**Features**:
- Max 2 rebuys per token (prevents revenge trading)
- If last trade profitable → 50% cooldown reduction
- Momentum threshold: 60/100 minimum

**Toggle**: `/settings` → `Enhanced Features` → `Enhanced Rebuy`

### Risk Management (Default: OFF)

#### 9. Daily Loss Limit
**What it does**: Stops trading after losing X% in a day.

**Default**: -10% daily loss limit

**Benefit**: Prevents catastrophic loss days and emotional trading.

**Toggle**: `/settings` → `Enhanced Features` → `Daily Loss Limit`

#### 10. Cool-Off Period
**What it does**: Pauses trading after 3 consecutive losses.

**Duration**: 30 minutes

**Benefit**: Prevents tilt/revenge trading after losses.

**Toggle**: `/settings` → `Enhanced Features` → `Cool-Off Period`

### MEV Protection (Default: OFF)

#### 11. Jito Private Transactions
**What it does**: Routes large trades through Jito private pool to avoid MEV/sandwich attacks.

**Threshold**: Trades ≥ 5 SOL

**Benefit**: Saves 1-5% on large trades by avoiding front-running.

**Toggle**: `/settings` → `Enhanced Features` → `Jito Protection`

### Token Safety (Default: OFF)

#### 12. RugCheck Integration
**What it does**: Fetches RugCheck.xyz safety scores for Solana tokens.

**Threshold**: Min score 60/100

**Checks**:
- Mint authority
- Freeze authority
- LP lock status
- Holder concentration
- Top holder % (excl. burns)

**Toggle**: `/settings` → `Enhanced Features` → `RugCheck Filter`

---

## 🛡️ Risk Management

### Position Sizing Guidelines
- **Conservative**: 5-10% per trade
- **Moderate**: 10-20% per trade
- **Aggressive**: 20-30% per trade

**Never** go above 30% per trade unless you're experienced.

### Stop Loss Settings
- **Hard stop**: -20% (automatic exit)
- **Trailing stop**: -15% from peak (locks profits)
- **Time decay**: 24 hours (exit if no movement)

### Diversification Rules
- Max 4-8 open positions
- Max 20% per token
- Avoid copying whales into same token simultaneously

### When to Pause Trading
- Hit daily loss limit (-10%)
- 3+ consecutive losses
- Market-wide crash (>10% in 24h)
- Bot latency > 60 seconds consistently

---

## 🔧 Troubleshooting

### "Transaction failed" errors
**Causes**:
- Slippage too low (increase to 3-5%)
- Network congestion (wait and retry)
- Insufficient SOL for fees (keep 0.05 SOL buffer)

**Fix**: Increase slippage tolerance or wait for network to settle.

### Copy trade not executing
**Causes**:
- Whale paused (check whale status)
- Token blocked by safety filter
- Insufficient balance
- Copy latency too high

**Fix**: Check whale status, reduce safety filters (not recommended), or add funds.

### Auto-trade not starting
**Causes**:
- No whale wallets added (copy trade)
- Insufficient balance (<0.05 SOL)
- Bot not initialized

**Fix**: Add whale wallets, fund wallet, restart bot.

### High copy latency (>30s)
**Causes**:
- RPC node congestion
- WebSocket connection issues
- High network activity

**Fix**:
1. Enable latency optimization (auto-adjusts slippage)
2. Switch to premium RPC (Helius, QuickNode)
3. Reduce number of watched wallets

### Smart trade buying risky tokens
**Fix**:
1. Increase minimum momentum score (65 → 75)
2. Enable RugCheck filter
3. Add risky tokens to blacklist
4. Reduce max positions

---

## 📞 Support & Updates

### Bot Commands
- `/start` - Main menu
- `/copytrade` - Copy trading menu
- `/smarttrade` - Smart trading menu
- `/settings` - Settings menu
- `/help` - This FAQ

### Best Practices
1. **Start small**: Test with 0.1-0.5 SOL before scaling
2. **Monitor performance**: Check whale rankings weekly
3. **Adjust settings**: Tweak based on your risk tolerance
4. **Stay informed**: Join Telegram channel for updates

### Risk Disclaimer
⚠️ **Trading cryptocurrencies involves substantial risk of loss.** This bot is a tool, not financial advice. Never trade more than you can afford to lose. Past performance does not guarantee future results.

---

## 🎯 Quick Reference Card

### Copy Trade Settings (Recommended)
```
Copy Scale: 0.5x - 1.0x
Copy Delay: 0-30s
Max Loss: 20%
Weight: 1.0x
Dynamic Scale: ON ✅
Enhanced Qual: ON ✅
```

### Smart Trade Settings (Recommended)
```
Trade %: 10-20%
Max Positions: 4-8
Min Momentum: 65/100
Hard Stop: -20%
Trailing Stop: 15%
TP Ladder: 30/60/100%
```

### Enhanced Features (Recommended)
```
✅ Dynamic Copy Scale (ON)
✅ Enhanced Whale Qual (ON)
✅ Latency Optimization (ON)
✅ Signal Aggregation (ON)
⬜ Kelly Copy Trades (OFF - enable after 10+ trades)
⬜ Token Discovery+ (OFF - optional)
✅ TP Ladder Opt (ON)
✅ Enhanced Rebuy (ON)
✅ Daily Loss Limit (ON)
✅ Cool-Off Period (ON)
⬜ Jito Protection (OFF - enable for trades >5 SOL)
⬜ RugCheck Filter (ON - recommended)
```

---

**Last Updated**: March 2026
**Bot Version**: 2.0 with Enhanced Features
