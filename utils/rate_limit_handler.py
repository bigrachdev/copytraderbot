"""
Rate Limit Handler - Intelligent caching, queuing, and backoff for API calls
Prevents rate limiting and ensures infinite data flow
"""
import logging
import time
import asyncio
import hashlib
from typing import Any, Dict, Optional, Callable
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
import json

logger = logging.getLogger(__name__)


class RateLimitCache:
    """Intelligent cache with TTL and smart invalidation"""
    
    def __init__(self, default_ttl: int = 300):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl
        self.access_count: Dict[str, int] = defaultdict(int)
        self.hit_rates: Dict[str, float] = {}
        
    def set(self, key: str, value: Any, ttl: int = None):
        """Store value with TTL"""
        self.cache[key] = {
            'value': value,
            'expires_at': datetime.now() + timedelta(seconds=ttl or self.default_ttl),
            'created_at': datetime.now(),
            'hits': 0
        }
        logger.debug(f"💾 Cached: {key} (TTL: {ttl or self.default_ttl}s)")
    
    def get(self, key: str) -> Optional[Any]:
        """Retrieve cached value if not expired"""
        if key not in self.cache:
            return None
        
        item = self.cache[key]
        if datetime.now() > item['expires_at']:
            del self.cache[key]
            logger.debug(f"🗑️  Cache expired: {key}")
            return None
        
        item['hits'] += 1
        self.access_count[key] += 1
        self.hit_rates[key] = item['hits'] / (time.time() - item['created_at'].timestamp())
        return item['value']
    
    def clear_expired(self):
        """Remove all expired entries"""
        now = datetime.now()
        expired_keys = [k for k, v in self.cache.items() if now > v['expires_at']]
        for key in expired_keys:
            del self.cache[key]
        if expired_keys:
            logger.info(f"🧹 Cleared {len(expired_keys)} expired cache entries")
    
    def stats(self) -> Dict:
        """Get cache statistics"""
        return {
            'total_cached': len(self.cache),
            'total_accesses': sum(self.access_count.values()),
            'avg_hit_rate': sum(self.hit_rates.values()) / len(self.hit_rates) if self.hit_rates else 0,
            'top_accessed': sorted(self.access_count.items(), key=lambda x: x[1], reverse=True)[:5]
        }


