"""Custom-field router contract (issue #19) — minimal app, service monkeypatched.

Covers CRUD status codes, UniqueViolation/ValueError/FK translation, PUT
exclude_unset semantics, attribution fallback to the JWT `sub`, and that the
new /{entity_type}/{entity_id}/fields paths don't shadow existing routes.
"""

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import field_service, service
from crm.router import router as crm_router
from conftest import fake_admin


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


# ── Definition CRUD ───────────────────────────────────────────────────────────

def test_create_definition_returns_200_not_201(client, monkeypatch):
    monkeypatch.setattr(field_service, "create_field_definition", lambda data: {"id": 1, **data})
    r = client.post("/api/crm/fields", json={
        "entity_type": "contact", "name": "Tier", "field_type": "select",
        "dropdown_options": ["A", "B"],
    })
    assert r.status_code == 200                      # matches the other create routes
    assert r.json()["id"] == 1


def test_create_definition_valueerror_400(client, monkeypatch):
    def raise_ve(data):
        raise ValueError("Invalid entity_type: widget")
    monkeypatch.setattr(field_service, "create_field_definition", raise_ve)
    r = client.post("/api/crm/fields", json={"entity_type": "widget", "name": "X", "field_type": "text"})
    assert r.status_code == 400


def test_create_definition_unique_violation_400(client, monkeypatch):
    def raise_unique(data):
        raise psycopg2.errors.UniqueViolation()
    monkeypatch.setattr(field_service, "create_field_definition", raise_unique)
    r = client.post("/api/crm/fields", json={"entity_type": "contact", "name": "Tier", "field_type": "text"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


def test_list_definitions_invalid_type_400(client, monkeypatch):
    def raise_ve(et=None):
        raise ValueError("Invalid entity_type: widget")
    monkeypatch.setattr(field_service, "list_field_definitions", raise_ve)
    assert client.get("/api/crm/fields?entity_type=widget").status_code == 400


def test_update_definition_exclude_unset_forwards_explicit_null(client, monkeypatch):
    captured = {}

    def fake_update(field_id, data):
        captured["data"] = data
        return {"id": field_id, **data}

    monkeypatch.setattr(field_service, "update_field_definition", fake_update)
    # Explicit null dropdown_options must reach the service (clear); unsent keys must not.
    client.put("/api/crm/fields/3", json={"dropdown_options": None})
    assert captured["data"] == {"dropdown_options": None}
    client.put("/api/crm/fields/3", json={"name": "Renamed"})
    assert captured["data"] == {"name": "Renamed"}   # no dropdown_options key


def test_update_definition_missing_404(client, monkeypatch):
    monkeypatch.setattr(field_service, "update_field_definition", lambda fid, data: None)
    assert client.put("/api/crm/fields/999", json={"name": "X"}).status_code == 404


def test_delete_definition_404_and_ok(client, monkeypatch):
    monkeypatch.setattr(field_service, "delete_field_definition", lambda fid: fid == 1)
    assert client.delete("/api/crm/fields/999").status_code == 404
    r = client.delete("/api/crm/fields/1")
    assert r.status_code == 200 and r.json() == {"ok": True}


# ── Values ────────────────────────────────────────────────────────────────────

def test_get_values_invalid_type_400(client):
    # Invalid entity_type is rejected before any DB access.
    assert client.get("/api/crm/widget/5/fields").status_code == 400


def test_get_values_missing_entity_404(client, monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: False)
    assert client.get("/api/crm/contact/999/fields").status_code == 404


def test_set_values_forwards_sub_as_editor(client, monkeypatch):
    captured = {}

    def fake_set(et, eid, values, user_email):
        captured.update(et=et, eid=eid, values=values, email=user_email)
        return {"ok": True, "updated": len(values), "errors": []}

    monkeypatch.setattr(field_service, "set_field_values", fake_set)
    r = client.put("/api/crm/contact/5/fields", json={"values": {"9": "A"}})
    assert r.status_code == 200
    # Since #60 the dependency returns a live user row, so the editor is a real address.
    assert captured == {
        "et": "contact", "eid": 5, "values": {"9": "A"}, "email": "admin@cakecrm.test",
    }


def test_set_values_valueerror_400(client, monkeypatch):
    def raise_ve(*a, **k):
        raise ValueError("contact with id 5 not found")
    monkeypatch.setattr(field_service, "set_field_values", raise_ve)
    assert client.put("/api/crm/contact/5/fields", json={"values": {"9": "A"}}).status_code == 400


def test_set_values_fk_violation_400_not_500(client, monkeypatch):
    def raise_fk(*a, **k):
        raise psycopg2.errors.ForeignKeyViolation()
    monkeypatch.setattr(field_service, "set_field_values", raise_fk)
    r = client.put("/api/crm/deal/5/fields", json={"values": {"9": "A"}})
    assert r.status_code == 400                       # backstop for a concurrent def delete


# ── Route non-shadowing ───────────────────────────────────────────────────────

def test_entity_field_route_does_not_shadow_entity_detail(client, monkeypatch):
    monkeypatch.setattr(service, "get_contact_detail", lambda cid: {"id": cid, "kind": "contact"})
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: True)
    monkeypatch.setattr(field_service, "get_field_values", lambda et, eid: [{"field_id": 1, "kind": "fields"}])
    # /contacts/5 -> the contact detail handler; /contact/5/fields -> the fields handler.
    assert client.get("/api/crm/contacts/5").json()["kind"] == "contact"
    fields = client.get("/api/crm/contact/5/fields").json()
    assert fields[0]["kind"] == "fields"
