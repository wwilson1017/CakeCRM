"""Gmail router contract (issue #8). Minimal FastAPI app; auth overridden; the
store/oauth/client/ops collaborators are monkeypatched so nothing touches a DB or
network. Callback assertions use follow_redirects=False to inspect the 302."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from gmail import oauth, router as router_mod
from gmail.router import router as gmail_router
from providers.router import setup_router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(gmail_router, prefix="/api/gmail")
    app.include_router(setup_router, prefix="/api/setup")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return app


@pytest.fixture
def api(app):
    return TestClient(app, follow_redirects=False)


# ── /status, /app ─────────────────────────────────────────────────────────────

def test_status_returns_sanitized(monkeypatch, api):
    monkeypatch.setattr(router_mod.store, "status_dict", lambda: {"connected": False, "email": "", "redirect_uri": "x"})
    r = api.get("/api/gmail/status")
    assert r.status_code == 200
    body = r.json()
    assert "client_secret" not in body
    assert body["connected"] is False


def test_app_rejects_blank(api):
    assert api.post("/api/gmail/app", json={"client_id": "  ", "client_secret": "x"}).status_code == 400
    assert api.post("/api/gmail/app", json={"client_id": "x", "client_secret": ""}).status_code == 400


def test_app_saves_and_returns_status(monkeypatch, api):
    saved = {}
    monkeypatch.setattr(router_mod.store, "save_app_credentials",
                        lambda c, s: saved.update(cid=c, sec=s) or "")
    monkeypatch.setattr(router_mod.store, "status_dict", lambda: {"connected": False})
    r = api.post("/api/gmail/app", json={"client_id": "cid", "client_secret": "sec"})
    assert r.status_code == 200
    assert saved == {"cid": "cid", "sec": "sec"}


# ── /oauth/start ──────────────────────────────────────────────────────────────

def test_oauth_start_requires_app_creds(monkeypatch, api):
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("", ""))
    assert api.post("/api/gmail/oauth/start").status_code == 400


def test_oauth_start_persists_state_and_returns_url(monkeypatch, api):
    captured = {}
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.store, "set_oauth_state_hash", lambda s: captured.update(state=s))
    monkeypatch.setattr(router_mod.oauth, "build_auth_url", lambda cid, state: f"https://g/auth?state={state}")
    r = api.post("/api/gmail/oauth/start")
    assert r.status_code == 200
    assert captured["state"] in r.json()["auth_url"]


# ── /oauth/callback (unauthenticated by design) ───────────────────────────────

def _tokens(scope=None):
    return {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 3600,
        "scope": scope if scope is not None else f"{oauth.GMAIL_READONLY_SCOPE} {oauth.GMAIL_COMPOSE_SCOPE}",
    }


def test_callback_needs_no_auth(app, monkeypatch):
    # No dependency override for get_current_user -> proves the route is public.
    app.dependency_overrides.clear()
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: None)
    c = TestClient(app, follow_redirects=False)
    r = c.get("/api/gmail/oauth/callback?state=x&code=y")
    assert r.status_code == 302  # not 401
    assert "gmail=error&reason=state" in r.headers["location"]


def test_callback_bad_state(monkeypatch, api):
    called = {"exchange": False}
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: None)
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: called.update(exchange=True))
    r = api.get("/api/gmail/oauth/callback?state=bad&code=c")
    assert "reason=state" in r.headers["location"]
    assert called["exchange"] is False  # state claimed BEFORE any exchange


def test_callback_denied_consumes_state_first(monkeypatch, api):
    claimed = {"n": 0}

    def claim(s):
        claimed["n"] += 1
        return 3

    monkeypatch.setattr(router_mod.store, "claim_oauth_state", claim)
    r = api.get("/api/gmail/oauth/callback?state=s&error=access_denied")
    assert "reason=denied" in r.headers["location"]
    assert claimed["n"] == 1  # denial still consumed the state


def test_callback_exchange_failure(monkeypatch, api):
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "reason=exchange" in r.headers["location"]


def test_callback_no_refresh_token_revokes(monkeypatch, api):
    revoked = []
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: {"access_token": "at", "expires_in": 3600})
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: revoked.append(t))
    saved = []
    monkeypatch.setattr(router_mod.store, "save_tokens", lambda **k: saved.append(k) or True)
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "reason=no_refresh_token" in r.headers["location"]
    assert revoked == ["at"]
    assert saved == []


def test_callback_missing_scope_revokes(monkeypatch, api):
    revoked = []
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: _tokens(scope=oauth.GMAIL_READONLY_SCOPE))
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: revoked.append(t))
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "reason=scopes" in r.headers["location"]
    assert revoked == ["rt"]


def test_callback_profile_failure_is_fatal(monkeypatch, api):
    revoked = []
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: _tokens())
    monkeypatch.setattr(router_mod.client, "call_with_token", lambda t, op: (_ for _ in ()).throw(RuntimeError("api down")))
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: revoked.append(t))
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "reason=profile" in r.headers["location"]
    assert revoked == ["rt"]


def test_callback_happy_path_saves_tokens(monkeypatch, api):
    saved = {}
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: _tokens())
    monkeypatch.setattr(router_mod.client, "call_with_token", lambda t, op: {"email": "me@example.com"})
    monkeypatch.setattr(router_mod.store, "save_tokens", lambda **k: saved.update(k) or True)
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "gmail=connected" in r.headers["location"]
    assert saved["email"] == "me@example.com"
    assert saved["refresh_token"] == "rt"
    assert oauth.GMAIL_READONLY_SCOPE in saved["scopes"]
    assert oauth.GMAIL_COMPOSE_SCOPE in saved["scopes"]
    # The generation captured by the state-claim is carried through as the CAS key.
    assert saved["expected_generation"] == 3


def test_callback_cas_miss_revokes_and_reports_conflict(monkeypatch, api):
    """THE #43 race: the admin disconnects (or replaces the OAuth app) during the
    Google round-trips. save_tokens' CAS misses, so the just-granted grant must be
    revoked rather than orphaned, and nothing may be resurrected."""
    revoked = []
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: _tokens())
    monkeypatch.setattr(router_mod.client, "call_with_token", lambda t, op: {"email": "me@x.com"})
    monkeypatch.setattr(router_mod.store, "save_tokens", lambda **k: False)
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: revoked.append(t))

    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "gmail=error&reason=conflict" in r.headers["location"]
    assert revoked == ["rt"]


def test_callback_accepts_generation_zero(monkeypatch, api):
    """A never-yet-connected instance sits at generation 0. Testing the claim for
    truthiness instead of `is None` would reject every first connect."""
    saved = {}
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 0)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: _tokens())
    monkeypatch.setattr(router_mod.client, "call_with_token", lambda t, op: {"email": "me@x.com"})
    monkeypatch.setattr(router_mod.store, "save_tokens", lambda **k: saved.update(k) or True)

    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "gmail=connected" in r.headers["location"]
    assert saved["expected_generation"] == 0


# ── DELETE /connection ────────────────────────────────────────────────────────

def test_disconnect_clears_then_revokes_what_it_cleared(monkeypatch, api):
    from core.encryption import encrypt_value

    events = []
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: events.append(("revoke", t)))
    monkeypatch.setattr(router_mod.store, "clear_connection",
                        lambda: (events.append(("clear",)), encrypt_value("live-rt"))[1])
    r = api.request("DELETE", "/api/gmail/connection")
    assert r.status_code == 200 and r.json() == {"ok": True}
    # Clear first (atomic, capturing the old ciphertext), then revoke exactly it.
    assert events == [("clear",), ("revoke", "live-rt")]


def test_disconnect_with_no_live_token_skips_revoke(monkeypatch, api):
    revoked = []
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: revoked.append(t))
    monkeypatch.setattr(router_mod.store, "clear_connection", lambda: "")
    r = api.request("DELETE", "/api/gmail/connection")
    assert r.status_code == 200
    assert revoked == []


# ── /api/setup/status gmail_connected ─────────────────────────────────────────

def test_setup_status_includes_gmail_connected(monkeypatch, api):
    import gmail.store as gmail_store

    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    r = api.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["gmail_connected"] is True


def test_callback_outer_catch_returns_302_not_500(monkeypatch, api):
    # A DB blip in claim_oauth_state itself (not one of the inner try/excepts) must
    # still yield a clean redirect, never a raw 500 on this public endpoint.
    monkeypatch.setattr(router_mod.store, "claim_oauth_state",
                        lambda s: (_ for _ in ()).throw(RuntimeError("db blip")))
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert r.status_code == 302
    assert "gmail=error" in r.headers["location"]


def test_setup_status_gmail_connected_degrades_on_error(monkeypatch, api):
    import gmail.store as gmail_store

    monkeypatch.setattr(gmail_store, "is_connected", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r = api.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["gmail_connected"] is False


def test_save_app_revokes_the_ciphertext_the_store_cleared(monkeypatch, api):
    """The revoked token comes from the save's own atomic clear-and-capture, not a
    separate pre-read — so it is by construction the grant that was just ended (#43)."""
    from core.encryption import encrypt_value

    revoked = []
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: revoked.append(t))
    monkeypatch.setattr(router_mod.store, "save_app_credentials",
                        lambda c, s: encrypt_value("old-rt"))
    monkeypatch.setattr(router_mod.store, "status_dict", lambda: {"connected": False})
    r = api.post("/api/gmail/app", json={"client_id": "cid", "client_secret": "sec"})
    assert r.status_code == 200
    assert revoked == ["old-rt"]