class RateLimiter:
    """Exponential backoff + smart retry with rate-limit detection"""
    
    def __init__(self):
        self.backoff_config: Dict[str, Dict] = {}  # api -> {attempts, delay, max_delay}
        self.rate_limit_detected: Dict[str, bool] = defaultdict(bool)
        self.cooldown_until: Dict[str, float] = {}
        self.request_history: Dict[str, list] = defaultdict(list)  # api -> [(timestamp, success), ...]
        
    def register_api(self, api_name: str, initial_delay: int = 1, max_delay: int = 300):
        """Register an API endpoint with backoff config"""
        self.backoff_config[api_name] = {
            'attempts': 0,
            'delay': initial_delay,
            'max_delay': max_delay
        }
    
    def _calculate_backoff(self, api_name: str) -> float:
        """Calculate exponential backoff delay"""
        config = self.backoff_config.get(api_name, {'attempts': 0, 'max_delay': 300})
        config['attempts'] += 1
        config['delay'] = min(config['delay'] * 2, config['max_delay'])
        return config['delay']
    
    def _reset_backoff(self, api_name: str):
        """Reset backoff counters on success"""
        if api_name in self.backoff_config:
            self.backoff_config[api_name]['attempts'] = 0
            self.backoff_config[api_name]['delay'] = 1
    
    async def execute_with_backoff(
        self,
        api_name: str,
        func: Callable,
        *args,
        max_retries: int = 3,
        **kwargs
    ) -> Optional[Any]:
        """Execute function with exponential backoff and rate-limit detection"""
        
        # Check if in cooldown
        if api_name in self.cooldown_until:
            wait_time = self.cooldown_until[api_name] - time.time()
            if wait_time > 0:
                logger.warning(f"⏳ Rate limit cooldown for {api_name}: {wait_time:.1f}s remaining")
                await asyncio.sleep(min(wait_time, 5))  # Wait max 5s then retry anyway
                del self.cooldown_until[api_name]
        
        for attempt in range(max_retries):
            try:
                # Check rate limit detection
                if self.rate_limit_detected.get(api_name):
                    await asyncio.sleep(self._calculate_backoff(api_name))
                
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                
                self._reset_backoff(api_name)
                self.rate_limit_detected[api_name] = False
                self.request_history[api_name].append((time.time(), True))
                logger.debug(f"✅ {api_name} request successful (attempt {attempt + 1})")
                return result
                
            except Exception as e:
                error_str = str(e).lower()
                
                # Detect rate limiting
                if any(keyword in error_str for keyword in ['429', 'rate', 'limit', 'too many', 'throttle']):
                    self.rate_limit_detected[api_name] = True
                    cooldown = self._calculate_backoff(api_name)
                    self.cooldown_until[api_name] = time.time() + cooldown
                    logger.warning(f"⚠️ Rate limit detected for {api_name}: backoff {cooldown}s")
                    
                    if attempt < max_retries - 1:
                        await asyncio.sleep(cooldown)
                        continue
                
                # Other errors
                self.request_history[api_name].append((time.time(), False))
                if attempt < max_retries - 1:
                    delay = self._calculate_backoff(api_name)
                    logger.warning(f"❌ {api_name} failed (attempt {attempt + 1}/{max_retries}): {e}")
                    logger.info(f"⏳ Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"❌ {api_name} failed after {max_retries} attempts: {e}")
                    self._reset_backoff(api_name)
        
        return None
    
    def get_stats(self) -> Dict:
        """Get rate limiter statistics"""
        stats = {}
        for api_name, history in self.request_history.items():
            recent = [h for h in history if time.time() - h[0] < 3600]  # Last hour
            success_rate = sum(1 for h in recent if h[1]) / len(recent) if recent else 0
            stats[api_name] = {
                'total_requests': len(history),
                'recent_success_rate': f"{success_rate * 100:.1f}%",
                'rate_limited': self.rate_limit_detected.get(api_name, False),
                'in_cooldown': api_name in self.cooldown_until
            }
        return stats


class RequestQueue:
    """Queue requests to prevent thundering herd and ensure fair distribution"""
    
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: Dict[str, int] = defaultdict(int)
        self.max_concurrent = max_concurrent
        
    async def queue_request(
        self,
        api_name: str,
        func: Callable,
        *args,
        **kwargs
    ) -> Optional[Any]:
        """Queue a request with async semaphore"""
        async with self.semaphore:
            self.active_tasks[api_name] += 1
            try:
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                logger.debug(f"✅ Queued request completed: {api_name}")
                return result
            finally:
                self.active_tasks[api_name] -= 1
    
    def get_queue_depth(self) -> Dict:
        """Get current queue depth per API"""
        return dict(self.active_tasks)


class IntelligentDataFetcher:
    """High-level API combining cache, rate limit, and queue"""
    
    def __init__(self):
        self.cache = RateLimitCache(default_ttl=300)
        self.rate_limiter = RateLimiter()
        self.request_queue = RequestQueue(max_concurrent=10)
        self.fetch_strategies: Dict[str, Dict] = {}
        
        logger.info("✅ Intelligent Data Fetcher initialized")
    
    def register_api(
        self,
        api_name: str,
        initial_backoff: int = 1,
        max_backoff: int = 300,
        cache_ttl: int = 300
    ):
        """Register API with custom settings"""
        self.rate_limiter.register_api(api_name, initial_backoff, max_backoff)
        self.fetch_strategies[api_name] = {
            'cache_ttl': cache_ttl,
            'last_fetch': 0,
            'fetch_count': 0
        }
    
    def _generate_cache_key(self, api_name: str, params: Dict) -> str:
        """Generate cache key from API name and parameters"""
        params_json = json.dumps(params, sort_keys=True, default=str)
        params_hash = hashlib.md5(params_json.encode()).hexdigest()
        return f"{api_name}:{params_hash}"
    
    async def fetch(
        self,
        api_name: str,
        func: Callable,
        params: Dict = None,
        use_cache: bool = True,
        cache_ttl: int = None,
        max_retries: int = 3
    ) -> Optional[Any]:
        """
        Fetch data with intelligent caching and rate limiting
        
        Args:
            api_name: Unique API identifier
            func: Async function to call
            params: Parameters to pass to function
            use_cache: Whether to use caching
            cache_ttl: Override cache TTL
            max_retries: Number of retry attempts
        
        Returns:
            Result or None if all retries fail
        """
        params = params or {}
        cache_key = self._generate_cache_key(api_name, params)
        
        # Check cache first
        if use_cache:
            cached_result = self.cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"💾 Cache HIT: {cache_key}")
                return cached_result
        
        # Fetch with rate limiting and queuing
        try:
            result = await self.rate_limiter.execute_with_backoff(
                api_name,
                await self.request_queue.queue_request(api_name, func, **params),
                max_retries=max_retries
            )
            
            if result is not None and use_cache:
                ttl = cache_ttl or self.fetch_strategies.get(api_name, {}).get('cache_ttl', 300)
                self.cache.set(cache_key, result, ttl=ttl)
                logger.debug(f"💾 Cached result: {cache_key}")
            
            return result
        except Exception as e:
            logger.error(f"❌ Fetch failed for {api_name}: {e}")
            return None
    
    def get_health_status(self) -> Dict:
        """Get health status of all APIs"""
        return {
            'cache_stats': self.cache.stats(),
            'rate_limit_stats': self.rate_limiter.get_stats(),
            'queue_depth': self.request_queue.get_queue_depth(),
            'cache_entries': len(self.cache.cache)
        }
    
    def cleanup(self):
        """Periodic cleanup"""
        self.cache.clear_expired()
        logger.info(f"🧹 Data fetcher cleanup completed. {len(self.cache.cache)} entries cached")


# Global instance
data_fetcher = IntelligentDataFetcher()


def register_api(api_name: str, cache_ttl: int = 300):
    """Decorator to register and auto-cache API calls"""
    def decorator(func):
        data_fetcher.register_api(api_name, cache_ttl=cache_ttl)
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await data_fetcher.fetch(
                api_name,
                func,
                params={'args': str(args), 'kwargs': kwargs},
                cache_ttl=cache_ttl
            )
            return result
        
        return wrapper
    return decorator
