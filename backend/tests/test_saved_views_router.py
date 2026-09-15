"""Saved views router contract — the error-code → HTTP-status mapping (#181).

Minimal FastAPI app; auth overridden; the service layer monkeypatched, so nothing touches a
DB. The one non-obvious pin is `test_router_does_not_gate_on_role`: the creator-or-admin
decision belongs to the service (it needs the row), and a duplicate role check here would be
a second, drifting source of truth.
"""

import pytest
from conftest import fake_admin, fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from saved_views import router as router_mod
from saved_views.router import router as saved_views_router


def _client(identity=fake_admin) -> TestClient:
    app = FastAPI()
    app.include_router(saved_views_router, prefix="/api/saved-views")
    app.dependency_overrides[get_current_user] = identity
    return TestClient(app)


@pytest.fixture
def client():
    return _client()


@pytest.fixture
def calls(monkeypatch):
    """Record what the router forwards to the service."""
    recorded: list[tuple] = []

    def spy(name, result):
        def _fn(*args, **kwargs):
            recorded.append((name, args, kwargs))
            return result
        monkeypatch.setattr(router_mod.service, name, _fn)

    spy.recorded = recorded
    return spy


VIEW = {"id": 1, "surface": "crm_pipeline", "name": "Q3", "version": 1, "payload": {},
        "created_by": 1, "created_by_name": "Test Admin", "can_edit": True}


# ── list ───────────────────────────────────────────────────────────────────

def test_list_requires_a_surface(client):
    assert client.get("/api/saved-views").status_code == 422


def test_list_returns_the_views(client, calls):
    calls("list_views", [VIEW])
    r = client.get("/api/saved-views?surface=crm_pipeline")
    assert r.status_code == 200
    assert r.json() == {"views": [VIEW]}


def test_list_forwards_surface_and_actor(client, calls):
    calls("list_views", [])
    client.get("/api/saved-views?surface=crm_tasks")
    _, args, _ = calls.recorded[0]
    assert args[0] == "crm_tasks"
    assert args[1]["id"] == 1


def test_list_service_error_maps_400(client, calls):
    calls("list_views", {"error": "surface is required", "code": "bad_request"})
    assert client.get("/api/saved-views?surface=nope").status_code == 400


# ── create ─────────────────────────────────────────────────────────────────

def test_create_forwards_every_field_and_the_actor(client, calls):
    calls("create_view", VIEW)
    body = {"surface": "crm_pipeline", "name": "Q3", "version": 1, "payload": {"query": "a"}}
    r = client.post("/api/saved-views", json=body)
    assert r.status_code == 200 and r.json()["id"] == 1
    _, args, _ = calls.recorded[0]
    assert args[:4] == ("crm_pipeline", "Q3", 1, {"query": "a"})
    assert args[4]["role"] == "admin"


@pytest.mark.parametrize("version", [True, False, "1", 1.0, 1.5, "abc", None])
def test_create_rejects_a_non_integer_version_before_the_service(client, calls, version):
    # Pydantic's DEFAULT coercion would turn true into 1, false into 0 and "1"/1.0 into 1,
    # laundering a boolean into a version number and making the service's own type check
    # unreachable over HTTP. StrictInt is what keeps the two layers agreeing.
    calls("create_view", VIEW)
    body = {"surface": "crm_pipeline", "name": "Q3", "version": version, "payload": {}}
    assert client.post("/api/saved-views", json=body).status_code == 422
    assert calls.recorded == []


@pytest.mark.parametrize("version", [True, False, "2", 2.0])
def test_update_rejects_a_non_integer_version_before_the_service(client, calls, version):
    calls("update_view", VIEW)
    body = {"payload": {}, "version": version}
    assert client.put("/api/saved-views/1", json=body).status_code == 422
    assert calls.recorded == []


def test_create_accepts_a_real_integer_version(client, calls):
    calls("create_view", VIEW)
    body = {"surface": "crm_pipeline", "name": "Q3", "version": 0, "payload": {}}
    assert client.post("/api/saved-views", json=body).status_code == 200
    assert calls.recorded[0][1][2] == 0


def test_create_conflict_maps_409(client, calls):
    calls("create_view", {"error": "duplicate", "code": "conflict"})
    body = {"surface": "crm_pipeline", "name": "Q3", "version": 1, "payload": {}}
    assert client.post("/api/saved-views", json=body).status_code == 409


def test_create_bad_request_maps_400(client, calls):
    calls("create_view", {"error": "name is required", "code": "bad_request"})
    body = {"surface": "crm_pipeline", "name": " ", "version": 1, "payload": {}}
    assert client.post("/api/saved-views", json=body).status_code == 400


# ── update ─────────────────────────────────────────────────────────────────

def test_update_with_no_fields_is_400_before_the_service(client, calls):
    calls("update_view", VIEW)
    assert client.put("/api/saved-views/1", json={}).status_code == 400
    assert calls.recorded == []


def test_update_forwards_only_the_supplied_fields(client, calls):
    calls("update_view", VIEW)
    client.put("/api/saved-views/1", json={"name": "renamed"})
    _, args, kwargs = calls.recorded[0]
    assert args[0] == 1
    assert kwargs == {"name": "renamed"}


def test_update_forwards_payload_with_version(client, calls):
    calls("update_view", VIEW)
    client.put("/api/saved-views/1", json={"payload": {"query": "x"}, "version": 2})
    _, _, kwargs = calls.recorded[0]
    assert kwargs == {"payload": {"query": "x"}, "version": 2}


def test_update_forbidden_maps_403(client, calls):
    calls("update_view", {"error": "Only the view's creator or an admin can change it",
                          "code": "forbidden"})
    assert client.put("/api/saved-views/1", json={"name": "x"}).status_code == 403


def test_update_not_found_maps_404(client, calls):
    calls("update_view", {"error": "View not found", "code": "not_found"})
    assert client.put("/api/saved-views/1", json={"name": "x"}).status_code == 404


def test_update_conflict_maps_409(client, calls):
    calls("update_view", {"error": "taken", "code": "conflict"})
    assert client.put("/api/saved-views/1", json={"name": "x"}).status_code == 409


# ── delete ─────────────────────────────────────────────────────────────────

def test_delete_ok(client, calls):
    calls("delete_view", {"ok": True, "id": 1})
    r = client.delete("/api/saved-views/1")
    assert r.status_code == 200 and r.json() == {"ok": True, "id": 1}


def test_delete_forbidden_maps_403(client, calls):
    calls("delete_view", {"error": "nope", "code": "forbidden"})
    assert client.delete("/api/saved-views/1").status_code == 403


# ── the router does not decide authorization ───────────────────────────────

def test_router_does_not_gate_on_role(calls):
    """A plain member reaches the service on both mutating routes.

    The creator-or-admin rule needs the row's `created_by`, so it lives in the service under
    the row lock. If the router ever grew a role check, this would 403 before the service ran
    and the two checks would drift.
    """
    calls("update_view", VIEW)
    calls("delete_view", {"ok": True, "id": 1})
    client = _client(fake_member)
    assert client.put("/api/saved-views/1", json={"name": "x"}).status_code == 200
    assert client.delete("/api/saved-views/1").status_code == 200
    assert [name for name, _, _ in calls.recorded] == ["update_view", "delete_view"]
