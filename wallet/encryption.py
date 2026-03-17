"""
Enhanced encryption for private key management
"""
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import os

logger = logging.getLogger(__name__)


class KeyEncryption:
    """Secure encryption/decryption of private keys"""
    
    def __init__(self, master_password: str = None):
        """
        Initialize with master password
        If no password provided, generates from environment
        """
        if not master_password:
            master_password = os.getenv('ENCRYPTION_MASTER_PASSWORD', 'default_master_key_change_me')
        
        self.master_password = master_password
        self.cipher_suite = self._get_cipher_suite(master_password)
    
    def _get_cipher_suite(self, password: str) -> Fernet:
        """Derive encryption key from password"""
        # Use PBKDF2 to derive key from password
        salt = b'solana_bot_salt_v1'  # In production, use random salt per user
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)
    
    def encrypt(self, private_key: str) -> str:
        """Encrypt private key"""
        try:
            encrypted = self.cipher_suite.encrypt(private_key.encode())
            return base64.b64encode(encrypted).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            return None
    
    def decrypt(self, encrypted_key: str) -> str:
        """Decrypt private key"""
        try:
            encrypted = base64.b64decode(encrypted_key.encode())
            decrypted = self.cipher_suite.decrypt(encrypted)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return None


# Global encryption instance
encryption = KeyEncryption()