def test_callback_persist_failure_revokes_tokens(monkeypatch, api):
    revoked = []
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: _tokens())
    monkeypatch.setattr(router_mod.client, "call_with_token", lambda t, op: {"email": "me@x.com"})
    monkeypatch.setattr(router_mod.store, "save_tokens",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(router_mod.oauth, "revoke_token", lambda t: revoked.append(t))
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "reason=exchange" in r.headers["location"]
    assert revoked == ["rt"]  # orphaned grant revoked


def test_callback_persists_only_minimal_scopes(monkeypatch, api):
    saved = {}
    monkeypatch.setattr(router_mod.store, "claim_oauth_state", lambda s: 3)
    monkeypatch.setattr(router_mod.store, "get_app_credentials", lambda: ("cid", "sec"))
    granted = f"{oauth.GMAIL_READONLY_SCOPE} {oauth.GMAIL_COMPOSE_SCOPE} https://www.googleapis.com/auth/gmail.modify"
    monkeypatch.setattr(router_mod.oauth, "exchange_code", lambda *a: _tokens(scope=granted))
    monkeypatch.setattr(router_mod.client, "call_with_token", lambda t, op: {"email": "me@x.com"})
    monkeypatch.setattr(router_mod.store, "save_tokens", lambda **k: saved.update(k) or True)
    r = api.get("/api/gmail/oauth/callback?state=s&code=c")
    assert "gmail=connected" in r.headers["location"]
    # Google granted an extra scope, but we persist ONLY the minimal requested set.
    assert saved["scopes"].split() == oauth.SCOPES
    assert "gmail.modify" not in saved["scopes"]
