"""
CakeCRM — Encryption at rest for credentials.

Encrypts sensitive fields (API keys, OAuth tokens) using Fernet (AES-128-CBC)
before writing to disk. The key resolves through the shared ladder in
`core.secret_store` — ENCRYPTION_KEY env var, then the OS keychain, then
`backend/data/.encryption-key` — which is the same ladder the JWT signing secret
uses (issue #222); see that module for why each step exists.

Unlike the JWT secret this key does NOT fall back to a process-local value when
it cannot be stored: a Fernet key that dies at restart would encrypt new
credentials into ciphertext nobody can ever read back, so a visible failure at
the first credential operation is the better outcome.

Encrypted values are prefixed with "enc:v1:" so plaintext values (pre-migration)
are detected and auto-encrypted on first load.
"""

import logging

from cryptography.fernet import Fernet

from core.secret_store import PersistedSecret

logger = logging.getLogger(__name__)

ENCRYPTED_PREFIX = "enc:v1:"

# JSON keys whose values contain secrets and must be encrypted on disk.
# When adding a new integration, add any secret field names here.
SENSITIVE_FIELDS = frozenset({
    "key",              # API keys (auth-profiles.json)
    "token",            # setup tokens
    "access",           # OAuth access tokens
    "refresh",          # OAuth refresh tokens
    "api_key",          # integration API keys
    "access_token",     # integration OAuth
    "refresh_token",    # integration OAuth
    "client_secret",    # BYO OAuth app credentials
    "bot_token",        # Telegram bot token
    "private_key",      # VAPID private key (vapid_keys.private_key_enc); documents
                        # the field class for encrypt_dict — the vapid_keys column
                        # is encrypted via encrypt_value() directly (issue #6).
})


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def _is_fernet_key(value: str) -> bool:
    try:
        Fernet(value.encode())
        return True
    except Exception:
        return False


_ENCRYPTION_KEY = PersistedSecret(
    env_var="ENCRYPTION_KEY",
    filename=".encryption-key",
    generate=lambda: Fernet.generate_key().decode(),
    validate=_is_fernet_key,
)


class EncryptionKeyManager:
    """Resolve or generate the Fernet encryption key.  Result is cached.

    A thin façade over the shared `PersistedSecret` ladder, kept because the
    whole app (and the test suite) already addresses the key through this name.
    """

    @classmethod
    def get_key(cls) -> bytes:
        return _ENCRYPTION_KEY.resolve().encode()

    @classmethod
    def reset_cache(cls) -> None:
        """Clear the cached key (useful for tests)."""
        _ENCRYPTION_KEY.reset_cache()


# ---------------------------------------------------------------------------
# Value-level encrypt / decrypt
# ---------------------------------------------------------------------------

def encrypt_value(plaintext: str) -> str:
    """Encrypt a single string value → ``enc:v1:<fernet_token>``."""
    if not plaintext or plaintext.startswith(ENCRYPTED_PREFIX):
        return plaintext  # empty or already encrypted
    key = EncryptionKeyManager.get_key()
    token = Fernet(key).encrypt(plaintext.encode()).decode()
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_value(stored: str) -> str:
    """Decrypt a value.  Returns ``""`` on failure (tamper / wrong key).

    Plaintext values (no ``enc:v1:`` prefix) pass through unchanged — this
    provides backwards compatibility during migration.
    """
    if not stored or not stored.startswith(ENCRYPTED_PREFIX):
        return stored  # plaintext pass-through
    token = stored[len(ENCRYPTED_PREFIX):]
    try:
        key = EncryptionKeyManager.get_key()
        return Fernet(key).decrypt(token.encode()).decode()
    except Exception as exc:
        logger.warning("Failed to decrypt value (tampered or wrong key): %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Dict-level helpers  (recursive for nested profile dicts)
# ---------------------------------------------------------------------------

def encrypt_dict(data: dict) -> dict:
    """Return a copy with sensitive string fields encrypted (recurses into nested dicts)."""
    result = {}
    for k, v in data.items():
        if k in SENSITIVE_FIELDS and isinstance(v, str):
            result[k] = encrypt_value(v)
        elif isinstance(v, dict):
            result[k] = encrypt_dict(v)
        else:
            result[k] = v
    return result


def decrypt_dict(data: dict) -> dict:
    """Return a copy with sensitive string fields decrypted (recurses into nested dicts)."""
    result = {}
    for k, v in data.items():
        if k in SENSITIVE_FIELDS and isinstance(v, str):
            result[k] = decrypt_value(v)
        elif isinstance(v, dict):
            result[k] = decrypt_dict(v)
        else:
            result[k] = v
    return result


def needs_migration(data: dict) -> bool:
    """Return True if any sensitive field is still plaintext (not encrypted)."""
    for k, v in data.items():
        if k in SENSITIVE_FIELDS and isinstance(v, str) and v and not v.startswith(ENCRYPTED_PREFIX):
            return True
        if isinstance(v, dict) and needs_migration(v):
            return True
    return False
