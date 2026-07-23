"""Postgres-backed CredentialStore — encrypt/store/reload round-trip, sanitized
serialization, usable-credential gating, and default-model resolution outside the
write transaction. pg helpers mocked; encryption runs for real."""

import json

from core.encryption import encrypt_value
from providers import credentials as cred_mod
from providers.credentials import CredentialStore


def _store(monkeypatch, fake_conn, *, settings_row=None, provider_rows=None):
    conn = fake_conn(
        monkeypatch, cred_mod,
        fetchone_results=[settings_row],
        fetchall_results=[provider_rows or []],
    )
    return CredentialStore(), conn


def test_load_decrypts_key(monkeypatch, fake_conn):
    enc = encrypt_value("test-key-1234")
    store, _ = _store(
        monkeypatch, fake_conn,
        settings_row=("anthropic", "claude-opus-4-8"),
        provider_rows=[("anthropic", "api_key", enc, "")],
    )
    assert store.get_api_key("anthropic") == "test-key-1234"
    assert store.data["active_provider"] == "anthropic"
    assert store.data["active_model"] == "claude-opus-4-8"


def test_to_dict_never_leaks_raw_key(monkeypatch, fake_conn):
    enc = encrypt_value("test-secret-9999")
    store, _ = _store(
        monkeypatch, fake_conn,
        settings_row=("anthropic", "m"),
        provider_rows=[("anthropic", "api_key", enc, "")],
    )
    d = store.to_dict()
    assert d["profiles"]["anthropic"]["configured"] is True
    assert d["profiles"]["anthropic"]["key_preview"] == "...9999"
    assert "test-secret-9999" not in json.dumps(d)


def test_is_configured_false_when_empty(monkeypatch, fake_conn):
    store, _ = _store(monkeypatch, fake_conn, settings_row=None, provider_rows=[])
    assert store.is_configured() is False
    assert store.configured_providers() == []


def test_usable_gating(monkeypatch, fake_conn):
    enc = encrypt_value("k")
    store, _ = _store(
        monkeypatch, fake_conn,
        settings_row=("anthropic", "m"),
        provider_rows=[
            ("anthropic", "api_key", enc, ""),
            ("ollama", "ollama_local", "", "http://localhost:11434"),
        ],
    )
    assert store.is_provider_usable("anthropic") is True
    assert store.is_provider_usable("ollama") is True
    assert store.is_provider_usable("openai") is False
    assert set(store.configured_providers()) == {"anthropic", "ollama"}


def test_empty_key_row_is_not_usable(monkeypatch, fake_conn):
    store, _ = _store(
        monkeypatch, fake_conn,
        settings_row=("anthropic", "m"),
        provider_rows=[("anthropic", "api_key", "", "")],  # empty enc -> decrypt "" -> unusable
    )
    assert store.is_provider_usable("anthropic") is False
    assert store.get_api_key("anthropic") is None
    assert store.is_configured() is False


def test_empty_ollama_url_is_not_usable(monkeypatch, fake_conn):
    store, _ = _store(
        monkeypatch, fake_conn,
        settings_row=("ollama", ""),
        provider_rows=[("ollama", "ollama_local", "", "")],  # empty base_url -> unusable
    )
    assert store.is_provider_usable("ollama") is False


def test_load_db_error_returns_empty_shape(monkeypatch):
    def boom():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(cred_mod, "get_connection", lambda: boom())
    store = CredentialStore()
    assert store.data == {"active_provider": "", "active_model": "", "profiles": {}}


def test_set_api_key_encrypts_never_plaintext(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, cred_mod, fetchone_results=[None], fetchall_results=[[]])
    monkeypatch.setattr(cred_mod, "_resolved_default_model", lambda p: "claude-opus-4-8")
    store = CredentialStore()
    store.set_api_key("anthropic", "test-plainkey")
    provider_writes = [(s, p) for (s, p) in conn.executed if "ai_providers" in s]
    assert provider_writes, "expected an ai_providers UPSERT"
    _, params = provider_writes[-1]
    provider, enc = params
    assert provider == "anthropic"
    assert enc.startswith("enc:v1:")            # stored value is encrypted
    assert "test-plainkey" not in enc             # never plaintext
    # a settings UPSERT also ran, and in-memory state reflects the plaintext
    assert any("ai_settings" in s for s, _ in conn.executed)
    assert store.get_api_key("anthropic") == "test-plainkey"


def test_remove_provider_clears_active_conditionally(monkeypatch, fake_conn):
    enc = encrypt_value("k")
    store, conn = _store(
        monkeypatch, fake_conn,
        settings_row=("anthropic", "m"),
        provider_rows=[("anthropic", "api_key", enc, "")],
    )
    store.remove_provider("anthropic")
    delete = [(s, p) for (s, p) in conn.executed if s.startswith("DELETE FROM ai_providers")]
    assert delete and delete[-1][1] == ("anthropic",)
    # conditional active-clear carries the provider as a guard param
    clear = [(s, p) for (s, p) in conn.executed if "active_provider = ''" in s]
    assert clear and clear[-1][1] == ("anthropic",)
    assert store.data["active_provider"] == ""


def test_set_active_returns_persisted_model(monkeypatch, fake_conn):
    fake_conn(monkeypatch, cred_mod, fetchone_results=[None], fetchall_results=[[]])
    monkeypatch.setattr(cred_mod, "_resolved_default_model", lambda p: "claude-opus-4-8")
    store = CredentialStore()
    # empty/"default" model resolves to the provider default
    persisted = store.set_active("anthropic", "")
    assert persisted == "claude-opus-4-8"
    assert store.data["active_model"] == "claude-opus-4-8"
