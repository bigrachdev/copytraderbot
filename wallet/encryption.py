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

    SALT_SIZE = 16

    def __init__(self, master_password: str = None):
        """
        Initialize with master password.
        If no password provided, reads from environment.
        Raises EnvironmentError if ENCRYPTION_MASTER_PASSWORD is not set.
        """
        if not master_password:
            master_password = os.getenv('ENCRYPTION_MASTER_PASSWORD')
        if not master_password:
            raise EnvironmentError(
                "ENCRYPTION_MASTER_PASSWORD must be set in your .env file. "
                "All private keys are encrypted with this password — losing it means "
                "losing access to all wallets."
            )
        self.master_password = master_password

    def _get_fernet(self, salt: bytes) -> Fernet:
        """Derive a Fernet instance from master password + given salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.master_password.encode()))
        return Fernet(key)

    def encrypt(self, private_key: str) -> str:
        """Encrypt private key with a fresh random salt.

        Stored format: base64(salt || fernet_token)
        The first SALT_SIZE bytes of the decoded value are the salt.
        """
        try:
            salt = os.urandom(self.SALT_SIZE)
            fernet = self._get_fernet(salt)
            token = fernet.encrypt(private_key.encode())
            combined = salt + token
            return base64.b64encode(combined).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            return None

    def decrypt(self, encrypted_key: str) -> str:
        """Decrypt private key.

        Expects the format written by encrypt(): base64(salt || fernet_token).
        """
        try:
            combined = base64.b64decode(encrypted_key.encode())
            salt = combined[:self.SALT_SIZE]
            token = combined[self.SALT_SIZE:]
            fernet = self._get_fernet(salt)
            return fernet.decrypt(token).decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return None


# Global encryption instance
encryption = KeyEncryption()
