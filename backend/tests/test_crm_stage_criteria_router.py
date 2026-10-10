"""Pipeline stage criteria routes (#289) — minimal app, service monkeypatched.

Pins the role split (members read, admins write), the merged GET shape, trimming before
validation, and that an unknown stage is a 404 rather than a stored orphan row.
"""

import pytest
from conftest import fake_admin, fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import service, stage_criteria
from crm.router import router as crm_router

_BODY = {"summary": "  Our own words.  ", "checklist": ["  First  ", "Second"]}


def _client(user):
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = user
    return TestClient(app)


@pytest.fixture
def writes(monkeypatch):
    calls = []

    def fake_set(stage, summary, checklist):
        calls.append(("set", stage, summary, checklist))
        return {"stage": stage, "summary": summary, "checklist": checklist, "source": "custom"}

    def fake_reset(stage):
        calls.append(("reset", stage))
        return stage_criteria._entry(stage, None)

    monkeypatch.setattr(stage_criteria, "set_criteria", fake_set)
    monkeypatch.setattr(stage_criteria, "reset_criteria", fake_reset)
    return calls


def test_the_standard_covers_exactly_the_board_stages():
    assert list(stage_criteria.STANDARD) == service.DEAL_STAGES
    for entry in stage_criteria.STANDARD.values():
        assert entry["summary"].strip()
        assert entry["checklist"] and all(i.strip() for i in entry["checklist"])


def test_get_merges_standard_and_custom_in_board_order(monkeypatch):
    monkeypatch.setattr(stage_criteria, "pg_fetchall", lambda *a, **k: [
        {"stage": "proposal", "summary": "Ours", "checklist": ["One"]},
    ])
    r = _client(fake_member).get("/api/crm/stage-criteria")
    assert r.status_code == 200
    body = r.json()
    assert [e["stage"] for e in body] == service.DEAL_STAGES
    by = {e["stage"]: e for e in body}
    assert by["proposal"] == {"stage": "proposal", "summary": "Ours", "checklist": ["One"],
                              "source": "custom"}
    assert by["lead"]["source"] == "standard"
    assert by["lead"]["checklist"] == stage_criteria.STANDARD["lead"]["checklist"]


@pytest.mark.parametrize("method", ["put", "delete"])
def test_a_member_cannot_write(writes, method):
    c = _client(fake_member)
    r = c.put("/api/crm/stage-criteria/lead", json=_BODY) if method == "put" \
        else c.delete("/api/crm/stage-criteria/lead")
    assert r.status_code == 403
    assert writes == []


def test_admin_put_trims_and_returns_the_custom_entry(writes):
    r = _client(fake_admin).put("/api/crm/stage-criteria/qualified", json=_BODY)
    assert r.status_code == 200
    assert r.json()["source"] == "custom"
    assert writes == [("set", "qualified", "Our own words.", ["First", "Second"])]


def test_admin_delete_returns_the_standard(writes):
    r = _client(fake_admin).delete("/api/crm/stage-criteria/won")
    assert r.status_code == 200
    assert r.json() == {**stage_criteria.STANDARD["won"], "stage": "won", "source": "standard"}
    assert writes == [("reset", "won")]


@pytest.mark.parametrize("body", [
    {"summary": "   ", "checklist": ["x"]},                 # whitespace-only summary
    {"summary": "ok", "checklist": []},                     # empty checklist
    {"summary": "ok", "checklist": ["x", "   "]},           # whitespace-only line
    {"summary": "x" * 1001, "checklist": ["x"]},            # oversized summary
    {"summary": "ok", "checklist": ["x" * 301]},            # oversized line
    {"summary": "ok", "checklist": ["x"] * 31},             # too many lines
])
def test_invalid_bodies_are_422_and_never_written(writes, body):
    r = _client(fake_admin).put("/api/crm/stage-criteria/lead", json=body)
    assert r.status_code == 422
    assert writes == []


@pytest.mark.parametrize("method", ["put", "delete"])
def test_unknown_stage_is_404(monkeypatch, method):
    # The real service functions, so the stage check itself is what answers; the DB is never
    # reached because the check runs first.
    monkeypatch.setattr(stage_criteria, "pg_fetchone", lambda *a, **k: pytest.fail("wrote"))
    monkeypatch.setattr(stage_criteria, "pg_execute", lambda *a, **k: pytest.fail("wrote"))
    c = _client(fake_admin)
    r = c.put("/api/crm/stage-criteria/closing", json=_BODY) if method == "put" \
        else c.delete("/api/crm/stage-criteria/closing")
    assert r.status_code == 404
