"""Hermetic tests for gmail.store — patches the imported pg_execute/pg_fetchone
seams on the module (they resolve core.postgres.get_connection internally, which
the raw-tuple fake_conn fixture can't satisfy). Encryption runs for real via the
autouse encryption_key fixture."""

from __future__ import annotations

import hashlib

import pytest

from core.encryption import encrypt_value
from gmail import store


class ExecRecorder:
    """Records (normalized_sql, params) and returns a configurable rowcount."""

    def __init__(self, rowcount=1):
        self.calls = []
        self.rowcount = rowcount

    def __call__(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.rowcount


class FetchRecorder:
    """Records (normalized_sql, params) for the pg_fetchone seam and returns a
    configurable row (None = no row matched)."""

    def __init__(self, row=None):
        self.calls = []
        self.row = row

    def __call__(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.row


def _row(**over):
    base = {
        "id": 1,
        "client_id": "",
        "client_secret_enc": "",
        "email": "",
        "access_token_enc": "",
        "refresh_token_enc": "",
        "token_expires_at": None,
        "scopes": "",
        "connection_status": "disconnected",
        "oauth_state_hash": "",
        "oauth_state_created_at": None,
        "connection_generation": 0,
    }
    base.update(over)
    return base


def _connected_row(**over):
    return _row(
        client_id="cid.apps.googleusercontent.com",
        client_secret_enc=encrypt_value("shh-secret"),
        refresh_token_enc=encrypt_value("refresh-token"),
        access_token_enc=encrypt_value("access-token"),
        email="user@example.com",
        scopes="https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose",
        connection_status="ok",
        **over,
    )


# ── save_app_credentials ──────────────────────────────────────────────────────

def test_save_app_credentials_encrypts_and_clears(monkeypatch):
    rec = FetchRecorder(row={"old_refresh_token_enc": "enc:v1:outgoing"})
    monkeypatch.setattr(store, "pg_fetchone", rec)
    # Returns the ciphertext it cleared, so the router revokes exactly that grant.
    assert store.save_app_credentials(" cid ", " shh ") == "enc:v1:outgoing"

    sql, params = rec.calls[0]
    assert params[0] == "cid"  # stripped client_id
    assert params[1].startswith("enc:v1:")  # encrypted client_secret
    # Replacing the app forces a fresh connect: tokens/status/state cleared.
    assert "access_token_enc = ''" in sql
    assert "refresh_token_enc = ''" in sql
    assert "connection_status = 'disconnected'" in sql
    assert "oauth_state_hash = ''" in sql
    # New identity -> generation bumped, and the old ciphertext is captured under
    # the same lock that clears it (one statement, no read-then-clear window).
    assert "connection_generation = gmail_connection.connection_generation + 1" in sql
    assert "SELECT refresh_token_enc FROM gmail_connection WHERE id = 1 FOR UPDATE" in sql
    assert "RETURNING old.refresh_token_enc" in sql


def test_clear_connection_bumps_generation_and_returns_old_ciphertext(monkeypatch):
    rec = FetchRecorder(row={"old_refresh_token_enc": "enc:v1:was-live"})
    monkeypatch.setattr(store, "pg_fetchone", rec)
    assert store.clear_connection() == "enc:v1:was-live"

    sql, _ = rec.calls[0]
    assert "connection_status = 'disconnected'" in sql
    assert "connection_generation = gmail_connection.connection_generation + 1" in sql
    assert "FOR UPDATE" in sql
    # App credentials survive a disconnect so reconnecting is one click.
    assert "client_id = %s" not in sql
    assert "client_secret_enc" not in sql


def test_clear_connection_no_row_returns_empty_string(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", FetchRecorder(row=None))
    assert store.clear_connection() == ""


# ── OAuth state ───────────────────────────────────────────────────────────────

def test_set_oauth_state_hash_stores_hash_not_raw(monkeypatch):
    rec = ExecRecorder()
    monkeypatch.setattr(store, "pg_execute", rec)
    store.set_oauth_state_hash("the-raw-state")
    sql, params = rec.calls[0]
    assert params[0] == hashlib.sha256(b"the-raw-state").hexdigest()
    assert "the-raw-state" not in str(params)


def test_claim_oauth_state_match_returns_generation_and_is_atomic(monkeypatch):
    rec = FetchRecorder(row={"connection_generation": 7})
    monkeypatch.setattr(store, "pg_fetchone", rec)
    # The claim hands back the generation it observed, in the SAME statement — the
    # callback carries it through the Google round-trips as its CAS key (#43).
    assert store.claim_oauth_state("good-state") == 7
    sql, params = rec.calls[0]
    # Single atomic compare-and-clear with TTL, keyed by the state's hash.
    assert "UPDATE gmail_connection" in sql
    assert "oauth_state_hash = ''" in sql
    assert "oauth_state_created_at >= now() - interval '10 minutes'" in sql
    assert "RETURNING connection_generation" in sql
    assert params[0] == hashlib.sha256(b"good-state").hexdigest()


def test_claim_oauth_state_generation_zero_is_a_real_claim(monkeypatch):
    """0 is a legitimate generation — callers must test `is None`, not truthiness."""
    monkeypatch.setattr(store, "pg_fetchone", FetchRecorder(row={"connection_generation": 0}))
    assert store.claim_oauth_state("good-state") == 0


def test_claim_oauth_state_mismatch_returns_none(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", FetchRecorder(row=None))
    assert store.claim_oauth_state("wrong") is None


def test_claim_oauth_state_empty_is_none_without_db(monkeypatch):
    rec = FetchRecorder(row=None)
    monkeypatch.setattr(store, "pg_fetchone", rec)
    assert store.claim_oauth_state("") is None
    assert rec.calls == []  # short-circuits, never queries


# ── save_tokens CAS (the OAuth callback race) ─────────────────────────────────

def test_save_tokens_cas_on_generation_and_bumps_it(monkeypatch):
    rec = ExecRecorder(rowcount=1)
    monkeypatch.setattr(store, "pg_execute", rec)
    assert store.save_tokens("at", "rt", None, "scope", "me@x.com", 4) is True

    sql, params = rec.calls[0]
    assert "WHERE id = 1 AND connection_generation = %s" in sql
    assert "connection_generation = connection_generation + 1" in sql
    assert params[-1] == 4  # CAS key is the generation captured at state-claim
    assert params[0].startswith("enc:v1:")  # access token encrypted
    assert params[1].startswith("enc:v1:")  # refresh token encrypted


def test_save_tokens_returns_false_when_generation_moved(monkeypatch):
    """The admin disconnected or replaced the app mid-handshake: the write is a
    no-op and the caller revokes the just-granted tokens instead of resurrecting a
    connection that was deliberately ended."""
    monkeypatch.setattr(store, "pg_execute", ExecRecorder(rowcount=0))
    assert store.save_tokens("at", "rt", None, "scope", "me@x.com", 4) is False


# ── is_connected truth table ──────────────────────────────────────────────────

def test_is_connected_true_when_status_ok_and_secrets_decrypt(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: _connected_row())
    assert store.is_connected() is True


@pytest.mark.parametrize("over", [
    {"connection_status": "disconnected"},
    {"connection_status": "broken"},
    {"refresh_token_enc": ""},
    {"client_id": ""},
    {"client_secret_enc": ""},
    {"client_secret_enc": "enc:v1:not-decryptable"},  # corrupt/rotated key -> "" -> False
    {"refresh_token_enc": "enc:v1:not-decryptable"},
])
def test_is_connected_false_cases(monkeypatch, over):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: _connected_row(**over))
    assert store.is_connected() is False


def test_get_row_and_is_connected_never_raise_on_db_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr(store, "pg_fetchone", boom)
    assert store.get_row() == {}
    assert store.is_connected() is False


# ── status_dict sanitization ──────────────────────────────────────────────────

def test_status_dict_leaks_no_secrets(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: _connected_row())
    d = store.status_dict()
    assert d["connected"] is True
    assert d["email"] == "user@example.com"
    assert d["client_id"] == "cid.apps.googleusercontent.com"
    assert d["client_secret_present"] is True
    assert "redirect_uri" in d and d["redirect_uri"].endswith("/api/gmail/oauth/callback")
    # No secret or ciphertext key/value anywhere in the payload.
    assert "client_secret" not in d
    assert "access_token" not in d
    assert "refresh_token" not in d
    for v in d.values():
        assert not (isinstance(v, str) and v.startswith("enc:v1:"))


# ── update_access_token (CAS) + mark_broken ───────────────────────────────────

def test_update_access_token_no_rotation_uses_cas(monkeypatch):
    rec = ExecRecorder(rowcount=1)
    monkeypatch.setattr(store, "pg_execute", rec)
    store.update_access_token("new-access", "2026-01-01T00:00:00+00:00", "enc:v1:prev-refresh")
    sql, params = rec.calls[0]
    assert "SET access_token_enc = %s" in sql
    assert "refresh_token_enc = %s" not in sql.split("WHERE")[0]  # not rotated in SET
    assert "WHERE id = 1 AND refresh_token_enc = %s" in sql  # compare-and-swap key
    assert params[0].startswith("enc:v1:")  # new access token encrypted
    assert params[-1] == "enc:v1:prev-refresh"  # CAS key is the prior ciphertext


def test_update_access_token_with_rotation_persists_new_refresh(monkeypatch):
    rec = ExecRecorder(rowcount=1)
    monkeypatch.setattr(store, "pg_execute", rec)
    store.update_access_token("new-access", None, "enc:v1:prev-refresh", refresh_token="rotated")
    sql, params = rec.calls[0]
    before_where = sql.split("WHERE")[0]
    assert "access_token_enc = %s" in before_where
    assert "refresh_token_enc = %s" in before_where  # rotated refresh is written
    assert "WHERE id = 1 AND refresh_token_enc = %s" in sql
    assert params[-1] == "enc:v1:prev-refresh"


def test_mark_broken_sets_status_and_never_raises(monkeypatch):
    rec = ExecRecorder()
    monkeypatch.setattr(store, "pg_execute", rec)
    store.mark_broken("enc:v1:prev-refresh")
    sql, params = rec.calls[0]
    assert "connection_status = 'broken'" in sql
    # CAS on the credential that actually failed, so a connection replaced (or a
    # token rotated by a concurrent call) meanwhile is not marked broken (#43).
    assert "WHERE id = 1 AND refresh_token_enc = %s" in sql
    assert params[0] == "enc:v1:prev-refresh"
    # 'broken' is a status change, not an identity change — must NOT bump.
    assert "connection_generation" not in sql

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "pg_execute", boom)
    store.mark_broken("enc:v1:prev-refresh")  # must not raise


def test_update_access_token_does_not_bump_generation(monkeypatch):
    """Refreshing a token keeps the same account. Bumping here would invalidate
    every pending draft on every hourly refresh."""
    rec = ExecRecorder(rowcount=1)
    monkeypatch.setattr(store, "pg_execute", rec)
    store.update_access_token("new-access", None, "enc:v1:prev-refresh")
    assert "connection_generation" not in rec.calls[0][0]


def test_set_oauth_state_hash_does_not_bump_generation(monkeypatch):
    """Starting an OAuth flow changes nothing about the connection that is live."""
    rec = ExecRecorder()
    monkeypatch.setattr(store, "pg_execute", rec)
    store.set_oauth_state_hash("s")
    assert "connection_generation" not in rec.calls[0][0]
