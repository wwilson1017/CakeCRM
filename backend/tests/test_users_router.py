"""User-management API (issue #60 Phase A).

The route-level rules that the service layer cannot express — mainly the ones that
stop an admin from doing something to THEMSELVES that has no in-app way back.
"""

import pytest
from conftest import fake_admin, fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from users import service
from users.router import router as users_router

ADMIN_ID = 1


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(users_router, prefix="/api/users")
    app.dependency_overrides[get_current_user] = fake_admin
    monkeypatch.setattr(service, "list_users", lambda **kw: [])
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_an_admin_cannot_reset_their_own_password_here(client, monkeypatch):
    """This route deliberately skips the current-password check and the 2FA code that
    /api/auth/change-password enforces — right for helping a locked-out colleague,
    wrong as a self-service path, because a hijacked admin session could then set a
    new password without knowing the old one or passing 2FA."""
    called = []
    monkeypatch.setattr(
        service, "set_password_as_admin", lambda *a, **k: called.append(a) or True
    )
    r = client.post(f"/api/users/{ADMIN_ID}/password", json={"new_password": "abcdefgh12"})
    assert r.status_code == 400
    assert "Change password" in r.json()["detail"]
    assert called == [], "the reset ran despite being refused"


def test_resetting_someone_else_is_allowed(client, monkeypatch):
    captured = {}

    def _reset(user_id, new_password, clear_two_factor=False):
        captured.update(user_id=user_id, clear_two_factor=clear_two_factor)
        return True

    monkeypatch.setattr(service, "set_password_as_admin", _reset)
    r = client.post("/api/users/2/password", json={"new_password": "abcdefgh12"})
    assert r.status_code == 200
    assert captured == {"user_id": 2, "clear_two_factor": False}


def test_clearing_two_factor_is_opt_in(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        service, "set_password_as_admin",
        lambda uid, pw, clear_two_factor=False: captured.update(c=clear_two_factor) or True,
    )
    client.post(
        "/api/users/2/password",
        json={"new_password": "abcdefgh12", "clear_two_factor": True},
    )
    assert captured["c"] is True


def test_a_short_reset_password_is_refused(client):
    assert client.post("/api/users/2/password", json={"new_password": "short"}).status_code == 400


def test_you_cannot_deactivate_or_demote_yourself(client):
    """The one self-inflicted lockout with no in-app way back. Refused before the
    last-admin guard, which would happily allow it whenever a second admin exists."""
    assert client.patch(f"/api/users/{ADMIN_ID}", json={"is_active": False}).status_code == 400
    assert client.patch(f"/api/users/{ADMIN_ID}", json={"role": "member"}).status_code == 400


def test_a_member_cannot_manage_users(monkeypatch):
    app = FastAPI()
    app.include_router(users_router, prefix="/api/users")
    app.dependency_overrides[get_current_user] = fake_member
    monkeypatch.setattr(service, "list_users", lambda **kw: [])
    with TestClient(app) as c:
        assert c.post("/api/users", json={"email": "a@b.c", "password": "abcdefgh12"}).status_code == 403
        assert c.patch("/api/users/1", json={"role": "admin"}).status_code == 403
        assert c.post("/api/users/1/password", json={"new_password": "abcdefgh12"}).status_code == 403
        # But the roster itself stays readable — owner pickers need the names.
        assert c.get("/api/users").status_code == 200
    app.dependency_overrides.clear()
