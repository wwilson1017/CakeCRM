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

# Two compare-and-swap mechanisms live in this module, by design — each guards a
# different invariant (issue #43):
#
#   * generation-CAS guards connection IDENTITY. `connection_generation` is bumped
#     by every mutation that changes which Google connection is live
#     (save_app_credentials / clear_connection / save_tokens). An action that
#     starts under one connection and completes later — the OAuth callback
#     (state-claim, then seconds of Google round-trips, then persist) and a pending
#     gmail_create_draft confirmation — captures the generation up front and
#     refuses to complete if it moved.
#   * ciphertext-CAS guards CREDENTIAL MATERIAL. update_access_token and
#     mark_broken key on the refresh-token ciphertext they acted under, so a
#     concurrently-rotated or replaced credential makes the stale write a no-op.
#
# They are not interchangeable. The callback cannot use ciphertext-CAS: on a fresh
# connect refresh_token_enc is '' and an intervening app-replace leaves it '' too,
# so an ''->'' compare would pass and persist tokens minted under the OLD
# client_id. And mark_broken must NOT bump the generation: a background scan
# hitting RefreshError mid-reconnect would then CAS-kill the admin's own in-flight
# callback — the exact bug class #43 exists to fix.


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


def save_app_credentials(client_id: str, client_secret: str) -> str:
    """Store BYO OAuth app credentials and force a fresh connect.

    Replacing the app invalidates any tokens minted under the old client, so we
    clear the tokens/email/scopes/status AND any in-flight OAuth state, and bump
    the generation (a new OAuth app is a new connection identity, so in-flight
    callbacks and pending drafts must not complete against it).

    Returns the refresh-token ciphertext that was cleared ('' if none) so the
    caller can revoke exactly the grant this call ended — the capture and the
    clear are one statement, closing the read-then-clear window (#43).
    """
    return _clear_returning_old_refresh(
        """
            client_id = %s,
            client_secret_enc = %s,
        """,
        (client_id.strip(), encrypt_value(client_secret.strip())),
    )


def _clear_returning_old_refresh(extra_set_sql: str, extra_params: tuple) -> str:
    """Clear the live connection, bump the generation, and return the refresh-token
    ciphertext that was cleared — atomically, in ONE statement.

    Shared by clear_connection (disconnect) and save_app_credentials (app replace):
    both end the current grant, and both need the OLD ciphertext afterwards so the
    router can revoke exactly the grant it just ended.

    The CTE takes `FOR UPDATE` before the UPDATE runs, so no other writer can slip
    between reading the old value and overwriting it. That lock is load-bearing:
    without it the CTE's snapshot could predate a concurrent write and hand back a
    ciphertext that is not the one actually cleared. Plain `UPDATE ... RETURNING`
    cannot be used at all here — RETURNING yields POST-update values, i.e. ''.

    ``extra_set_sql`` is a trusted, caller-supplied fragment of literal SQL
    assignments (never user input) whose placeholders bind ``extra_params`` first.
    """
    row = pg_fetchone(
        f"""
        WITH old AS (
            SELECT refresh_token_enc FROM gmail_connection WHERE id = 1 FOR UPDATE
        )
        UPDATE gmail_connection SET
            {extra_set_sql}
            access_token_enc = '',
            refresh_token_enc = '',
            email = '',
            scopes = '',
            token_expires_at = NULL,
            connection_status = 'disconnected',
            oauth_state_hash = '',
            oauth_state_created_at = NULL,
            connection_generation = gmail_connection.connection_generation + 1,
            updated_at = now()
        FROM old
        WHERE gmail_connection.id = 1
        RETURNING old.refresh_token_enc AS old_refresh_token_enc
        """,
        extra_params,
    )
    return (row or {}).get("old_refresh_token_enc") or ""


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


