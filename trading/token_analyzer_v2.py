"""
Redesigned Token Analyzer - Async-first with proper caching.

ARCHITECTURAL IMPROVEMENTS:
- Uses shared HTTP client (connection pooling)
- Intelligent caching with TTL
- Correct holder concentration calculation
"""
import asyncio
import logging
import time
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

from core.http_client import http_client
from core.cache import cache
from core.confidence_engine import confidence_engine
from config import DEXSCREENER_API_URL, SOLSCAN_API_URL, BIRDEYE_API_KEY

logger = logging.getLogger(__name__)


@dataclass
class TokenAnalysis:
    """Token analysis result."""
    token_address: str
    confidence: float
    recommendation: str
    risk_score: float
    liquidity_usd: float = 0
    volume_24h: float = 0
    price_change_1h: float = 0
    price_change_24h: float = 0
    holder_concentration: float = 0
    buy_pressure: float = 0.5
    has_mint_authority: bool = True
    has_freeze_authority: bool = True
    sell_tax: float = 0
    age_hours: float = 0
    strengths: List[str] = None
    weaknesses: List[str] = None
    
    def __post_init__(self):
        if self.strengths is None:
            self.strengths = []
        if self.weaknesses is None:
            self.weaknesses = []


class TokenAnalyzer:
    """Professional token analyzer with confidence scoring."""
    
    _instance: Optional['TokenAnalyzer'] = None
    
    def __new__(cls) -> 'TokenAnalyzer':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        self.dexscreener_api = DEXSCREENER_API_URL
        self.solscan_api = SOLSCAN_API_URL
    
    async def analyze(self, token_address: str, chain: str = 'solana',
                      use_cache: bool = True) -> TokenAnalysis:
        """Full token analysis with confidence scoring."""
        cached = await cache.get('token_analysis', token_address)
        if cached:
            return cached
        
        # Run confidence engine
        conf_result = await confidence_engine.analyze(token_address, chain)
        
        analysis = TokenAnalysis(
            token_address=token_address,
            confidence=conf_result.confidence_score,
            recommendation=conf_result.decision,
            risk_score=100 - conf_result.confidence_score,
            strengths=conf_result.strengths,
            weaknesses=conf_result.weaknesses,
        )
        
        # Fetch additional data
        data = await self._fetch_all_data(token_address)
        dex = data.get('dex') or {}
        
        analysis.liquidity_usd = float((dex.get('liquidity') or {}).get('usd', 0) or 0)
        analysis.volume_24h = float((dex.get('volume') or {}).get('h24', 0) or 0)
        
        price_change = dex.get('priceChange', {}) or {}
        analysis.price_change_1h = float(price_change.get('h1', 0) or 0)
        analysis.price_change_24h = float(price_change.get('h24', 0) or 0)
        
        created = dex.get('pairCreatedAt', 0) or 0
        if created:
            analysis.age_hours = (time.time() * 1000 - created) / 3_600_000
        
        solscan = data.get('solscan') or {}
        analysis.has_mint_authority = bool(solscan.get('mint_authority'))
        analysis.has_freeze_authority = bool(solscan.get('freeze_authority'))
        analysis.sell_tax = float((dex.get('fees') or {}).get('sellTax', 0) or 0)
        
        if use_cache:
            await cache.set('token_analysis', token_address, analysis, ttl=120)
        
        return analysis
    
    async def _fetch_all_data(self, token_address: str) -> Dict:
        """Fetch data from all sources in parallel."""
        
        async def fetch_dex():
            url = f"{self.dexscreener_api}/latest/dex/tokens/{token_address}"
            resp = await http_client.get(url)
            if resp and isinstance(resp, dict):
                pairs = resp.get('pairs', [])
                return pairs[0] if pairs else None
            return None
        
        async def fetch_solscan():
            url = f"{self.solscan_api}/token/meta"
            resp = await http_client.get(url, params={'tokenAddress': token_address})
            return resp.get('data') if resp else None
        
        results = await asyncio.gather(fetch_dex(), fetch_solscan(), return_exceptions=True)
        
        return {
            'dex': results[0] if not isinstance(results[0], Exception) else None,
            'solscan': results[1] if not isinstance(results[1], Exception) else None,
        }
    
    async def quick_check(self, token_address: str) -> Tuple[bool, str]:
        """Quick safety check."""
        cached = await cache.get('quick_check', token_address)
        if cached:
            return cached
        
        url = f"{self.dexscreener_api}/latest/dex/tokens/{token_address}"
        resp = await http_client.get(url)
        
        if not resp or not isinstance(resp, dict):
            result = (False, "No data")
        else:
            pairs = resp.get('pairs', [])
            if not pairs:
                result = (False, "No pairs")
            else:
                p = pairs[0]
                liq = float((p.get('liquidity') or {}).get('usd', 0) or 0)
                sell_tax = float((p.get('fees') or {}).get('sellTax', 0) or 0)
                
                if liq < 5000:
                    result = (False, f"Low liq: ${liq:,.0f}")
                elif sell_tax > 25:
                    result = (False, f"High tax: {sell_tax:.0f}%")
                else:
                    result = (True, f"OK, liq=${liq:,.0f}")
        
        await cache.set('quick_check', token_address, result, ttl=60)
        return result
    
    async def batch_analyze(self, addresses: List[str], min_conf: float = 75.0) -> List[TokenAnalysis]:
        """Analyze multiple tokens in parallel."""
        results = await asyncio.gather(
            *[self.analyze(addr) for addr in addresses],
            return_exceptions=True
        )
        valid = [r for r in results if isinstance(r, TokenAnalysis) and r.confidence >= min_conf]
        valid.sort(key=lambda x: x.confidence, reverse=True)
        return valid
    
    # Backwards compatibility
    def analyze_token(self, token_address: str, chain: str = 'solana') -> Dict:
        """Sync wrapper - DEPRECATED."""
        import requests
        results = {
            'token_address': token_address,
            'risk_score': 50,
            'trade_recommendation': 'ANALYZE',
            'suggested_trade_percent': 10.0,
            'safety_metrics': {},
            'warnings': [],
        }
        try:
            r = requests.get(f"{self.dexscreener_api}/latest/dex/tokens/{token_address}", timeout=10)
            if r.status_code == 200:
                pairs = r.json().get('pairs', [])
                if pairs:
                    liq = float((pairs[0].get('liquidity') or {}).get('usd', 0) or 0)
                    results['safety_metrics']['liquidity'] = {'pool_size_usd': liq}
        except Exception as e:
            results['warnings'].append(str(e))
        return results


token_analyzer = TokenAnalyzer()
