"""
Solana wallet integration for key management and transactions
"""
import logging
from typing import Optional, Dict, Tuple
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction, Transaction
from solders.system_program import TransferParams, transfer
from solders.message import MessageV0
import base58
import base64
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

    def send_sol(self, from_wallet_address: str, to_address: str, amount: float, 
                 private_key: str = None, keypair: Keypair = None) -> Optional[Dict]:
        """Send SOL to another address.
        
        Args:
            from_wallet_address: Sender's wallet address
            to_address: Recipient's address
            amount: Amount of SOL to send
            private_key: Optional private key (base58 encoded)
            keypair: Optional Keypair object (takes precedence over private_key)
        
        Returns:
            Dict with 'success', 'tx_hash', and optional 'error'
        """
        try:
            # Get keypair
            if not keypair:
                if not private_key:
                    return {'success': False, 'error': 'No private key provided'}
                keypair = self.import_keypair(private_key)
                if not keypair:
                    return {'success': False, 'error': 'Invalid private key'}
            
            # Validate recipient address
            try:
                to_pubkey = Pubkey.from_string(to_address)
            except Exception as e:
                return {'success': False, 'error': f'Invalid recipient address: {str(e)}'}
            
            # Get recent blockhash
            blockhash = self.get_recent_blockhash()
            if not blockhash:
                return {'success': False, 'error': 'Failed to get recent blockhash'}
            
            # Create transfer instruction
            from_pubkey = Pubkey.from_string(from_wallet_address)
            lamports = int(amount * 1e9)  # Convert SOL to lamports
            
            ix = transfer(
                TransferParams(
                    from_pubkey=from_pubkey,
                    to_pubkey=to_pubkey,
                    lamports=lamports
                )
            )
            
            # Create and sign transaction
            msg = MessageV0.try_compile(
                payer=from_pubkey,
                instructions=[ix],
                address_lookup_table_accounts=[],
                recent_blockhash=Pubkey.from_string(blockhash)
            )
            
            tx = VersionedTransaction(msg, [keypair])
            
            # Send transaction
            tx_encoded = base64.b64encode(bytes(tx)).decode()
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    tx_encoded,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "confirmed"
                    }
                ]
            }
            
            response = requests.post(self.rpc_url, json=payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if 'result' in data:
                    tx_hash = data['result']
                    return {'success': True, 'tx_hash': tx_hash, 'signature': tx_hash}
                elif 'error' in data:
                    error_msg = data['error'].get('message', 'Unknown error')
                    return {'success': False, 'error': error_msg}
            
            return {'success': False, 'error': 'Failed to send transaction'}
            
        except Exception as e:
            logger.error(f"Error sending SOL: {e}")
            return {'success': False, 'error': str(e)}

    def send_spl_token(self, from_wallet_address: str, to_address: str, 
                       token_mint: str, amount: float, decimals: int = 9,
                       private_key: str = None, keypair: Keypair = None) -> Optional[Dict]:
        """Send SPL token to another address.
        
        Args:
            from_wallet_address: Sender's wallet address
            to_address: Recipient's address
            token_mint: Token mint address
            amount: Amount of tokens to send
            decimals: Token decimals (default 9)
            private_key: Optional private key (base58 encoded)
            keypair: Optional Keypair object
        
        Returns:
            Dict with 'success', 'tx_hash', and optional 'error'
        """
        try:
            # This requires more complex SPL token program integration
            # For now, return a helpful error
            return {
                'success': False, 
                'error': 'SPL token transfers require additional setup. Please use Phantom or another wallet for token transfers.'
            }
            
        except Exception as e:
            logger.error(f"Error sending SPL token: {e}")
            return {'success': False, 'error': str(e)}


# Utility function for encryption
def encrypt_private_key(private_key: str, password: str = None) -> str:
    """Encrypt private key for storage using Fernet with random salt"""
    from wallet.encryption import encryption
    return encryption.encrypt(private_key)


def decrypt_private_key(encrypted_key: str, password: str = None) -> str:
    """Decrypt stored private key"""
    from wallet.encryption import encryption
    return encryption.decrypt(encrypted_key)
