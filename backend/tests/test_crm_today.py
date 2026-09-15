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

TODAY = "2026-06-05"

#: Needle for the hot-deals read. NOT "FROM deals": `LIVE_TASK_PREDICATE` carries a
#: `FROM deals` subquery of its own, so that needle finds the TASKS statement first and
#: every assertion below would be made against the wrong query.
HOT_DEALS = "deal_temperature = 'hot'"


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
    """Recorder wired into the reads get_today makes."""
    r = Recorder()
    monkeypatch.setattr(today_service, "pg_fetchall", r.fetchall)
    return r


@pytest.fixture
def pinned_today(monkeypatch):
    """Freeze the app's notion of today. Patching the module attribute covers every
    caller, since all of them look it up on `crm.gtd_common` at call time."""
    monkeypatch.setattr(gtd_common, "today_local_str", lambda: TODAY)
    return TODAY


def task(id, *, due="", star=False, owner=None, title="t"):
    return {"id": id, "title": title, "due_date": due, "owner_id": owner, "star": star}


def deal(id, *, idle_days=0.0, stale=False, value=0.0, owner=None, title=None):
    """A row shaped like `_fetch_hot_deals` returns one.

    `is_stale` is a separate field rather than derived from `idle_days` on purpose: the
    server decides staleness in SQL against the shared interval, and the pure ladder is
    told the answer. Letting the fixture derive it would quietly reintroduce the second
    definition the SQL placement exists to avoid.
    """
    return {
        "id": id,
        "title": title or f"deal {id}",
        "value": value,
        "owner_id": owner,
        "idle_seconds": idle_days * 86400.0,
        "is_stale": stale,
    }


# ── The ladder (pure — no mocks, no clock) ────────────────────────────────────

def test_ladder_interleaves_every_source_in_rank_order():
    """The whole feature: starred → hot+stale → overdue → due-today, one merged list,
    with the unranked hot tail last."""
    items = today_service.build_today_items(
        [task(1, due=TODAY), task(2, due="2026-06-01"), task(3, star=True, due="2026-06-02")],
        TODAY,
        deals=[deal(8, idle_days=20, stale=True), deal(9, idle_days=1)],
    )
    assert [(i["rank"], i["id"]) for i in items] == [
        (1, 3), (2, 8), (3, 2), (4, 1), (None, 9),
    ]
    assert [i["why"] for i in items] == [
        "starred", "hot_stale", "overdue", "due_today", "hot",
    ]


def test_starred_overdue_task_is_rank_one_exactly_once():
    """The issue's explicit rule. Also the bucketing's key invariant: a task that
    qualifies twice must not be emitted twice."""
    items = today_service.build_today_items([task(4, due="2026-01-01", star=True)], TODAY)
    assert [(i["id"], i["rank"]) for i in items] == [(4, 1)]


def test_starred_bucket_puts_dated_before_undated():
    items = today_service.build_today_items(
        [task(1, star=True), task(2, star=True, due="2026-06-02")], TODAY
    )
    assert [i["id"] for i in items] == [2, 1]


def test_overdue_sorts_most_overdue_first():
    items = today_service.build_today_items(
        [task(1, due="2026-06-04"), task(2, due="2026-05-01"), task(3, due="2026-06-03")], TODAY,
    )
    assert [i["id"] for i in items] == [2, 3, 1]


def test_due_today_tiebreaks_on_id():
    """Date-only granularity has no finer 'earlier due', so the order must still be
    total (#58) — otherwise the client's slice-at-5 cuts arbitrarily."""
    items = today_service.build_today_items(
        [task(9, due=TODAY), task(2, due=TODAY), task(5, due=TODAY)], TODAY
    )
    assert [i["id"] for i in items] == [2, 5, 9]


# ── Hot deals: rank 2 and the unranked tail (issue #131) ─────────────────────

def test_ladder_ranks_are_a_pinned_wire_contract():
    """The ladder's numbers are a wire contract the client's types spell out, so they are
    pinned here: the frontend's `CrmTodayTaskItem.rank` union has to say the same thing."""
    assert (today_service.RANK_STARRED, today_service.RANK_HOT_DEAL) == (1, 2)
    assert (today_service.RANK_OVERDUE, today_service.RANK_DUE_TODAY) == (3, 4)


