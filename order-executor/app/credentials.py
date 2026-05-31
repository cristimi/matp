"""
Credential decryption helper (AES-256-GCM).
"""

import os
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


def _get_key() -> bytes:
    key_str = os.environ.get("MASTER_KEY", "")
    if len(key_str) < 32:
        raise ValueError(
            "MASTER_KEY environment variable must be at least 32 characters."
        )
    return key_str[:32].encode("utf-8")


def encrypt(plaintext: str) -> bytes:
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt(ciphertext_bytes: bytes) -> str:
    if ciphertext_bytes in (b'\x00', bytes([0])):
        raise ValueError(
            "Placeholder credential detected. "
            "Update this account's credentials via the Dashboard."
        )
    if len(ciphertext_bytes) < 28:
        raise ValueError(
            f"Credential data too short ({len(ciphertext_bytes)} bytes). "
            "Expected at least 28 bytes (12 nonce + 16 tag minimum)."
        )
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce      = ciphertext_bytes[:12]
    ciphertext = ciphertext_bytes[12:]
    plaintext  = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
