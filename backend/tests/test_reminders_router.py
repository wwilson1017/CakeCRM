"""Reminders REST routes (issue #283): identity from the token, errors to HTTP."""

import pytest
from conftest import fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm.gtd_common import NotFoundError, ValidationError
from reminders import router as reminders_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(reminders_router.router, prefix="/api/crm/reminders")
    app.dependency_overrides[get_current_user] = fake_member
    return TestClient(app)


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(name):
        def _f(*args, **kwargs):
            seen.append((name, args, kwargs))
            return {"id": 1}
        return _f

    for name in ("create_reminder", "update_reminder", "void_reminder", "remind_again"):
        monkeypatch.setattr(reminders_router.service, name, fake(name))
    monkeypatch.setattr(reminders_router.service, "list_reminders",
                        lambda **kw: seen.append(("list", (), kw)) or [])
    return seen


def test_create_binds_owner_and_author_from_the_token_not_the_body(client, calls):
    res = client.post("/api/crm/reminders", json={
        "note": "Call", "due_date": "2030-01-01", "owner_id": 99, "created_by": 99})
    assert res.status_code == 200
    _, _, kw = calls[0]
    assert kw["owner_id"] == 2 and kw["created_by"] == 2


def test_scope_mine_passes_the_callers_id_and_all_passes_none(client, calls):
    client.get("/api/crm/reminders?scope=mine&sort=id&limit=501&after_id=4")
    client.get("/api/crm/reminders")
    assert calls[0][2]["mine_user_id"] == 2 and calls[0][2]["after_id"] == 4
    assert calls[1][2]["mine_user_id"] is None


def test_update_sends_only_the_fields_present(client, calls):
    client.put("/api/crm/reminders/7", json={"note": "New"})
    assert calls[0][1] == (7, {"note": "New"})


def test_void_and_remind_again_reach_the_service(client, calls):
    client.post("/api/crm/reminders/7/void", json={"reason": "done"})
    client.post("/api/crm/reminders/7/remind-again", json={"due_date": "2030-01-02"})
    assert calls[0][:2] == ("void_reminder", (7, "done"))
    assert calls[1][0] == "remind_again" and calls[1][2]["owner_id"] == 2


def test_validation_error_is_400_and_not_found_is_404(client, monkeypatch):
    def bad(*a, **k):
        raise ValidationError("Only an upcoming reminder can be voided")

    def gone(*a, **k):
        raise NotFoundError("x")

    monkeypatch.setattr(reminders_router.service, "void_reminder", bad)
    res = client.post("/api/crm/reminders/7/void", json={"reason": "done"})
    assert res.status_code == 400 and "upcoming" in res.json()["detail"]
    monkeypatch.setattr(reminders_router.service, "void_reminder", gone)
    assert client.post("/api/crm/reminders/7/void", json={"reason": "x"}).status_code == 404
    monkeypatch.setattr(reminders_router.service, "get_reminder", lambda rid: None)
    assert client.get("/api/crm/reminders/7").status_code == 404
