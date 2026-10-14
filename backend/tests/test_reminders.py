"""Reminders engine (issue #283) — hermetic: no database.

The firing guarantees live here: the claim commits BEFORE anything is delivered, and a
failure inside the claim transaction rolls the whole tick back with nothing sent. The
real-Postgres half (SKIP LOCKED between two ticks, the CHECKs, the unique backstop) is
tests/test_integration_reminders_pg.py.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from crm.gtd_common import NotFoundError, ValidationError
from reminders import service

CHI = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)  # 10:00 Chicago
COLS = ["id", "owner_id", "created_by", "note", "people", "due_date", "due_time", "due_at",
        "repeat", "anchor_date", "series_id", "status", "void_reason", "fired_at", "voided_at",
        "created_at", "updated_at"]


def _row(**over):
    row = {"id": 7, "owner_id": 1, "created_by": 1, "note": "Call Acme back", "people": [1, 2],
           "due_date": "2026-01-31", "due_time": None, "due_at": "2026-01-31T15:00:00+00:00",
           "repeat": None, "anchor_date": "2026-01-31", "series_id": None,
           "status": "upcoming", "void_reason": "", "fired_at": None, "voided_at": None,
           "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"}
    row.update(over)
    return tuple(row[c] for c in COLS)


class _Tx:
    """A connection whose context manager records commit / rollback, in order, into the
    same event log the delivery recorder writes to."""

    def __init__(self, log, rows, fail_on=None):
        self.log, self.rows, self.fail_on, self.executed = log, rows, fail_on, []
        self.rowcount = 1

    def cursor(self):
        return self

    @property
    def description(self):
        return [(c,) for c in COLS]

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("insert failed")
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


@pytest.fixture
def tick(monkeypatch):
    log = []

    def install(rows, *, fail_on=None, active=(1, 2)):
        conn = _Tx(log, rows, fail_on)

        @contextmanager
        def get_connection():
            log.append("begin")
            try:
                yield conn
            except Exception:
                log.append("rollback")
                raise
            log.append("commit")

        monkeypatch.setattr(service, "get_connection", get_connection)
        monkeypatch.setattr(service, "tz", lambda: CHI)
        monkeypatch.setattr(service, "pg_fetchall",
                            lambda sql, params=(): [{"id": i} for i in active])
        monkeypatch.setattr(service.users_service, "users_by_id",
                            lambda: {1: {"id": 1, "name": "Ada"}, 2: {"id": 2, "name": "Bo"}})
        monkeypatch.setattr(service, "deliver_notification",
                            lambda title, msg, *, user_id, link: log.append(
                                ("deliver", user_id, msg, link)))
        return conn, log

    return install


def test_fire_due_claims_marks_fired_and_spawns_from_the_anchor(tick):
    conn, _ = tick([_row(repeat="monthly")])
    out = service.fire_due(now=NOW)
    sqls = [s for s, _ in conn.executed]
    assert "FOR UPDATE SKIP LOCKED" in sqls[0] and sqls[0].endswith("ORDER BY due_at ASC, id ASC LIMIT %s FOR UPDATE SKIP LOCKED")
    assert sqls[1].startswith("UPDATE reminders SET status = 'fired'")
    assert sqls[2].startswith("INSERT INTO reminders") and "ON CONFLICT" in sqls[2]
    # Jan 31 fired late on Oct 14 → the next occurrence after now, clamped from the anchor.
    next_date, next_at, src = conn.executed[2][1]
    assert str(next_date) == "2026-10-31" and src == 7
    assert next_at == datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)
    assert out == {"fired": 1, "spawned": 1, "attempted": 2}


def test_delivery_happens_only_after_the_claim_commits(tick):
    _, log = tick([_row()])
    service.fire_due(now=NOW)
    assert log[:2] == ["begin", "commit"]
    delivered = [e for e in log if isinstance(e, tuple)]
    assert [d[1] for d in delivered] == [1, 2]
    assert all(d[3] == "/crm/reminders" for d in delivered)
    # The owner gets the note verbatim; anyone else learns who set it.
    assert delivered[0][2] == "Call Acme back"
    assert delivered[1][2] == "Call Acme back\n— set by Ada"


def test_a_failed_successor_insert_rolls_back_the_claim_and_delivers_nothing(tick):
    _, log = tick([_row(repeat="daily")], fail_on="INSERT INTO reminders")
    with pytest.raises(RuntimeError):
        service.fire_due(now=NOW)
    assert log == ["begin", "rollback"]


def test_a_one_time_row_spawns_nothing(tick):
    conn, _ = tick([_row()])
    assert service.fire_due(now=NOW)["spawned"] == 0
    assert not any(s.startswith("INSERT") for s, _ in conn.executed)


def test_an_inactive_person_is_skipped_at_delivery(tick):
    _, log = tick([_row()], active=(1,))
    assert service.fire_due(now=NOW)["attempted"] == 1
    assert [e[1] for e in log if isinstance(e, tuple)] == [1]


def test_nothing_due_reads_once_and_delivers_nothing(tick):
    conn, log = tick([])
    assert service.fire_due(now=NOW) == {"fired": 0, "spawned": 0, "attempted": 0}
    assert len(conn.executed) == 1 and log == ["begin", "commit"]


# ── create / update / void validation ─────────────────────────────────────────

@pytest.fixture
def writes(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "tz", lambda: CHI)
    monkeypatch.setattr(service, "pg_fetchall",
                        lambda sql, params=(): [{"id": i} for i in params[0] if i in (1, 2)])

    def fetchone(sql, params=()):
        seen["sql"], seen["params"] = " ".join(sql.split()), params
        return seen.get("returns", {"id": 1})

    monkeypatch.setattr(service, "pg_fetchone", fetchone)
    return seen


@pytest.mark.parametrize("kwargs, message", [
    ({"note": "  "}, "needs a note"),
    ({"due_date": "2026-10-13"}, "date has already passed"),
    ({"due_time": "09:30"}, "time has already passed"),
    ({"repeat": "hourly"}, "repeat must be"),
    ({"due_time": "9am"}, "HH:MM"),
    ({"due_date": ""}, "due_date is required"),
    ({"people": [3]}, "Not an active user: 3"),
])
def test_create_refuses_bad_input(writes, kwargs, message):
    args = {"note": "x", "due_date": "2026-10-14", "owner_id": 1, "created_by": 1, "now": NOW}
    args.update(kwargs)
    with pytest.raises(ValidationError, match=message):
        service.create_reminder(**args)


def test_a_date_only_reminder_for_today_is_accepted_after_the_morning_hour(writes):
    service.create_reminder(note="x", due_date="2026-10-14", owner_id=1, created_by=1, now=NOW)
    params = writes["params"]
    assert params[3] == [1]  # people default to the owner
    assert params[6] == datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)  # 09:00 Chicago
    assert str(params[8]) == "2026-10-14"  # anchor = due date


def test_people_are_deduplicated_and_sorted(writes):
    service.create_reminder(note="x", due_date="2026-10-15", people=[2, 1, 2], owner_id=1,
                            created_by=1, now=NOW)
    assert writes["params"][3] == [1, 2]


def test_void_needs_a_reason_and_an_upcoming_row(writes, monkeypatch):
    with pytest.raises(ValidationError, match="needs a reason"):
        service.void_reminder(1, "  ")
    writes["returns"] = None
    monkeypatch.setattr(service, "get_reminder", lambda rid: {"id": rid, "status": "fired"})
    with pytest.raises(ValidationError, match="Only an upcoming"):
        service.void_reminder(1, "done")
    monkeypatch.setattr(service, "get_reminder", lambda rid: None)
    with pytest.raises(NotFoundError):
        service.void_reminder(1, "done")


def test_update_refuses_a_fired_row(tick):
    tick([_row(status="fired")])
    with pytest.raises(ValidationError, match="Only an upcoming"):
        service.update_reminder(7, {"note": "new"}, now=NOW)


def test_a_note_only_edit_of_an_overdue_row_succeeds(tick):
    conn, _ = tick([_row(due_date="2026-10-13", due_at="2026-10-13T14:00:00+00:00")])
    service.update_reminder(7, {"note": "new"}, now=NOW)
    sql, params = conn.executed[-1]
    assert sql.startswith("UPDATE reminders SET note = %s, updated_at = now()")
    assert params == ("new", 7)


def test_a_new_date_reanchors_the_series(tick):
    conn, _ = tick([_row(repeat="weekly")])
    service.update_reminder(7, {"due_date": "2026-10-20"}, now=NOW)
    sql, params = conn.executed[-1]
    assert "anchor_date = %s" in sql
    assert str(params[0]) == "2026-10-20" and str(params[3]) == "2026-10-20"


def test_update_refuses_unknown_fields():
    with pytest.raises(ValidationError, match="Cannot change: status"):
        service.update_reminder(7, {"status": "fired"})


def test_remind_again_copies_note_and_active_people_as_a_one_time_row(monkeypatch):
    monkeypatch.setattr(service, "get_reminder", lambda rid: {
        "id": rid, "note": "Ship it", "people": [1, 2, 3], "repeat": "weekly"})
    monkeypatch.setattr(service, "_active_ids", lambda ids: {1, 3})
    seen = {}
    monkeypatch.setattr(service, "create_reminder", lambda **kw: seen.update(kw) or kw)
    service.remind_again(5, due_date="2026-10-20", owner_id=2, created_by=2)
    assert seen["note"] == "Ship it" and seen["people"] == [1, 3]
    assert seen["repeat"] is None and seen["owner_id"] == 2


def test_list_refuses_after_id_without_sort_id():
    with pytest.raises(ValidationError):
        service.list_reminders(after_id=3)


def test_list_scopes_mine_to_owner_or_person_and_orders_end_on_id(monkeypatch):
    seen = []
    monkeypatch.setattr(service, "pg_fetchall", lambda sql, params: seen.append((sql, params)) or [])
    service.list_reminders(mine_user_id=4, status="upcoming", sort="id", after_id=10, limit=501)
    service.list_reminders()
    (sql1, p1), (sql2, _) = seen
    assert "(owner_id = %s OR %s = ANY(people))" in sql1 and p1 == [4, 4, "upcoming", 10, 501]
    assert sql1.endswith("ORDER BY id ASC LIMIT %s")
    assert sql2.endswith("due_at ASC, id ASC LIMIT %s")
