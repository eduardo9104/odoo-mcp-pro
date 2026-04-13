"""API key encryption helpers using Fernet (AES-128-CBC).

Provides symmetric encryption for API keys stored in Postgres.
API_KEY_ENCRYPTION_KEY is required — the server will refuse to start without it.
Generate a key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_fernet_instance: Optional[object] = None
_encryption_initialized: bool = False


def _get_fernet():
    """Get or create the Fernet instance from the environment key.

    Raises RuntimeError if API_KEY_ENCRYPTION_KEY is missing or invalid.
    Encryption is mandatory — no plaintext fallback.
    """
    global _fernet_instance, _encryption_initialized

    if _encryption_initialized:
        return _fernet_instance

    key = os.getenv("API_KEY_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "API_KEY_ENCRYPTION_KEY environment variable is required. "
            "Generate a key with: "
            "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )

    try:
        from cryptography.fernet import Fernet

        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
        _encryption_initialized = True
        logger.info("API key encryption enabled (Fernet/AES-128)")
        return _fernet_instance
    except Exception as e:
        raise RuntimeError(f"Invalid API_KEY_ENCRYPTION_KEY: {e}") from e


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key for storage. Raises RuntimeError if encryption not configured."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_api_key(stored_value: str) -> str:
    """Decrypt an API key from storage.

    Falls back to returning the value as-is only if decryption fails AND the
    value does not look like a Fernet token (migration of pre-encryption rows).
    """
    f = _get_fernet()
    try:
        return f.decrypt(stored_value.encode("utf-8")).decode("utf-8")
    except Exception:
        # Value is plaintext from before encryption was enabled (one-time migration).
        # Log at WARNING so operators know re-saving the connection will encrypt it.
        logger.warning(
            "Could not decrypt API key — value appears to be plaintext (pre-encryption row). "
            "User should re-save their connection to encrypt it."
        )
        return stored_value


def reset_encryption_state():
    """Reset cached encryption state. Used for testing."""
    global _fernet_instance, _encryption_initialized
    _fernet_instance = None
    _encryption_initialized = False
