"""VAPID key management for Web Push (issue #6).

Chatty stored the keypair in a JSON file under ``data/`` — that does NOT survive
Railway's ephemeral filesystem, and regenerating the keypair invalidates every
existing browser push subscription. Here the keypair is:

  1. taken from env (``VAPID_PUBLIC_KEY`` + ``VAPID_PRIVATE_KEY``) when an operator
     manages it — both must be set (partial config is rejected loudly); OR
  2. generated ONCE and persisted in the ``vapid_keys`` singleton (private key
     Fernet-encrypted), with a race-safe ``ON CONFLICT DO NOTHING`` claim so two
     concurrent generators converge on one row.

Decryption failure is FATAL (never a silent empty key → silently broken push):
this happens only when ``ENCRYPTION_KEY`` changed under a persisted key, and the
fix is operator action, not a broken keypair. All heavy libs (py_vapid,
cryptography) are imported lazily so the module graph imports without them.
"""

import logging

from core.config import settings
from core.encryption import decrypt_value, encrypt_value
from core.postgres import pg_execute, pg_fetchone

logger = logging.getLogger(__name__)


def _generate_keypair() -> tuple[str, str]:
    """Generate a new VAPID keypair → (public_b64url, private_b64url).

    Generated with ``cryptography`` directly (a P-256 EC key) rather than
    ``py_vapid.generate_keys()`` — that avoids any dependency on py-vapid's
    key-gen path and produces the exact b64url encoding pywebpush's
    ``vapid_private_key`` (raw 32-byte value) and the browser's
    ``applicationServerKey`` (uncompressed public point) expect.
    """
    import base64

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_bytes = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    pub = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    priv_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
    priv = base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode()
    return pub, priv


def get_vapid_keys() -> tuple[str, str]:
    """Return ``(public_key, private_key)`` — env-provided or generated-and-persisted."""
    env_pub = (settings.vapid_public_key or "").strip()
    env_priv = (settings.vapid_private_key or "").strip()
    if env_pub or env_priv:
        if not (env_pub and env_priv):
            raise RuntimeError(
                "Partial VAPID env config: set BOTH VAPID_PUBLIC_KEY and "
                "VAPID_PRIVATE_KEY, or neither (to auto-generate).")
        return env_pub, env_priv

    row = pg_fetchone("SELECT public_key, private_key_enc FROM vapid_keys WHERE id = 1")
    if row:
        priv = decrypt_value(row["private_key_enc"])
        if not priv:
            # Fail loud: the persisted key can't be decrypted (ENCRYPTION_KEY
            # changed?). Returning "" would silently break every push send.
            raise RuntimeError(
                "Stored VAPID private key could not be decrypted — ENCRYPTION_KEY "
                "may have changed. Set a stable ENCRYPTION_KEY (required for Web Push).")
        return row["public_key"], priv

    # Generate + persist, race-safe.
    pub, priv = _generate_keypair()
    inserted = pg_execute(
        "INSERT INTO vapid_keys (id, public_key, private_key_enc) VALUES (1, %s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        (pub, encrypt_value(priv)),
    )
    if inserted == 1:
        logger.info("Generated and persisted VAPID keypair (vapid_keys singleton)")
        return pub, priv
    # Lost the race — another writer won; use the stored pair.
    row = pg_fetchone("SELECT public_key, private_key_enc FROM vapid_keys WHERE id = 1")
    if not row:
        raise RuntimeError("VAPID key row vanished after a lost insert race")
    priv = decrypt_value(row["private_key_enc"])
    if not priv:
        raise RuntimeError("Stored VAPID private key could not be decrypted (post-race).")
    return row["public_key"], priv


def get_vapid_public_key() -> str:
    return get_vapid_keys()[0]


def get_vapid_private_key() -> str:
    return get_vapid_keys()[1]


def get_vapid_claims() -> dict:
    return {"sub": settings.vapid_subject}
