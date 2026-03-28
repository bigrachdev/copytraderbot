# Suggested Wallets Fix - Birdeye API Update

## Problem
The suggested wallets feature was not working because the Birdeye API changed its request parameters and response format.

### Issues Found:
1. **Wrong parameter name**: API was using `type: "24h"` but Birdeye now expects `period: "24h"`
2. **Wrong response field names**: Code was looking for `PnL` and `trade` but API now returns `pnl` and `trade_count`
3. **Invalid limit**: API limit must be 1-10, code was requesting 20

### Error Messages:
```json
{"success":false,"message":"type invalid format"}
{"success":false,"message":"limit should be integer, range 1-10"}
```

## Solution
Updated `bot/telegram_bot.py` function `fetch_top_traders()`:

### Changes Made:
1. Changed API parameter from `type: "24h"` to `period: "24h"`
2. Updated response field mapping:
   - `t.get("PnL", 0)` → `t.get("pnl", 0)`
   - `t.get("trade", 0)` → `t.get("trade_count", 0)`
3. Limited request limit to max 10: `min(limit, 10)`
4. Updated function default limit from 20 to 10

### Code Changes (bot/telegram_bot.py):
```python
# Before:
params={
    "type":   "24h",
    "sort_by": "PnL",
    "sort_type": "desc",
    "offset": 0,
    "limit":  limit,
}
# ...
"pnl_usd":     float(t.get("PnL", 0) or 0),
"trade_count": int(t.get("trade", 0) or 0),

# After:
params={
    "period": "24h",
    "limit":  min(limit, 10),  # API limit: 1-10
}
# ...
"pnl_usd":     float(t.get("pnl", 0) or 0),
"trade_count": int(t.get("trade_count", 0) or 0),
```

## Testing
✅ API now returns 10 traders successfully
✅ Response fields correctly mapped
✅ Cache working properly
✅ UI callback functions compatible with new field names

## API Response Format (New)
```json
{
  "data": {
    "items": [
      {
        "network": "solana",
        "address": "48Jv5mJxqMehrGjtB2sUr2cTJAwvbEGx1jCFNJKGwaue",
        "pnl": 83414722.84,
        "volume": 1320806250.88,
        "trade_count": 24
      }
    ]
  }
}
```

## Files Modified
- `bot/telegram_bot.py` 
  - Fixed `fetch_top_traders()` function (lines 55-100)
  - Updated caller in `suggested_whales_callback()` to use `limit=10` (line 1813)

## No Breaking Changes
- UI code already uses the internal field names (`pnl_usd`, `volume_usd`, `trade_count`)
- All callback functions remain compatible
- Cache mechanism unchanged
