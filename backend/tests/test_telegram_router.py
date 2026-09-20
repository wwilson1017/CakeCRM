"""Telegram router — the only externally-reachable surface of the feature.

Hermetic: a minimal FastAPI app mounts the router; auth is overridden for the happy
paths and left real for the auth check; client/store/poller are faked. Pins: endpoints
require auth, an invalid token → 400, connect stops→mutates→starts the poller and
deletes any webhook, and NO response ever contains the bot token.

Since #193 it also pins the gating split. Bot config (connect/disconnect) is admin;
minting MY link code and unlinking MY chat are member-legal, because a per-seat code
claims the caller's own row. The old member redaction is gone with its premise, and
``/link-code/regenerate`` is gone with it — both asserted here so neither can quietly
come back. ``test_route_authz`` holds the bidirectional ``require_admin`` pin.
"""


from conftest import fake_admin, fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from telegram import router as tgrouter


def _app():
    app = FastAPI()
    app.include_router(tgrouter.router, prefix="/api/telegram")
    return app


def _fake_backend(monkeypatch, *, valid_token=True, link=None):
    calls = []
    monkeypatch.setattr(tgrouter.client, "validate_token",
                        lambda t: ({"username": "acmebot"} if valid_token else None))
    monkeypatch.setattr(tgrouter.client, "delete_webhook",
                        lambda t, drop: calls.append(("delete_webhook", t, drop)))
    monkeypatch.setattr(tgrouter.store, "connect",
                        lambda t, u: calls.append(("connect", t, u)))
    monkeypatch.setattr(tgrouter.store, "disconnect",
                        lambda: calls.append(("disconnect",)))
    monkeypatch.setattr(tgrouter.store, "mint_link_code",
                        lambda uid: calls.append(("mint", uid)) or "CODE123")
    monkeypatch.setattr(tgrouter.store, "unlink",
                        lambda uid: calls.append(("unlink", uid)) or True)
    monkeypatch.setattr(tgrouter.store, "get_settings", lambda: {
        "connected": True, "bot_username": "acmebot", "bot_token_enc": "enc:v1:SECRET",
    })
    monkeypatch.setattr(tgrouter.store, "get_link",
                        lambda uid: (calls.append(("get_link", uid)) or link))

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


def test_connect_and_disconnect_stay_admin_only(monkeypatch):
    """A member must not be able to swap or kill the workspace's bot."""
    _fake_backend(monkeypatch)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_member
    client = TestClient(app)
    assert client.post("/api/telegram/connect", json={"bot_token": "123:REAL"}).status_code == 403
    assert client.post("/api/telegram/disconnect").status_code == 403


# ── The per-seat half ───────────────────────────────────────────────────────

def test_status_describes_my_own_link(monkeypatch):
    _fake_backend(monkeypatch, link={
        "link_code": "", "chat_id": "chat1", "telegram_name": "Alex",
    })
    app = _app()
    app.dependency_overrides[get_current_user] = fake_member
    body = TestClient(app).get("/api/telegram/status").json()
    assert body["connected"] is True and body["bot_username"] == "acmebot"
    assert body["linked"] is True and body["linked_name"] == "Alex"
    assert "bot_token" not in body and "bot_token_enc" not in body


def test_status_is_scoped_to_the_calling_seat(monkeypatch):
    """`get_link` is asked about the caller — never about the install."""
    calls = _fake_backend(monkeypatch, link=None)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_member
    TestClient(app).get("/api/telegram/status")
    assert ("get_link", 2) in calls  # FAKE_MEMBER's id


def test_status_reports_unlinked_when_this_seat_has_no_row(monkeypatch):
    _fake_backend(monkeypatch, link=None)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_member
    body = TestClient(app).get("/api/telegram/status").json()
    assert body["linked"] is False
    assert body["link_code"] == "" and body["link_url"] == ""
    # An admin connected a bot, so the install half still reads true.
    assert body["connected"] is True


def test_status_never_mints_a_code(monkeypatch):
    """A GET must not have a side effect — POST /link-code is what mints."""
    calls = _fake_backend(monkeypatch, link=None)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_member
    TestClient(app).get("/api/telegram/status")
    assert not [c for c in calls if c[0] == "mint"]


def test_member_may_mint_their_own_link_code(monkeypatch):
    """The redaction workaround dissolves: your code claims YOUR row."""
    calls = _fake_backend(monkeypatch, link={"link_code": "CODE123", "chat_id": ""})
    app = _app()
    app.dependency_overrides[get_current_user] = fake_member
    resp = TestClient(app).post("/api/telegram/link-code")
    assert resp.status_code == 200
    assert ("mint", 2) in calls
    body = resp.json()
    assert body["link_code"] == "CODE123"
    assert body["link_url"] == "https://t.me/acmebot?start=CODE123"


def test_member_may_unlink_their_own_chat(monkeypatch):
    calls = _fake_backend(monkeypatch, link={"link_code": "", "chat_id": ""})
    app = _app()
    app.dependency_overrides[get_current_user] = fake_member
    resp = TestClient(app).post("/api/telegram/unlink")
    assert resp.status_code == 200
    assert ("unlink", 2) in calls
    assert resp.json()["linked"] is False


def test_link_code_and_unlink_require_auth(monkeypatch):
    _fake_backend(monkeypatch)
    client = TestClient(_app())
    assert client.post("/api/telegram/link-code").status_code in (401, 403)
    assert client.post("/api/telegram/unlink").status_code in (401, 403)


def test_the_install_wide_regenerate_route_is_gone(monkeypatch):
    """It claimed the ONE binding. Its replacement claims only the caller's own row."""
    _fake_backend(monkeypatch)
    app = _app()
    app.dependency_overrides[get_current_user] = fake_admin
    assert TestClient(app).post("/api/telegram/link-code/regenerate").status_code == 404


def test_status_payload_excludes_token(monkeypatch):
    _fake_backend(monkeypatch, link={"link_code": "CODE123", "chat_id": ""})
    payload = tgrouter._status_payload(2)
    assert "bot_token" not in payload and "bot_token_enc" not in payload
    assert payload["link_url"] == "https://t.me/acmebot?start=CODE123"
