"""Router contract — built on a minimal FastAPI app (no lifespan/DATABASE_URL
coupling), store + factory mocked. Covers status, connect, disconnect, active,
models, tiers, unknown-provider 404s, malformed bodies, and the auth dependency."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.auth import get_current_user
from providers import router as router_mod
from providers.router import (
    _validated_ollama_url,
    router as providers_router,
    setup_router,
)


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

    def set_active_model(self, model):
        self.data["active_model"] = model

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
    _use_store(monkeypatch, FakeStore(
        data={"active_provider": "anthropic", "active_model": "claude-opus-4-8", "profiles": {}}))

    class FakeProv:
        async def validate(self):
            return True

        async def list_models(self):
            return ["claude-opus-4-8", "claude-sonnet-4-6"]

    monkeypatch.setattr(router_mod, "_make_key_provider", lambda p, k, m: FakeProv())
    r = client.post("/api/providers/anthropic/connect-key",
                    json={"api_key": "good", "model": "claude-opus-4-8"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "provider": "anthropic", "model": "claude-opus-4-8"}


def test_connect_key_rejects_model_not_in_catalog(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())

    class FakeProv:
        async def validate(self):
            return True

        async def list_models(self):
            return ["gpt-5.5", "gpt-5.4"]

    monkeypatch.setattr(router_mod, "_make_key_provider", lambda p, k, m: FakeProv())
    r = client.post("/api/providers/openai/connect-key",
                    json={"api_key": "good", "model": "not-a-real-model"})
    assert r.status_code == 400
    assert "not an available" in r.json()["detail"]


def test_connect_key_reconciles_inaccessible_active_model(client, monkeypatch):
    # set_api_key leaves active_model at a value outside the new key's catalog;
    # connect must reconcile it to a model the key can actually access.
    _use_store(monkeypatch, FakeStore(
        data={"active_provider": "openai", "active_model": "gpt-old-retired", "profiles": {}}))

    class FakeProv:
        async def validate(self):
            return True

        async def list_models(self):
            return ["gpt-5.5", "gpt-5.4"]

    monkeypatch.setattr(router_mod, "_make_key_provider", lambda p, k, m: FakeProv())
    import providers.tiers as t
    monkeypatch.setattr(t, "resolve_tier_model", lambda p, tier: "")  # force fallback to catalog[0]
    r = client.post("/api/providers/openai/connect-key", json={"api_key": "good"})
    assert r.status_code == 200
    assert r.json()["model"] in ("gpt-5.5", "gpt-5.4")


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


class FakeProvider:
    def __init__(self, models):
        self._models = models

    async def list_models(self):
        return list(self._models)


class FakeOllama:
    def __init__(self, reachable, models):
        self._reachable = reachable
        self._models = models

    async def validate(self):
        return self._reachable

    async def list_models(self):
        return list(self._models)


def _patch_ollama(monkeypatch, *, reachable, models):
    monkeypatch.setattr(
        "providers.ollama_provider.OllamaProvider",
        lambda base_url="http://localhost:11434": FakeOllama(reachable, models),
    )


# ── PUT /tiers ─────────────────────────────────────────────────────────────────

def test_set_tiers_unknown_provider_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.put("/api/providers/tiers",
                      json={"provider": "nope", "models": {"top": "x"}}).status_code == 400


def test_set_tiers_bad_tier_key_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.put("/api/providers/tiers",
                      json={"provider": "anthropic", "models": {"bogus": "x"}}).status_code == 400


def test_set_tiers_provider_not_configured_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: None)
    assert client.put("/api/providers/tiers",
                      json={"provider": "anthropic", "models": {"top": "claude-opus-4-8"}}).status_code == 400


def test_set_tiers_oversized_model_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: FakeProvider(["x" * 201]))
    assert client.put("/api/providers/tiers",
                      json={"provider": "anthropic", "models": {"top": "x" * 201}}).status_code == 400


def test_set_tiers_model_not_in_catalog_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: FakeProvider(["claude-opus-4-8"]))
    r = client.put("/api/providers/tiers", json={"provider": "anthropic", "models": {"top": "not-real"}})
    assert r.status_code == 400
    assert "not an available" in r.json()["detail"]


def test_set_tiers_success(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: FakeProvider(["claude-opus-4-8"]))
    import providers.model_tiers as mt
    called = {}
    monkeypatch.setattr(mt, "set_overrides", lambda p, m: called.update(provider=p, models=m))
    monkeypatch.setattr(mt, "get_resolved", lambda p: {"top": "claude-opus-4-8", "mid": "", "light": ""})
    r = client.put("/api/providers/tiers", json={"provider": "anthropic", "models": {"top": "claude-opus-4-8"}})
    assert r.status_code == 200
    assert called == {"provider": "anthropic", "models": {"top": "claude-opus-4-8"}}
    assert r.json()["tier_models"]["top"] == "claude-opus-4-8"


# ── SSRF guard (_validated_ollama_url) ─────────────────────────────────────────

def test_validated_ollama_url_accepts_localhost():
    assert _validated_ollama_url("http://localhost:11434") == "http://localhost:11434"


def test_validated_ollama_url_accepts_lan():
    assert _validated_ollama_url("http://192.168.1.50:11434") == "http://192.168.1.50:11434"


@pytest.mark.parametrize("bad", [
    "ftp://host:11434",       # non-http(s) scheme
    "file:///etc/passwd",     # non-http(s) scheme
    "http://",                # missing host
    "http://user:pass@host",  # embedded credentials
    "http://host:11434/#f",   # fragment
    "   ",                    # empty
])
def test_validated_ollama_url_rejects(bad):
    with pytest.raises(HTTPException) as exc:
        _validated_ollama_url(bad)
    assert exc.value.status_code == 400


# ── Ollama connect / status ────────────────────────────────────────────────────

def test_ollama_connect_unreachable_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    _patch_ollama(monkeypatch, reachable=False, models=[])
    assert client.post("/api/providers/ollama/connect",
                       json={"base_url": "http://localhost:11434"}).status_code == 400


def test_ollama_connect_no_models_400(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    _patch_ollama(monkeypatch, reachable=True, models=[])
    assert client.post("/api/providers/ollama/connect",
                       json={"base_url": "http://localhost:11434"}).status_code == 400


def test_ollama_connect_success(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    _patch_ollama(monkeypatch, reachable=True, models=["llama3.1", "qwen3.5"])
    r = client.post("/api/providers/ollama/connect",
                    json={"base_url": "http://localhost:11434", "model": "qwen3.5"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["model"] == "qwen3.5" and "llama3.1" in body["models"]


def test_ollama_connect_ssrf_rejected_before_fetch(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert client.post("/api/providers/ollama/connect",
                       json={"base_url": "ftp://evil"}).status_code == 400


def test_ollama_status(client, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    _patch_ollama(monkeypatch, reachable=True, models=["llama3.1"])
    body = client.get("/api/providers/ollama/status").json()
    assert body["reachable"] is True and body["models"] == ["llama3.1"]


# ── set_active: catalog reset (key provider) + Ollama installed-model pick ──────

def test_active_resets_model_not_in_catalog(client, monkeypatch):
    _use_store(monkeypatch, FakeStore(usable={"anthropic"}))
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: FakeProvider(["claude-opus-4-8"]))
    r = client.put("/api/providers/active", json={"provider": "anthropic", "model": "not-in-catalog"})
    assert r.status_code == 200
    # requested model isn't in the catalog → reset to "" → FakeStore derives a default
    assert r.json()["model"] == "resolved-default"


def test_active_ollama_picks_installed_model(client, monkeypatch):
    # Regression for the "Set as active blanks Ollama's model" bug.
    _use_store(monkeypatch, FakeStore(usable={"ollama"}))
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: FakeProvider(["llama3.1", "qwen3.5"]))
    r = client.put("/api/providers/active", json={"provider": "ollama", "model": ""})
    assert r.status_code == 200
    assert r.json()["model"] == "llama3.1"  # first installed model, never blanked to ""


def test_active_ollama_unreachable_rejects_without_blanking(client, monkeypatch):
    # A transient Ollama outage must refuse the switch, not persist a blank model.
    _use_store(monkeypatch, FakeStore(usable={"ollama"}))
    monkeypatch.setattr("providers.get_ai_provider", lambda *a, **k: FakeProvider([]))
    r = client.put("/api/providers/active", json={"provider": "ollama", "model": ""})
    assert r.status_code == 400
    assert "Cannot reach Ollama" in r.json()["detail"]
