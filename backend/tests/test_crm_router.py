"""CRM router contract — minimal FastAPI app, service layer monkeypatched.

Covers 404/400 handling, the int `completed` wire contract, search-vs-list
routing, the demo/first-run endpoints, and the KEYLESS CSV import (no AI
provider exists anywhere in the app under test — the /import path never touches
one). Auth is a dependency-override, mirroring tests/test_router.py.
"""

import io

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import provenance_service, service, touch_count_service
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
    monkeypatch.setattr(service, "count_search_contacts", lambda q, **kw: 1)
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


def test_smart_import_parse_csv_keyless(client, monkeypatch):
    # No provider is configured anywhere; a CSV with a name column parses
    # deterministically and never touches AI (keyless acceptance, UI path).
    from crm import smart_import
    monkeypatch.setattr(smart_import, "get_ai_provider",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI called")))
    csv_text = "Name,Email\nAda,ada@x.io\n"
    resp = client.post(
        "/api/crm/smart-import/parse",
        files={"file": ("c.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_used"] is False
    assert any(c["name"] == "Ada" for c in body["contacts"])


def test_smart_import_confirm_happy_and_validator(client, monkeypatch):
    created = []
    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})
    ok = client.post("/api/crm/smart-import/confirm",
                     json={"contacts": [{"name": "Ada", "email": "a@x.io"}]})
    assert ok.status_code == 200 and ok.json()["imported"] == 1
    # empty list rejected by the SmartImportConfirm validator (422)
    assert client.post("/api/crm/smart-import/confirm", json={"contacts": []}).status_code == 422


def test_smart_import_confirm_falls_back_to_email_as_name(client, monkeypatch):
    created = []
    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})
    # a nameless-but-emailed entry (kept by the vCard/AI parsers) must import, not be skipped
    resp = client.post("/api/crm/smart-import/confirm", json={"contacts": [{"email": "ada@x.io"}]})
    assert resp.status_code == 200 and resp.json()["imported"] == 1
    assert created[0]["name"] == "ada@x.io"


def test_create_deal_fk_violation_returns_400(client, monkeypatch):
    import psycopg2
    def _boom(**kw):
        raise psycopg2.errors.ForeignKeyViolation("dead contact")
    monkeypatch.setattr(service, "create_deal", _boom)
    resp = client.post("/api/crm/deals", json={"title": "Deal", "contact_id": 999999})
    assert resp.status_code == 400  # not a raw 500


# ── Search pagination is forwarded (fixes the silent 20-row cap) ──────────────

def test_contacts_search_forwards_limit_offset_and_total(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "search_contacts",
                        lambda q, **kw: seen.update(kw) or [{"id": 1}])
    monkeypatch.setattr(service, "count_search_contacts", lambda q, **kw: 137)
    body = client.get("/api/crm/contacts?q=acme&limit=50&offset=100").json()
    assert seen["limit"] == 50 and seen["offset"] == 100  # forwarded, not defaulted to 20
    assert body["total"] == 137  # real count, not len(contacts)


# ── Unlinking a contact: explicit null reaches the service ────────────────────

def test_deal_update_can_clear_contact(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "update_deal",
                        lambda did, **kw: seen.update(kw) or {"id": did})
    client.put("/api/crm/deals/5", json={"contact_id": None})
    assert "contact_id" in seen and seen["contact_id"] is None  # explicit null survives


def test_deal_update_omitted_contact_not_touched(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "update_deal",
                        lambda did, **kw: seen.update(kw) or {"id": did})
    client.put("/api/crm/deals/5", json={"title": "Renamed"})
    assert seen == {"title": "Renamed"}  # contact_id not sent → not in the update


def test_task_update_can_clear_contact(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "update_task",
                        lambda tid, **kw: seen.update(kw) or {"id": tid})
    client.put("/api/crm/tasks/9", json={"contact_id": None})
    assert seen.get("contact_id") is None and "contact_id" in seen


# ── Demo / first-run endpoints ────────────────────────────────────────────────

def test_demo_status_shape(client, monkeypatch):
    monkeypatch.setattr(service, "get_demo_status",
                        lambda: {"empty": True, "sample_data_loaded": False,
                                 "show_onboarding": True, "ai_key_prompt_dismissed": False})
    body = client.get("/api/crm/demo-status").json()
    assert set(body) == {"empty", "sample_data_loaded", "show_onboarding", "ai_key_prompt_dismissed"}


