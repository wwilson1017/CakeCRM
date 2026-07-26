"""Reminders router contract — the error-code → HTTP-status mapping (issue #6).

Minimal FastAPI app; auth overridden; the service layer monkeypatched, so nothing
touches a DB. Verifies structured service codes map to 404/409/400 and happy paths
return 200 — the mapping used to be message-substring based, which this pins down.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from reminders import router as router_mod
from reminders.router import router as reminders_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(reminders_router, prefix="/api/reminders")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


def _svc(monkeypatch, name, result):
    monkeypatch.setattr(router_mod.service, name, lambda *a, **k: result)


def test_list_ok(client, monkeypatch):
    _svc(monkeypatch, "list_reminders", [{"id": "r1"}])
    r = client.get("/api/reminders?status=pending")
    assert r.status_code == 200
    assert r.json() == {"reminders": [{"id": "r1"}], "count": 1}


def test_create_ok(client, monkeypatch):
    _svc(monkeypatch, "create_reminder", {"ok": True, "id": "r1"})
    r = client.post("/api/reminders", json={"message": "m", "due_at": "2026-07-25T09:00:00Z"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_create_bad_request_code_maps_400(client, monkeypatch):
    _svc(monkeypatch, "create_reminder", {"error": "message is required", "code": "bad_request"})
    r = client.post("/api/reminders", json={"message": "m", "due_at": "2026-07-25T09:00:00Z"})
    assert r.status_code == 400


def test_create_bad_recurrence_maps_400(client):
    # Unparseable recurrence is rejected by the router before the service.
    r = client.post("/api/reminders", json={"message": "m", "due_at": "2026-07-25T09:00:00Z", "recurrence": "whenever"})
    assert r.status_code == 400


def test_update_not_found_maps_404(client, monkeypatch):
    _svc(monkeypatch, "update_reminder", {"error": "reminder not found", "code": "not_found"})
    r = client.patch("/api/reminders/r1", json={"message": "new"})
    assert r.status_code == 404


def test_update_conflict_maps_409(client, monkeypatch):
    _svc(monkeypatch, "update_reminder", {"error": "only pending reminders can be edited", "code": "conflict"})
    r = client.patch("/api/reminders/r1", json={"message": "new"})
    assert r.status_code == 409


def test_update_no_fields_maps_400(client):
    r = client.patch("/api/reminders/r1", json={})
    assert r.status_code == 400


def test_cancel_conflict_maps_409(client, monkeypatch):
    _svc(monkeypatch, "cancel_reminder", {"error": "reminder already cancelled", "code": "conflict"})
    r = client.post("/api/reminders/r1/cancel")
    assert r.status_code == 409


def test_delete_pending_maps_409(client, monkeypatch):
    _svc(monkeypatch, "delete_reminder", {"error": "cancel a pending reminder before deleting it", "code": "conflict"})
    r = client.delete("/api/reminders/r1")
    assert r.status_code == 409


def test_delete_ok(client, monkeypatch):
    _svc(monkeypatch, "delete_reminder", {"ok": True, "id": "r1"})
    r = client.delete("/api/reminders/r1")
    assert r.status_code == 200
