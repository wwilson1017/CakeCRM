"""`/api/mcp` connections, tokens and the admin switch (#322), with the store faked."""

from datetime import datetime, timezone

import pytest
from conftest import fake_admin, fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user, require_admin
from mcp_endpoint import oauth, store
from mcp_endpoint.router import router

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


class Fake:
    def __init__(self):
        self.members = False
        self.rows: dict[int, dict] = {}
        self.calls: dict[int, list] = {}
        self.minted: list[dict] = []
        self.cap = False

    def members_enabled(self):
        return self.members

    def set_members_enabled(self, flag):
        self.members = bool(flag)
        return self.members

    def create_personal_token(self, user_id, *, name, read_only, lifetime_days):
        if self.cap:
            raise store.CapReached()
        cid = len(self.rows) + 1
        row = {"id": cid, "user_id": user_id, "kind": "token", "name": name, "read_only": read_only,
               "lifetime_days": lifetime_days, "created_at": NOW}
        self.rows[cid] = row
        self.minted.append(row)
        return f"plaintext-{cid}", {k: v for k, v in row.items() if k != "user_id"}

    def list_connections(self, *, user_id=None):
        return [r for r in self.rows.values() if user_id is None or r["user_id"] == user_id]

    def revoke(self, connection_id, *, user_id=None):
        row = self.rows.get(connection_id)
        if row is None or (user_id is not None and row["user_id"] != user_id):
            return False
        del self.rows[connection_id]
        return True

    def list_calls(self, connection_id, *, user_id=None):
        return [c for c in self.calls.get(connection_id, []) if user_id is None or c["user_id"] == user_id]


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    for name in ("members_enabled", "set_members_enabled", "create_personal_token",
                 "list_connections", "revoke", "list_calls"):
        monkeypatch.setattr(store, name, getattr(f, name))
    monkeypatch.setattr(oauth, "oauth_base_url", lambda: None)
    return f


def _client(user) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/mcp")
    app.dependency_overrides[get_current_user] = lambda: user

    def admin_only():
        if user["role"] != "admin":
            from fastapi import HTTPException
            raise HTTPException(status_code=403)
        return user

    app.dependency_overrides[require_admin] = admin_only
    return TestClient(app)


def test_a_member_cannot_create_a_token_while_the_switch_is_off(fake):
    c = _client(fake_member())
    assert c.get("/api/mcp/connections").json()["may_connect"] is False
    assert c.post("/api/mcp/tokens", json={"name": "Codex"}).status_code == 403
    fake.members = True
    assert c.get("/api/mcp/connections").json()["may_connect"] is True
    assert c.post("/api/mcp/tokens", json={"name": "Codex"}).status_code == 200


def test_an_admin_can_always_create_a_token(fake):
    r = _client(fake_admin()).post("/api/mcp/tokens", json={"name": "Claude Code", "read_only": True})
    assert r.status_code == 200
    assert r.json()["token"] == "plaintext-1" and r.json()["read_only"] is True


def test_the_plaintext_is_returned_once_and_never_listed(fake):
    c = _client(fake_admin())
    c.post("/api/mcp/tokens", json={"name": "Claude Code"})
    listed = c.get("/api/mcp/connections").json()["connections"]
    assert listed and "token" not in listed[0] and "plaintext-1" not in str(listed)


@pytest.mark.parametrize(("days", "status"), [(30, 200), (90, 200), (365, 200), (None, 200),
                                              (7, 422), (0, 422)])
def test_lifetimes_are_exactly_30_90_365_or_never(fake, days, status):
    r = _client(fake_admin()).post("/api/mcp/tokens", json={"name": "x", "lifetime_days": days})
    assert r.status_code == status


def test_the_default_lifetime_is_90_days(fake):
    _client(fake_admin()).post("/api/mcp/tokens", json={"name": "x"})
    assert fake.minted[0]["lifetime_days"] == 90


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_a_token_needs_a_short_name(fake, name):
    assert _client(fake_admin()).post("/api/mcp/tokens", json={"name": name}).status_code == 422


def test_the_cap_is_a_409(fake):
    fake.cap = True
    assert _client(fake_admin()).post("/api/mcp/tokens", json={"name": "x"}).status_code == 409


def test_a_member_cannot_revoke_another_seats_connection_but_an_admin_can(fake):
    fake.members = True
    _client(fake_admin()).post("/api/mcp/tokens", json={"name": "admin's"})
    member = _client(fake_member())
    assert member.delete("/api/mcp/connections/1").status_code == 404
    assert member.delete("/api/mcp/admin/connections/1").status_code == 403
    assert _client(fake_admin()).delete("/api/mcp/admin/connections/1").status_code == 200
    assert fake.rows == {}


def test_the_members_switch_round_trips_for_an_admin_only(fake):
    assert _client(fake_member()).put("/api/mcp/admin/settings",
                                      json={"members_enabled": True}).status_code == 403
    admin = _client(fake_admin())
    assert admin.put("/api/mcp/admin/settings", json={"members_enabled": True}).json() == {
        "members_enabled": True}
    assert admin.get("/api/mcp/admin/connections").json()["members_enabled"] is True


def test_calls_are_scoped_to_the_seat(fake):
    fake.calls[1] = [{"user_id": fake_admin()["id"], "tool": "crm_find_contact"}]
    assert _client(fake_member()).get("/api/mcp/connections/1/calls").json() == {"calls": []}
    assert len(_client(fake_admin()).get("/api/mcp/admin/connections/1/calls").json()["calls"]) == 1


def test_oauth_url_is_null_without_a_public_address_and_set_with_one(fake, monkeypatch):
    c = _client(fake_admin())
    assert c.get("/api/mcp/connections").json()["oauth_url"] is None
    monkeypatch.setattr(oauth, "oauth_base_url", lambda: "https://crm.example")
    assert c.get("/api/mcp/connections").json()["oauth_url"] == "https://crm.example/mcp"
