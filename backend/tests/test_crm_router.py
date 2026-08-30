"""CRM router contract — minimal FastAPI app, service layer monkeypatched.

Covers 404/400 handling, the int `completed` wire contract, search-vs-list
routing, the demo/first-run endpoints, and the KEYLESS CSV import (no AI
provider exists anywhere in the app under test — the /import path never touches
one). Auth is a dependency-override, mirroring tests/test_router.py.
"""

import io

import psycopg2
import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import provenance_service, scoring_service, service, touch_count_service
from crm.router import router as crm_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
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


def test_csv_import_batch_resolves_and_links_companies(client, monkeypatch):
    """Issue #35: an import must populate Companies, in ONE batched lookup."""
    created = []
    resolver_calls = []

    def _resolve(names):
        resolver_calls.append(list(names))
        return {"Acme Corp": 5}

    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})
    monkeypatch.setattr(service, "resolve_or_create_company_ids", _resolve)
    csv_text = (
        "Name,Company\n"
        "Ada Lovelace,Acme Corp\n"
        ",Ghost Co\n"           # blank name -> skipped, must NOT reach the resolver
        "Bob Stone,\n"          # no company
    )
    resp = client.post(
        "/api/crm/import",
        files={"file": ("contacts.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 2 and resp.json()["skipped"] == 1
    # ONE batched call for the whole file, and the skipped row's company is absent
    # (it must not leave an auto-created orphan company behind)
    assert resolver_calls == [["Acme Corp", ""]]
    by_name = {c["name"]: c for c in created}
    assert by_name["Ada Lovelace"]["company_id"] == 5
    assert by_name["Ada Lovelace"]["company"] == "Acme Corp"  # legacy text preserved
    assert by_name["Bob Stone"]["company_id"] is None


def test_csv_import_unnamed_column_is_not_read_as_a_field(client, monkeypatch):
    """csv.DictReader keys an UNNAMED header cell as "", which the old
    `row.get(col or "", "")` idiom read whenever a field had no mapped column —
    silently importing an unrelated column (and, post-#35, auto-creating a
    company from it)."""
    created = []
    resolver_calls = []
    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})
    monkeypatch.setattr(service, "resolve_or_create_company_ids",
                        lambda names: resolver_calls.append(list(names)) or {})
    # no Company header at all; the third column is unnamed
    csv_text = "Name,Email,,Phone\nAda,ada@x.io,STRAY VALUE,555\n"
    resp = client.post(
        "/api/crm/import",
        files={"file": ("c.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 200 and resp.json()["imported"] == 1
    assert created[0]["company"] == ""          # not "STRAY VALUE"
    assert resolver_calls == [[""]]             # and no company auto-created
    assert created[0]["title"] == "" and created[0]["notes"] == ""


def test_csv_import_survives_batch_resolver_failure(client, monkeypatch):
    """A poison company cell (e.g. a NUL byte) must not 500 the whole import —
    it degrades to per-row resolution inside create_contact."""
    created = []
    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})

    def _boom(names):
        raise ValueError("A string literal cannot contain NUL (0x00) characters.")

    monkeypatch.setattr(service, "resolve_or_create_company_ids", _boom)
    resp = client.post(
        "/api/crm/import",
        files={"file": ("c.csv", io.BytesIO(b"Name,Company\nAda,Acme\n"), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1
    assert created[0]["company_id"] is None  # fell back; create_contact resolves


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


def test_smart_import_confirm_passes_resolved_company_id(client, monkeypatch):
    """Issue #35: the smart-import write path links companies too, batched."""
    created = []
    resolver_calls = []

    def _resolve(names):
        resolver_calls.append(list(names))
        return {"Acme Corp": 5}

    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})
    monkeypatch.setattr(service, "resolve_or_create_company_ids", _resolve)
    resp = client.post("/api/crm/smart-import/confirm", json={"contacts": [
        {"name": "Ada", "company": "Acme Corp"},
        {"company": "Ghost Co"},   # no name/email/phone -> skipped, excluded from batch
    ]})
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1 and resp.json()["skipped"] == 1
    assert resolver_calls == [["Acme Corp"]]
    assert created[0]["company_id"] == 5


def test_smart_import_confirm_survives_batch_resolver_failure(client, monkeypatch):
    """Counterpart to the CSV case: both import loops share one degrade path, so
    both are covered — a batch failure must not fail the whole import."""
    created = []
    monkeypatch.setattr(service, "create_contact",
                        lambda **kw: created.append(kw) or {"id": len(created)})

    def _boom(names):
        raise ValueError("A string literal cannot contain NUL (0x00) characters.")

    monkeypatch.setattr(service, "resolve_or_create_company_ids", _boom)
    resp = client.post("/api/crm/smart-import/confirm",
                       json={"contacts": [{"name": "Ada", "company": "Acme"}]})
    assert resp.status_code == 200 and resp.json()["imported"] == 1
    assert created[0]["company_id"] is None  # fell back; create_contact resolves


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
        # require_admin depends on get_current_user, so either one authenticates the
        # route. Which routes are admin-gated is pinned separately, in
        # tests/test_route_authz.py.
        assert {"get_current_user", "require_admin"} & set(dep_names), (
            f"{route.path} is missing the auth dependency"
        )


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


# ── POST /companies/resolve — get-or-create for the inline picker (issue #123) ─

def test_company_resolve_delegates_to_the_shared_resolver(client, monkeypatch):
    """The whole point of the route: it must not match the name itself.

    The normalization is the uq_companies_name_ci index expression, and the primitive's
    own docstring warns that Python's case-folding can disagree with the database's
    LOWER() — so a second spelling of the rule in the router is how a company we just
    created gets stranded and a duplicate appears anyway.
    """
    seen = {}
    monkeypatch.setattr(service, "resolve_or_create_company_ids",
                        lambda names: seen.update(names=names) or {names[0]: 7})
    monkeypatch.setattr(service, "get_company", lambda cid: {"id": cid, "name": "Acme"})

    resp = client.post("/api/crm/companies/resolve", json={"name": "acme"})

    assert resp.status_code == 200
    assert resp.json() == {"id": 7, "name": "Acme"}
    assert seen["names"] == ["acme"]


def test_company_resolve_passes_the_name_through_untrimmed(client, monkeypatch):
    """The primitive's contract is {raw spelling exactly as passed: id}, so the route
    looks the result up by the same string it sent. Trimming here would be a second
    owner of a rule that belongs to SQL — and would break the lookup if the two
    disagreed about what counts as whitespace."""
    seen = {}
    monkeypatch.setattr(service, "resolve_or_create_company_ids",
                        lambda names: seen.update(names=names) or {names[0]: 3})
    monkeypatch.setattr(service, "get_company", lambda cid: {"id": cid, "name": "Acme"})

    assert client.post("/api/crm/companies/resolve", json={"name": "  Acme  "}).status_code == 200
    assert seen["names"] == ["  Acme  "]


def test_company_resolve_blank_name_400(client, monkeypatch):
    """Mirrors POST /companies' guard rather than inventing its own."""
    def unreached(names):
        raise AssertionError("the resolver must not be called for a blank name")
    monkeypatch.setattr(service, "resolve_or_create_company_ids", unreached)

    for name in ("", "   ", "\t\n"):
        resp = client.post("/api/crm/companies/resolve", json={"name": name})
        assert resp.status_code == 400, name
        assert "required" in resp.json()["detail"].lower()


def test_company_resolve_unresolvable_is_409_not_a_half_answer(client, monkeypatch):
    """The primitive yields no id only in its documented race (the row was deleted
    between its two statements). Nothing was linked, so the route must refuse rather
    than return something the form would store as a company_id."""
    monkeypatch.setattr(service, "resolve_or_create_company_ids", lambda names: {})
    monkeypatch.setattr(service, "get_company", lambda cid: None)

    assert client.post("/api/crm/companies/resolve", json={"name": "Acme"}).status_code == 409


def test_company_resolve_missing_row_is_409(client, monkeypatch):
    """Same refusal when the id resolves but the read-back finds nothing."""
    monkeypatch.setattr(service, "resolve_or_create_company_ids", lambda names: {names[0]: 9})
    monkeypatch.setattr(service, "get_company", lambda cid: None)

    assert client.post("/api/crm/companies/resolve", json={"name": "Acme"}).status_code == 409


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


def test_touch_count_evidence_200_shape(client, monkeypatch):
    """The #56 drill-down endpoint, through the real ASGI stack. Four path segments, so it
    must not be swallowed by /deals/{deal_id} or the literal touch-count routes."""
    seen = {}

    def fake_evidence(deal_id):
        seen["deal_id"] = deal_id
        return {"deal_id": deal_id, "open": True, "stage": "qualified",
                "ai_touch_count": 2, "computed_at": "2026-08-19T10:00:00+00:00",
                "verdict_state": "current", "counted": 2, "evaluated": 3,
                "truncated": False,
                "events": [{"source": "note", "source_id": 11,
                            "event_at": "2026-08-18T00:00:00+00:00",
                            "line": "2026-08-18 [note] called", "state": "touch",
                            "reason": ""}]}

    monkeypatch.setattr(touch_count_service, "get_touch_evidence", fake_evidence)
    r = client.get("/api/crm/deals/7/touch-count/evidence")
    assert r.status_code == 200
    assert seen["deal_id"] == 7                      # the path param really arrived
    body = r.json()
    assert body["verdict_state"] == "current" and body["counted"] == 2
    assert body["events"][0]["state"] == "touch"


def test_touch_count_evidence_404_for_a_missing_deal(client, monkeypatch):
    monkeypatch.setattr(touch_count_service, "get_touch_evidence", lambda deal_id: None)
    assert client.get("/api/crm/deals/999/touch-count/evidence").status_code == 404


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


def test_update_deal_refusal_is_a_400_not_a_500(client, monkeypatch):
    """A stage change on an archived deal is a refusal the caller can act on (issue
    #22). Letting the ValueError escape gave the Kanban drag an HTTP 500."""
    def refuse(deal_id, **kw):
        raise ValueError("Cannot change the stage of archived deal #3 — restore it first")

    monkeypatch.setattr(service, "update_deal", refuse)
    r = client.put("/api/crm/deals/3", json={"stage": "won"})
    assert r.status_code == 400
    assert "restore it first" in r.json()["detail"]


# ── Lead scores (issue #18) ───────────────────────────────────────────────────

def test_contacts_sort_forwarded_browse_and_search(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "list_contacts",
                        lambda **kw: seen.update({"browse": kw}) or {"contacts": [], "total": 0})
    monkeypatch.setattr(service, "search_contacts",
                        lambda *a, **kw: seen.update({"search": kw}) or [])
    monkeypatch.setattr(service, "count_search_contacts", lambda *a, **kw: 0)
    client.get("/api/crm/contacts?sort=lead_score")
    assert seen["browse"]["sort"] == "lead_score"
    client.get("/api/crm/contacts?q=acme&sort=lead_score")
    assert seen["search"]["sort"] == "lead_score"  # sort honored during search too (P1.8)


def test_contacts_sort_defaults_to_updated_at(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "list_contacts",
                        lambda **kw: seen.update(kw) or {"contacts": [], "total": 0})
    client.get("/api/crm/contacts")
    assert seen["sort"] == "updated_at"


def test_scores_backfill_happy_path(client, monkeypatch):
    monkeypatch.setattr(scoring_service, "backfill_scores",
                        lambda scope: {"deals_scored": 2, "contacts_scored": 3, "errors": 0, "capped": False})
    r = client.post("/api/crm/scores/backfill?scope=all")
    assert r.status_code == 200
    assert r.json()["deals_scored"] == 2 and r.json()["contacts_scored"] == 3


def test_scores_backfill_default_scope_null(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(scoring_service, "backfill_scores",
                        lambda scope: seen.update({"scope": scope}) or {"deals_scored": 0, "contacts_scored": 0, "errors": 0, "capped": False})
    assert client.post("/api/crm/scores/backfill").status_code == 200
    assert seen["scope"] == "null"


def test_scores_backfill_bad_scope_rejected(client):
    # Query(pattern="^(null|all)$") rejects out-of-pattern values before the handler (422).
    assert client.post("/api/crm/scores/backfill?scope=everything").status_code == 422


# ── POST /deals/bulk-move (#55) ───────────────────────────────────────────────

def test_bulk_move_returns_the_service_shape_verbatim(client, monkeypatch):
    seen = {}
    payload = {"ok": True, "updated": 2, "updated_ids": [1, 2], "errors": ["Deal 7 not found"]}
    monkeypatch.setattr(service, "bulk_move_deals",
                        lambda ids, stage: seen.update(ids=ids, stage=stage) or payload)
    r = client.post("/api/crm/deals/bulk-move", json={"deal_ids": [1, 2, 7], "stage": "won"})
    assert r.status_code == 200
    assert r.json() == payload
    assert seen == {"ids": [1, 2, 7], "stage": "won"}


def test_bulk_move_refusal_is_a_200_body_not_an_error_status(client, monkeypatch):
    """The board's rejected-vs-unconfirmed split depends on this: only transport and
    5xx failures may throw at the client, so a refusal has to arrive as ok:false/200."""
    monkeypatch.setattr(service, "bulk_move_deals",
                        lambda ids, stage: {"ok": False, "updated": 0, "updated_ids": [],
                                            "errors": ["Invalid stage: nope"]})
    r = client.post("/api/crm/deals/bulk-move", json={"deal_ids": [1], "stage": "nope"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_bulk_move_rejects_a_malformed_body(client):
    assert client.post("/api/crm/deals/bulk-move",
                       json={"deal_ids": "all", "stage": "won"}).status_code == 422
    assert client.post("/api/crm/deals/bulk-move", json={"deal_ids": [1]}).status_code == 422


def test_bulk_move_refuses_ids_that_are_not_strictly_integers(client, monkeypatch):
    """Pydantic's LAX int would coerce JSON `true` to 1, `1.0` to 1 and "3" to 3 — a
    malformed body would silently move deal #1. StrictInt rejects all three at the model,
    which is the only layer that can: by the time the service runs, the bool IS an int."""
    def explode(*a, **k):
        raise AssertionError("a non-strict deal id reached the service")
    monkeypatch.setattr(service, "bulk_move_deals", explode)
    for bad in ([True], [1.5], ["3"]):
        assert client.post("/api/crm/deals/bulk-move",
                           json={"deal_ids": bad, "stage": "won"}).status_code == 422


def test_bulk_move_path_is_not_shadowed_by_the_deal_detail_route(client, monkeypatch):
    """"bulk-move" must reach the bulk handler, not POST /deals/{id}-style routing."""
    monkeypatch.setattr(service, "bulk_move_deals",
                        lambda ids, stage: {"ok": True, "updated": 1, "updated_ids": [3], "errors": []})
    r = client.post("/api/crm/deals/bulk-move", json={"deal_ids": [3], "stage": "lead"})
    assert r.status_code == 200 and r.json()["updated_ids"] == [3]


# ── archived-deal reachability (issue #83) ────────────────────────────────────

def test_deals_board_defaults_to_live_only(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "get_pipeline", lambda **kw: seen.update(kw) or {"deals": []})
    assert client.get("/api/crm/deals").status_code == 200
    assert seen == {"include_archived": False}


def test_deals_board_passes_include_archived_through(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "get_pipeline", lambda **kw: seen.update(kw) or {"deals": []})
    assert client.get("/api/crm/deals?include_archived=true").status_code == 200
    assert seen == {"include_archived": True}


def test_include_archived_is_refused_not_ignored_with_other_filters(client, monkeypatch):
    """`list_deals` is a different service function and keeps the sweep, so honoring the
    flag there would be a second hole. Silently dropping an advertised flag is worse than
    a 400 — the caller would believe it had asked for archived deals and got none."""
    def explode(*a, **k):
        raise AssertionError("the filtered branch ran with include_archived set")
    monkeypatch.setattr(service, "list_deals", explode)
    assert client.get("/api/crm/deals?stage=lead&include_archived=true").status_code == 400
    assert client.get("/api/crm/deals?contact_id=4&include_archived=true").status_code == 400


def test_filtered_deal_list_still_works_without_the_flag(client, monkeypatch):
    monkeypatch.setattr(service, "list_deals", lambda **kw: [{"id": 1}])
    r = client.get("/api/crm/deals?stage=lead")
    assert r.status_code == 200 and r.json()["count"] == 1


def test_restore_deal_unarchives_and_returns_the_fresh_row(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        service, "archive_deal",
        lambda did, **kw: seen.update(deal_id=did, **kw) or {"id": did, "archived_at": None},
    )
    r = client.post("/api/crm/deals/7/restore")
    assert r.status_code == 200
    # The route must un-archive, never archive — the flag is the whole contract.
    assert seen == {"deal_id": 7, "archived": False}
    # The board patches this row in place instead of trusting a refetch that can fail.
    assert r.json() == {"id": 7, "archived_at": None}


def test_restore_missing_deal_404(client, monkeypatch):
    monkeypatch.setattr(service, "archive_deal", lambda did, **kw: None)
    assert client.post("/api/crm/deals/999/restore").status_code == 404


def test_restore_path_is_not_shadowed_by_the_touch_count_routes(client, monkeypatch):
    """/deals/{id}/restore and /deals/touch-count/backfill have the same segment count."""
    monkeypatch.setattr(service, "archive_deal", lambda did, **kw: {"id": did})
    assert client.post("/api/crm/deals/3/restore").json()["id"] == 3


def test_there_is_no_archive_route(client):
    """Scope ceiling: view + restore only. Archiving stays an assistant verb until a UI
    affordance for it is designed — an unreachable write route is risk for nothing."""
    assert client.post("/api/crm/deals/3/archive").status_code == 404


def test_contact_id_zero_is_a_filter_not_a_fallthrough(client, monkeypatch):
    """`?contact_id=0` is falsy, so a truthiness test would route it to the BOARD branch —
    returning the whole pipeline for a request that asked to filter, and slipping the
    include_archived refusal at the same time."""
    def explode(**kw):
        raise AssertionError("contact_id=0 reached the board branch")
    monkeypatch.setattr(service, "get_pipeline", explode)
    monkeypatch.setattr(service, "list_deals", lambda **kw: [])
    assert client.get("/api/crm/deals?contact_id=0").status_code == 200
    assert client.get("/api/crm/deals?contact_id=0&include_archived=true").status_code == 400


# ── #77: the list pages' keyset assembly parameters ──────────────────────────

def test_list_params_reach_the_service_with_backward_compatible_defaults(client, monkeypatch):
    """`after_id`/`sort` are forwarded, and an omitting caller sees the old behaviour."""
    seen: dict = {}

    def fake_tasks(**kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(service, "list_tasks", fake_tasks)

    assert client.get("/api/crm/tasks?after_id=500&sort=id&limit=501").status_code == 200
    assert (seen["after_id"], seen["sort"], seen["limit"]) == (500, "id", 501)

    seen.clear()
    assert client.get("/api/crm/tasks").status_code == 200
    # Every pre-#77 caller keeps the historical order and no cursor.
    assert seen["after_id"] is None and seen["sort"] == "due"


def test_contacts_and_companies_forward_the_cursor(client, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(service, "list_contacts", lambda **kw: seen.update(kw) or {"contacts": [], "total": 0})
    monkeypatch.setattr(service, "list_companies", lambda **kw: seen.update(kw) or {"companies": [], "total": 0})

    assert client.get("/api/crm/contacts?after_id=42&sort=id").status_code == 200
    assert (seen["after_id"], seen["sort"]) == (42, "id")

    seen.clear()
    assert client.get("/api/crm/companies?after_id=42&sort=id").status_code == 200
    assert (seen["after_id"], seen["sort"]) == (42, "id")


def test_a_cursor_against_a_mutable_order_is_a_400_not_a_500(client, monkeypatch):
    """The service refuses the pairing; the route must surface it as a client error.

    Left unhandled this is a ValueError → 500, which reads as "the server is broken"
    rather than "that request does not mean anything".
    """
    def boom(**kw):
        raise ValueError("after_id is only valid with sort='id'")

    for name, path in (
        ("list_tasks", "/api/crm/tasks?after_id=5&sort=due"),
        ("list_contacts", "/api/crm/contacts?after_id=5&sort=name"),
        ("list_companies", "/api/crm/companies?after_id=5&sort=name"),
    ):
        monkeypatch.setattr(service, name, boom)
        res = client.get(path)
        assert res.status_code == 400, path
        assert "sort='id'" in res.json()["detail"]


def test_a_negative_cursor_is_rejected_by_validation(client):
    assert client.get("/api/crm/tasks?after_id=-1").status_code == 422


def test_a_cursor_is_refused_on_the_search_branch(client, monkeypatch):
    """search_* has no cursor, so accepting one would silently return page one forever.

    That is the same failure `_check_assembly_cursor` exists to prevent, and it looks
    identical to a client stuck in a loop — so the route refuses instead of ignoring.
    """
    monkeypatch.setattr(service, "search_contacts", lambda *a, **k: [])
    monkeypatch.setattr(service, "count_search_contacts", lambda *a, **k: 0)
    monkeypatch.setattr(service, "search_companies", lambda *a, **k: [])
    monkeypatch.setattr(service, "count_search_companies", lambda *a, **k: 0)

    for path in ("/api/crm/contacts", "/api/crm/companies"):
        res = client.get(f"{path}?q=acme&after_id=5&sort=id")
        assert res.status_code == 400, path
        assert "after_id" in res.json()["detail"]
        # …and a plain search still works.
        assert client.get(f"{path}?q=acme").status_code == 200, path


def test_an_out_of_range_cursor_is_a_422_not_a_500(client):
    """id columns are int4. Without an upper bound Postgres raises a range error that is
    NOT a ValueError, so it would escape the route's handler as an unhandled 500."""
    too_big = 2_147_483_648
    for path in ("/api/crm/tasks", "/api/crm/contacts", "/api/crm/companies"):
        assert client.get(f"{path}?after_id={too_big}&sort=id").status_code == 422, path


# ── POST /deals/:id/mark-lost (issue #128) ────────────────────────────────────
# The only human writer of `lost_reason`. `_DEAL_USER_WRITABLE` excludes the column
# on purpose, so these pin that the route reaches the lifecycle verb (and carries the
# author) rather than the general update path.

def test_mark_lost_passes_reason_and_author(client, monkeypatch):
    seen = {}

    def fake(deal_id, lost_reason="", author_id=None):
        seen.update(deal_id=deal_id, lost_reason=lost_reason, author_id=author_id)
        return {"id": deal_id, "stage": "lost", "lost_reason": lost_reason}

    monkeypatch.setattr(service, "mark_deal_lost", fake)
    res = client.post(
        "/api/crm/deals/7/mark-lost",
        json={"lost_reason": "Chose a competitor.\nPrice was the deciding factor."},
    )

    assert res.status_code == 200
    assert seen["deal_id"] == 7
    # Newlines survive the round trip — the whole point of a multi-line reason.
    assert "\n" in seen["lost_reason"]
    # Authorship, not ownership (#60): a reason a rep typed must credit that rep, or
    # per-rep activity undercounts them. FAKE_ADMIN's id.
    assert seen["author_id"] == 1


def test_mark_lost_with_a_blank_reason_still_uses_the_lifecycle_verb(client, monkeypatch):
    """An explicit Mark Lost with no prose is still a close, not a plain stage edit.

    PUT /deals/:id with {stage: 'lost'} would leave `probability` untouched; only this
    verb zeroes it. So the endpoint is chosen by the ACTION, never by whether the user
    happened to type something.
    """
    calls = []
    monkeypatch.setattr(
        service, "mark_deal_lost",
        lambda deal_id, lost_reason="", author_id=None: (
            calls.append(lost_reason) or {"id": deal_id, "stage": "lost"}
        ),
    )
    assert client.post("/api/crm/deals/7/mark-lost", json={}).status_code == 200
    assert calls == [""]


def test_mark_lost_archived_deal_is_400_not_500(client, monkeypatch):
    """_write_deal_update raises on a stage change to an archived deal — a refusal the
    caller can act on, mapped like PUT /deals/:id does."""
    def boom(deal_id, lost_reason="", author_id=None):
        raise ValueError("Cannot change the stage of an archived deal")

    monkeypatch.setattr(service, "mark_deal_lost", boom)
    res = client.post("/api/crm/deals/7/mark-lost", json={"lost_reason": "x"})
    assert res.status_code == 400
    assert "archived" in res.json()["detail"]


def test_mark_lost_missing_deal_404(client, monkeypatch):
    monkeypatch.setattr(
        service, "mark_deal_lost", lambda deal_id, lost_reason="", author_id=None: None
    )
    assert client.post("/api/crm/deals/999/mark-lost", json={}).status_code == 404


def test_mark_lost_rejects_an_oversized_reason_instead_of_truncating(client, monkeypatch):
    """The service TRUNCATES at MAX_LOST_REASON. Silently dropping the tail of a rep's
    typed prose is data loss, so the REST boundary refuses and the service is never
    reached — the browser caps at the same length, so only a raw client can hit this."""
    called = []
    monkeypatch.setattr(
        service, "mark_deal_lost",
        lambda deal_id, lost_reason="", author_id=None: called.append(1),
    )
    over = "x" * (service.MAX_LOST_REASON + 1)
    assert client.post(
        "/api/crm/deals/7/mark-lost", json={"lost_reason": over}
    ).status_code == 422
    assert called == []
    # …and exactly at the cap is still accepted.
    monkeypatch.setattr(
        service, "mark_deal_lost",
        lambda deal_id, lost_reason="", author_id=None: {"id": deal_id, "stage": "lost"},
    )
    at_cap = "x" * service.MAX_LOST_REASON
    assert client.post(
        "/api/crm/deals/7/mark-lost", json={"lost_reason": at_cap}
    ).status_code == 200


def test_the_frontend_lost_reason_cap_matches_the_server():
    """The composer's cap is a hand-copied mirror of MAX_LOST_REASON, so it can drift.

    Drift is not symmetric: a frontend cap ABOVE the server's turns a 422 into the user's
    problem after they have written the reason, which is exactly what the Pydantic bound
    exists to prevent them from hitting. Read the shipped constant rather than restating
    the number, the way inkContrast.test.ts parses the shipped CSS.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "crm" / "constants.ts").read_text(encoding="utf-8")
    match = re.search(r"export const MAX_LOST_REASON\s*=\s*(\d+)", src)
    assert match, "MAX_LOST_REASON is gone from frontend/src/crm/constants.ts"
    assert int(match.group(1)) == service.MAX_LOST_REASON
