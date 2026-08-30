"""Gmail transport bounds (issue #64) — the per-socket-op stall timeout and the
per-call request budget that gmail/client.py now OWNS.

Issue #64 was deferred on the premise that this "cannot be verified in the automation
environment" without a live mailbox. That premise is wrong, and this file is the proof:

  * Gmail uses STATIC discovery (the doc ships inside google-api-python-client), so
    googleapiclient's real build() makes no network call. Every test here drives the
    REAL SDK and the REAL ops — asserting against a monkeypatched build() would only
    prove we passed a kwarg, never that the SDK honoured it.
  * The fake wire is installed BELOW the gate, by overriding httplib2's internal
    _conn_request. _BudgetHttp.request is the code under test and is never replaced.
  * A loopback black-hole socket proves a real read-hang is bounded for real.

Hermetic: no Postgres, no Google, no outbound network. The one socket binds 127.0.0.1
and lives for milliseconds, so these stay in the default (non-integration) suite.
"""

import json
import socket
import threading
import time

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient import _auth
from googleapiclient.discovery import build

from gmail import client, ops, tools
from gmail.client import GmailAuthError, GmailTimeoutError

SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose"


# ── fake wire ─────────────────────────────────────────────────────────────────
#
# Replaces httplib2's transport internals only. Everything above it -- _BudgetHttp's
# deadline check, AuthorizedHttp's credential/refresh handling, googleapiclient's
# request assembly and the gmail ops themselves -- is the real code.

def _wire(service, replies):
    """Install a fake wire under the service's transport. Returns the call log."""
    http = service._http.http          # the _BudgetHttp instance build() was handed
    calls = []

    def fake_conn_request(conn, uri, method, body, headers):
        calls.append(uri)
        status, payload = replies(uri, body)
        return (
            httplib2.Response({"status": str(status), "content-type": "application/json"}),
            json.dumps(payload).encode(),
        )

    http._conn_request = fake_conn_request
    return calls


def _list_replies(count):
    """Answer a messages.list plus one messages.get per returned id."""
    def replies(uri, body):
        if "/messages/" in uri:
            return 200, {
                "id": "m", "threadId": "t", "snippet": "x",
                "payload": {"headers": [
                    {"name": "From", "value": "a@b.com"},
                    {"name": "Subject", "value": "s"},
                    {"name": "Date", "value": "Mon, 1 Jan 2024 00:00:00 +0000"},
                ]},
            }
        return 200, {"messages": [{"id": f"m{i}"} for i in range(count)]}
    return replies


def _connected_row(monkeypatch):
    """Make _build_credentials_and_service see a connected account."""
    row = {
        "access_token_enc": "at", "refresh_token_enc": "rt",
        "client_id": "cid", "client_secret_enc": "csec",
        "scopes": SCOPES, "token_expires_at": None, "email": "me@example.com",
    }
    monkeypatch.setattr(client.store, "get_row", lambda: row)
    monkeypatch.setattr(client.store, "is_connected", lambda r=None: True)
    monkeypatch.setattr(client, "decrypt_value", lambda v: v)
    return row


# ── the transport build() actually receives ───────────────────────────────────

def test_service_is_built_on_our_own_bounded_transport(monkeypatch):
    """The REAL build() + REAL Credentials: prove the SDK accepted our transport and
    that the values on it are ours, not googleapiclient's implicit 60s default."""
    _connected_row(monkeypatch)

    creds, service, _prev, _refresh = client._build_credentials_and_service()

    transport = service._http
    assert transport.__class__.__name__ == "AuthorizedHttp"
    # Our timeout, not build_http()'s DEFAULT_HTTP_TIMEOUT_SEC (60) and not whatever
    # socket.setdefaulttimeout() happens to be set to process-wide.
    assert transport.http.timeout == client._HTTP_TIMEOUT_SECONDS
    assert transport.http.timeout != 60
    # The refresh round-trip rides the SAME budget-gated http (this is what makes a
    # hung token refresh bounded, and an outer wrapper would not achieve it).
    assert transport._request.http is transport.http
    # Universe-domain resolution still finds the credentials through the transport.
    assert _auth.get_credentials_from_http(transport) is creds
    # build_http() parity: 308 is a resumable-upload signal, not a redirect.
    assert 308 not in transport.redirect_codes
    service.close()


