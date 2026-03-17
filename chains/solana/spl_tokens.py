"""
SPL Token utilities - get token balances, metadata, and manage token accounts
"""
import logging
import aiohttp
from typing import Dict, List, Optional
from config import SOLANA_RPC_URL

logger = logging.getLogger(__name__)


class SPLTokenManager:
    """Manage Solana Program Library tokens"""
    
    def __init__(self, rpc_url: str = SOLANA_RPC_URL):
        self.rpc_url = rpc_url
    
    async def get_token_balance(self, wallet_address: str, 
                               token_mint: str) -> Optional[Dict]:
        """Get token balance for wallet"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        wallet_address,
                        {
                            "mint": token_mint
                        },
                        {
                            "encoding": "jsonParsed"
                        }
                    ]
                }
                
                async with session.post(self.rpc_url, json=payload, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if data.get('result', {}).get('value'):
                            account = data['result']['value'][0]
                            token_amount = account['account']['data']['parsed']['info']['tokenAmount']
                            
                            return {
                                'amount': float(token_amount['amount']) / (10 ** token_amount['decimals']),
                                'decimals': token_amount['decimals'],
                                'address': account['pubkey']
                            }
        
        except Exception as e:
            logger.error(f"Error getting token balance: {e}")
        
        return None
    
    async def get_all_token_balances(self, wallet_address: str) -> List[Dict]:
        """Get all token balances for wallet"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        wallet_address,
                        {
                            "programId": "TokenkegQfeZyiNwAJsyFbPVwwQQfoza5ThxV3LL7f1t"  # Token program
                        },
                        {
                            "encoding": "jsonParsed"
                        }
                    ]
                }
                
                async with session.post(self.rpc_url, json=payload, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        tokens = []
                        
                        for account in data.get('result', {}).get('value', []):
                            info = account['account']['data']['parsed']['info']
                            amount = info['tokenAmount']
                            
                            tokens.append({
                                'mint': info['mint'],
                                'amount': float(amount['amount']) / (10 ** amount['decimals']),
                                'decimals': amount['decimals'],
                                'address': account['pubkey']
                            })
                        
                        return tokens
        
        except Exception as e:
            logger.error(f"Error getting token balances: {e}")
        
        return []
    
    async def get_token_metadata(self, token_mint: str) -> Optional[Dict]:
        """Get token metadata"""
        try:
            # Use Jupiter verification endpoint
            async with aiohttp.ClientSession() as session:
                url = f"https://token.jup.ag/mint/{token_mint}"
                
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
        
        except Exception as e:
            logger.error(f"Error getting token metadata: {e}")
        
        return None
    
    async def estimate_swap_output(self, input_mint: str, output_mint: str,
                                  amount: float) -> Optional[Dict]:
        """Estimate output amount for swap"""
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://quote-api.jup.ag/v6/quote"
                params = {
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": int(amount * 1e9) if input_mint == "11111111111111111111111111111111" else int(amount),
                    "slippageBps": 200  # 2% slippage
                }
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
        
        except Exception as e:
            logger.error(f"Error estimating swap output: {e}")
        
        return None


token_manager = SPLTokenManager()
