"""Notification log service (notifications/service.py). Hermetic: pg helpers mocked."""

import pytest

from notifications import service


@pytest.fixture
def rc(monkeypatch):
    box = {"n": 1, "calls": []}
    monkeypatch.setattr(service, "pg_execute",
                        lambda sql, params=(): (box["calls"].append((" ".join(sql.split()), params)) or box["n"]))
    return box


def test_dismiss_found(rc):
    rc["n"] = 1
    assert service.dismiss_notification("n1") == {"ok": True, "id": "n1"}


def test_dismiss_not_found(rc):
    rc["n"] = 0
    assert "error" in service.dismiss_notification("missing")


def test_dismiss_all_returns_count(rc):
    rc["n"] = 4
    assert service.dismiss_all() == {"ok": True, "dismissed": 4}


def test_get_active_count(monkeypatch):
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"n": 7})
    assert service.get_active_count() == 7


def test_get_active_count_none(monkeypatch):
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: None)
    assert service.get_active_count() == 0


def test_list_notifications_status_filter(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "pg_fetchall",
                        lambda sql, params=(): seen.update(sql=" ".join(sql.split()), params=params) or [])
    service.list_notifications(status="active", limit=5)
    assert "WHERE status = %s" in seen["sql"]
    assert seen["params"] == ("active", 5)


def test_list_notifications_all(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "pg_fetchall",
                        lambda sql, params=(): seen.update(sql=" ".join(sql.split())) or [])
    service.list_notifications(status="all")
    assert "WHERE status" not in seen["sql"]


def test_create_notification_caps_lengths(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "pg_execute",
                        lambda sql, params=(): captured.update(params=params) or 1)
    service.create_notification("t" * 500, "m" * 9000, ["web_push"], notification_id="nid")
    # id, title(capped 200), message(capped 5000), channels JSON
    assert captured["params"][0] == "nid"
    assert len(captured["params"][1]) == 200
    assert len(captured["params"][2]) == 5000
