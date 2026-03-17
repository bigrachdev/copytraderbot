"""
DEX swap integration for Jupiter, Raydium, Orca
"""
import logging
import aiohttp
import asyncio
from typing import Optional, Dict, List
from config import JUPITER_API, RAYDIUM_API, ORCA_URL, SLIPPAGE_TOLERANCE, WSOL_MINT, SOL_MINT

logger = logging.getLogger(__name__)


class DEXSwapper:
    """Handle swaps across multiple DEXs"""
    
    def __init__(self):
        self.jupiter_api = JUPITER_API
        self.raydium_api = RAYDIUM_API
        self.orca_url = ORCA_URL
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def get_jupiter_price(self, input_mint: str, output_mint: str, 
                                amount: float) -> Optional[Dict]:
        """Get price quote from Jupiter"""
        try:
            lamports = int(amount * 1e9)  # Convert SOL to lamports
            
            async with aiohttp.ClientSession() as session:
                url = f"{self.jupiter_api}/quote"
                params = {
                    'inputMint': input_mint,
                    'outputMint': output_mint,
                    'amount': lamports,
                    'slippageBps': int(SLIPPAGE_TOLERANCE * 100)  # Convert % to basis points
                }
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            'dex': 'jupiter',
                            'price': data.get('outAmount', 0) / 1e9 if output_mint == WSOL_MINT else data.get('outAmount', 0),
                            'priceImpact': data.get('priceImpactPct', 0),
                            'route': data.get('route', [])
                        }
        except Exception as e:
            logger.error(f"Error getting Jupiter price: {e}")
        
        return None
    
    async def get_raydium_price(self, input_mint: str, output_mint: str, 
                                amount: float) -> Optional[Dict]:
        """Get price quote from Raydium"""
        try:
            # Raydium API integration
            async with aiohttp.ClientSession() as session:
                url = f"{self.raydium_api}/swap/info"
                params = {
                    'inputMint': input_mint,
                    'outputMint': output_mint,
                    'amount': amount
                }
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            'dex': 'raydium',
                            'price': data.get('outputAmount', 0),
                            'priceImpact': data.get('priceImpact', 0)
                        }
        except Exception as e:
            logger.error(f"Error getting Raydium price: {e}")
        
        return None
    
    async def get_orca_price(self, input_mint: str, output_mint: str, 
                             amount: float) -> Optional[Dict]:
        """Get price quote from Orca"""
        try:
            # Orca API integration
            async with aiohttp.ClientSession() as session:
                url = f"{self.orca_url}/price"
                params = {
                    'inputMint': input_mint,
                    'outputMint': output_mint,
                    'amount': amount
                }
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            'dex': 'orca',
                            'price': data.get('price', 0),
                            'priceImpact': data.get('priceImpact', 0)
                        }
        except Exception as e:
            logger.error(f"Error getting Orca price: {e}")
        
        return None
    
    async def get_best_price(self, input_mint: str, output_mint: str, 
                             amount: float) -> Dict:
        """Get best price across all DEXs"""
        try:
            tasks = [
                self.get_jupiter_price(input_mint, output_mint, amount),
                self.get_raydium_price(input_mint, output_mint, amount),
                self.get_orca_price(input_mint, output_mint, amount)
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            results = [r for r in results if r and not isinstance(r, Exception)]
            
            if not results:
                logger.warning("No price quotes available")
                return {}
            
            # Find best price (highest output amount)
            best = max(results, key=lambda x: x.get('price', 0))
            logger.info(f"✅ Best price from {best['dex']}: {best['price']}")
            
            return best
        
        except Exception as e:
            logger.error(f"Error getting best price: {e}")
            return {}
    
    async def execute_swap(self, input_mint: str, output_mint: str, 
                          amount: float, dex: str = 'jupiter') -> Optional[Dict]:
        """Execute swap on specified DEX"""
        try:
            if dex == 'jupiter':
                return await self.execute_jupiter_swap(input_mint, output_mint, amount)
            elif dex == 'raydium':
                return await self.execute_raydium_swap(input_mint, output_mint, amount)
            elif dex == 'orca':
                return await self.execute_orca_swap(input_mint, output_mint, amount)
        except Exception as e:
            logger.error(f"Error executing swap on {dex}: {e}")
        
        return None
    
    async def execute_jupiter_swap(self, input_mint: str, output_mint: str, 
                                   amount: float) -> Optional[Dict]:
        """Execute swap via Jupiter"""
        try:
            # Get quote first
            price_data = await self.get_jupiter_price(input_mint, output_mint, amount)
            if not price_data:
                return None
            
            # In production, would build and sign transaction
            # For now, return transaction data
            return {
                'dex': 'jupiter',
                'status': 'ready',
                'inputAmount': amount,
                'expectedOutput': price_data['price'],
                'priceImpact': price_data['priceImpact']
            }
        except Exception as e:
            logger.error(f"Error executing Jupiter swap: {e}")
            return None
    
    async def execute_raydium_swap(self, input_mint: str, output_mint: str, 
                                   amount: float) -> Optional[Dict]:
        """Execute swap via Raydium"""
        try:
            price_data = await self.get_raydium_price(input_mint, output_mint, amount)
            if not price_data:
                return None
            
            return {
                'dex': 'raydium',
                'status': 'ready',
                'inputAmount': amount,
                'expectedOutput': price_data['price']
            }
        except Exception as e:
            logger.error(f"Error executing Raydium swap: {e}")
            return None
    
    async def execute_orca_swap(self, input_mint: str, output_mint: str, 
                               amount: float) -> Optional[Dict]:
        """Execute swap via Orca"""
        try:
            price_data = await self.get_orca_price(input_mint, output_mint, amount)
            if not price_data:
                return None
            
            return {
                'dex': 'orca',
                'status': 'ready',
                'inputAmount': amount,
                'expectedOutput': price_data['price']
            }
        except Exception as e:
            logger.error(f"Error executing Orca swap: {e}")
            return None


swapper = DEXSwapper()
