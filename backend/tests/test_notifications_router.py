"""Notifications REST API — the identity half (issue #192).

The service tests prove the SQL is scoped. This file proves the ROUTER actually hands
the service a seat, which is the layer where identity enters the system: every endpoint
here changed from an ignored ``_user`` dependency to one whose ``id`` is threaded into a
recipient-scoped call. Without a test at this layer, a later copy-pasted endpoint or a
rename back to ``_user`` would ship with every other test green — and the one property
this whole issue exists to guarantee would be gone.

The services are monkeypatched to capture what they were called with, so this file is
about wiring only and touches no database. The TestClient + ``dependency_overrides``
idiom is the repo's, from ``test_users_router.py``.
"""

import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from notifications import delivery, service, subscriptions
from notifications.router import router as notifications_router

SEAT = fake_admin()["id"]


@pytest.fixture
def captured(monkeypatch):
    """Record the kwargs/args each endpoint forwards to its service function."""
    box: dict = {}
    monkeypatch.setattr(service, "list_notifications",
                        lambda **kw: box.update(list=kw) or [])
    monkeypatch.setattr(service, "get_active_count",
                        lambda **kw: box.update(counts=kw) or 0)
    monkeypatch.setattr(service, "dismiss_notification",
                        lambda nid, **kw: box.update(dismiss=(nid, kw)) or {"ok": True, "id": nid})
    monkeypatch.setattr(service, "dismiss_all",
                        lambda **kw: box.update(dismiss_all=kw) or {"ok": True, "dismissed": 0})
    monkeypatch.setattr(subscriptions, "save_subscription",
                        lambda *a, **kw: box.update(save=(a, kw)) or {"ok": True})
    monkeypatch.setattr(subscriptions, "remove_subscription",
                        lambda *a, **kw: box.update(remove=(a, kw)) or True)
    monkeypatch.setattr(delivery, "deliver_notification",
                        lambda *a, **kw: box.update(deliver=(a, kw)) or {"ok": True, "web_push": False})
    return box


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(notifications_router, prefix="/api/notifications")
    app.dependency_overrides[get_current_user] = fake_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_is_called_with_the_callers_seat(client, captured):
    assert client.get("/api/notifications?status=all&limit=3").status_code == 200
    assert captured["list"] == {"user_id": SEAT, "status": "all", "limit": 3}


def test_badge_count_is_called_with_the_callers_seat(client, captured):
    assert client.get("/api/notifications/counts").status_code == 200
    assert captured["counts"] == {"user_id": SEAT}


def test_dismiss_is_called_with_the_callers_seat(client, captured):
    assert client.post("/api/notifications/n1/dismiss").status_code == 200
    assert captured["dismiss"] == ("n1", {"user_id": SEAT})


def test_dismiss_all_is_called_with_the_callers_seat(client, captured):
    assert client.post("/api/notifications/dismiss-all").status_code == 200
    assert captured["dismiss_all"] == {"user_id": SEAT}


def test_subscribe_stamps_the_caller_as_the_endpoints_owner(client, captured):
    r = client.post("/api/notifications/push/subscribe", json={
        "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
        "keys": {"p256dh": "p", "auth": "a"},
        "user_agent": "UA",
    })
    assert r.status_code == 200
    args, _ = captured["save"]
    assert args == ("https://fcm.googleapis.com/fcm/send/abc", "p", "a", SEAT, "UA")


def test_unsubscribe_is_scoped_to_the_caller(client, captured):
    """The endpoint arrives in the request BODY, so an unscoped delete would let one
    member unsubscribe another's devices."""
    r = client.post("/api/notifications/push/unsubscribe",
                    json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc"})
    assert r.status_code == 200
    args, _ = captured["remove"]
    assert args == ("https://fcm.googleapis.com/fcm/send/abc", SEAT)


def test_test_push_targets_the_caller_not_the_install(client, captured):
    """'Does push work for me' must not fire on every colleague's phone."""
    r = client.post("/api/notifications/test", json={"title": "T", "message": "M"})
    assert r.status_code == 200
    args, kwargs = captured["deliver"]
    assert args == ("T", "M")
    assert kwargs == {"user_id": SEAT}


def test_a_notification_that_is_not_mine_is_a_404(client, monkeypatch):
    """The service reports 'not found' for another seat's row; the router must turn that
    into 404 rather than 200, and never into a 403 that would confirm it exists."""
    monkeypatch.setattr(service, "dismiss_notification",
                        lambda nid, **kw: {"error": "notification not found or already dismissed"})
    assert client.post("/api/notifications/theirs/dismiss").status_code == 404