def test_a_hot_stale_deal_outranks_your_own_overdue_work():
    items = today_service.build_today_items(
        [task(1, due="2026-01-01")], TODAY, deals=[deal(8, idle_days=30, stale=True)],
    )
    assert [(i["kind"], i["rank"]) for i in items] == [("deal", 2), ("task", 3)]


def test_only_two_hot_deals_may_sit_above_your_commitments():
    """The issue's anti-flood cap. A neglected pipeline must never bury the calls and
    promises the user made themselves."""
    items = today_service.build_today_items(
        [], TODAY,
        deals=[deal(i, idle_days=40 - i, stale=True) for i in (1, 2, 3, 4)],
    )
    assert [(i["id"], i["rank"]) for i in items] == [(1, 2), (2, 2), (3, None), (4, None)]
    assert today_service.HOT_DEAL_SLOTS == 2


def test_a_hot_deal_touched_recently_is_unranked_not_absent():
    """The issue: it must NOT be in the Top 5, but it must still appear behind the
    expander. A null rank is what says both at once — a sixth rung would say neither,
    since the collapsed card slices ITEMS and would show it beside one overdue task."""
    (item,) = today_service.build_today_items([], TODAY, deals=[deal(8, idle_days=2)])
    assert (item["rank"], item["why"]) == (None, "hot")


def test_a_demoted_stale_deal_keeps_saying_it_is_going_cold():
    """`why` describes the DEAL, `rank` decides the SLOT. Overflow is demoted rather than
    dropped — a panel that silently hides the most neglected deals in the CRM is worse
    than one that puts them a click away — so the badge must stay honest down there."""
    items = today_service.build_today_items(
        [], TODAY, deals=[deal(i, idle_days=40 - i, stale=True) for i in (1, 2, 3)],
    )
    assert [(i["rank"], i["why"]) for i in items] == [
        (2, "hot_stale"), (2, "hot_stale"), (None, "hot_stale"),
    ]


def test_hot_deals_order_on_exact_idle_time_not_on_whole_days():
    """Two deals idle 14d1h and 14d23h floor to the SAME day count, so a day-granular sort
    would let the id decide which takes a scarce slot. That is not most-idle-first."""
    items = today_service.build_today_items(
        [], TODAY,
        deals=[deal(1, idle_days=14 + 1 / 24, stale=True),
               deal(2, idle_days=14 + 23 / 24, stale=True)],
    )
    assert [i["id"] for i in items] == [2, 1]
    assert [i["days_since_touch"] for i in items] == [14, 14]


def test_equal_idle_time_tiebreaks_on_the_unique_id():
    items = today_service.build_today_items(
        [], TODAY, deals=[deal(9, idle_days=20, stale=True), deal(4, idle_days=20, stale=True)],
    )
    assert [i["id"] for i in items] == [4, 9]


def test_a_stale_deal_can_never_be_out_idled_by_a_fresh_one():
    """Which is why the tail needs no second sort key: stale means idle past the
    threshold, so the demoted stale rows land above the recently-touched ones for free."""
    items = today_service.build_today_items(
        [], TODAY,
        deals=[deal(1, idle_days=1), deal(2, idle_days=99, stale=True),
               deal(3, idle_days=60, stale=True), deal(4, idle_days=20, stale=True)],
    )
    assert [(i["id"], i["rank"]) for i in items] == [(2, 2), (3, 2), (4, None), (1, None)]


def test_deal_items_carry_the_payload_and_not_the_sort_key():
    (item,) = today_service.build_today_items(
        [], TODAY, deals=[deal(8, idle_days=12.75, value=30000.0, owner=3, title="Acme")],
    )
    assert item == {
        "kind": "deal", "id": 8, "title": "Acme", "value": 30000.0, "owner_id": 3,
        "days_since_touch": 12, "why": "hot", "rank": None,
    }


def test_days_since_touch_is_never_negative():
    """A deal whose updated_at sits slightly in the future — clock skew on a restore —
    reads as idle 0d, never as idle -1d."""
    (item,) = today_service.build_today_items([], TODAY, deals=[deal(8, idle_days=-0.5)])
    assert item["days_since_touch"] == 0


