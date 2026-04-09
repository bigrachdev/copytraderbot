# RATE LIMIT INTEGRATION GUIDE

## Quick Overview

The bot now has a **Rate Limit Handler** (`utils/rate_limit_handler.py`) that provides:
- Intelligent caching with TTL
- Exponential backoff + retry logic
- Request queuing (prevents thundering herd)
- Rate-limit detection and automatic cooldown
- Per-API configuration

## Integration Steps

### Step 1: Import the Data Fetcher

In any module that makes API calls (token_analyzer.py, smart_trader.py, etc.):

```python
from utils.rate_limit_handler import data_fetcher
```

### Step 2: Register APIs on Startup

In your module's `__init__` or at module load:

```python
class TokenAnalyzer:
    def __init__(self):
        # ... existing code ...
        
        # Register APIs with rate limiter
        data_fetcher.register_api('dexscreener', cache_ttl=120)
        data_fetcher.register_api('birdeye', cache_ttl=180)
        data_fetcher.register_api('solscan', cache_ttl=300)
        
        logger.info("✅ Rate limiter initialized for TokenAnalyzer")
```

### Step 3: Replace Direct API Calls

**Before (Direct call - can get rate limited):**
```python
def check_liquidity(self, token_address: str) -> Dict:
    try:
        r = requests.get(
            f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
            timeout=RPC_TIMEOUT
        )
        # ... handle response ...
    except Exception as e:
        logger.error(f"Error: {e}")
```

**After (With rate limiter):**
```python
async def check_liquidity(self, token_address: str) -> Dict:
    def _fetch():
        return requests.get(
            f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
            timeout=10
        )
    
    response = await data_fetcher.fetch(
        api_name='dexscreener',
        func=_fetch,
        params={'token': token_address},
        use_cache=True,
        cache_ttl=120,  # Override if needed
        max_retries=3
    )
    
    if not response:
        return default_metrics  # Graceful degradation
    
    # ... handle response ...
```

## Complete Integration Examples

### Token Analyzer Integration

```python
# trading/token_analyzer.py

import asyncio
from utils.rate_limit_handler import data_fetcher

class TokenAnalyzer:
    def __init__(self):
        self.birdeye_api = BIRDEYE_API_URL
        self.solscan_api = SOLSCAN_API_URL
        self.dex_screener_api = DEXSCREENER_API_URL
        
        # Register APIs
        data_fetcher.register_api('birdeye', cache_ttl=180)
        data_fetcher.register_api('solscan', cache_ttl=300)
        data_fetcher.register_api('dexscreener', cache_ttl=120)
        
        logger.info("✅ Token analyzer rate limiter initialized")
    
    async def check_liquidity(self, token_address: str) -> Dict:
        """Check liquidity with rate limiting"""
        
        def _fetch():
            return requests.get(
                f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                timeout=10
            )
        
        response = await data_fetcher.fetch(
            api_name='dexscreener',
            func=_fetch,
            params={'token': token_address},
            use_cache=True,
            cache_ttl=120
        )
        
        metrics = {'pool_size_usd': 0, 'is_liquid': False, 'score': 50}
        
        if not response:
            return metrics
        
        try:
            if response.status_code == 200:
                pairs = response.json().get('pairs') or []
                if pairs:
                    liq_usd = float(pairs[0].get('liquidity', {}).get('usd', 0) or 0)
                    metrics['pool_size_usd'] = liq_usd
                    # ... rest of logic ...
        except Exception as e:
            logger.error(f"Error parsing response: {e}")
        
        return metrics
```

### Smart Trader Integration

```python
# trading/smart_trader.py

from utils.rate_limit_handler import data_fetcher

class SmartTrader:
    def __init__(self):
        # ... existing code ...
        
        # Register discovery APIs
        data_fetcher.register_api('birdeye_trending', cache_ttl=300)
        data_fetcher.register_api('dexscreener_new', cache_ttl=120)
        
        logger.info("✅ Smart trader rate limiter initialized")
    
    async def discover_tokens(self):
        """Discover tokens with caching"""
        
        # Fetch trending tokens
        def _fetch_trending():
            return requests.get(
                BIRDEYE_TRENDING_URL,
                params={'limit': 50},
                timeout=15
            )
        
        response = await data_fetcher.fetch(
            api_name='birdeye_trending',
            func=_fetch_trending,
            params={'limit': 50},
            use_cache=True,
            cache_ttl=300  # Cache for 5 minutes
        )
        
        if not response:
            logger.warning("Could not fetch trending tokens - returning cached data")
            return []
        
        # ... process response ...
```

## Advanced Features

### 1. Check Cache Statistics

