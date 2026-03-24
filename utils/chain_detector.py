"""
Address validation helpers — Solana only.
"""
import re

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _is_base58(s: str) -> bool:
    return all(c in _BASE58_ALPHABET for c in s)


def is_solana_address(address: str) -> bool:
    """Return True if address looks like a Solana public key (base58, 32-44 chars)."""
    if not address or not isinstance(address, str):
        return False
    address = address.strip()
    return 32 <= len(address) <= 44 and _is_base58(address)
