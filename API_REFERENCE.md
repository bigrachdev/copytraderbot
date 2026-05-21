# 🔧 Developer API Reference - All Improvements

## 🚀 Quick Import & Usage

```python
# Core improvements engine
from trading.improved_copy_trading import improved_copy_trading

# Individual modules (if needed)
from utils.circuit_breaker import circuit_breaker_manager, rpc_breaker
from trading.whale_scorer import whale_scorer
from trading.exit_strategy import exit_strategy_manager
from trading.grid_trading import grid_manager
from utils.speed_optimizer import latency_optimizer
```

---

## 1️⃣ Speed Optimizer API

### `latency_optimizer.initialize()`
Initialize latency optimization subsystem.

```python
await latency_optimizer.initialize()
# Starts mempool monitoring, parallel connections, etc.
```

### `latency_optimizer.get_commitment_level()`
Returns WebSocket commitment level based on optimization level.

```python
commitment = latency_optimizer.get_commitment_level()
# Returns: "processed" (level 3) or "confirmed" (level 2) or "finalized" (level 1)
```

### `latency_optimizer._get_expected_latency()`
Get expected latency range for current optimization level.

```python
latency = latency_optimizer._get_expected_latency()
# Returns: "10,000-25,000" ms or "2,000-5,000" ms or "<1,000" ms
```

### `FastSignatureCache` Class

```python
from utils.speed_optimizer import FastSignatureCache

cache = FastSignatureCache(max_age_seconds=300)

# Check if seen
if cache.is_seen(signature):
    continue  # Already processed

# Mark as processed
cache.mark_seen(signature)

# Check size
print(f"Cache size: {cache.size()}")
```

---

## 2️⃣ Whale Scorer API

### `whale_scorer.score_whale(user_id, whale_address)`
Score a single whale on 0-100 scale.

```python
score = whale_scorer.score_whale(user_id=123, whale_address="7xL8Cq8h...")
# Returns: float between 0-100

if score > 50:
    await copy_whale_trades(whale_address)
```

**Scoring Breakdown:**
- Win Rate (30%): `wins / (wins + losses)`
- Avg Profit (20%): Weighted by price action
- Max Drawdown (25%): Largest peak-to-trough loss
- Recency (15%): Recent trades weighted higher
- Consistency (10%): Low variance = better

### `whale_scorer.rank_whales(user_id, whale_addresses)`
Score and rank multiple whales, sorted by score descending.

```python
whales = ["7xL8Cq8h...", "5kP2Jm3Q...", "9wN7Rt4X..."]
ranked = whale_scorer.rank_whales(user_id=123, whale_addresses=whales)
# Returns: [("9wN7Rt4X...", 85.2), ("7xL8Cq8h...", 72.1), ("5kP2Jm3Q...", 41.3)]

for whale, score in ranked[:5]:
    await copy_top_whales(whale)
```

### `whale_scorer.check_consecutive_losses(user_id, whale_address)`
Check if whale exceeded max consecutive losses.

```python
should_trade, loss_count = whale_scorer.check_consecutive_losses(
    user_id=123, 
    whale_address="7xL8Cq8h..."
)

if not should_trade:
    logger.warning(f"Whale paused: {loss_count} losses (max: 5)")
    # Don't copy this whale for 1-2 days
```

---

## 3️⃣ Exit Strategy API

### `exit_strategy_manager.get_tp_ladder_for_token(token_address, volatility=None)`
Get volatility-adjusted take-profit ladder.

```python
tp_ladder = exit_strategy_manager.get_tp_ladder_for_token(
    token_address="EPjFWdd5...",
    volatility=0.12  # 12% volatility
)
# Returns: [(0.30, 0.25), (0.60, 0.50), (1.00, 1.00)]
# Meaning: At +30% sell 25%, at +60% sell 50%, at +100% sell all

# Use in position monitoring
for threshold, fraction in tp_ladder:
    if current_gain >= threshold:
        amount_to_sell = position_size * fraction
        await sell(amount_to_sell)
```

