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
    rec = ExecRecorder()
    monkeypatch.setattr(store, "pg_execute", rec)
    store.save_app_credentials(" cid ", " shh ")

    sql, params = rec.calls[0]
    assert params[0] == "cid"  # stripped client_id
    assert params[1].startswith("enc:v1:")  # encrypted client_secret
    # Replacing the app forces a fresh connect: tokens/status/state cleared.
    assert "access_token_enc = ''" in sql
    assert "refresh_token_enc = ''" in sql
    assert "connection_status = 'disconnected'" in sql
    assert "oauth_state_hash = ''" in sql


# ── OAuth state ───────────────────────────────────────────────────────────────

def test_set_oauth_state_hash_stores_hash_not_raw(monkeypatch):
    rec = ExecRecorder()
    monkeypatch.setattr(store, "pg_execute", rec)
    store.set_oauth_state_hash("the-raw-state")
    sql, params = rec.calls[0]
    assert params[0] == hashlib.sha256(b"the-raw-state").hexdigest()
    assert "the-raw-state" not in str(params)


def test_claim_oauth_state_match_true_and_atomic(monkeypatch):
    rec = ExecRecorder(rowcount=1)
    monkeypatch.setattr(store, "pg_execute", rec)
    assert store.claim_oauth_state("good-state") is True
    sql, params = rec.calls[0]
    # Single atomic compare-and-clear with TTL, keyed by the state's hash.
    assert "UPDATE gmail_connection" in sql
    assert "oauth_state_hash = ''" in sql
    assert "oauth_state_created_at >= now() - interval '10 minutes'" in sql
    assert params[0] == hashlib.sha256(b"good-state").hexdigest()


def test_claim_oauth_state_mismatch_false(monkeypatch):
    rec = ExecRecorder(rowcount=0)
    monkeypatch.setattr(store, "pg_execute", rec)
    assert store.claim_oauth_state("wrong") is False


def test_claim_oauth_state_empty_is_false_without_db(monkeypatch):
    rec = ExecRecorder(rowcount=0)
    monkeypatch.setattr(store, "pg_execute", rec)
    assert store.claim_oauth_state("") is False
    assert rec.calls == []  # short-circuits, never queries


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
    store.mark_broken()
    assert "connection_status = 'broken'" in rec.calls[0][0]

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "pg_execute", boom)
    store.mark_broken()  # must not raise
