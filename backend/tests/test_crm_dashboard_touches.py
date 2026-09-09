"""Hermetic Weekly Touches tests (issue #76) — window resolution, SQL shape, router.

Mirrors cake_os's ``test_weekly_touches.py``: DB-free, with the pg helpers
monkeypatched per test (the same Recorder pattern as ``test_crm_analytics.py``).
Window resolution is a pure function, so the interesting half needs no mock at all.
"""

from datetime import datetime, timezone

import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import service
from crm.router import router as crm_router


class FakeCursor:
    """A cursor over the same queue the helpers read, for the paths that manage their own.

    Rows go back out as TUPLES with a real ``description``, so ``row_to_dict`` runs for
    real rather than being monkeypatched away — which matters here specifically, because
    the drill-down reuses ONE cursor for two statements and ``row_to_dict`` reads
    ``cursor.description``. A stub that handed back dicts would make the one bug this
    shape is prone to (converting after the next ``execute``) invisible.
    """

    def __init__(self, rec):
        self._rec = rec
        self._rows: list = []
        self.description = None

    def execute(self, sql, params=()):
        # Isolation-level statements carry no rows and are not part of the recorded SQL.
        if sql.strip().upper().startswith("SET TRANSACTION"):
            self._rows = []
            self.description = None
            return
        self._rec.calls.append((" ".join(sql.split()), list(params)))
        rows = self._rec.fetchall_queue.pop(0) if self._rec.fetchall_queue else []
        self._rows = [tuple(r.values()) for r in rows]
        self.description = [(k,) for k in rows[0]] if rows else []

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rec):
        self._rec = rec

    def cursor(self):
        return FakeCursor(self._rec)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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

    def connection(self):
        return FakeConnection(self)

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
    r = Recorder()
    monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
    # The drill-down runs both its reads on ONE cursor so they share a snapshot, so the
    # recorder has to be able to serve that path too.
    monkeypatch.setattr(service, "get_connection", lambda: r.connection())
    return r


# ── Window resolution (pure — no DB) ─────────────────────────────────────────

def test_default_window_is_rolling_last_7_days():
    start, end, label, custom = service._resolve_touch_window(None, None)
    assert label == "Last 7 days"
    assert custom is False
    assert (end - start).days == 7
    assert start.tzinfo is not None and end.tzinfo is not None


def test_blank_params_are_treated_as_absent_not_malformed():
    """A cleared filter sends empty strings — that's the default window, not a 400."""
    _, _, label, custom = service._resolve_touch_window("", "   ")
    assert (label, custom) == ("Last 7 days", False)


def test_custom_window_is_inclusive_of_the_whole_end_day():
    start, end, label, custom = service._resolve_touch_window("2026-06-16", "2026-06-20")
    assert start == datetime(2026, 6, 16, tzinfo=timezone.utc)
    # Exclusive bound is the NEXT day's midnight, so the 20th counts in full.
    assert end == datetime(2026, 6, 21, tzinfo=timezone.utc)
    assert label == "2026-06-16 – 2026-06-20"
    assert custom is True


def test_single_day_window_spans_exactly_that_day():
    start, end, _, _ = service._resolve_touch_window("2026-06-20", "2026-06-20")
    assert (end - start).days == 1


@pytest.mark.parametrize("start,end", [("2026-06-16", None), (None, "2026-06-20")])
def test_half_specified_range_is_rejected(start, end):
    with pytest.raises(ValueError, match="both start and end"):
        service._resolve_touch_window(start, end)


@pytest.mark.parametrize(
    "bad", ["2026/06/16", "June 16", "20260616", "2026-6-1", "2026-02-30"]
)
def test_malformed_dates_are_rejected(bad):
    with pytest.raises(ValueError):
        service._resolve_touch_window(bad, "2026-06-20")


def test_max_date_raises_value_error_not_overflow_error():
    """The inclusive end-day bound adds a day, which overflows at datetime.max. The
    date picker's year spinner reaches 9999, and OverflowError is NOT a ValueError —
    so without the guard it escapes the router's handler as an unhandled 500."""
    with pytest.raises(ValueError):
        service._resolve_touch_window("9999-12-31", "9999-12-31")


