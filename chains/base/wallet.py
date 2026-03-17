"""
Base (Coinbase L2) wallet integration
"""
import logging
import os
import requests
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

BASE_RPC_URL = os.getenv('BASE_RPC_URL', 'https://mainnet.base.org')
BASE_CHAIN_ID = 8453


class BaseWallet:
    """Manage Base (Coinbase L2) wallet operations"""

    def __init__(self, rpc_url: str = BASE_RPC_URL):
        self.rpc_url = rpc_url
        self.chain_id = BASE_CHAIN_ID

    def get_balance(self, address: str) -> Optional[float]:
        """Get ETH balance on Base for a given address"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": [address, "latest"],
                "id": 1,
            }
            response = requests.post(self.rpc_url, json=payload, timeout=10)
            result = response.json().get("result")
            if result:
                return int(result, 16) / 1e18
            return None
        except Exception as e:
            logger.error(f"Error getting Base balance: {e}")
            return None

    def get_transaction_count(self, address: str) -> Optional[int]:
        """Get nonce / transaction count for address"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getTransactionCount",
                "params": [address, "latest"],
                "id": 1,
            }
            response = requests.post(self.rpc_url, json=payload, timeout=10)
            result = response.json().get("result")
            if result:
                return int(result, 16)
            return None
        except Exception as e:
            logger.error(f"Error getting transaction count: {e}")
            return None

    def get_token_balance(self, wallet_address: str, token_contract: str) -> Optional[float]:
        """Get ERC-20 token balance on Base"""
        try:
            # ERC-20 balanceOf selector: 0x70a08231
            data = "0x70a08231" + wallet_address[2:].zfill(64)
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": token_contract, "data": data}, "latest"],
                "id": 1,
            }
            response = requests.post(self.rpc_url, json=payload, timeout=10)
            result = response.json().get("result")
            if result and result != "0x":
                return int(result, 16) / 1e18
            return 0.0
        except Exception as e:
            logger.error(f"Error getting token balance: {e}")
            return None


base_wallet = BaseWallet()
