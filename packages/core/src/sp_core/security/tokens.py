"""Opaque token generation and at-rest encryption.

Two separate concerns:

* **Tokens** (sessions, email verification, password reset) are random bytes. We
  store only their SHA-256, so a database read alone cannot be replayed as a valid
  token (AUTH.md §2).
* **Strava OAuth tokens** must be recoverable, so they are encrypted with Fernet
  rather than hashed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

from sp_core.config import get_settings

_TOKEN_BYTES = 32


def generate_token() -> str:
    """A URL-safe opaque token. This value is only ever shown once."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """What we store. Plain SHA-256 is fine here: the input is already 256 bits of
    entropy, so there is nothing for a slow KDF to protect against."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate, expected)


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.token_encryption_key.strip()
    if not key:
        # Derive a stable dev key from the session secret so local development works
        # without extra setup. Production must set TOKEN_ENCRYPTION_KEY explicitly.
        if settings.environment != "development":
            raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set outside development")
        digest = hashlib.sha256(settings.session_secret.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> bytes:
    """Encrypt a third-party token for storage. Never logged, never returned."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str | None:
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
