"""Hermetic Today-panel tests (issue #130) — the ladder, day bounds, SQL shape, route.

DB-free: the pg helpers are monkeypatched per test with the same Recorder pattern as
``test_crm_dashboard_touches.py``. The ladder itself is a pure function, so the most
important half of this file needs no mock at all.

The last section pins the local-day sweep the panel forced (``get_dashboard_stats``,
``get_deal_health``, ``collect_digest``). Those three live here rather than in each
function's own test file because they are ONE decision — "is this task overdue" must not
depend on which report asked — and splitting them across three files is how the next
person changes one and leaves the others behind. Note there was nothing to "update":
none of the three had an assertion that pinned the day at all, which is exactly why the
UTC/local split survived this long.
"""

from datetime import datetime, timezone

import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import localtime
from core.auth import get_current_user
from crm import analytics_service, gtd_common, service, today_service
from crm.router import router as crm_router
from proactive import service as proactive_service
from reminders import service as reminders_service

TODAY = "2026-06-05"


class Recorder:
    """Records (normalized_sql, params) per helper call; returns queued rows."""

    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.fetchone_queue: list = []
        self.fetchall_queue: list = []

    def fetchone(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def fetchall(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchall_queue.pop(0) if self.fetchall_queue else []

    def sql_containing(self, needle: str) -> str:
        for sql, _ in self.calls:
            if needle in sql:
                return sql
        raise AssertionError(f"no recorded SQL contains {needle!r}: {[s for s, _ in self.calls]}")

    def params_for(self, needle: str) -> list:
        for sql, params in self.calls:
            if needle in sql:
                return params
        raise AssertionError(f"no recorded SQL contains {needle!r}")


@pytest.fixture
def rec(monkeypatch):
    """Recorder wired into both modules get_today reads through."""
    r = Recorder()
    monkeypatch.setattr(today_service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(reminders_service, "pg_fetchall", r.fetchall)
    return r


@pytest.fixture
def pinned_today(monkeypatch):
    """Freeze the app's notion of today. Patching the module attribute covers every
    caller, since all of them look it up on `crm.gtd_common` at call time."""
    monkeypatch.setattr(gtd_common, "today_local_str", lambda: TODAY)
    return TODAY


def task(id, *, due="", star=False, owner=None, title="t"):
    return {"id": id, "title": title, "due_date": due, "owner_id": owner, "star": star}


def reminder(id, due_at, message="ping"):
    return {"id": id, "message": message, "due_at": due_at}


# ── The ladder (pure — no mocks, no clock) ────────────────────────────────────

def test_ladder_interleaves_every_source_in_rank_order():
    """The whole feature: starred → overdue → reminders → due-today, one merged list."""
    items = today_service.build_today_items(
        [task(1, due=TODAY), task(2, due="2026-06-01"), task(3, star=True, due="2026-06-02")],
        [reminder("r1", f"{TODAY}T09:00:00+00:00")],
        TODAY,
    )
    assert [(i["rank"], i["id"]) for i in items] == [
        (1, 3), (3, 2), (4, "r1"), (5, 1),
    ]
    assert [i["why"] for i in items] == ["starred", "overdue", "reminder", "due_today"]


def test_starred_overdue_task_is_rank_one_exactly_once():
    """The issue's explicit rule. Also the bucketing's key invariant: a task that
    qualifies twice must not be emitted twice."""
    items = today_service.build_today_items([task(4, due="2026-01-01", star=True)], [], TODAY)
    assert [(i["id"], i["rank"]) for i in items] == [(4, 1)]


def test_starred_bucket_puts_dated_before_undated():
    items = today_service.build_today_items(
        [task(1, star=True), task(2, star=True, due="2026-06-02")], [], TODAY
    )
    assert [i["id"] for i in items] == [2, 1]


def test_overdue_sorts_most_overdue_first():
    items = today_service.build_today_items(
        [task(1, due="2026-06-04"), task(2, due="2026-05-01"), task(3, due="2026-06-03")],
        [], TODAY,
    )
    assert [i["id"] for i in items] == [2, 3, 1]


def test_due_today_tiebreaks_on_id():
    """Date-only granularity has no finer 'earlier due', so the order must still be
    total (#58) — otherwise the client's slice-at-5 cuts arbitrarily."""
    items = today_service.build_today_items(
        [task(9, due=TODAY), task(2, due=TODAY), task(5, due=TODAY)], [], TODAY
    )
    assert [i["id"] for i in items] == [2, 5, 9]


def test_reminders_sort_by_instant_not_by_string():
    """ISO strings sort by their OFFSET as well as their instant, so across a DST
    fall-back the string order and the true order disagree — these two are chosen so a
    lexicographic sort returns them BACKWARDS, which is what makes this test able to
    fail if the parse is ever dropped."""
    earlier = reminder("a", "2026-11-01T01:45:00-05:00")  # 06:45Z — sorts LAST as text
    later = reminder("b", "2026-11-01T01:15:00-06:00")    # 07:15Z — sorts FIRST as text
    assert later["due_at"] < earlier["due_at"]            # the trap, made explicit
    items = today_service.build_today_items([], [later, earlier], "2026-11-01")
    assert [i["id"] for i in items] == ["a", "b"]


def test_reminder_with_unreadable_due_at_sorts_last_but_is_kept():
    """A reminder you cannot order is still a reminder you need to see."""
    items = today_service.build_today_items(
        [], [reminder("bad", "not-a-timestamp"), reminder("ok", f"{TODAY}T10:00:00+00:00")], TODAY
    )
    assert [i["id"] for i in items] == ["ok", "bad"]


def test_reminder_items_carry_no_owner_and_no_internal_sort_key():
    """Reminders are unownable (no column, install-wide), so the row must not imply an
    owner — and the parsed instant is an implementation detail, not payload."""
    (item,) = today_service.build_today_items([], [reminder("r", f"{TODAY}T10:00:00+00:00")], TODAY)
    assert "owner_id" not in item and "instant" not in item
    assert item["due_at"] == f"{TODAY}T10:00:00+00:00"


def test_rank_two_is_reserved_and_never_emitted():
    """Hot+stale deals ship with #125. Reserving the slot is what lets that land as an
    insertion instead of a renumbering of everything below it."""
    items = today_service.build_today_items(
        [task(1, star=True), task(2, due="2026-01-01"), task(3, due=TODAY)],
        [reminder("r", f"{TODAY}T10:00:00+00:00")], TODAY,
    )
    assert {i["rank"] for i in items} == {1, 3, 4, 5}
    assert today_service.RANK_HOT_DEAL == 2


def test_future_or_undated_unstarred_task_is_dropped():
    """Unreachable from the query's membership; dropped rather than mis-bucketed so a
    caller that widens the query cannot silently mis-rank rows."""
    assert today_service.build_today_items(
        [task(1, due="2026-12-31"), task(2)], [], TODAY
    ) == []


def test_empty_inputs_produce_an_empty_list():
    assert today_service.build_today_items([], [], TODAY) == []


# ── local_day_bounds ──────────────────────────────────────────────────────────

def utc_hours(start, end):
    """DST length must be measured in absolute time: subtracting two datetimes that
    carry the same ZoneInfo does WALL-clock arithmetic and always reports 24h."""
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600


def test_day_bounds_span_one_ordinary_day(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    start, end = localtime.local_day_bounds(datetime(2026, 6, 5).date())
    assert start.isoformat() == "2026-06-05T00:00:00-05:00"
    assert end.isoformat() == "2026-06-06T00:00:00-05:00"
    assert utc_hours(start, end) == 24


def test_day_bounds_is_23_hours_on_the_spring_forward_day(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    start, end = localtime.local_day_bounds(datetime(2026, 3, 8).date())
    assert utc_hours(start, end) == 23


def test_day_bounds_is_25_hours_on_the_fall_back_day(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    start, end = localtime.local_day_bounds(datetime(2026, 11, 1).date())
    assert utc_hours(start, end) == 25


def test_day_bounds_falls_back_to_utc_on_a_bogus_timezone(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "Not/AZone")
    start, end = localtime.local_day_bounds(datetime(2026, 6, 5).date())
    assert start.utcoffset().total_seconds() == 0
    assert utc_hours(start, end) == 24


def test_day_bounds_defaults_to_today_and_is_always_aware(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "UTC")
    start, end = localtime.local_day_bounds()
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start.date() == localtime.today_local()


# ── SQL shape ─────────────────────────────────────────────────────────────────

def test_tasks_query_carries_the_house_predicates(rec, pinned_today):
    """A new task reader must sweep dropped todos and tasks on archived deals."""
    today_service.get_today()
    sql = rec.sql_containing("FROM tasks")
    assert service.NOT_DROPPED_TASK in sql
    assert service.LIVE_TASK_PREDICATE in sql
    assert "completed = 0" in sql
    assert "::date" not in sql  # TEXT compare — a malformed row must not 500


def test_tasks_and_reminders_queries_end_their_order_by_on_id(rec, pinned_today):
    """#58's total order. The determinism guard's AST sweep skips uncapped SQL, so this
    is the only thing standing between a later LIMIT and a non-deterministic window."""
    today_service.get_today()
    assert rec.sql_containing("FROM tasks").endswith("ORDER BY (due_date = '') ASC, due_date ASC, id ASC")
    assert rec.sql_containing("FROM reminders").endswith("ORDER BY due_at ASC, id ASC")


def test_owner_scope_includes_unassigned_tasks(rec, pinned_today):
    """The issue's rule: unowned work appears in 'my' view because someone has to catch
    it. Deliberately wider than list_tasks' strict owner filter."""
    today_service.get_today(owner_id=7)
    sql = rec.sql_containing("FROM tasks")
    assert "(owner_id = %s OR owner_id IS NULL)" in sql
    assert rec.params_for("FROM tasks") == [TODAY, 7]


def test_absent_owner_id_filters_nothing(rec, pinned_today):
    today_service.get_today()
    assert "owner_id = %s" not in rec.sql_containing("FROM tasks")
    assert rec.params_for("FROM tasks") == [TODAY]


def test_reminders_are_not_owner_filtered_in_any_scope(rec, pinned_today):
    """They have no owner column — install-wide by design. Pinned so a later 'fix'
    that adds one has to confront this decision instead of quietly narrowing it."""
    today_service.get_today(owner_id=7)
    sql = rec.sql_containing("FROM reminders")
    assert "owner_id" not in sql
    assert "status = 'pending'" in sql


def test_one_clock_bounds_both_reads(rec, pinned_today, monkeypatch):
    """The panel and the GTD Today view must agree on 'today', and the two reads within
    one request must agree with each other: a second clock read can straddle midnight
    and bound tasks to one day but reminders to the next."""
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    out = today_service.get_today()
    assert out["date"] == TODAY
    assert rec.params_for("FROM tasks") == [TODAY]
    start, end = rec.params_for("FROM reminders")
    assert start.isoformat() == f"{TODAY}T00:00:00-05:00"
    assert end.isoformat() == "2026-06-06T00:00:00-05:00"
    assert out["next_refresh_at"] == end.isoformat()


def test_payload_envelope(rec, pinned_today):
    rec.fetchall_queue = [[task(1, due=TODAY)], []]
    out = today_service.get_today(owner_id=3)
    assert out["date"] == TODAY
    assert out["scope"] == {"owner_id": 3}
    assert [i["id"] for i in out["items"]] == [1]


# ── Route ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


def test_route_returns_the_payload(client, monkeypatch):
    monkeypatch.setattr(today_service, "get_today", lambda owner_id=None: {
        "date": TODAY, "next_refresh_at": "x", "scope": {"owner_id": owner_id}, "items": []
    })
    res = client.get("/api/crm/dashboard/today")
    assert res.status_code == 200
    assert res.json()["scope"] == {"owner_id": None}


def test_route_forwards_owner_id_as_an_int(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(today_service, "get_today", lambda owner_id=None: seen.setdefault("owner", owner_id) and None or {"items": []})
    client.get("/api/crm/dashboard/today?owner_id=12")
    assert seen["owner"] == 12


# ── The local-day sweep this panel forced (see the module docstring) ──────────

def test_dashboard_overdue_uses_the_configured_timezone_day(monkeypatch, pinned_today):
    r = Recorder()
    monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(service, "get_activity_log", lambda limit=10: [])
    service.get_dashboard_stats()
    assert r.params_for("due_date < %s") == [TODAY]


def test_deal_health_overdue_uses_the_configured_timezone_day(monkeypatch, pinned_today):
    r = Recorder()
    monkeypatch.setattr(analytics_service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(analytics_service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(analytics_service.scoring_service, "score_deal",
                        lambda deal_id: {"score": 0, "factors": {}})
    r.fetchone_queue = [{"id": 7, "title": "d", "stage": "lead", "value": 0, "currency": "USD",
                         "probability": 0, "expected_close_date": "", "lost_reason": None,
                         "contact_id": None, "company_id": None, "archived": False,
                         "contact_name": None, "company_name": None, "days_since_touch": 1,
                         "days_in_stage": 1, "open_tasks": 0, "overdue_tasks": 0,
                         "note_count": 0, "activity_count": 0}]
    analytics_service.get_deal_health(7)
    assert TODAY in r.params_for("FROM deals d")


def test_digest_counts_use_the_configured_timezone_day(monkeypatch, pinned_today):
    """The digest tells a person in prose what is due today. On a UTC day an evening run
    west of Greenwich reported TOMORROW's tasks as due today."""
    r = Recorder()
    monkeypatch.setattr(proactive_service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(proactive_service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(analytics_service, "get_stale_deals",
                        lambda limit=5: {"total_stale": 0, "stale_days": 14})
    proactive_service.collect_digest()
    assert r.params_for("FILTER (WHERE due_date = %s)") == [TODAY, TODAY]
