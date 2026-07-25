"""Postgres-backed store for the single Gmail connection (issue #8).

Mirrors providers/credentials.py's discipline: reads never raise (a missing/
uninitialized pool degrades to "disconnected", never an error), and the sanitized
status_dict never returns secrets or ciphertext. Secrets are Fernet-encrypted
per-column via core.encryption; the OAuth CSRF state is stored only as a SHA-256
hash.

Only stdlib + core helpers are imported here (no SDKs) so the module imports
cleanly with no DATABASE_URL.
"""

from __future__ import annotations

import hashlib
import logging

from core.encryption import decrypt_value, encrypt_value
from core.postgres import pg_execute, pg_fetchone

logger = logging.getLogger(__name__)

# TTL for an in-flight OAuth redirect's CSRF state.
_STATE_TTL_SQL = "now() - interval '10 minutes'"


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def get_row() -> dict:
    """The singleton row as a dict, or {} on any error (never raises).

    TIMESTAMPTZ columns come back as ISO strings (row_to_dict convention).
    """
    try:
        row = pg_fetchone("SELECT * FROM gmail_connection WHERE id = 1")
        return row or {}
    except Exception as e:  # pool not initialized, DB down, etc.
        logger.debug("gmail.store.get_row failed (treating as disconnected): %s", e)
        return {}


def is_connected(row: dict | None = None) -> bool:
    """True only when the connection is usable: status 'ok' AND the client
    credentials and refresh token actually DECRYPT to non-empty values.

    Basing this on decrypted values (not mere ciphertext presence) means a
    rotated/corrupt encryption key reads as disconnected instead of falsely
    "connected" — same principle as providers/credentials.is_provider_usable.
    Never raises.
    """
    row = get_row() if row is None else row
    if row.get("connection_status") != "ok":
        return False
    if not row.get("client_id"):
        return False
    if not decrypt_value(row.get("client_secret_enc", "")):
        return False
    if not decrypt_value(row.get("refresh_token_enc", "")):
        return False
    return True


def save_app_credentials(client_id: str, client_secret: str) -> None:
    """Store BYO OAuth app credentials and force a fresh connect.

    Replacing the app invalidates any tokens minted under the old client, so we
    clear the tokens/email/scopes/status AND any in-flight OAuth state.
    """
    pg_execute(
        """
        UPDATE gmail_connection SET
            client_id = %s,
            client_secret_enc = %s,
            access_token_enc = '',
            refresh_token_enc = '',
            email = '',
            scopes = '',
            token_expires_at = NULL,
            connection_status = 'disconnected',
            oauth_state_hash = '',
            oauth_state_created_at = NULL,
            updated_at = now()
        WHERE id = 1
        """,
        (client_id.strip(), encrypt_value(client_secret.strip())),
    )


def get_app_credentials() -> tuple[str, str]:
    """(client_id, client_secret) — client_secret decrypted, '' if unset."""
    row = get_row()
    return row.get("client_id", ""), decrypt_value(row.get("client_secret_enc", ""))


def set_oauth_state_hash(state: str) -> None:
    """Persist the SHA-256 hash of a freshly minted CSRF state for the redirect."""
    pg_execute(
        "UPDATE gmail_connection SET oauth_state_hash = %s, oauth_state_created_at = now() WHERE id = 1",
        (_hash_state(state),),
    )


def claim_oauth_state(state: str) -> bool:
    """Single-use, TTL-bounded claim of the OAuth CSRF state.

    Atomic compare-and-clear: the UPDATE matches only if the stored hash equals
    this state's hash AND it was created within the TTL, and clears it in the same
    statement (so a replay can't reuse it). A mismatch clears nothing (an attacker
    guessing states can't invalidate a legitimate in-flight connect). Returns True
    exactly once for a valid state.
    """
    if not state:
        return False
    rows = pg_execute(
        f"""
        UPDATE gmail_connection
           SET oauth_state_hash = '', oauth_state_created_at = NULL
         WHERE id = 1
           AND oauth_state_hash <> ''
           AND oauth_state_hash = %s
           AND oauth_state_created_at >= {_STATE_TTL_SQL}
        """,
        (_hash_state(state),),
    )
    return rows == 1


def save_tokens(access_token: str, refresh_token: str, expires_at, scopes: str, email: str) -> None:
    """Persist a freshly granted connection (called from the OAuth callback)."""
    pg_execute(
        """
        UPDATE gmail_connection SET
            access_token_enc = %s,
            refresh_token_enc = %s,
            token_expires_at = %s,
            scopes = %s,
            email = %s,
            connection_status = 'ok',
            updated_at = now()
        WHERE id = 1
        """,
        (
            encrypt_value(access_token),
            encrypt_value(refresh_token),
            expires_at,
            scopes,
            email,
        ),
    )


def update_access_token(
    access_token: str,
    expiry,
    prev_refresh_enc: str,
    refresh_token: str | None = None,
) -> None:
    """Persist an SDK-refreshed access token (and a rotated refresh token, if any).

    Compare-and-swap on the refresh-token ciphertext we refreshed under: if the
    connection was replaced meanwhile (a different account connected), the WHERE
    misses and this stale write is a no-op — it can't overwrite the new account's
    token. Never raises on the no-match case (rowcount 0).
    """
    if refresh_token is not None:
        pg_execute(
            """
            UPDATE gmail_connection SET
                access_token_enc = %s,
                refresh_token_enc = %s,
                token_expires_at = %s,
                connection_status = 'ok',
                updated_at = now()
            WHERE id = 1 AND refresh_token_enc = %s
            """,
            (encrypt_value(access_token), encrypt_value(refresh_token), expiry, prev_refresh_enc),
        )
    else:
        pg_execute(
            """
            UPDATE gmail_connection SET
                access_token_enc = %s,
                token_expires_at = %s,
                connection_status = 'ok',
                updated_at = now()
            WHERE id = 1 AND refresh_token_enc = %s
            """,
            (encrypt_value(access_token), expiry, prev_refresh_enc),
        )


def mark_broken() -> None:
    """Flag the connection as broken (refresh failed / revoked) so the UI prompts
    a reconnect. Never raises."""
    try:
        pg_execute(
            "UPDATE gmail_connection SET connection_status = 'broken', updated_at = now() WHERE id = 1"
        )
    except Exception as e:
        logger.warning("gmail.store.mark_broken failed: %s", e)


def clear_connection() -> None:
    """Disconnect: clear tokens/email/scopes/state; KEEP app credentials so a
    reconnect is one click."""
    pg_execute(
        """
        UPDATE gmail_connection SET
            access_token_enc = '',
            refresh_token_enc = '',
            email = '',
            scopes = '',
            token_expires_at = NULL,
            connection_status = 'disconnected',
            oauth_state_hash = '',
            oauth_state_created_at = NULL,
            updated_at = now()
        WHERE id = 1
        """
    )


def status_dict() -> dict:
    """Sanitized connection summary for the frontend — NEVER secrets or ciphertext.

    Imports oauth lazily (avoids an import cycle: oauth imports nothing from store).
    """
    from gmail import oauth

    row = get_row()
    return {
        "connected": is_connected(row),
        "email": row.get("email", ""),
        "connection_status": row.get("connection_status", "disconnected"),
        "client_id": row.get("client_id", ""),
        "client_secret_present": bool(row.get("client_secret_enc")),
        "scopes": row.get("scopes", "").split() if row.get("scopes") else [],
        "redirect_uri": oauth.redirect_uri(),
    }