**Volatility Tiers:**
- Low vol (<5%): `0.30 → 0.60 → 1.00` (hold longer)
- Mid vol (5-15%): `0.50 → 1.00 → 2.00` (normal)
- High vol (>15%): `0.75 → 1.50 → 3.00` (exit faster)

### `exit_strategy_manager.get_dynamic_trailing_stop(token_address, volatility=None, tp_hit=False)`
Get trailing stop percentage adjusted for volatility.

```python
trailing_pct = exit_strategy_manager.get_dynamic_trailing_stop(
    token_address="EPjFWdd5...",
    volatility=0.18,
    tp_hit=True  # Only activate after TP1 hit
)
# Returns: 0.20 (20% trailing stop for high volatility)

if current_price < peak_price * (1 - trailing_pct):
    await exit_position()  # Stop trailing
```

### `exit_strategy_manager.should_enable_breakeven_stop(tp1_hit, current_gain)`
Check if should move stop-loss to breakeven.

```python
if exit_strategy_manager.should_enable_breakeven_stop(
    tp1_hit=True,  # TP1 already hit
    current_gain=0.35  # +35% gain
):
    await move_sl_to_breakeven()  # Protect remaining position
```

### `exit_strategy_manager.get_exit_strategy_summary(token_address, volatility=None)`
Get full exit strategy for a token as readable dict.

```python
summary = exit_strategy_manager.get_exit_strategy_summary("EPjFWdd5...")
# Returns:
# {
#     "token": "EPjFWdd…",
#     "tp_ladder": ["+30%→sell25%", "+60%→sell50%", "+100%→sell100%"],
#     "trailing_stop": "20%",
#     "breakeven_enabled": True,
#     "volatility_adjustment": True
# }
```

---

## 4️⃣ Circuit Breaker API

### `circuit_breaker_manager.get_or_create(name, failure_threshold=5, recovery_timeout=60)`
Get or create a circuit breaker for a service.

```python
my_breaker = circuit_breaker_manager.get_or_create(
    name="my-api",
    failure_threshold=3,
    recovery_timeout=30
)

try:
    result = await my_breaker.call(api_call_func, arg1, arg2)
except CircuitBreakerOpen:
    logger.error("Service unavailable, will retry later")
```

### `breaker.get_status()`
Get current circuit breaker status.

```python
status = rpc_breaker.get_status()
# Returns:
# {
#     "name": "solana-rpc",
#     "state": "CLOSED" or "OPEN" or "HALF_OPEN",
#     "failure_count": 2,
#     "success_count": 145,
#     "last_failure_time": "2024-05-20T10:30:45",
#     "state_change_time": "2024-05-20T10:25:12"
# }
```

### `circuit_breaker_manager.get_status_all()`
Get status of all circuit breakers.

```python
all_status = circuit_breaker_manager.get_status_all()
for name, status in all_status.items():
    if status['state'] == 'OPEN':
        logger.warning(f"⚠️ {name} is OPEN - service degraded")
```

### `breaker.reset()`
Manually reset a circuit breaker to CLOSED state.

```python
rpc_breaker.reset()  # Force reset after manual intervention
```

---

## 5️⃣ Grid Trading API

### `grid_manager.create_grid(token_address, current_price, investment_sol)`
Create a new grid for a token.

```python
grid = grid_manager.create_grid(
    token_address="EPjFWdd5...",
    current_price=0.0001,
    investment_sol=2.0
)
# Automatically generates 10 buy levels below + 10 sell levels above

# Grid creates 20 orders automatically:
# - 10 BUY orders at 20% below to 0% current price
# - 10 SELL orders at 0% current to 20% above
```

### `grid.update_price(new_price)`
Update current price and check for fills.

