"""Gmail service factory + execution seam (issue #8).

Builds a google-auth Credentials from the stored tokens and lets the SDK
auto-refresh (proactively on expiry, retry-once on 401) — no hand-rolled refresh
timing. call_gmail() is the single seam every tool executor uses; it runtime
allow-lists the operation (defense-in-depth: only the four read/draft ops can ever
run — no send op exists to pass), persists any refreshed token under a
compare-and-swap, and always closes the transport.

All google/googleapiclient imports are lazy (inside functions) so the module
imports with no SDK / no DATABASE_URL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.encryption import decrypt_value
from gmail import oauth, ops, store

logger = logging.getLogger(__name__)


class GmailAuthError(Exception):
    """Raised when Gmail is not connected or the connection is no longer valid.
    The message is user-facing."""


# Runtime allow-list: the ONLY operations call_gmail will execute. There is no
# send op anywhere, and this guarantees none can be introduced via a stray caller.
_APPROVED_OPS = frozenset({
    ops.list_messages_op,
    ops.get_thread_op,
    ops.create_draft_op,
    ops.get_profile_op,
})


def _parse_expiry(iso_value) -> "datetime | None":
    """Parse the stored ISO expiry to NAIVE UTC (google-auth treats
    Credentials.expiry as naive UTC)."""
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value) if isinstance(iso_value, str) else iso_value
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def build_service_from_token(access_token: str):
    """Build a Gmail service from a bare access token (used by the OAuth callback
    to fetch the profile right after the exchange, before a row exists)."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(token=access_token)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def call_with_token(access_token: str, op, **kwargs):
    """Run an approved op against a service built from a bare access token (the
    OAuth callback path, before a stored connection exists). Enforces the SAME
    _APPROVED_OPS allow-list as call_gmail — so this second service-building path
    can't invoke any Gmail method outside the read/draft set — and always closes
    the transport."""
    if op not in _APPROVED_OPS:
        raise GmailAuthError("Unsupported Gmail operation.")
    service = build_service_from_token(access_token)
    try:
        return op(service, **kwargs)
    finally:
        try:
            service.close()
        except Exception:
            pass


def _build_credentials_and_service():
    """(creds, service, prev_refresh_enc, refresh_before) for the stored
    connection. Raises GmailAuthError when not connected."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    row = store.get_row()
    if not store.is_connected(row):
        raise GmailAuthError("Gmail is not connected. Connect it in Settings.")

    prev_refresh_enc = row.get("refresh_token_enc", "")
    refresh_before = decrypt_value(prev_refresh_enc)
    creds = Credentials(
        token=decrypt_value(row.get("access_token_enc", "")) or None,
        refresh_token=refresh_before,
        token_uri=oauth.TOKEN_ENDPOINT,
        client_id=row.get("client_id", ""),
        client_secret=decrypt_value(row.get("client_secret_enc", "")),
        scopes=row.get("scopes", "").split() if row.get("scopes") else None,
    )
    creds.expiry = _parse_expiry(row.get("token_expires_at"))
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return creds, service, prev_refresh_enc, refresh_before


def _persist_if_refreshed(creds, token_before, refresh_before, prev_refresh_enc) -> None:
    """If the SDK refreshed the access token during the call, persist it (and a
    rotated refresh token) under a CAS keyed by the ciphertext we started with.
    Never raises."""
    try:
        if creds.token and creds.token != token_before:
            rotated = creds.refresh_token if creds.refresh_token and creds.refresh_token != refresh_before else None
            store.update_access_token(creds.token, creds.expiry, prev_refresh_enc, refresh_token=rotated)
    except Exception as e:
        # The token still worked for this call; a failed persist just means one
        # extra refresh next time.
        logger.warning("gmail.client: failed to persist refreshed token: %s", e)


def call_gmail(op, **kwargs):
    """Execute an approved Gmail op against the connected account.

    Raises GmailAuthError when disconnected/expired; other Gmail/HTTP errors
    propagate to the executor's curated handler.
    """
    from google.auth.exceptions import RefreshError

    if op not in _APPROVED_OPS:
        # Unreachable via the shipped executors; a hard stop if a future caller
        # ever tries to run a non-allow-listed (e.g. send) operation.
        raise GmailAuthError("Unsupported Gmail operation.")

    creds, service, prev_refresh_enc, refresh_before = _build_credentials_and_service()
    token_before = creds.token
    refresh_failed = False
    try:
        return op(service, **kwargs)
    except RefreshError:
        refresh_failed = True
        store.mark_broken()
        raise GmailAuthError("Gmail connection expired — reconnect it in Settings.")
    finally:
        try:
            service.close()
        except Exception:
            pass
        if not refresh_failed:
            _persist_if_refreshed(creds, token_before, refresh_before, prev_refresh_enc)