def test_no_hot_deals_leaves_the_task_ladder_untouched():
    assert today_service.build_today_items([task(1, due=TODAY)], TODAY, deals=[]) == [
        {"kind": "task", "id": 1, "title": "t", "due_date": TODAY, "owner_id": None,
         "rank": 4, "why": "due_today"},
    ]


def test_future_or_undated_unstarred_task_is_dropped():
    """Unreachable from the query's membership; dropped rather than mis-bucketed so a
    caller that widens the query cannot silently mis-rank rows."""
    assert today_service.build_today_items(
        [task(1, due="2026-12-31"), task(2)], TODAY
    ) == []


def test_empty_inputs_produce_an_empty_list():
    assert today_service.build_today_items([], TODAY) == []


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


def test_every_query_ends_its_order_by_on_id(rec, pinned_today):
    """#58's total order. The determinism guard's AST sweep skips uncapped SQL, so this
    is the only thing standing between a later LIMIT and a non-deterministic window."""
    today_service.get_today()
    assert rec.sql_containing("FROM tasks").endswith("ORDER BY (due_date = '') ASC, due_date ASC, id ASC")
    assert rec.sql_containing(HOT_DEALS).endswith("ORDER BY idle_seconds DESC, d.id ASC")


def test_hot_deals_query_imports_its_predicates_rather_than_retyping_them(rec, pinned_today):
    """Hot is a human's column; stale is analytics_service's own definition. Both live in
    ONE place each, so the panel and the "Needs a touch" list cannot drift apart about
    what a touch is or which deals are eligible."""
    today_service.get_today()
    sql = rec.sql_containing(HOT_DEALS)
    assert "d.deal_temperature = 'hot'" in sql
    assert " ".join(service.OPEN_PREDICATE_D.split()) in sql
    assert " ".join(service.LIVE_PREDICATE_D.split()) in sql
    assert " ".join(service.LAST_TOUCH_SQL.split()) in sql
    # The stale test is decided in SQL, against the interval — never in Python from a
    # floored day count, which answers differently at the exact boundary instant.
    assert "make_interval(days => %s)) AS is_stale" in sql


def test_hot_deals_bind_the_stale_threshold_before_the_owner_filter(rec, pinned_today):
    """psycopg2 substitutes in TEXT order and the stale test sits in the SELECT list, so
    the threshold binds first. Asserted as a full sequence with an owner id that is NOT
    the threshold, so a swapped pair cannot pass."""
    today_service.get_today(owner_id=7)
    assert rec.params_for(HOT_DEALS) == [analytics_service.DEFAULT_DEAL_STALE_DAYS, 7]
    assert "(d.owner_id = %s OR d.owner_id IS NULL)" in rec.sql_containing(HOT_DEALS)


def test_hot_deals_are_not_owner_filtered_when_no_scope_is_asked(rec, pinned_today):
    today_service.get_today()
    assert "d.owner_id = %s" not in rec.sql_containing(HOT_DEALS)
    assert rec.params_for(HOT_DEALS) == [analytics_service.DEFAULT_DEAL_STALE_DAYS]


def test_get_today_feeds_the_fetched_deals_into_the_ladder(rec, pinned_today):
    """`build_today_items` defaults `deals` to empty, which is only safe while something
    proves the real caller passes them — a defaulted parameter is otherwise exactly how a
    whole source drops silently out of a merged list."""
    rec.fetchall_queue = [[], [deal(8, idle_days=30, stale=True, value=250.0)]]
    (item,) = today_service.get_today()["items"]
    assert (item["kind"], item["id"], item["rank"]) == ("deal", 8, 2)


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


def test_one_clock_bounds_the_tasks_read_and_the_refresh_boundary(rec, pinned_today, monkeypatch):
    """The panel and the GTD Today view must agree on 'today', and the reload boundary
    must come from that SAME captured day: a second clock read can straddle midnight and
    bound tasks to one day while arming the client's reload for another."""
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    out = today_service.get_today()
    assert out["date"] == TODAY
    assert rec.params_for("FROM tasks") == [TODAY]
    assert out["next_refresh_at"] == "2026-06-06T00:00:00-05:00"


def test_payload_envelope(rec, pinned_today):
    # Two reads, in call order: tasks, hot deals.
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
