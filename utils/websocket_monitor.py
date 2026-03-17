"""
Real-time Solana blockchain monitoring using WebSocket
"""
import logging
import asyncio
import aiohttp
from typing import Dict, List, Callable, Optional
from solders.pubkey import Pubkey
import json

logger = logging.getLogger(__name__)


class SolanaWebSocketMonitor:
    """Monitor Solana blockchain in real-time"""
    
    def __init__(self, wss_url: str = "wss://api.mainnet-beta.solana.com"):
        self.wss_url = wss_url
        self.websocket = None
        self.subscriptions = {}
        self.callbacks = {}
    
    async def connect(self):
        """Connect to WebSocket"""
        try:
            self.websocket = await aiohttp.ClientSession().ws_connect(self.wss_url)
            logger.info("✅ WebSocket connected")
            return True
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from WebSocket"""
        if self.websocket:
            await self.websocket.close()
            logger.info("✅ WebSocket disconnected")
    
    async def subscribe_account(self, account_address: str, 
                               callback: Optional[Callable] = None) -> bool:
        """Subscribe to account changes"""
        try:
            if not self.websocket:
                await self.connect()
            
            pubkey = Pubkey.from_string(account_address)
            
            subscription = {
                "jsonrpc": "2.0",
                "id": len(self.subscriptions) + 1,
                "method": "accountSubscribe",
                "params": [str(pubkey), {"encoding": "jsonParsed"}]
            }
            
            await self.websocket.send_json(subscription)
            
            sub_id = len(self.subscriptions) + 1
            self.subscriptions[account_address] = sub_id
            
            if callback:
                self.callbacks[account_address] = callback
            
            logger.info(f"📡 Subscribed to account: {account_address[:10]}...")
            return True
        
        except Exception as e:
            logger.error(f"Subscription error: {e}")
            return False
    
    async def subscribe_signature(self, signature: str, 
                                 callback: Optional[Callable] = None) -> bool:
        """Subscribe to transaction signature"""
        try:
            if not self.websocket:
                await self.connect()
            
            subscription = {
                "jsonrpc": "2.0",
                "id": len(self.subscriptions) + 1,
                "method": "signatureSubscribe",
                "params": [signature, {"commitment": "confirmed"}]
            }
            
            await self.websocket.send_json(subscription)
            
            sub_id = len(self.subscriptions) + 1
            self.subscriptions[signature] = sub_id
            
            if callback:
                self.callbacks[signature] = callback
            
            logger.info(f"🔍 Subscribed to signature: {signature[:10]}...")
            return True
        
        except Exception as e:
            logger.error(f"Signature subscription error: {e}")
            return False
    
    async def listen(self):
        """Listen for WebSocket messages"""
        try:
            async for msg in self.websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    
                    # Handle subscription confirmations
                    if 'result' in data and data.get('method') != 'notification':
                        logger.debug(f"Subscription confirmed: {data['result']}")
                    
                    # Handle notifications
                    elif data.get('method') == 'accountNotification':
                        account = data['params']['result']['context']['account']
                        await self._handle_account_update(account, data)
                    
                    elif data.get('method') == 'signatureNotification':
                        await self._handle_signature_update(data)
                
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
        
        except Exception as e:
            logger.error(f"Listening error: {e}")
    
    async def _handle_account_update(self, account: str, data: Dict):
        """Handle account change notification"""
        logger.info(f"📊 Account updated: {account[:10]}...")
        
        # Call registered callback
        if account in self.callbacks:
            await self.callbacks[account](data)
    
    async def _handle_signature_update(self, data: Dict):
        """Handle transaction signature notification"""
        result = data['params']['result']['value']['err']
        
        if result is None:
            logger.info("✅ Transaction confirmed")
        else:
            logger.warning(f"❌ Transaction failed: {result}")
    
    async def unsubscribe_account(self, account_address: str) -> bool:
        """Unsubscribe from account"""
        try:
            sub_id = self.subscriptions.get(account_address)
            if sub_id:
                unsubscribe = {
                    "jsonrpc": "2.0",
                    "id": sub_id,
                    "method": "accountUnsubscribe",
                    "params": [sub_id]
                }
                await self.websocket.send_json(unsubscribe)
                del self.subscriptions[account_address]
                if account_address in self.callbacks:
                    del self.callbacks[account_address]
                logger.info(f"🔇 Unsubscribed from account: {account_address[:10]}...")
                return True
        except Exception as e:
            logger.error(f"Unsubscribe error: {e}")
        
        return False


ws_monitor = SolanaWebSocketMonitor()
