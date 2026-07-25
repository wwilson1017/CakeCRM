"""Reminders data service (reminders/service.py).

Hermetic: pg helpers mocked. Covers due_at normalization (incl. past-allowed and
garbage-rejected), create with recurrence series init, CRUD error mapping, and the
atomic claim (rowcount 1 → claimed + successor created; rowcount 0 → lost).
"""

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from reminders import service

# ── _normalize_due_at ───────────────────────────────────────────────────────

def test_normalize_accepts_z_offset_naive():
    for s in ("2026-07-25T09:00:00Z", "2026-07-25T09:00:00+00:00", "2026-07-25T09:00:00"):
        dt = service._normalize_due_at(s)
        assert dt.tzinfo is not None and dt.year == 2026


def test_normalize_rejects_garbage():
    with pytest.raises(ValueError):
        service._normalize_due_at("not a date")


def test_normalize_allows_past():
    dt = service._normalize_due_at("2000-01-01T00:00:00Z")   # past is fine
    assert dt.year == 2000


# ── create_reminder ─────────────────────────────────────────────────────────

@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "pg_execute", lambda sql, params=(): (calls.append((sql, params)) or 1))
    return calls


def test_create_reminder_ok(captured):
    r = service.create_reminder("Call Dana", "2026-07-25T09:00:00Z")
    assert r["ok"] is True and r["recurring"] is False
    # due_at persisted as a datetime (TIMESTAMPTZ), not a bare string
    _sql, params = captured[-1]
    assert isinstance(params[3], datetime)


def test_create_reminder_recurring_sets_series(captured):
    r = service.create_reminder("Standup", "2026-07-25T09:00:00Z", recurrence_rule={"type": "daily"})
    assert r["recurring"] is True
    assert r["series_id"] == r["id"]      # head of a new series


def test_create_reminder_bad_due(captured):
    assert "error" in service.create_reminder("x", "nope")


def test_create_reminder_bad_rule(captured):
    assert "error" in service.create_reminder("x", "2026-07-25T09:00:00Z",
                                              recurrence_rule={"type": "interval", "minutes": 0})


def test_create_reminder_blank_message(captured):
    assert "error" in service.create_reminder("   ", "2026-07-25T09:00:00Z")


# ── update / cancel / delete error mapping ──────────────────────────────────

def test_update_not_pending_or_missing(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 0)   # nothing updated
    monkeypatch.setattr(service, "get_reminder", lambda rid: {"id": rid, "status": "fired"})
    assert "only pending" in service.update_reminder("r1", message="new")["error"]

    monkeypatch.setattr(service, "get_reminder", lambda rid: None)
    assert "not found" in service.update_reminder("r1", message="new")["error"]


def test_update_no_fields(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    assert "no fields" in service.update_reminder("r1")["error"]


def test_cancel_success_and_missing(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "get_reminder", lambda rid: {"id": rid, "is_recurring": True})
    out = service.cancel_reminder("r1")
    assert out["ok"] and "series" in out["note"]

    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 0)
    monkeypatch.setattr(service, "get_reminder", lambda rid: None)
    assert "not found" in service.cancel_reminder("r1")["error"]


def test_delete_pending_rejected(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 0)
    monkeypatch.setattr(service, "get_reminder", lambda rid: {"id": rid, "status": "pending"})
    assert "cancel" in service.delete_reminder("r1")["error"]


# ── claim_reminder (atomic, R5) ─────────────────────────────────────────────

# The claim uses UPDATE ... RETURNING, so the fake cursor exposes description +
# fetchone matching the returned columns (message, context, due_at, recurrence_rule,
# series_id) — mirroring row_to_dict's expectations.
_RETURN_COLS = ("message", "context", "due_at", "recurrence_rule", "series_id")


class _Cur:
    def __init__(self, rowcount, row=None):
        self.rowcount = rowcount
        self.row = row
        self.executed = []
        self.description = [(c,) for c in _RETURN_COLS]

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, rowcount, row=None):
        self._cur = _Cur(rowcount, row)

    def cursor(self):
        return self._cur


def _install_conn(monkeypatch, rowcount, row=None):
    conn = _Conn(rowcount, row)

    @contextmanager
    def _get():
        yield conn

    monkeypatch.setattr(service, "get_connection", _get)
    return conn


def test_claim_won_creates_successor(monkeypatch):
    row = ("Standup", "", datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc),
           {"type": "daily"}, "r1")  # fresh row from RETURNING
    conn = _install_conn(monkeypatch, rowcount=1, row=row)
    reminder = {"id": "r1", "due_at": "stale-ignored", "recurrence_rule": {"type": "daily"}}
    assert service.claim_reminder(reminder) is True
    sqls = [e[0] for e in conn._cur.executed]
    assert any("UPDATE reminders SET status = 'fired'" in s and "due_at <= now()" in s for s in sqls)
    assert any("INSERT INTO reminders" in s for s in sqls)   # successor in same tx, from fresh row


def test_claim_lost_returns_false_no_successor(monkeypatch):
    conn = _install_conn(monkeypatch, rowcount=0)   # rescheduled to future / lost
    reminder = {"id": "r1", "recurrence_rule": {"type": "daily"}, "series_id": "r1"}
    assert service.claim_reminder(reminder) is False
    sqls = [e[0] for e in conn._cur.executed]
    assert not any("INSERT INTO reminders" in s for s in sqls)   # no double-fire successor


def test_claim_non_recurring_no_successor(monkeypatch):
    row = ("x", "", datetime.now(timezone.utc), None, None)  # recurrence_rule None
    conn = _install_conn(monkeypatch, rowcount=1, row=row)
    reminder = {"id": "r1", "recurrence_rule": None}
    assert service.claim_reminder(reminder) is True
    sqls = [e[0] for e in conn._cur.executed]
    assert not any("INSERT INTO reminders" in s for s in sqls)