```python
result = await grid.update_price(0.00011)
# Returns:
# {
#     "current_price": 0.00011,
#     "fills": [
#         {
#             "status": "filled",
#             "level": 3,
#             "price": 0.00011,
#             "amount": 100,
#             "profit": 0.02
#         }
#     ],
#     "grid_status": {...}
# }

# Process fills
for fill in result['fills']:
    if fill['status'] == 'filled':
        logger.info(f"Grid level {fill['level']} filled at {fill['price']}")
```

### `grid.get_status()`
Get current grid status.

```python
status = grid.get_status()
# Returns:
# {
#     "token": "EPjFWdd…",
#     "current_price": 0.00011,
#     "total_invested": 2.0,
#     "buy_levels_filled": 3,
#     "sell_levels_filled": 1,
#     "total_bought": 15000,
#     "total_sold": 2000,
#     "realized_profit": 0.05,
#     "unrealized_profit": 0.08,
#     "roi_pct": 2.5
# }
```

### `grid.get_summary_table()`
Get formatted summary for display/logging.

```python
summary = grid.get_summary_table()
print(summary)
# Output:
# 📊 **Grid Trading Summary**
# Token: `EPjFWdd…`
# Current Price: `$0.00011`
# Total Invested: `2.0000 SOL`
# Filled Buy Orders: `3/10`
# Filled Sell Orders: `1/10`
# Realized Profit: `$0.0500`
# Unrealized Profit: `$0.0800`
# ROI: `2.50%`
```

### `grid_manager.get_grid(token_address)`
Get existing grid for a token.

```python
grid = grid_manager.get_grid("EPjFWdd5...")
if grid:
    status = grid.get_status()
```

### `grid_manager.get_all_grids_summary()`
Get summary of all active grids.

```python
summary = grid_manager.get_all_grids_summary()
# Returns:
# **Active Grids:**
#   • `EPjFWdd…`: $0.2500 profit (5.2%)
#   • `4zZ2Kp7M…`: $0.1200 profit (2.1%)
```

---

## 6️⃣ Unified Improvements Engine API

### `improved_copy_trading.initialize()`
Initialize all subsystems at startup.

```python
await improved_copy_trading.initialize()
# Starts:
# - Latency optimizer
# - Circuit breaker manager
# - Whale scorer caches
# - Grid trading engine (if enabled)
```

### `improved_copy_trading.qualify_whale_for_copy(user_id, whale_address)`
Qualify whale using advanced scoring.

```python
qualified, score, reason = await improved_copy_trading.qualify_whale_for_copy(
    user_id=123,
    whale_address="7xL8Cq8h..."
)
# Returns: (True, 72.5, "Advanced scoring: 72.5/100")

if qualified:
    await start_copying_trades(whale_address)
else:
    logger.warning(f"Whale rejected: {reason}")
```

### `improved_copy_trading.get_optimized_exit_strategy(token_address, volatility=None)`
Get complete exit strategy for a token.

```python
strategy = await improved_copy_trading.get_optimized_exit_strategy(
    token_address="EPjFWdd5...",
    volatility=0.15
)
# Returns:
# {
#     "tp_ladder": [(0.75, 0.25), (1.50, 0.50), (3.00, 1.00)],
#     "trailing_stop": 0.20,
#     "breakeven_enabled": True,
#     "summary": {...}
# }
```

### `improved_copy_trading.start_grid_trading(token_address, current_price, investment_sol)`
Start grid trading for a token (if enabled).

```python
grid_id = await improved_copy_trading.start_grid_trading(
    token_address="EPjFWdd5...",
    current_price=0.0001,
    investment_sol=5.0
)

if grid_id:
    logger.info(f"Grid trading active for {grid_id}")
```

### `improved_copy_trading.get_system_status()`
Get overall system status.

```python
status = improved_copy_trading.get_system_status()
# Returns:
# {
#     "latency_optimization_level": 2,
#     "expected_latency_ms": "2,000-5,000",
#     "circuit_breakers": {...},
#     "active_grids": 3,
#     "advanced_whale_scoring": True,
#     "volatility_adjusted_tp": True,
#     "dynamic_trailing_stops": True
# }
```