def test_reversed_range_is_rejected():
    with pytest.raises(ValueError, match="on or after"):
        service._resolve_touch_window("2026-06-20", "2026-06-16")


# The drill-down takes the SAME calendar days the card takes — there is no separate
# instant format any more (see `get_weekly_touch_detail`).
_START, _END = "2026-06-16", "2026-06-20"
_BOUNDS = [
    datetime(2026, 6, 16, tzinfo=timezone.utc),
    datetime(2026, 6, 21, tzinfo=timezone.utc),
]


# ── Owner parsing ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [("7", 7), (" 12 ", 12), ("2147483647", 2147483647)])
def test_parse_touch_owner_accepts_ids(raw, expected):
    assert service.parse_touch_owner(raw) == expected


def test_parse_touch_owner_maps_the_literal_to_the_null_bucket():
    assert service.parse_touch_owner(service.TOUCH_OWNER_UNASSIGNED) is None


@pytest.mark.parametrize(
    "raw",
    [
        "", None, "abc", "-1", "7.0", "0",
        "Unassigned",      # case-sensitive: the URL has exactly one spelling
        "²",          # str.isdigit() is True here but int() raises — a 500, not a 400
        "٣",          # int() DOES accept this one, so only an ASCII-only regex rejects it
        "2147483648",      # past a 32-bit column — out of range at the database, not here
        "99999999999999999999",
        "9" * 5000,        # past int()'s own 4300-digit limit, whose ValueError message
                           # names sys.set_int_max_str_digits and would be handed to the
                           # caller verbatim as the 400 detail
    ],
)
def test_parse_touch_owner_rejects_everything_else(raw):
    with pytest.raises(ValueError, match="user id or 'unassigned'"):
        service.parse_touch_owner(raw)


# ── Payload shaping + SQL ────────────────────────────────────────────────────

def _rep(user_id, name="", email="", open_deals=0, computed=0, touched=0):
    """One row as `_touch_rep_rows` returns it."""
    return {
        "user_id": user_id, "name": name, "email": email,
        "open_deals": open_deals, "computed_deals": computed, "touched_deals": touched,
    }


def _deal(deal_id, owner_id, touch_count=1):
    return {
        "id": deal_id, "title": f"Deal {deal_id}", "value": 100, "stage": "proposal",
        "owner_id": owner_id, "touch_count": touch_count,
        "touched_at": "2026-06-18T00:00:00+00:00",
        "contact_name": None, "company_name": None,
    }


