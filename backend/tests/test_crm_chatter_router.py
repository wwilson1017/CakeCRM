"""Chatter router contract — minimal FastAPI app, chatter_service monkeypatched.

Validation 400s (bad entity_type, blank message) run against the REAL service:
those checks short-circuit before any pg call, so no DB or mock is needed. Happy
and missing paths monkeypatch the service functions.
"""

import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import chatter_service
from crm.router import router as crm_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


# ── GET /chatter/{type}/{id} ────────────────────────────────────────────────────

def test_get_chatter_returns_notes_and_count(client, monkeypatch):
    monkeypatch.setattr(chatter_service, "get_chatter", lambda *a, **k: [{"id": 1}, {"id": 2}])
    r = client.get("/api/crm/chatter/deal/3")
    assert r.status_code == 200
    assert r.json() == {"notes": [{"id": 1}, {"id": 2}], "count": 2}


def test_get_chatter_bad_entity_type_400(client):
    # real service: _check_entity_type raises before any pg call. 'company' became a
    # VALID type in issue #22, so the invalid-type probe uses a type that never exists.
    assert client.get("/api/crm/chatter/invoice/3").status_code == 400


def test_get_chatter_passes_include_archived(client, monkeypatch):
    seen = {}

    def fake(entity_type, entity_id, limit=50, offset=0, include_archived=False):
        seen.update(include_archived=include_archived, entity_type=entity_type)
        return []

    monkeypatch.setattr(chatter_service, "get_chatter", fake)
    client.get("/api/crm/chatter/contact/9?include_archived=true")
    assert seen == {"include_archived": True, "entity_type": "contact"}


# ── POST /chatter/{type}/{id}/note ──────────────────────────────────────────────

def test_add_note_returns_created(client, monkeypatch):
    captured = {}

    def _add(t, i, m, author_id=None, mentions=None):
        captured.update(entity_type=t, entity_id=i, author_id=author_id)
        return {"id": 7, "message": m}, []

    monkeypatch.setattr(chatter_service, "post_note", _add)
    r = client.post("/api/crm/chatter/deal/3/note", json={"message": "hello"})
    assert r.status_code == 200
    assert r.json() == {"id": 7, "message": "hello"}
    # The note is credited to the human who wrote it (issue #60), not to the record's
    # owner — per-rep activity is attributed by actor.
    assert captured == {"entity_type": "deal", "entity_id": 3, "author_id": 1}


def test_add_note_blank_message_400(client):
    # real service: _clean_message raises before pg
    assert client.post("/api/crm/chatter/deal/3/note", json={"message": "   "}).status_code == 400


def test_add_note_bad_entity_type_400(client):
    assert client.post("/api/crm/chatter/invoice/3/note", json={"message": "hi"}).status_code == 400


def test_company_is_a_valid_chatter_entity_type(client, monkeypatch):
    """Issue #22: notes threads extend to companies — the HTTP path widened with the
    service's CHATTER_ENTITY_TYPES, with no route change."""
    monkeypatch.setattr(chatter_service, "get_chatter", lambda *a, **k: [])
    assert client.get("/api/crm/chatter/company/3").status_code == 200


# ── PATCH /chatter/note/{id} ────────────────────────────────────────────────────

def test_update_note_ok(client, monkeypatch):
    monkeypatch.setattr(chatter_service, "edit_note",
                        lambda nid, m, mentions=None, actor_id=None: ({"id": nid, "message": m}, []))
    r = client.patch("/api/crm/chatter/note/5", json={"message": "edited"})
    assert r.status_code == 200
    assert r.json()["message"] == "edited"


def test_update_note_missing_404(client, monkeypatch):
    monkeypatch.setattr(chatter_service, "edit_note",
                        lambda nid, m, mentions=None, actor_id=None: (None, []))
    assert client.patch("/api/crm/chatter/note/999", json={"message": "x"}).status_code == 404


def test_update_note_blank_400(client):
    assert client.patch("/api/crm/chatter/note/5", json={"message": ""}).status_code == 400


# ── archive / unarchive ─────────────────────────────────────────────────────────

def test_archive_ok_and_missing(client, monkeypatch):
    monkeypatch.setattr(chatter_service, "archive_note", lambda nid: True if nid == 5 else None)
    assert client.post("/api/crm/chatter/note/5/archive").json() == {"ok": True}
    assert client.post("/api/crm/chatter/note/999/archive").status_code == 404


def test_unarchive_ok_and_missing(client, monkeypatch):
    monkeypatch.setattr(chatter_service, "unarchive_note", lambda nid: True if nid == 5 else None)
    assert client.post("/api/crm/chatter/note/5/unarchive").json() == {"ok": True}
    assert client.post("/api/crm/chatter/note/999/unarchive").status_code == 404