def test_load_sample_data_passthrough(client, monkeypatch):
    monkeypatch.setattr(service, "load_sample_data", lambda: {"ok": True, "seeded": True})
    assert client.post("/api/crm/load-sample-data").json() == {"ok": True, "seeded": True}


def test_dismiss_ai_prompt_passthrough(client, monkeypatch):
    monkeypatch.setattr(service, "dismiss_ai_prompt", lambda: {"ok": True})
    assert client.post("/api/crm/dismiss-ai-prompt").json() == {"ok": True}


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


# ── Companies (issue #13) ─────────────────────────────────────────────────────

def test_company_missing_404s(client, monkeypatch):
    monkeypatch.setattr(service, "get_company_detail", lambda cid: None)
    monkeypatch.setattr(service, "update_company", lambda cid, **kw: None)
    monkeypatch.setattr(service, "delete_company", lambda cid: False)
    assert client.get("/api/crm/companies/999").status_code == 404
    assert client.put("/api/crm/companies/999", json={"name": "X"}).status_code == 404
    assert client.delete("/api/crm/companies/999").status_code == 404


def test_company_create_blank_name_400(client):
    assert client.post("/api/crm/companies", json={"name": "  "}).status_code == 400


def test_company_update_empty_400(client):
    assert client.put("/api/crm/companies/1", json={}).status_code == 400


def test_company_update_blank_name_400(client):
    # A whitespace-only name is truthy but empty — must 400, not silently blank the record.
    assert client.put("/api/crm/companies/1", json={"name": "   "}).status_code == 400


def test_companies_query_routes_to_search(client, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "search_companies",
                        lambda q, **kw: calls.append(("search", q, kw.get("limit"), kw.get("offset"))) or [{"id": 1}])
    monkeypatch.setattr(service, "count_search_companies", lambda q, **kw: 1)
    monkeypatch.setattr(service, "list_companies",
                        lambda **kw: calls.append(("list", None, None, None)) or {"companies": []})
    client.get("/api/crm/companies?q=acme&limit=5&offset=10")
    client.get("/api/crm/companies")
    # search forwards pagination (no silent 20-row cap)
    assert ("search", "acme", 5, 10) in calls
    assert ("list", None, None, None) in calls


def test_company_create_duplicate_name_400(client, monkeypatch):
    def raise_unique(**kw):
        raise psycopg2.errors.UniqueViolation()
    monkeypatch.setattr(service, "create_company", raise_unique)
    resp = client.post("/api/crm/companies", json={"name": "Acme"})
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_company_update_duplicate_name_400(client, monkeypatch):
    def raise_unique(cid, **kw):
        raise psycopg2.errors.UniqueViolation()
    monkeypatch.setattr(service, "update_company", raise_unique)
    assert client.put("/api/crm/companies/1", json={"name": "Acme"}).status_code == 400


def test_contact_create_invalid_company_400(client, monkeypatch):
    def raise_fk(**kw):
        raise psycopg2.errors.ForeignKeyViolation()
    monkeypatch.setattr(service, "create_contact", raise_fk)
    resp = client.post("/api/crm/contacts", json={"name": "Ana", "company_id": 999})
    assert resp.status_code == 400
    assert "company" in resp.json()["detail"].lower()


def test_contact_update_invalid_company_400(client, monkeypatch):
    def raise_fk(cid, **kw):
        raise psycopg2.errors.ForeignKeyViolation()
    monkeypatch.setattr(service, "update_contact", raise_fk)
    assert client.put("/api/crm/contacts/1", json={"company_id": 999}).status_code == 400


def test_deal_create_invalid_company_400(client, monkeypatch):
    def raise_fk(**kw):
        raise psycopg2.errors.ForeignKeyViolation()
    monkeypatch.setattr(service, "create_deal", raise_fk)
    resp = client.post("/api/crm/deals", json={"title": "D", "company_id": 999})
    assert resp.status_code == 400


def test_deal_update_invalid_company_400(client, monkeypatch):
    def raise_fk(did, **kw):
        raise psycopg2.errors.ForeignKeyViolation()
    monkeypatch.setattr(service, "update_deal", raise_fk)
    assert client.put("/api/crm/deals/1", json={"company_id": 999}).status_code == 400