### `improved_copy_trading.get_status_report()`
Get human-readable status report.

```python
report = improved_copy_trading.get_status_report()
print(report)
# Output:
# **🚀 Improved Copy Trading Engine Status**
#
# **Latency Optimization**: Level 2 (2,000-5,000ms)
# **Active Grids**: 3
# **Advanced Whale Scoring**: ✅ ON
# **Volatility-Adjusted TP**: ✅ ON
# **Dynamic Trailing Stops**: ✅ ON
#
# **Circuit Breakers**:
#   • solana-rpc: CLOSED
#   • token-analyzer: CLOSED
#   • dex-screener: HALF_OPEN
```

---

## 🔌 Integration Examples

### Example 1: Copy Trading with All Improvements
```python
# Qualify whale
qualified, score, reason = await improved_copy_trading.qualify_whale_for_copy(
    user_id=user_id,
    whale_address=whale_address
)

if not qualified:
    logger.warning(f"Whale {whale_address[:8]}… rejected: {reason}")
    return

# Get exit strategy
exit_plan = await improved_copy_trading.get_optimized_exit_strategy(
    token_address=token_address,
    volatility=token_volatility
)

# Execute trade
await execute_copy_trade(
    whale_address=whale_address,
    token_address=token_address,
    tp_ladder=exit_plan['tp_ladder'],
    trailing_stop=exit_plan['trailing_stop']
)

logger.info(f"✅ Copy trade started: {whale_address[:8]}… {token_address[:8]}…")
```

### Example 2: Grid Trading
```python
# Start grid
grid_id = await improved_copy_trading.start_grid_trading(
    token_address=token_address,
    current_price=current_price,
    investment_sol=5.0
)

# Monitor price updates
for price in price_stream:
    result = await grid_manager.get_grid(grid_id).update_price(price)
    
    # Log fills
    for fill in result['fills']:
        if fill['status'] == 'filled':
            logger.info(f"✅ Grid fill: {fill['amount']} tokens at {fill['price']}")
    
    # Check status
    if result['grid_status']['roi_pct'] > 5.0:
        logger.info(f"📈 Grid ROI: {result['grid_status']['roi_pct']:.1f}%")
```

### Example 3: Error Recovery with Circuit Breaker
```python
# API call protected by circuit breaker
try:
    result = await rpc_breaker.call(rpc.get_account, wallet_address)
except CircuitBreakerOpen:
    logger.warning("RPC service is temporarily unavailable")
    # Fallback to cached data or skip
    return cached_balance or 0
```

---

## 📊 Configuration Reference

See `.env.improvements` for all configurable values.

Most important:
- `LATENCY_OPTIMIZATION_LEVEL`: 1-3 (higher = faster but riskier)
- `WHALE_SCORE_THRESHOLD_TO_TRADE`: 0-100 (higher = only trade best whales)
- `ENABLE_GRID_TRADING`: true/false (optional passive income)
- `ENABLE_VOLATILITY_ADJUSTED_TP`: true/false (adapt exit strategy)

---

## 🆘 Common Issues

### "CircuitBreakerOpen exception"
Service is temporarily unavailable. Wait and retry.
```python
except CircuitBreakerOpen:
    logger.warning("Service unavailable, will retry in 60s")
    # Implement exponential backoff
```

### "Whale score is 0"
Whale has no track record. Need more historical trades.
```python
if score < 10:
    logger.warning("Not enough data on whale, skipping")
```

### "Grid not filling"
Orders might be at unrealistic prices. Check price action.
```python
grid = grid_manager.get_grid(token_address)
status = grid.get_status()
if status['buy_levels_filled'] == 0:
    logger.warning("No buys filled - adjust grid range")
```

---

**For more examples, see INTEGRATION_CHECKLIST.md**
