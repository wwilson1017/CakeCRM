"""Telegram admin router — the only externally-reachable surface of the feature.

Hermetic: a minimal FastAPI app mounts the router; auth is overridden for the happy
paths and left real for the auth check; client/store/poller are faked. Pins: endpoints
require auth, an invalid token → 400, connect stops→mutates→starts the poller and
deletes any webhook, and NO response ever contains the bot token.
"""


from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from telegram import router as tgrouter
from conftest import fake_admin


def _app():
    app = FastAPI()
    app.include_router(tgrouter.router, prefix="/api/telegram")
    return app


def _fake_backend(monkeypatch, *, valid_token=True):
    calls = []
    monkeypatch.setattr(tgrouter.client, "validate_token",
                        lambda t: ({"username": "acmebot"} if valid_token else None))
    monkeypatch.setattr(tgrouter.client, "delete_webhook",
                        lambda t, drop: calls.append(("delete_webhook", t, drop)))
    monkeypatch.setattr(tgrouter.store, "connect",
                        lambda t, u: calls.append(("connect", t, u)))
    monkeypatch.setattr(tgrouter.store, "disconnect",
                        lambda: calls.append(("disconnect",)))
    monkeypatch.setattr(tgrouter.store, "regenerate_link_code",
                        lambda: calls.append(("regen",)))
    monkeypatch.setattr(tgrouter.store, "get_settings", lambda: {
        "connected": True, "bot_username": "acmebot", "linked": False,
        "linked_name": "", "link_code": "CODE123", "bot_token_enc": "enc:v1:SECRET",
    })

    async def fake_stop():
        calls.append(("stop",))

    monkeypatch.setattr(tgrouter.poller, "stop", fake_stop)
    monkeypatch.setattr(tgrouter.poller, "start", lambda: calls.append(("start",)))
    return calls


def test_status_requires_auth(monkeypatch):
    _fake_backend(monkeypatch)
    # No auth override → the real get_current_user rejects an unauthenticated request.
    client = TestClient(_app())
    resp = client.get("/api/telegram/status")
    assert resp.status_code in (401, 403)


def test_connect_rejects_invalid_token(monkeypatch):
    _fake_backend(monkeypatch, valid_token=False)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_admin
    resp = TestClient(app).post("/api/telegram/connect", json={"bot_token": "bad"})
    assert resp.status_code == 400


def test_connect_wires_poller_and_never_leaks_token(monkeypatch):
    calls = _fake_backend(monkeypatch)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_admin
    resp = TestClient(app).post("/api/telegram/connect", json={"bot_token": "123:REAL"})
    assert resp.status_code == 200
    names = [c[0] for c in calls]
    # Poller stopped BEFORE the config mutation, started after; webhook cleared.
    assert names.index("stop") < names.index("connect") < names.index("start")
    assert ("delete_webhook", "123:REAL", True) in calls  # drop the pre-connect backlog
    assert ("connect", "123:REAL", "acmebot") in calls
    body = resp.json()
    assert "bot_token" not in body and "bot_token_enc" not in body
    assert body["bot_username"] == "acmebot"


def test_disconnect_restarts_poller(monkeypatch):
    calls = _fake_backend(monkeypatch)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_admin
    resp = TestClient(app).post("/api/telegram/disconnect")
    assert resp.status_code == 200
    names = [c[0] for c in calls]
    assert names.index("stop") < names.index("disconnect") < names.index("start")
    assert "bot_token_enc" not in resp.json()


def test_status_payload_excludes_token(monkeypatch):
    _fake_backend(monkeypatch)
    payload = tgrouter._status_payload(include_link=True)
    assert "bot_token" not in payload and "bot_token_enc" not in payload
    assert payload["link_url"] == "https://t.me/acmebot?start=CODE123"


def test_status_payload_redacts_the_link_code_for_members(monkeypatch):
    """A member must not be able to claim the install-wide Telegram binding.

    Until Phase B gives Telegram per-user bindings, the link code hands whoever
    uses it the ONE seat that receives every notification and can drive the
    assistant from a phone — so gating connect/disconnect but leaking the code
    would gate nothing.
    """
    _fake_backend(monkeypatch)
    payload = tgrouter._status_payload(include_link=False)
    assert payload["link_code"] == ""
    assert payload["link_url"] == ""
    # Connection state itself is not secret.
    assert payload["connected"] is True
