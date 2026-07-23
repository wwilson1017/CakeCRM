"""Router contract — built on a minimal FastAPI app (no lifespan/DATABASE_URL
coupling), store + factory mocked. Covers status, connect, disconnect, active,
models, tiers, unknown-provider 404s, malformed bodies, and the auth dependency."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from providers import router as router_mod
from providers.router import router as providers_router, setup_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(providers_router, prefix="/api/providers")
    app.include_router(setup_router, prefix="/api/setup")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


class FakeStore:
    def __init__(self, *, to_dict=None, is_configured=False, configured=None,
                 data=None, usable=frozenset()):
        self._to_dict = to_dict or {"active_provider": "", "active_model": "", "profiles": {}}
        self._is_configured = is_configured
        self._configured = configured or []
        self.data = data or {"active_provider": "", "active_model": "", "profiles": {}}
        self._usable = usable

    def to_dict(self):
        return self._to_dict

    def is_configured(self):
        return self._is_configured

    def configured_providers(self):
        return list(self._configured)

    def is_provider_usable(self, provider):
        return provider in self._usable

    def set_api_key(self, *a, **k):
        pass

    def set_ollama(self, *a, **k):
        pass

    def set_active(self, provider, model):
        return model or "resolved-default"

    def remove_provider(self, *a, **k):
        pass


def _use_store(monkeypatch, store):
    monkeypatch.setattr(router_mod, "CredentialStore", lambda: store)


def test_setup_status_zero_config(client, monkeypatch):
    _use_store(monkeypatch, FakeStore(is_configured=False, configured=[]))
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: None)
    body = client.get("/api/setup/status").json()
    assert body["ai_ready"] is False
    assert body["credentials_present"] is False
    assert body["configured_providers"] == []


def test_get_providers_zero_config(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    body = client.get("/api/providers").json()
    assert body["profiles"] == {}
    assert "is_railway" in body


def test_connect_key_invalid_returns_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())

    class FakeProv:
        async def validate(self):
            return False

    monkeypatch.setattr(router_mod, "_make_key_provider", lambda p, k, m: FakeProv())
    r = client.post("/api/providers/anthropic/connect-key", json={"api_key": "bad"})
    assert r.status_code == 400
    assert "Anthropic" in r.json()["detail"]


def test_connect_key_valid(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())

    class FakeProv:
        async def validate(self):
            return True

    async def fake_mat(provider, requested):
        return requested or "resolved"

    monkeypatch.setattr(router_mod, "_make_key_provider", lambda p, k, m: FakeProv())
    monkeypatch.setattr(router_mod, "_materialize_inferred_tiers", fake_mat)
    r = client.post("/api/providers/anthropic/connect-key",
                    json={"api_key": "good", "model": "claude-opus-4-8"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "provider": "anthropic", "model": "claude-opus-4-8"}


def test_connect_key_unknown_provider_404(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.post("/api/providers/nope/connect-key", json={"api_key": "x"}).status_code == 404


def test_connect_key_empty_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.post("/api/providers/anthropic/connect-key",
                       json={"api_key": "   "}).status_code == 400


def test_connect_key_malformed_body_422(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.post("/api/providers/anthropic/connect-key", json={}).status_code == 422


def test_disconnect_unknown_404(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.post("/api/providers/nope/disconnect").status_code == 404


def test_disconnect_ok(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    r = client.post("/api/providers/anthropic/disconnect")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "provider": "anthropic"}


def test_models_unknown_provider_404(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.get("/api/providers/nope/models").status_code == 404


def test_models_unconfigured_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: None)
    assert client.get("/api/providers/anthropic/models").status_code == 400


def test_active_unknown_provider_404(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.put("/api/providers/active",
                      json={"provider": "nope", "model": "x"}).status_code == 404


def test_active_not_usable_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore(usable=frozenset()))
    assert client.put("/api/providers/active",
                      json={"provider": "anthropic", "model": "x"}).status_code == 400


def test_active_ok_returns_persisted_model(client, monkeypatch):
    _use_store(monkeypatch, FakeStore(usable={"anthropic"}))
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: None)  # catalog check -> []
    r = client.put("/api/providers/active",
                   json={"provider": "anthropic", "model": "claude-opus-4-8"})
    assert r.status_code == 200
    assert r.json()["model"] == "claude-opus-4-8"


def test_get_tiers(client, monkeypatch):
    _use_store(monkeypatch, FakeStore(data={"active_provider": "anthropic", "active_model": "m", "profiles": {}}))
    import providers.model_tiers as mt
    monkeypatch.setattr(mt, "pg_fetchone", lambda sql, params=(): None)  # -> hardcoded fallback
    body = client.get("/api/providers/tiers").json()
    assert "anthropic" in body["tier_models"]
    assert set(body["auto_triage_providers"]) >= {"anthropic", "openai", "google"}


def test_every_route_requires_auth():
    for route in list(providers_router.routes) + list(setup_router.routes):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        dep_names = [d.call.__name__ for d in dependant.dependencies]
        assert "get_current_user" in dep_names, f"{route.path} is missing the auth dependency"