def claim_oauth_state(state: str) -> int | None:
    """Single-use, TTL-bounded claim of the OAuth CSRF state.

    Atomic compare-and-clear: the UPDATE matches only if the stored hash equals
    this state's hash AND it was created within the TTL, and clears it in the same
    statement (so a replay can't reuse it). A mismatch clears nothing (an attacker
    guessing states can't invalidate a legitimate in-flight connect). Succeeds
    exactly once for a valid state.

    Returns the connection generation observed AT CLAIM TIME, or None when the
    claim failed. The callback carries that number through the Google round-trips
    and hands it back to save_tokens as the CAS key, so a disconnect or app-replace
    during the handshake makes the persist a no-op instead of an overwrite (#43).
    Capturing it here rather than in a second query is what makes it race-free —
    there is no window between the claim and the read.

    (The statement never modifies connection_generation, so RETURNING's post-update
    value is the current one — no old/new pitfall.)
    """
    if not state:
        return None
    row = pg_fetchone(
        f"""
        UPDATE gmail_connection
           SET oauth_state_hash = '', oauth_state_created_at = NULL
         WHERE id = 1
           AND oauth_state_hash <> ''
           AND oauth_state_hash = %s
           AND oauth_state_created_at >= {_STATE_TTL_SQL}
        RETURNING connection_generation
        """,
        (_hash_state(state),),
    )
    if not row:
        return None
    return int(row.get("connection_generation") or 0)


def save_tokens(
    access_token: str,
    refresh_token: str,
    expires_at,
    scopes: str,
    email: str,
    expected_generation: int,
) -> bool:
    """Persist a freshly granted connection (called from the OAuth callback).

    Compare-and-swap on the generation captured by claim_oauth_state: if the admin
    disconnected, replaced the OAuth app, or completed a competing connect during
    the Google round-trips, the WHERE misses and this write is a no-op. Returns
    True when the connection was persisted, False on a CAS miss — the caller
    revokes the just-granted tokens rather than orphaning a live Google grant.

    A successful persist bumps the generation: a new grant is a new connection
    identity, so any pending draft proposed under the previous one must not
    execute against it, and a second racing callback must miss too (#43).
    """
    return pg_execute(
        """
        UPDATE gmail_connection SET
            access_token_enc = %s,
            refresh_token_enc = %s,
            token_expires_at = %s,
            scopes = %s,
            email = %s,
            connection_status = 'ok',
            connection_generation = connection_generation + 1,
            updated_at = now()
        WHERE id = 1 AND connection_generation = %s
        """,
        (
            encrypt_value(access_token),
            encrypt_value(refresh_token),
            expires_at,
            scopes,
            email,
            expected_generation,
        ),
    ) == 1


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


def mark_broken(prev_refresh_enc: str) -> None:
    """Flag the connection as broken (refresh failed / revoked) so the UI prompts
    a reconnect. Never raises.

    Compare-and-swap on the refresh-token ciphertext the failing call ran under
    (#43): if the connection was replaced or another call rotated the token
    meanwhile, the WHERE misses and a healthy connection is not marked broken.
    Concretely — call A refreshes successfully and rotates the refresh token while
    call B, still holding the old one, gets a RefreshError; B's CAS misses, so B
    cannot break A's working connection.

    Deliberately does NOT bump connection_generation: 'broken' is a status change,
    not an identity change, and bumping here would let a background scan's
    RefreshError invalidate the admin's own in-flight reconnect.
    """
    try:
        pg_execute(
            "UPDATE gmail_connection SET connection_status = 'broken', updated_at = now() "
            "WHERE id = 1 AND refresh_token_enc = %s",
            (prev_refresh_enc,),
        )
    except Exception as e:
        logger.warning("gmail.store.mark_broken failed: %s", e)


def clear_connection() -> str:
    """Disconnect: clear tokens/email/scopes/state and bump the generation; KEEP
    app credentials so a reconnect is one click.

    Returns the refresh-token ciphertext that was cleared ('' if none) so the
    caller can revoke exactly the grant this call ended — capture and clear happen
    in one statement, closing the read-then-clear window (#43).
    """
    return _clear_returning_old_refresh("", ())


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