def test_payload_shape_and_window_bounds_are_passed_to_both_queries(rec):
    rec.fetchall_queue = [
        [_rep(3, "Ada", open_deals=9, computed=4, touched=3)],
        [_deal(2, 3, touch_count=7)],
    ]

    out = service.get_weekly_touches(start="2026-06-16", end="2026-06-20")

    assert out["total_open_deals"] == 9
    assert out["total_touches"] == 3
    assert out["computed_deals"] == 4
    assert out["window"]["label"] == "2026-06-16 – 2026-06-20"
    assert out["window"]["custom"] is True
    assert [d["id"] for d in out["reps"][0]["deals"]] == [2]

    bounds = (datetime(2026, 6, 16, tzinfo=timezone.utc),
              datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert rec.params_for("GROUP BY d.owner_id") == list(bounds)
    # The row query takes the same bounds, then the PER-REP cap.
    assert rec.params_for("LEFT JOIN companies")[:2] == list(bounds)
    assert rec.params_for("LEFT JOIN companies")[2] == service.WEEKLY_TOUCHES_LIMIT


def test_counts_only_live_open_deals(rec):
    """Archived (#22) and closed deals must not inflate either side of the ratio."""
    service.get_weekly_touches()

    for sql in (rec.sql_containing("FILTER"), rec.sql_containing("LEFT JOIN companies")):
        assert service.LIVE_PREDICATE_D in sql
        assert service.OPEN_PREDICATE_D in sql


def test_window_membership_uses_last_touch_not_the_ai_watermark(rec):
    """Window membership must come from LAST_TOUCH_SQL (keyless, event-grained), NOT
    from deals.ai_touch_count_at. That column is #16's stale-write-guard watermark: it
    only advances when a provider answered, and it falls back to the deal's created_at
    — so keying the window off it made every provider timeout silently drop a deal from
    the count, and disagreed with the stale-deal panel on the same page."""
    service.get_weekly_touches()

    for sql in (rec.sql_containing("FILTER"), rec.sql_containing("LEFT JOIN companies")):
        assert "ai_touch_count_at" not in sql
        assert "GREATEST" in sql  # the LAST_TOUCH_SQL expression

    # The per-deal NUMBER is still #16's count — that is the "#16 data" the issue
    # asked to key off, and it is what drives the zero-keys gate.
    rows_sql = rec.sql_containing("LEFT JOIN companies")
    assert "ai_touch_count AS touch_count" in rows_sql


def test_creating_a_deal_is_not_a_touch(rec):
    """create_deal leaves updated_at == created_at, so without this guard a bulk
    import or a sample-data load reports every brand-new deal as worked."""
    service.get_weekly_touches()

    for sql in (rec.sql_containing("FILTER"), rec.sql_containing("LEFT JOIN companies")):
        assert "<> " in sql and "created_at" in sql


def test_computed_deals_is_not_scoped_to_the_window(rec):
    """The gate must stay window-INDEPENDENT. If computed_deals ever gets pulled
    inside the FILTER that scopes touched_deals, "no provider configured" silently
    becomes "no touches this week" and the card vanishes during a quiet week on a
    fully-configured install — the exact failure the hidden-affordance rule forbids."""
    rec.fetchall_queue = [[_rep(3, computed=2), _rep(None, computed=0)], []]
    out = service.get_weekly_touches()

    totals_sql = rec.sql_containing("FILTER")
    assert totals_sql.index("computed_deals") < totals_sql.index("FILTER")
    # Summed across every bucket, so a shaper that read only the first row would fail here.
    assert out["computed_deals"] == 2


def test_computed_deals_is_the_zero_keys_gate(rec):
    """No provider configured → the worker never ran → every ai_touch_count is NULL.
    The touch COUNTS stay real (membership is keyless); it is computed_deals == 0 that
    tells the client to hide the card rather than render rows of blank estimates."""
    rec.fetchall_queue = [[_rep(3, open_deals=12, computed=0, touched=4)], []]
    out = service.get_weekly_touches()

    assert out["computed_deals"] == 0
    # Both sides of the ratio still report honestly — the CRM has deals and real
    # touches, just no AI estimates to put beside them.
    assert (out["total_touches"], out["total_open_deals"]) == (4, 12)


def test_null_scalars_degrade_to_zero_not_none(rec):
    """model_dump/SQL NULLs make the key PRESENT but None, so .get(k, 0) wouldn't
    fire — the shaper must use `or 0` or the UI receives None."""
    rec.fetchall_queue = [
        [{"user_id": 3, "name": "Ada", "email": "", "open_deals": None,
          "computed_deals": None, "touched_deals": None}],
        [],
    ]
    out = service.get_weekly_touches()
    assert (out["total_open_deals"], out["computed_deals"], out["total_touches"]) == (0, 0, 0)
    assert out["reps"][0]["open_deals"] == 0


def test_no_rep_rows_degrades_to_zeros_and_an_empty_list(rec):
    """An empty CRM (or a mock with nothing queued) must not raise."""
    out = service.get_weekly_touches()
    assert out["total_open_deals"] == 0
    assert out["reps"] == []


# ── Per-rep grouping (issue #146) ────────────────────────────────────────────

def test_totals_are_the_sum_of_the_buckets_including_unassigned(rec):
    """The headline is arithmetically the rows beneath it.

    This pins the SHAPER, not the SQL — the Recorder hands back whatever rows it is
    given. The SQL-side claim (that the unowned bucket is never excluded) is
    `test_grouped_query_never_excludes_the_unassigned_bucket` below, and the real
    grouping is exercised against Postgres in test_weekly_touches_integration.py.
    """
    reps = [
        _rep(3, "Ada", open_deals=5, touched=2),
        _rep(4, "Sam", open_deals=4, touched=0),
        _rep(None, open_deals=3, touched=1),
    ]
    rec.fetchall_queue = [reps, []]
    out = service.get_weekly_touches()

    assert out["total_open_deals"] == sum(r["open_deals"] for r in out["reps"])
    assert out["total_touches"] == sum(r["touches"] for r in out["reps"])
    assert (out["total_open_deals"], out["total_touches"]) == (12, 3)


def test_grouped_query_never_excludes_the_unassigned_bucket(rec):
    """The one edit that would silently drop unowned deals — and leave every other
    assertion in this file green — is an owner_id IS NOT NULL filter or an INNER JOIN
    onto users. The blueprint's query does exactly that; ours must not."""
    service.get_weekly_touches()
    sql = rec.sql_containing("GROUP BY d.owner_id")

    assert "owner_id IS NOT NULL" not in sql
    assert "LEFT JOIN users" in sql


def test_deal_cap_is_per_rep_and_applied_after_the_window_filter(rec):
    """A global LIMIT would leave rep rows showing a count with no rows beneath them.
    And the cap must rank only deals that COUNT: capping before the window filter would
    silently return fewer than the limit for a rep with older untouched deals."""
    service.get_weekly_touches()
    sql = rec.sql_containing("LEFT JOIN companies")

    assert "PARTITION BY d.owner_id" in sql
    assert " LIMIT " not in sql
    # Structural, not merely "both substrings exist": the window filter is inside the
    # ranked subquery and the cap is outside it.
    assert sql.index("t.last_touch >= %s") < sql.index(") ranked")
    assert sql.index(") ranked") < sql.index("rn <= %s")


def test_a_rep_with_zero_touches_still_gets_a_row(rec):
    """Seeing who did nothing is the point of a weekly accountability pull, so reps come
    from the aggregate query, never from the deal rows."""
    rec.fetchall_queue = [[_rep(4, "Sam", open_deals=6, touched=0)], []]
    out = service.get_weekly_touches()

    assert len(out["reps"]) == 1
    assert (out["reps"][0]["touches"], out["reps"][0]["deals"]) == (0, [])


def test_unassigned_bucket_is_named_unassigned_and_sinks_last(rec):
    """Even when it leads on touches. 'Unassigned' is the OWNERSHIP word — _shape_per_rep's
    'Unattributed' is about authorship and would be wrong here."""
    rec.fetchall_queue = [
        [_rep(None, open_deals=9, touched=9), _rep(3, "Ada", open_deals=1, touched=1)],
        [],
    ]
    out = service.get_weekly_touches()

    assert [r["user_id"] for r in out["reps"]] == [3, None]
    assert out["reps"][-1]["name"] == "Unassigned"


def test_reps_sort_by_touches_then_open_deals_then_name(rec):
    """Each tiebreak is exercised by a pair that is tied on everything above it, so no
    key in the tuple can be deleted without turning this red."""
    rec.fetchall_queue = [
        [
            # Tied on touches AND open_deals → only the name separates them.
            _rep(1, "Zoe", open_deals=2, touched=1),
            _rep(2, "Ada", open_deals=2, touched=1),
            # Tied on touches with the pair above, but a bigger book → outranks both.
            _rep(4, "Mo", open_deals=7, touched=1),
            # Most touches → leads regardless of the smaller book.
            _rep(3, "Bob", open_deals=1, touched=5),
        ],
        [],
    ]
    out = service.get_weekly_touches()
    assert [r["name"] for r in out["reps"]] == ["Bob", "Mo", "Ada", "Zoe"]


def test_deal_rows_attach_under_their_owner_in_query_order(rec):
    """The query's ORDER BY is what the per-rep cap was computed against, so re-sorting
    here would show a different ten than the one the cap selected."""
    rec.fetchall_queue = [
        [_rep(3, "Ada", open_deals=3, touched=2), _rep(None, open_deals=1, touched=1)],
        [_deal(9, 3), _deal(8, None), _deal(2, 3)],
    ]
    out = service.get_weekly_touches()
    by_owner = {r["user_id"]: [d["id"] for d in r["deals"]] for r in out["reps"]}

    assert by_owner == {3: [9, 2], None: [8]}


def test_a_deal_whose_bucket_vanished_is_dropped_not_rendered_as_a_phantom_rep(rec):
    """Two reads, two snapshots: a write between them can return a deal whose owner has
    no aggregate row. Dropping it costs one row on one refresh; minting a bucket would
    render '0 of 0 touched' above real deals, which is a visible lie."""
    rec.fetchall_queue = [[_rep(3, "Ada", open_deals=1, touched=1)], [_deal(9, 3), _deal(8, 4)]]
    out = service.get_weekly_touches()

    assert [r["user_id"] for r in out["reps"]] == [3]
    assert [d["id"] for d in out["reps"][0]["deals"]] == [9]


def test_rep_label_falls_back_from_name_to_email_to_id(rec):
    rec.fetchall_queue = [
        [_rep(5, "  ", "sam@example.test", open_deals=1), _rep(6, "", "", open_deals=1)],
        [],
    ]
    out = service.get_weekly_touches()
    assert {r["user_id"]: r["name"] for r in out["reps"]} == {
        5: "sam@example.test", 6: "User 6",
    }


# ── Drill-down detail (issue #146) ───────────────────────────────────────────

@pytest.fixture
def known_user(monkeypatch):
    """`get_user` returns the row including a password hash — the detail payload must
    never carry it, only the resolved label."""
    user = {"id": 7, "name": "Dana", "email": "dana@example.test", "password_hash": "x"}
    monkeypatch.setattr(service.users_service, "get_user", lambda uid: user if uid == 7 else None)
    return user


def test_detail_scopes_both_queries_to_the_owner(rec, known_user):
    rec.fetchall_queue = [[_rep(7, "Dana", open_deals=4, touched=2)], [_deal(1, 7)]]
    out = service.get_weekly_touch_detail(owner_id=7, start=_START, end=_END)

    for needle in ("GROUP BY d.owner_id", "LEFT JOIN companies"):
        assert "d.owner_id = %s" in rec.sql_containing(needle)
        assert rec.params_for(needle)[2] == 7
    assert out["rep"] == {"user_id": 7, "name": "Dana", "open_deals": 4, "touches": 2}
    assert out["truncated"] is False
    assert "password_hash" not in out["rep"]
    assert [d["id"] for d in out["deals"]] == [1]
    # No `custom` on this window: it means "the user picked a range" on the card, and
    # forwarded rolling bounds would set it while the card said "Last 7 days".
    assert set(out["window"]) == {"start", "end", "label"}


def test_detail_for_unassigned_uses_is_null_and_never_looks_up_a_user(rec, monkeypatch):
    called = []
    monkeypatch.setattr(service.users_service, "get_user", lambda uid: called.append(uid))
    rec.fetchall_queue = [[_rep(None, open_deals=2, touched=1)], [_deal(5, None)]]

    out = service.get_weekly_touch_detail(owner_id=None, start=_START, end=_END)

    assert called == []
    for needle in ("GROUP BY d.owner_id", "LEFT JOIN companies"):
        assert "d.owner_id IS NULL" in rec.sql_containing(needle)
    # No owner param is bound on the IS NULL branch; the rows query still carries its cap.
    assert rec.params_for("GROUP BY d.owner_id") == _BOUNDS
    assert rec.params_for("LEFT JOIN companies") == _BOUNDS + [
        service.WEEKLY_TOUCHES_DETAIL_MAX + 1
    ]
    assert out["rep"]["name"] == "Unassigned"


def test_detail_asks_for_far_more_than_the_card_and_probes_one_past_its_ceiling(rec, known_user):
    """The card caps each rep at ten; this page's job is the rest of the list. It is still
    bounded — an unbounded query on a very large book is a production hazard — and it asks
    for one row past the ceiling so a full page can be told from an overflowing one without
    a second COUNT."""
    service.get_weekly_touch_detail(owner_id=7, start=_START, end=_END)

    cap = rec.params_for("LEFT JOIN companies")[-1]
    assert cap == service.WEEKLY_TOUCHES_DETAIL_MAX + 1
    assert cap > service.WEEKLY_TOUCHES_LIMIT


def test_detail_reports_truncation_and_keeps_the_aggregate_count(rec, known_user):
    """A page whose contract is "the full list" must not quietly serve a prefix — and once
    the list IS a prefix, the count can no longer be derived from it."""
    over = service.WEEKLY_TOUCHES_DETAIL_MAX + 1
    rec.fetchall_queue = [
        [_rep(7, "Dana", open_deals=900, touched=800)],
        [_deal(i, 7) for i in range(over)],
    ]
    out = service.get_weekly_touch_detail(owner_id=7, start=_START, end=_END)

    assert out["truncated"] is True
    assert len(out["deals"]) == service.WEEKLY_TOUCHES_DETAIL_MAX
    assert out["rep"]["touches"] == 800


def test_detail_returns_none_for_an_unknown_user_before_reading_any_deals(rec, known_user):
    assert service.get_weekly_touch_detail(owner_id=999, start=_START, end=_END) is None
    assert rec.calls == []


@pytest.mark.parametrize("call", ["card", "detail"])
def test_both_surfaces_read_on_one_repeatable_read_snapshot(rec, known_user, monkeypatch, call):
    """Each surface shows a count and the rows behind it on one screen, so two snapshots
    are a way to render a contradiction — "6 of 5 open deals touched", a rep row with more
    deals under it than its own number admits, or every row dropped because the bucket
    vanished between the reads."""
    seen: list[str] = []

    class Recording(FakeCursor):
        def execute(self, sql, params=()):
            seen.append(" ".join(sql.split()))
            super().execute(sql, params)

    one_cursor = Recording(rec)

    class OneCursorConnection(FakeConnection):
        def cursor(self):
            return one_cursor

    monkeypatch.setattr(service, "get_connection", lambda: OneCursorConnection(rec))
    if call == "card":
        service.get_weekly_touches()
    else:
        service.get_weekly_touch_detail(owner_id=7, start=_START, end=_END)

    assert seen[0].upper().startswith("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    # Both reads land on that ONE cursor, in order, so they cannot describe two instants.
    assert "GROUP BY d.owner_id" in seen[1]
    assert "LEFT JOIN companies" in seen[2]
    assert len(seen) == 3


def test_detail_validates_the_window_before_touching_the_database(rec, known_user):
    with pytest.raises(ValueError):
        service.get_weekly_touch_detail(owner_id=7, start="nonsense", end=_END)
    assert rec.calls == []


def test_detail_for_a_rep_with_nothing_open_is_a_zero_row_not_a_missing_page(rec, known_user):
    out = service.get_weekly_touch_detail(owner_id=7, start=_START, end=_END)

    assert out["rep"] == {"user_id": 7, "name": "Dana", "open_deals": 0, "touches": 0}
    assert out["deals"] == []


def test_dashboard_stats_reports_company_count(rec):
    rec.fetchone_queue = [
        {"cnt": 5},   # contacts
        {"cnt": 3},   # companies
        {"cnt": 1},   # overdue tasks
        {"cnt": 2},   # pending tasks
    ]
    out = service.get_dashboard_stats()
    assert out["total_contacts"] == 5
    assert out["total_companies"] == 3


# ── Router ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


def test_route_passes_params_through(client, monkeypatch):
    seen = {}

    def fake(start=None, end=None):
        seen.update(start=start, end=end)
        return {"window": {}, "deals": [], "total_touches": 0,
                "total_open_deals": 0, "computed_deals": 0}

    monkeypatch.setattr(service, "get_weekly_touches", fake)
    res = client.get("/api/crm/dashboard/weekly-touches?start=2026-06-16&end=2026-06-20")

    assert res.status_code == 200
    assert seen == {"start": "2026-06-16", "end": "2026-06-20"}


def test_route_maps_bad_range_to_400(client, monkeypatch):
    def fake(start=None, end=None):
        raise ValueError("end date must be on or after start date")

    monkeypatch.setattr(service, "get_weekly_touches", fake)
    res = client.get("/api/crm/dashboard/weekly-touches?start=2026-06-20&end=2026-06-16")

    assert res.status_code == 400
    assert "on or after" in res.json()["detail"]


def test_weekly_touches_route_is_not_shadowed_by_the_dashboard_route(client, monkeypatch):
    """/dashboard/weekly-touches must reach its own handler, not GET /dashboard."""
    monkeypatch.setattr(service, "get_dashboard_stats", lambda: {"sentinel": "dashboard"})
    monkeypatch.setattr(
        service, "get_weekly_touches",
        lambda start=None, end=None: {"sentinel": "touches"},
    )
    res = client.get("/api/crm/dashboard/weekly-touches")

    assert res.json() == {"sentinel": "touches"}


# ── Drill-down route (issue #146) ────────────────────────────────────────────

@pytest.fixture
def detail_spy(monkeypatch):
    """Records the kwargs the route hands the service, and returns a sentinel payload."""
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return {"sentinel": "detail"}

    monkeypatch.setattr(service, "get_weekly_touch_detail", fake)
    return seen


def test_detail_route_maps_the_owner_and_forwards_the_window(client, detail_spy):
    res = client.get(
        "/api/crm/dashboard/weekly-touches/detail?owner=7&start=2026-06-16&end=2026-06-20"
    )

    assert res.status_code == 200
    assert detail_spy["owner_id"] == 7
    # The SAME calendar days the card takes, forwarded verbatim — the client does no date
    # arithmetic, so a hand-typed URL and a card link resolve identically.
    assert detail_spy["start"] == "2026-06-16"
    assert detail_spy["end"] == "2026-06-20"


def test_detail_route_maps_the_unassigned_literal_to_none(client, detail_spy):
    res = client.get("/api/crm/dashboard/weekly-touches/detail?owner=unassigned")

    assert res.status_code == 200
    assert detail_spy["owner_id"] is None


def test_detail_route_requires_an_owner(client, detail_spy):
    """Absent is never a state here: the drill-down is always exactly one bucket."""
    res = client.get("/api/crm/dashboard/weekly-touches/detail")
    assert res.status_code == 422


def test_detail_route_maps_a_bad_owner_to_400(client, detail_spy):
    res = client.get("/api/crm/dashboard/weekly-touches/detail?owner=dana")

    assert res.status_code == 400
    assert "unassigned" in res.json()["detail"]
    assert detail_spy == {}  # rejected before the service was ever called


def test_detail_route_maps_a_bad_window_to_400(client, monkeypatch):
    def fake(**kwargs):
        raise ValueError("end date must be on or after start date")

    monkeypatch.setattr(service, "get_weekly_touch_detail", fake)
    res = client.get(
        "/api/crm/dashboard/weekly-touches/detail?owner=7&start=2026-06-20&end=2026-06-16"
    )

    assert res.status_code == 400
    assert "on or after" in res.json()["detail"]


def test_detail_route_maps_an_unknown_user_to_404(client, monkeypatch):
    monkeypatch.setattr(service, "get_weekly_touch_detail", lambda **kwargs: None)
    res = client.get("/api/crm/dashboard/weekly-touches/detail?owner=999")

    assert res.status_code == 404


def test_detail_route_is_not_shadowed_by_its_siblings(client, monkeypatch):
    """It sits under /dashboard/weekly-touches, so both ancestors could swallow it."""
    monkeypatch.setattr(service, "get_dashboard_stats", lambda: {"sentinel": "dashboard"})
    monkeypatch.setattr(
        service, "get_weekly_touches",
        lambda start=None, end=None: {"sentinel": "touches"},
    )
    monkeypatch.setattr(service, "get_weekly_touch_detail", lambda **kwargs: {"sentinel": "detail"})
    res = client.get("/api/crm/dashboard/weekly-touches/detail?owner=unassigned")

    assert res.json() == {"sentinel": "detail"}