```python
# Anywhere in your code
from utils.rate_limit_handler import data_fetcher

stats = data_fetcher.get_health_status()
print(stats)

# Output:
# {
#     'cache_stats': {
#         'total_cached': 45,
#         'total_accesses': 1234,
#         'avg_hit_rate': 78.5,
#         'top_accessed': [('dexscreener', 567), ...]
#     },
#     'rate_limit_stats': {
#         'birdeye': {
#             'total_requests': 234,
#             'recent_success_rate': '95.2%',
#             'rate_limited': False,
#             'in_cooldown': False
#         },
#         ...
#     }
# }
```

### 2. Manual Cache Cleanup

```python
# Clean up expired cache entries
data_fetcher.cleanup()
```

### 3. Per-Request Configuration

```python
# Override cache TTL for specific request
result = await data_fetcher.fetch(
    api_name='dexscreener',
    func=_fetch,
    params={'token': token},
    use_cache=True,
    cache_ttl=60,  # Use 1 minute instead of default 120
    max_retries=5  # Use 5 retries instead of default 3
)
```

## Configuration in .env

```bash
# Keep-Alive (Render sleep prevention)
RENDER_EXTERNAL_URL=https://your-service.onrender.com
KEEP_ALIVE_PING_INTERVAL=120

# Rate Limiting & Caching
API_CACHE_ENABLED=true
API_CACHE_DEFAULT_TTL=300
API_MAX_CONCURRENT_REQUESTS=10
API_MAX_RETRIES=5

# Per-API Cache TTLs
TOKEN_ANALYZER_CACHE_TTL=300
DEX_SCREENER_CACHE_TTL=120
BIRDEYE_CACHE_TTL=180
SOLSCAN_CACHE_TTL=300

# Exponential Backoff
API_BACKOFF_INITIAL_DELAY=1
API_BACKOFF_MAX_DELAY=300
```

## Monitoring & Debugging

### Log Messages You'll See

```
💾 Cached: dexscreener:abc123 (TTL: 120s)      # Cache write
💾 Cache HIT: dexscreener:abc123               # Cache hit  
✅ dexscreener request successful              # Successful fetch
⚠️ Rate limit detected for birdeye             # Rate limit detected
⏳ Rate limit cooldown for birdeye: 30.5s      # Backoff active
❌ Fetch failed after 5 attempts               # All retries exhausted
🧹 Cleared 12 expired cache entries            # Automatic cleanup
```

### Enable Debug Logging

```python
import logging
logging.getLogger('utils.rate_limit_handler').setLevel(logging.DEBUG)
```

## Graceful Degradation

If an API call fails after retries:

1. **Try to return cached data** - Even if expired
2. **Return sensible defaults** - Empty list, 0 values, etc.
3. **Log warning** - So you know it happened
4. **Continue operation** - Don't crash

Example:
```python
result = await data_fetcher.fetch(...)

if not result:
    logger.warning(f"API call failed, using default for {api_name}")
    return DEFAULT_RESPONSE  # Pre-defined safe default

return result
```

## Performance Tips

1. **Set appropriate cache TTLs**
   - Fast-changing data (prices): 60-120 seconds
   - Medium-change data (liquidity): 180-300 seconds
   - Slow-changing data (metadata): 600+ seconds

2. **Use concurrent requests wisely**
   - Default is 10 concurrent requests
   - Increase if backend can handle it
   - Decrease if getting rate limited

3. **Monitor cache hit rates**
   - Aim for 60-80% cache hit rate
   - If lower, increase TTLs
   - If higher, might be too stale

4. **Batch similar requests**
   - Query multiple tokens in one API call if possible
   - Reduces total request count

## Troubleshooting

### Issue: Still getting rate limited?

```python
# 1. Check if cache is working
stats = data_fetcher.get_health_status()
print(stats['cache_stats'])  # Look for high hit rate

# 2. Increase cache TTLs
data_fetcher.register_api('birdeye', cache_ttl=600)  # 10 minutes instead of 3

# 3. Reduce scan frequency
SMART_SCAN_INTERVAL=600  # Scan every 10 min instead of 5
```

### Issue: Getting stale data?

```python
# 1. Decrease cache TTL
data_fetcher.register_api('dexscreener', cache_ttl=60)  # 1 minute instead of 2

# 2. Skip cache for critical queries
result = await data_fetcher.fetch(
    ...,
    use_cache=False  # Force fresh data
)
```

### Issue: App still crashing on API errors?

```python
# Always handle None responses
result = await data_fetcher.fetch(...)

if result is None:
    # Use fallback/cached/default
    logger.warning("API unavailable, using fallback")
    return SAFE_DEFAULT
```

## Questions?

See the main file for more details:
- `utils/rate_limit_handler.py` - Full implementation
- `BOT_RELIABILITY_FIX.md` - Deployment guide
- `main.py` - Auto-recovery example