def test_contact_update_unlink_company_explicit_null(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "update_contact",
                        lambda cid, **kw: seen.update(kw) or {"id": cid})
    resp = client.put("/api/crm/contacts/1", json={"company_id": None})
    assert resp.status_code == 200
    # explicit null survives the exclude_unset filter → unlinks
    assert seen == {"company_id": None}


def test_deal_update_unlink_company_explicit_null(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "update_deal",
                        lambda did, **kw: seen.update(kw) or {"id": did})
    resp = client.put("/api/crm/deals/1", json={"company_id": None})
    assert resp.status_code == 200
    assert seen == {"company_id": None}


def test_contact_update_omitted_company_id_not_sent(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "update_contact",
                        lambda cid, **kw: seen.update(kw) or {"id": cid})
    client.put("/api/crm/contacts/1", json={"name": "Ana"})
    # omission != explicit null: company_id is absent when the client didn't send it
    assert "company_id" not in seen and seen == {"name": "Ana"}


def test_deal_update_omitted_company_id_not_sent(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "update_deal",
                        lambda did, **kw: seen.update(kw) or {"id": did})
    client.put("/api/crm/deals/1", json={"title": "D"})
    assert "company_id" not in seen and seen == {"title": "D"}


# ── AI touch counts + provenance (issue #16) — HTTP wiring through the ASGI stack ──

def test_touch_count_backfill_passes_scope_and_force(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(touch_count_service, "start_backfill",
                        lambda scope, force: seen.update(scope=scope, force=force)
                        or {"started": True, "scope": scope, "queued": 0})
    r = client.post("/api/crm/deals/touch-count/backfill?scope=all&force=true")
    assert r.status_code == 200 and r.json()["started"] is True
    assert seen == {"scope": "all", "force": True}


def test_touch_count_backfill_defaults_scope_null(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(touch_count_service, "start_backfill",
                        lambda scope, force: seen.update(scope=scope) or {"started": True})
    client.post("/api/crm/deals/touch-count/backfill")
    assert seen == {"scope": "null"}


def test_touch_count_backfill_bad_scope_422(client):
    # The Query pattern ^(null|all)$ rejects it before the handler runs.
    assert client.post("/api/crm/deals/touch-count/backfill?scope=everything").status_code == 422


def test_touch_count_backfill_status_200(client, monkeypatch):
    monkeypatch.setattr(touch_count_service, "backfill_status",
                        lambda: {"remaining_null": 3, "queue_depth": 0})
    r = client.get("/api/crm/deals/touch-count/backfill/status")
    assert r.status_code == 200 and r.json()["remaining_null"] == 3


def test_get_provenance_200_shape(client, monkeypatch):
    monkeypatch.setattr(provenance_service, "get_provenance",
                        lambda et, eid: [{"field_name": "phone", "stale": False}])
    r = client.get("/api/crm/provenance/contact/1")
    assert r.status_code == 200 and r.json() == {"provenance": [{"field_name": "phone", "stale": False}], "count": 1}


def test_get_provenance_bad_entity_400(client, monkeypatch):
    def _raise(et, eid):
        raise ValueError("Invalid entity_type: widget")
    monkeypatch.setattr(provenance_service, "get_provenance", _raise)
    assert client.get("/api/crm/provenance/widget/1").status_code == 400


def test_confirm_provenance_200_confirmed(client, monkeypatch):
    monkeypatch.setattr(provenance_service, "confirm",
                        lambda et, eid, fn: {"field_name": fn, "confirmed_at": "c"})
    r = client.post("/api/crm/provenance/contact/1/confirm", json={"field_name": "phone"})
    assert r.status_code == 200 and r.json() == {"confirmed": True, "provenance": {"field_name": "phone", "confirmed_at": "c"}}


def test_confirm_provenance_200_stale(client, monkeypatch):
    monkeypatch.setattr(provenance_service, "confirm", lambda et, eid, fn: {"stale": True})
    r = client.post("/api/crm/provenance/deal/1/confirm", json={"field_name": "stage"})
    assert r.status_code == 200 and r.json() == {"confirmed": False, "stale": True}


def test_confirm_provenance_404_when_no_row(client, monkeypatch):
    monkeypatch.setattr(provenance_service, "confirm", lambda et, eid, fn: None)
    r = client.post("/api/crm/provenance/contact/1/confirm", json={"field_name": "phone"})
    assert r.status_code == 404
