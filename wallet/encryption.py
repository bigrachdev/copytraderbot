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

    def __init__(self, master_password: str = None, fallback_passwords=None):
        """
        Initialize with master password.
        If no password provided, reads from environment.
        Raises EnvironmentError if ENCRYPTION_MASTER_PASSWORD is not set.
        """
        if not master_password:
            master_password = os.getenv('ENCRYPTION_MASTER_PASSWORD')

        if fallback_passwords is None:
            fallback_passwords = [p.strip() for p in os.getenv('ENCRYPTION_FALLBACK_PASSWORDS', '').split(',') if p.strip()]

        if not master_password:
            raise EnvironmentError(
                "ENCRYPTION_MASTER_PASSWORD must be set in your .env file. "
                "All private keys are encrypted with this password — losing it means "
                "losing access to all wallets."
            )
        self.master_password = master_password
        self.fallback_passwords = fallback_passwords

    def _get_fernet(self, salt: bytes, password: str) -> Fernet:
        """Derive a Fernet instance from the given password and salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)

    def encrypt(self, private_key: str) -> str:
        """Encrypt private key with a fresh random salt.

        Stored format: base64(salt || fernet_token)
        The first SALT_SIZE bytes of the decoded value are the salt.
        """
        try:
            salt = os.urandom(self.SALT_SIZE)
            fernet = self._get_fernet(salt, self.master_password)
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
        except Exception as e:
            logger.error(f"Decryption error: invalid encrypted payload: {e}")
            return None

        # Try current password first, then fallbacks if configured.
        passwords_to_try = [self.master_password] + list(self.fallback_passwords or [])
        for password in passwords_to_try:
            try:
                fernet = self._get_fernet(salt, password)
                return fernet.decrypt(token).decode()
            except Exception:
                continue

        logger.error("Decryption error: failed to decrypt with current and fallback password(s)")
        return None


# Global encryption instance
encryption = KeyEncryption()
