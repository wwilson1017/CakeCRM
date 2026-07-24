"""CRM router contract — minimal FastAPI app, service layer monkeypatched.

Covers 404/400 handling, the int `completed` wire contract, search-vs-list
routing, the demo/first-run endpoints, and the KEYLESS CSV import (no AI
provider exists anywhere in the app under test — the /import path never touches
one). Auth is a dependency-override, mirroring tests/test_router.py.
"""

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import service
from crm.router import router as crm_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


# ── 404s and 400s ─────────────────────────────────────────────────────────────

def test_get_missing_contact_404(client, monkeypatch):
    monkeypatch.setattr(service, "get_contact_detail", lambda cid: None)
    assert client.get("/api/crm/contacts/999").status_code == 404


def test_missing_deal_task_activity_404(client, monkeypatch):
    monkeypatch.setattr(service, "get_deal_detail", lambda did: None)
    monkeypatch.setattr(service, "complete_task", lambda tid: None)
    monkeypatch.setattr(service, "delete_activity", lambda aid: False)
    assert client.get("/api/crm/deals/5").status_code == 404
    assert client.put("/api/crm/tasks/5/complete").status_code == 404
    assert client.delete("/api/crm/activity/5").status_code == 404


def test_blank_required_fields_400(client):
    assert client.post("/api/crm/contacts", json={"name": "  "}).status_code == 400
    assert client.post("/api/crm/deals", json={"title": ""}).status_code == 400
    assert client.post("/api/crm/tasks", json={"title": ""}).status_code == 400
    assert client.post("/api/crm/activity", json={"activity": ""}).status_code == 400


def test_empty_update_400(client):
    assert client.put("/api/crm/contacts/1", json={}).status_code == 400
    assert client.put("/api/crm/deals/1", json={}).status_code == 400


# ── int `completed` wire contract ─────────────────────────────────────────────

def test_task_update_accepts_completed_zero(client, monkeypatch):
    seen = {}

    def fake_update_task(task_id, **kw):
        seen.update(kw)
        return {"id": task_id, "completed": kw.get("completed")}

    monkeypatch.setattr(service, "update_task", fake_update_task)
    resp = client.put("/api/crm/tasks/7", json={"completed": 0})
    assert resp.status_code == 200
    assert seen == {"completed": 0}  # int 0 reaches the service, not dropped as falsy


# ── search vs list routing ────────────────────────────────────────────────────

def test_contacts_query_routes_to_search(client, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "search_contacts",
                        lambda q, **kw: calls.append(("search", q)) or [{"id": 1}])
    monkeypatch.setattr(service, "list_contacts",
                        lambda **kw: calls.append(("list", None)) or {"contacts": []})
    client.get("/api/crm/contacts?q=acme")
    client.get("/api/crm/contacts")
    assert ("search", "acme") in calls and ("list", None) in calls


# ── Keyless CSV import (no provider anywhere in the app) ──────────────────────

def test_csv_import_keyless(client, monkeypatch):
    created = []
    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})
    csv_text = "Full Name,Email\nAda Lovelace,ada@x.io\n,noname@x.io\nGrace Hopper,grace@x.io\n"
    resp = client.post(
        "/api/crm/import",
        files={"file": ("contacts.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 2 and body["skipped"] == 1  # blank-name row skipped
    assert {c["name"] for c in created} == {"Ada Lovelace", "Grace Hopper"}
    # alias header "Full Name" resolved to name
    assert created[0]["email"] == "ada@x.io"


def test_csv_import_rejects_non_csv(client):
    resp = client.post(
        "/api/crm/import",
        files={"file": ("data.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert resp.status_code == 400


def test_csv_import_requires_name_column(client):
    resp = client.post(
        "/api/crm/import",
        files={"file": ("c.csv", io.BytesIO(b"email\nx@y.io\n"), "text/csv")},
    )
    assert resp.status_code == 400


def test_smart_import_rejects_oversized_upload(client):
    big = io.BytesIO(b"a" * (1_048_576 + 1))
    resp = client.post(
        "/api/crm/smart-import/parse",
        files={"file": ("big.txt", big, "text/plain")},
    )
    assert resp.status_code == 400


# ── Demo / first-run endpoints ────────────────────────────────────────────────

def test_demo_status_shape(client, monkeypatch):
    monkeypatch.setattr(service, "get_demo_status",
                        lambda: {"empty": True, "sample_data_loaded": False, "show_onboarding": True})
    body = client.get("/api/crm/demo-status").json()
    assert set(body) == {"empty", "sample_data_loaded", "show_onboarding"}


def test_load_sample_data_passthrough(client, monkeypatch):
    monkeypatch.setattr(service, "load_sample_data", lambda: {"ok": True, "seeded": True})
    assert client.post("/api/crm/load-sample-data").json() == {"ok": True, "seeded": True}


def test_clear_all_requires_confirmation_phrase(client, monkeypatch):
    monkeypatch.setattr(service, "clear_all", lambda: {"ok": True})
    assert client.post("/api/crm/clear-all", json={"confirmation": "nope"}).status_code == 400
    assert client.post("/api/crm/clear-all", json={"confirmation": "clear crm"}).status_code == 200


# ── Auth on every route ───────────────────────────────────────────────────────

def test_every_route_requires_auth():
    for route in crm_router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        dep_names = [d.call.__name__ for d in dependant.dependencies]
        assert "get_current_user" in dep_names, f"{route.path} is missing the auth dependency"
