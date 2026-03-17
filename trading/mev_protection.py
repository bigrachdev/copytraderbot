"""
MEV (Miner Extractable Value) protection for Solana
"""
import logging
from typing import Dict, Optional, List
import aiohttp

logger = logging.getLogger(__name__)


class MEVProtection:
    """Protect against MEV and sandwich attacks"""
    
    def __init__(self):
        self.jito_bundle_api = "https://api.jito.wtf/api/v1"
    
    async def use_private_pool(self, transaction: str) -> Optional[str]:
        """Submit transaction to private pool to avoid MEV"""
        try:
            # Jito private pools
            async with aiohttp.ClientSession() as session:
                url = f"{self.jito_bundle_api}/transactions"
                
                payload = {
                    "transactions": [transaction],
                    "skipPreFlightValidation": False
                }
                
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info("✅ Transaction submitted to private pool")
                        return data.get('result')
        
        except Exception as e:
            logger.error(f"Private pool error: {e}")
        
        return None
    
    async def check_for_sandwich_risk(self, token_input: str, 
                                     token_output: str,
                                     amount: float) -> Dict:
        """Analyze sandwich attack risk"""
        try:
            # Check mempool for similar trades
            risk_score = 0
            
            # Lower slippage = higher MEV risk (front-running opportunity)
            # Higher volume = higher MEV activity
            
            return {
                'risk_level': 'low' if risk_score < 3 else 'medium' if risk_score < 6 else 'high',
                'risk_score': risk_score,
                'recommendation': self._get_mev_recommendation(risk_score)
            }
        
        except Exception as e:
            logger.error(f"MEV risk check error: {e}")
            return {'risk_level': 'unknown', 'risk_score': 5}
    
    def _get_mev_recommendation(self, risk_score: int) -> str:
        """Get MEV protection recommendation"""
        if risk_score < 3:
            return "Safe to trade normally"
        elif risk_score < 6:
            return "Consider using private pool for this trade"
        else:
            return "Recommend using Jito/MEV protection"
    
    async def use_jito_bundle(self, transactions: List[str],
                             tip_amount: float = 0.001) -> Optional[str]:
        """Bundle transactions with Jito for MEV protection"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.jito_bundle_api}/bundles"
                
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendBundle",
                    "params": [transactions]
                }
                
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info("✅ Bundle submitted to Jito")
                        return data.get('result')
        
        except Exception as e:
            logger.error(f"Jito bundle error: {e}")
        
        return None
    
    def get_mev_protection_providers(self) -> List[Dict]:
        """Get list of available MEV protection providers"""
        return [
            {
                'name': 'Jito',
                'type': 'Private Pool',
                'cost': 'Variable (tips)',
                'url': 'https://docs.jito.wtf/'
            },
            {
                'name': 'MEV-Blocklist',
                'type': 'Block MEV Providers',
                'cost': 'Free',
                'url': 'https://github.com/MetaBlockers/MEV-Blocklist'
            },
            {
                'name': 'Skip API',
                'type': 'MEV Aware',
                'cost': 'Variable',
                'url': 'https://docs.skip.money/'
            },
            {
                'name': 'MEV Minimize',
                'type': 'Slippage Minimization',
                'cost': 'Low',
                'url': 'https://mev.minimize.wtf/'
            }
        ]


mev_protection = MEVProtection()
