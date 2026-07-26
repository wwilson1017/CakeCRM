"""Alerts service (alerts/service.py).

Hermetic: pg helpers mocked. Covers the dedup upsert (ON CONFLICT on the partial
unique index when source_id is present), plain insert without source_id, and the
rowcount-based acknowledge/resolve/resolve_by_source claims.
"""

import pytest

from alerts import service


@pytest.fixture
def captured_execute(monkeypatch):
    calls = []

    def pg_execute(sql, params=()):
        calls.append((" ".join(sql.split()), params))
        return calls_rowcount["n"]

    calls_rowcount = {"n": 1}
    monkeypatch.setattr(service, "pg_execute", pg_execute)
    return calls, calls_rowcount


def test_create_alert_with_source_id_upserts(captured_execute):
    calls, _ = captured_execute
    service.create_alert("Heartbeat failing", "3 in a row", source="heartbeat", source_id="heartbeat")
    sql = calls[-1][0]
    assert "ON CONFLICT (source, source_id) WHERE status = 'active'" in sql
    assert "DO UPDATE" in sql


def test_create_alert_without_source_id_plain_insert(captured_execute):
    calls, _ = captured_execute
    service.create_alert("One-off", "no dedup", source="heartbeat", source_id=None)
    sql = calls[-1][0]
    assert "ON CONFLICT" not in sql


def test_acknowledge_and_resolve_rowcount(captured_execute):
    calls, rc = captured_execute
    rc["n"] = 1
    assert service.acknowledge_alert("a1")["ok"] is True
    assert service.resolve_alert("a1")["ok"] is True
    rc["n"] = 0
    assert "error" in service.acknowledge_alert("missing")
    assert "error" in service.resolve_alert("missing")


def test_resolve_by_source_returns_count(captured_execute):
    calls, rc = captured_execute
    rc["n"] = 2
    assert service.resolve_by_source("heartbeat", "heartbeat") == 2


def test_get_active_count(monkeypatch):
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"n": 4})
    assert service.get_active_count() == 4
