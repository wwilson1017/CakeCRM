"""Gmail service factory + execution seam (issue #8).

Builds a google-auth Credentials from the stored tokens and lets the SDK
auto-refresh (proactively on expiry, retry-once on 401) — no hand-rolled refresh
timing. call_gmail() is the single seam every tool executor uses; it runtime
allow-lists the operation (defense-in-depth: only the four read/draft ops can ever
run — no send op exists to pass), persists any refreshed token under a
compare-and-swap, and always closes the transport.

THE TRANSPORT IS OURS (issue #64). build() is handed ``http=`` rather than
``credentials=`` (the SDK treats the two as mutually exclusive) so both the
per-socket stall timeout and the per-call request budget are values we chose —
see _build_transport. A timeout is RETRYABLE by contract: it raises
GmailTimeoutError, which can never reach mark_broken and never sets
needs_reconnect.

All google/googleapiclient imports are lazy (inside functions) so the module
imports with no SDK / no DATABASE_URL.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from core.encryption import decrypt_value
from gmail import oauth, ops, store

logger = logging.getLogger(__name__)

# Transport bounds we own (issue #64). Before this, build(credentials=...) handed
# transport construction to the SDK, whose build_http() applies
# socket.getdefaulttimeout() if set and otherwise DEFAULT_HTTP_TIMEOUT_SEC = 60 —
# so the effective timeout was 60s, undocumented, and silently redefinable
# process-wide by any dependency that calls socket.setdefaulttimeout().
_HTTP_TIMEOUT_SECONDS = 20   # per SOCKET OP (connect / each recv), NOT total request
                             # duration: a silent-peer detector, so a large response
                             # that keeps flowing is never cut off.
_CALL_BUDGET_SECONDS = 90    # default budget for ONE call_gmail invocation. The real
                             # defect #64 fixes is the aggregate: an op fans out
                             # sequentially (gmail_search(25) = 26 requests), and
                             # before this nothing bounded the sum.

_TIMEOUT_MESSAGE = (
    "Gmail took too long to respond and the request was stopped. Try again — if it "
    "keeps happening, narrow the request (fewer results or a more specific query)."
)


class GmailAuthError(Exception):
    """Raised when Gmail is not connected or the connection is no longer valid.
    The message is user-facing."""


class GmailTimeoutError(Exception):
    """A Gmail call ran out of time — one stalled request, or the per-call budget.

    RETRYABLE, and never a broken connection: call_gmail must not mark_broken on it
    and the executors must not return needs_reconnect for it. Deliberately NOT a
    subclass of TimeoutError/OSError — googleapiclient's _retry_request treats socket
    errors specially, and this must stay invisible to it. The message is user-facing."""


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


def _resolve_deadline(budget_seconds: float | None) -> float:
    """Absolute monotonic deadline for ONE call.

    Callers resolve this BEFORE any setup work, so the store read, decryption and
    build() all sit inside the budget. That shared epoch is what lets gmail_scan
    compare its budget against its own wall-clock join deadline (see
    gmail_scan.service._SCAN_CALL_BUDGET) — a budget started after setup would be
    measuring from a later, unknown instant.

    Rejects a non-positive or non-finite budget rather than accepting it: NaN in
    particular would disable the gate silently, since every comparison against it
    is False."""
    budget = _CALL_BUDGET_SECONDS if budget_seconds is None else float(budget_seconds)
    if not budget > 0 or budget == float("inf"):
        raise ValueError(f"budget_seconds must be finite and positive, got {budget_seconds!r}")
    return time.monotonic() + budget


def _build_transport(creds, deadline: float):
    """AuthorizedHttp over an httplib2.Http that refuses to START a request past
    `deadline`. Passed to build(http=...) INSTEAD of credentials= — the SDK treats
    the two as mutually exclusive and raises if given both.

    The budget gate is the INNER http, not a wrapper around AuthorizedHttp, and that
    placement is load-bearing: AuthorizedHttp builds its refresh transport as
    Request(self.http), so a token refresh is gated too — an outer wrapper would let
    the refresh round-trip past the budget entirely. It also means build() receives a
    genuine AuthorizedHttp, so the SDK's own get_credentials_from_http (universe-domain
    resolution) and every property proxy keep working with no delegation code.

    What it bounds, stated precisely: every top-level SDK request and every OAuth
    refresh. It gates request STARTS, so it cannot interrupt one already in flight —
    the ceiling is the budget plus one request, itself bounded at
    _HTTP_TIMEOUT_SECONDS per socket operation. A peer that trickles bytes forever
    would defeat that, which is out of threat model here (the peer is Google's API);
    gmail_scan keeps its own job-layer deadline for what this cannot bound."""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    class _BudgetHttp(httplib2.Http):
        def request(self, *args, **kwargs):
            if time.monotonic() >= deadline:
                raise GmailTimeoutError(_TIMEOUT_MESSAGE)
            return super().request(*args, **kwargs)

    http = _BudgetHttp(timeout=_HTTP_TIMEOUT_SECONDS)
    # Parity with the SDK's build_http(): Google uses 308 for resumable uploads, not
    # redirects. Our ops never upload, but the transport we replaced carried this and
    # dropping it would be an unrelated behavior change smuggled in with the timeout.
    http.redirect_codes = http.redirect_codes - {308}
    return AuthorizedHttp(creds, http=http)


def build_service_from_token(access_token: str, deadline: float | None = None):
    """Build a Gmail service from a bare access token (used by the OAuth callback
    to fetch the profile right after the exchange, before a row exists)."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if deadline is None:
        deadline = _resolve_deadline(None)
    creds = Credentials(token=access_token)
    return build("gmail", "v1", http=_build_transport(creds, deadline), cache_discovery=False)


def call_with_token(access_token: str, op, **kwargs):
    """Run an approved op against a service built from a bare access token (the
    OAuth callback path, before a stored connection exists). Enforces the SAME
    _APPROVED_OPS allow-list as call_gmail — so this second service-building path
    can't invoke any Gmail method outside the read/draft set — and always closes
    the transport.

    Deliberately does NOT translate a per-request TimeoutError into GmailTimeoutError
    the way call_gmail does: its only caller is the OAuth callback's broad handler,
    which treats every failure the same way, so translating would change nothing."""
    if op not in _APPROVED_OPS:
        raise GmailAuthError("Unsupported Gmail operation.")
    service = build_service_from_token(access_token, _resolve_deadline(None))
    try:
        return op(service, **kwargs)
    finally:
        try:
            service.close()
        except Exception:
            pass


def _build_credentials_and_service(deadline: float | None = None):
    """(creds, service, prev_refresh_enc, refresh_before) for the stored
    connection. Raises GmailAuthError when not connected."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if deadline is None:
        deadline = _resolve_deadline(None)

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
    service = build("gmail", "v1", http=_build_transport(creds, deadline), cache_discovery=False)
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


def call_gmail(op, *, budget_seconds: float | None = None, **kwargs):
    """Execute an approved Gmail op against the connected account.

    Raises GmailAuthError when disconnected/expired, GmailTimeoutError when the call
    outran its budget or a single request stalled; other Gmail/HTTP errors propagate
    to the executor's curated handler.

    `budget_seconds` is keyword-only so it can never collide with an op's own kwargs
    (no op declares that name, and this reserves it). Default: _CALL_BUDGET_SECONDS.
    """
    from google.auth.exceptions import RefreshError

    if op not in _APPROVED_OPS:
        # Unreachable via the shipped executors; a hard stop if a future caller
        # ever tries to run a non-allow-listed (e.g. send) operation.
        raise GmailAuthError("Unsupported Gmail operation.")

    # Resolved FIRST so this call's own setup (store read, decrypt, build) is inside
    # the budget — see _resolve_deadline on why the epoch matters to gmail_scan.
    deadline = _resolve_deadline(budget_seconds)

    creds, service, prev_refresh_enc, refresh_before = _build_credentials_and_service(deadline)
    token_before = creds.token
    refresh_failed = False
    try:
        return op(service, **kwargs)
    except RefreshError:
        refresh_failed = True
        # CAS on the ciphertext this call refreshed under: a connection replaced (or
        # a token rotated by a concurrent call) meanwhile must not be marked broken.
        store.mark_broken(prev_refresh_enc)
        raise GmailAuthError("Gmail connection expired — reconnect it in Settings.")
    except TimeoutError as e:
        # One stalled socket op (socket.timeout IS TimeoutError on 3.10+), raised by
        # the transport — the ops layer never raises it. Disjoint from RefreshError,
        # so a timeout can never reach mark_broken: a stalled request says nothing
        # about whether the credential is still good.
        raise GmailTimeoutError(_TIMEOUT_MESSAGE) from e
    finally:
        try:
            service.close()
        except Exception:
            pass
        if not refresh_failed:
            # Still runs after a timeout, deliberately: it writes only if the token
            # actually rotated (a refresh that succeeded before the call stalled),
            # and it swallows its own failures.
            _persist_if_refreshed(creds, token_before, refresh_before, prev_refresh_enc)