def test_build_parity_with_the_credentials_kwarg_it_replaced(monkeypatch):
    """Passing http= skips the credential preprocessing build(credentials=...) does.
    For an authorized-user credential every piece of that is a no-op -- pin it as an
    invariant rather than an argument, since a service-account credential would NOT
    be equivalent and this is what makes the substitution safe."""
    _connected_row(monkeypatch)
    _creds, ours, _p, _r = client._build_credentials_and_service()

    scoped = SCOPES.split()
    theirs = build(
        "gmail", "v1",
        credentials=Credentials(
            token="at", refresh_token="rt", token_uri="https://oauth2.googleapis.com/token",
            client_id="cid", client_secret="csec", scopes=scoped,
        ),
        cache_discovery=False,
    )
    assert ours._baseUrl == theirs._baseUrl
    assert getattr(ours, "_universe_domain", None) == getattr(theirs, "_universe_domain", None)
    assert sorted(m for m in dir(ours) if not m.startswith("_")) == \
           sorted(m for m in dir(theirs) if not m.startswith("_"))
    # requires_scopes is False for an authorized user, so with_scopes_if_required --
    # the one preprocessing step that could have mattered -- is the identity.
    assert _auth.get_credentials_from_http(ours._http).requires_scopes is False
    ours.close()
    theirs.close()


def test_every_request_an_op_makes_passes_the_gate(monkeypatch):
    """The gate must see EVERY wire call, not just the first. This also closes the
    bypass route where an op hands execute() its own http= -- such a request would
    never reach our transport and the counts would diverge."""
    _connected_row(monkeypatch)
    _creds, service, _p, _r = client._build_credentials_and_service()

    gated = []
    real_request = service._http.http.request
    monkeypatch.setattr(
        service._http.http, "request",
        lambda *a, **k: (gated.append(a[0]), real_request(*a, **k))[1],
    )
    calls = _wire(service, _list_replies(3))

    result = ops.list_messages_op(service, query="x", max_results=3)

    assert len(result) == 3
    assert len(calls) == 4          # 1 list + 1 get per message -- the #64 fan-out
    # Every wire call went through the deadline check. The gate sees absolute URLs and
    # the wire sees paths, so pair them rather than comparing the lists directly.
    assert len(gated) == len(calls)
    assert all(url.endswith(path) for url, path in zip(gated, calls))
    service.close()


# ── the aggregate budget: the defect #64 actually fixes ───────────────────────

def test_budget_aborts_a_multi_request_op_partway(monkeypatch):
    """A per-request timeout does not bound an op that fans out sequentially. Drive
    the REAL list op against the real SDK and expire the budget mid-flight; it must
    stop starting requests. Deterministic -- the clock is moved, never slept on."""
    _connected_row(monkeypatch)
    _creds, service, _p, _r = client._build_credentials_and_service()

    calls = _wire(service, _list_replies(5))
    inner = service._http.http._conn_request

    def expire_after_two(*a, **k):
        if len(calls) >= 2:
            monkeypatch.setattr(client.time, "monotonic", lambda: float("inf"))
        return inner(*a, **k)

    service._http.http._conn_request = expire_after_two

    with pytest.raises(GmailTimeoutError):
        ops.list_messages_op(service, query="x", max_results=5)

    assert len(calls) == 3          # stopped early; without the budget it would be 6
    service.close()


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf")])
def test_unusable_budget_is_refused_rather_than_disabling_the_gate(budget):
    """NaN is the dangerous one: every comparison against it is False, so a NaN
    deadline would silently switch the gate off while looking configured."""
    with pytest.raises(ValueError):
        client._resolve_deadline(budget)


def test_deadline_covers_setup_not_only_http(monkeypatch):
    """call_gmail resolves the deadline BEFORE building anything, so slow setup eats
    the budget instead of extending it past the caller's own wall clock -- which is
    what lets gmail_scan compare its budget against its join deadline."""
    _connected_row(monkeypatch)
    seen = []
    real = client._resolve_deadline
    monkeypatch.setattr(client, "_resolve_deadline", lambda b: seen.append(("resolved", b)) or real(b))
    real_build = client._build_transport
    monkeypatch.setattr(client, "_build_transport", lambda c, d: seen.append(("built", d)) or real_build(c, d))

    def op(service):
        return {"ok": True}

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))
    client.call_gmail(op, budget_seconds=30)

    assert [s[0] for s in seen] == ["resolved", "built"]


# ── a real socket hang really is bounded ──────────────────────────────────────

