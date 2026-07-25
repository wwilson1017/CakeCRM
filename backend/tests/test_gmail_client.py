"""Hermetic tests for gmail.client — the execution seam. Covers expiry parsing,
refreshed-token persistence (CAS), RefreshError handling, the runtime op
allow-list, and transport cleanup. The google SDK is not exercised directly;
_build_credentials_and_service is stubbed."""

from __future__ import annotations

from datetime import datetime

import pytest

from gmail import client
from gmail.client import GmailAuthError

# ── _parse_expiry ─────────────────────────────────────────────────────────────

def test_parse_expiry_none():
    assert client._parse_expiry(None) is None
    assert client._parse_expiry("") is None


def test_parse_expiry_tz_aware_iso_to_naive_utc():
    # 12:00:00+02:00 -> 10:00:00 naive UTC (google-auth treats expiry as naive UTC).
    out = client._parse_expiry("2026-07-24T12:00:00+02:00")
    assert out == datetime(2026, 7, 24, 10, 0, 0)
    assert out.tzinfo is None


def test_parse_expiry_bad_value():
    assert client._parse_expiry("not-a-date") is None


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeCreds:
    def __init__(self, token, refresh_token, expiry):
        self.token = token
        self.refresh_token = refresh_token
        self.expiry = expiry


class _FakeService:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


# ── call_gmail: allow-list ────────────────────────────────────────────────────

def test_call_gmail_rejects_non_allowlisted_op():
    def rogue(service):  # not in _APPROVED_OPS
        return "should never run"

    with pytest.raises(GmailAuthError):
        client.call_gmail(rogue)


# ── call_gmail: happy path, no refresh ────────────────────────────────────────

def test_call_gmail_runs_op_closes_service_no_persist(monkeypatch):
    creds = _FakeCreds(token="tok", refresh_token="rt", expiry="EXP")
    svc = _FakeService()
    persisted = []

    def op(service):
        return {"result": "ok"}

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))
    monkeypatch.setattr(client, "_build_credentials_and_service", lambda: (creds, svc, "enc-prev", "rt"))
    monkeypatch.setattr(client.store, "update_access_token", lambda *a, **k: persisted.append((a, k)))

    result = client.call_gmail(op)
    assert result == {"result": "ok"}
    assert svc.closed is True
    assert persisted == []  # token unchanged -> nothing persisted


# ── call_gmail: token refreshed mid-call -> persisted under CAS ───────────────

def test_call_gmail_persists_refreshed_token(monkeypatch):
    creds = _FakeCreds(token="old", refresh_token="rt", expiry="EXP")
    svc = _FakeService()
    persisted = []

    def op(service):
        creds.token = "new"  # simulate SDK proactive refresh during the call
        return "done"

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))
    monkeypatch.setattr(client, "_build_credentials_and_service", lambda: (creds, svc, "enc-prev", "rt"))
    monkeypatch.setattr(client.store, "update_access_token", lambda *a, **k: persisted.append((a, k)))

    client.call_gmail(op)
    assert len(persisted) == 1
    args, kwargs = persisted[0]
    assert args == ("new", "EXP", "enc-prev")  # (new access token, expiry, CAS key)
    assert kwargs == {"refresh_token": None}  # refresh token unchanged


def test_call_gmail_persists_rotated_refresh_token(monkeypatch):
    creds = _FakeCreds(token="old", refresh_token="rt-new", expiry="EXP")
    svc = _FakeService()
    persisted = []

    def op(service):
        creds.token = "new"
        return "done"

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))
    # refresh_before = "rt-old"; creds.refresh_token = "rt-new" -> rotation persisted.
    monkeypatch.setattr(client, "_build_credentials_and_service", lambda: (creds, svc, "enc-prev", "rt-old"))
    monkeypatch.setattr(client.store, "update_access_token", lambda *a, **k: persisted.append((a, k)))

    client.call_gmail(op)
    assert persisted[0][1] == {"refresh_token": "rt-new"}


# ── call_gmail: RefreshError -> mark_broken + GmailAuthError ──────────────────

def test_call_gmail_refresh_error_marks_broken(monkeypatch):
    from google.auth.exceptions import RefreshError

    creds = _FakeCreds(token="old", refresh_token="rt", expiry="EXP")
    svc = _FakeService()
    marked = []
    persisted = []

    def op(service):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(client, "_APPROVED_OPS", frozenset({op}))
    monkeypatch.setattr(client, "_build_credentials_and_service", lambda: (creds, svc, "enc-prev", "rt"))
    monkeypatch.setattr(client.store, "mark_broken", lambda: marked.append(True))
    monkeypatch.setattr(client.store, "update_access_token", lambda *a, **k: persisted.append((a, k)))

    with pytest.raises(GmailAuthError):
        client.call_gmail(op)
    assert marked == [True]
    assert svc.closed is True  # transport still closed
    assert persisted == []  # no persist on refresh failure


# ── _build_credentials_and_service: disconnected -> GmailAuthError ────────────

def test_build_service_raises_when_disconnected(monkeypatch):
    monkeypatch.setattr(client.store, "get_row", lambda: {})
    monkeypatch.setattr(client.store, "is_connected", lambda row=None: False)
    with pytest.raises(GmailAuthError):
        client._build_credentials_and_service()
