"""
Solana wallet integration for key management and transactions
"""
import logging
from typing import Optional, Dict, Tuple
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import base58
import json
import requests
from config import SOLANA_RPC_URL, RPC_TIMEOUT

logger = logging.getLogger(__name__)


class SolanaWallet:
    """Manage Solana wallet operations"""
    
    def __init__(self, rpc_url: str = SOLANA_RPC_URL):
        self.rpc_url = rpc_url
    
    def generate_keypair(self) -> Tuple[str, str]:
        """Generate a new keypair"""
        try:
            keypair = Keypair()
            public_key = str(keypair.pubkey())
            secret_key = base58.b58encode(bytes(keypair)).decode()
            return public_key, secret_key
        except Exception as e:
            logger.error(f"Error generating keypair: {e}")
            return None, None
    
    def import_keypair(self, private_key_base58: str) -> Optional[Keypair]:
        """Import keypair from base58-encoded private key.

        Handles both formats:
          - 32-byte seed  (Phantom / most wallets)
          - 64-byte keypair bytes (Solana CLI)
        """
        try:
            secret_bytes = base58.b58decode(private_key_base58)
            if len(secret_bytes) == 64:
                return Keypair.from_bytes(secret_bytes)
            elif len(secret_bytes) == 32:
                return Keypair.from_seed(secret_bytes)
            else:
                logger.error(f"Unexpected key length: {len(secret_bytes)} bytes")
                return None
        except Exception as e:
            logger.error(f"Error importing keypair: {e}")
            return None
    
    def get_balance(self, public_key: str) -> Optional[float]:
        """Get SOL balance for address"""
        try:
            pubkey = Pubkey.from_string(public_key)
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [public_key]
            }
            response = requests.post(self.rpc_url, json=payload, timeout=RPC_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                return data['result']['value'] / 1e9  # Convert lamports to SOL
        except Exception as e:
            logger.error(f"Error getting balance: {e}")

    def get_token_balance(self, wallet_address: str, token_mint: str) -> Optional[Dict]:
        """Get token balance for wallet"""
        try:
            # This would require SPL token program integration
            # Simplified version - would need proper implementation
            return None
        except Exception as e:
            logger.error(f"Error getting token balance: {e}")
            return None
    
    def validate_address(self, address: str) -> bool:
        """Validate Solana address format (base58, 32-byte pubkey)"""
        try:
            Pubkey.from_string(address)
            return True
        except Exception:
            return False
    
    def send_transaction(self, transaction_data: Dict) -> Optional[str]:
        """Send transaction"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [transaction_data]
            }
            response = requests.post(self.rpc_url, json=payload, timeout=RPC_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                return data['result']
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
        return None
    
    def get_recent_blockhash(self) -> Optional[str]:
        """Get recent blockhash for transactions"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getLatestBlockhash",
                "params": []
            }
            response = requests.post(self.rpc_url, json=payload, timeout=RPC_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                return data['result']['value']['blockhash']
        except Exception as e:
            logger.error(f"Error getting blockhash: {e}")
            return None


# Utility function for encryption
def encrypt_private_key(private_key: str, password: str = None) -> str:
    """Encrypt private key for storage using Fernet with random salt"""
    from wallet.encryption import encryption
    return encryption.encrypt(private_key)


def decrypt_private_key(encrypted_key: str, password: str = None) -> str:
    """Decrypt stored private key"""
    from wallet.encryption import encryption
    return encryption.decrypt(encrypted_key)