def test_real_socket_hang_is_bounded(monkeypatch):
    """The failure class the issue is about: a peer that accepts the connection and
    then never replies. Nothing is mocked below the transport here -- a genuine TCP
    socket proves httplib2's timeout bounds a READ, not merely a connect."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    held = []
    threading.Thread(target=lambda: held.append(srv.accept()), daemon=True).start()

    monkeypatch.setattr(client, "_HTTP_TIMEOUT_SECONDS", 0.25)
    transport = client._build_transport(Credentials(token="t"), time.monotonic() + 60)

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError) as caught:
            transport.request(f"http://127.0.0.1:{port}/", "GET")
        elapsed = time.monotonic() - started
        # Bounded at all is the claim; the generous ceiling keeps this off CI's
        # flakiness budget while still failing loudly against an unbounded read.
        assert elapsed < 10
        # It arrives as a builtin TimeoutError -- NOT a RefreshError, which is the
        # only class that reaches store.mark_broken.
        assert not isinstance(caught.value, RefreshError)
    finally:
        transport.close()
        for conn, _addr in held:
            conn.close()
        srv.close()


# ── a timeout is never a broken connection ────────────────────────────────────

def test_timeout_during_op_never_marks_the_connection_broken(monkeypatch):
    _connected_row(monkeypatch)
    marked = []
    monkeypatch.setattr(client.store, "mark_broken", lambda enc: marked.append(enc))
    monkeypatch.setattr(client.store, "update_access_token", lambda *a, **k: None)

    def op(service):
        raise TimeoutError("timed out")

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))

    with pytest.raises(GmailTimeoutError):
        client.call_gmail(op)
    assert marked == []


def test_timeout_during_token_refresh_never_marks_the_connection_broken(monkeypatch):
    """The path that decides whether mark_broken is reachable from a timeout: the
    credential refresh, not the API call. google-auth wraps only HttpLib2Error into
    TransportError, so a stalled socket surfaces raw and cannot become a RefreshError
    -- proven here through the real SDK refresh machinery rather than asserted."""
    _connected_row(monkeypatch)
    marked = []
    monkeypatch.setattr(client.store, "mark_broken", lambda enc: marked.append(enc))
    monkeypatch.setattr(client.store, "update_access_token", lambda *a, **k: None)

    def op(service):
        # Force a refresh: an expired credential refreshes before the request goes out.
        service._http.credentials.token = None
        service._http.http._conn_request = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out"))
        return ops.get_profile_op(service)

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))

    with pytest.raises(GmailTimeoutError):
        client.call_gmail(op)
    assert marked == []


def test_budget_exhaustion_during_refresh_never_marks_the_connection_broken(monkeypatch):
    """Same path, other trigger: the budget expiring while the refresh is being made."""
    _connected_row(monkeypatch)
    marked = []
    monkeypatch.setattr(client.store, "mark_broken", lambda enc: marked.append(enc))
    monkeypatch.setattr(client.store, "update_access_token", lambda *a, **k: None)

    def op(service):
        service._http.credentials.token = None
        monkeypatch.setattr(client.time, "monotonic", lambda: float("inf"))
        return ops.get_profile_op(service)

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))

    with pytest.raises(GmailTimeoutError):
        client.call_gmail(op)
    assert marked == []


def test_timeout_error_is_outside_the_socket_error_hierarchy():
    """googleapiclient's _retry_request special-cases socket errors. Keeping
    GmailTimeoutError out of that hierarchy is what stops a budget refusal from being
    retried as a transient network blip -- which would multiply the very wall clock
    the budget exists to bound."""
    err = GmailTimeoutError("x")
    assert not isinstance(err, (OSError, TimeoutError, GmailAuthError))


@pytest.mark.parametrize(
    "executor, kwargs",
    [
        (tools.gmail_search, {"query": "q"}),
        (tools.gmail_read_thread, {"thread_id": "t"}),
        (tools.gmail_create_draft, {"to": "a@b.com", "subject": "s", "body": "b"}),
    ],
)
def test_executors_report_a_timeout_as_retryable_not_reconnect(monkeypatch, executor, kwargs):
    """A timeout must never tell the user to reconnect a connection that is fine."""
    def boom(*a, **k):
        raise GmailTimeoutError(client._TIMEOUT_MESSAGE)

    monkeypatch.setattr(tools.client, "call_gmail", boom)
    out = executor(**kwargs)
    assert "error" in out
    assert "needs_reconnect" not in out
    assert "too long" in out["error"]


# ── the SDK refresh is still observed through our transport ───────────────────

def test_sdk_refresh_through_our_transport_is_still_persisted(monkeypatch):
    """The issue's explicit worry: refreshing now happens through AuthorizedHttp, so
    does _persist_if_refreshed still see it? Drive the real 401 -> refresh -> retry
    flow and check both the rotated access token and the rotated refresh token."""
    _connected_row(monkeypatch)
    persisted = []
    monkeypatch.setattr(client.store, "update_access_token",
                        lambda tok, exp, prev, refresh_token=None: persisted.append((tok, refresh_token)))
    monkeypatch.setattr(client.store, "mark_broken", lambda enc: pytest.fail("must not mark broken"))

    def op(service):
        seen = []

        def replies(conn, uri, method, body, headers):
            seen.append(uri)
            if "oauth2" in uri or "token" in uri:
                payload = {"access_token": "NEW", "refresh_token": "ROTATED",
                           "expires_in": 3600, "token_type": "Bearer"}
            elif len(seen) == 1:
                return httplib2.Response({"status": "401"}), b"{}"
            else:
                payload = {"emailAddress": "me@example.com"}
            return (
                httplib2.Response({"status": "200", "content-type": "application/json"}),
                json.dumps(payload).encode(),
            )

        service._http.http._conn_request = replies
        return ops.get_profile_op(service)

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))
    result = client.call_gmail(op)

    assert result["email"] == "me@example.com"
    assert persisted == [("NEW", "ROTATED")]
