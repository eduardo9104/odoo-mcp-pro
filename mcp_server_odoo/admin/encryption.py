"""API key encryption helpers using Fernet (AES-128-CBC).

Provides symmetric encryption for API keys stored in Postgres.
If API_KEY_ENCRYPTION_KEY is not set, falls back to plaintext with a warning.
Handles migration of existing plaintext keys by detecting decryption failures.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_fernet_instance: Optional[object] = None
_encryption_available: Optional[bool] = None


def _get_fernet():
    """Get or create the Fernet instance from the environment key."""
    global _fernet_instance, _encryption_available

    if _encryption_available is not None:
        return _fernet_instance

    key = os.getenv("API_KEY_ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning(
            "API_KEY_ENCRYPTION_KEY not set — API keys will be stored in plaintext. "
            "Generate a key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
        _encryption_available = False
        _fernet_instance = None
        return None

    try:
        from cryptography.fernet import Fernet

        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
        _encryption_available = True
        logger.info("API key encryption enabled (Fernet/AES-128)")
        return _fernet_instance
    except Exception as e:
        logger.error(f"Invalid API_KEY_ENCRYPTION_KEY: {e} — falling back to plaintext")
        _encryption_available = False
        _fernet_instance = None
        return None


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key for storage. Returns ciphertext or plaintext if encryption unavailable."""
    f = _get_fernet()
    if f is None:
        return plaintext
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_api_key(stored_value: str) -> str:
    """Decrypt an API key from storage.

    Handles backward compatibility: if decryption fails (e.g. the value is
    plaintext from before encryption was enabled), returns the value as-is.
    """
    f = _get_fernet()
    if f is None:
        return stored_value

    try:
        return f.decrypt(stored_value.encode("utf-8")).decode("utf-8")
    except Exception:
        # Value is likely plaintext (pre-encryption migration)
        logger.debug("Could not decrypt API key — assuming plaintext (pre-migration)")
        return stored_value


def reset_encryption_state():
    """Reset cached encryption state. Used for testing."""
    global _fernet_instance, _encryption_available
    _fernet_instance = None
    _encryption_available = None
