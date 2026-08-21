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
    r = Recorder()
    monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
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


# ── Payload shaping + SQL ────────────────────────────────────────────────────

def test_payload_shape_and_window_bounds_are_passed_to_both_queries(rec):
    rec.fetchone_queue = [{"open_deals": 9, "computed_deals": 4, "touched_deals": 3}]
    rec.fetchall_queue = [[
        {"id": 2, "title": "Big deal", "value": 500, "stage": "proposal",
         "touch_count": 7, "touched_at": "2026-06-18T00:00:00+00:00",
         "contact_name": "Ada", "company_name": "Acme"},
    ]]

    out = service.get_weekly_touches(start="2026-06-16", end="2026-06-20")

    assert out["total_open_deals"] == 9
    assert out["total_touches"] == 3
    assert out["computed_deals"] == 4
    assert out["window"]["label"] == "2026-06-16 – 2026-06-20"
    assert out["window"]["custom"] is True
    assert [d["id"] for d in out["deals"]] == [2]

    bounds = (datetime(2026, 6, 16, tzinfo=timezone.utc),
              datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert rec.params_for("FILTER") == list(bounds)
    # The row query takes the same bounds, then the LIMIT.
    assert rec.params_for("LEFT JOIN companies")[:2] == list(bounds)
    assert rec.params_for("LEFT JOIN companies")[2] == service.WEEKLY_TOUCHES_LIMIT


def test_counts_only_live_open_deals(rec):
    """Archived (#22) and closed deals must not inflate either side of the ratio."""
    rec.fetchone_queue = [{"open_deals": 0, "computed_deals": 0, "touched_deals": 0}]
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
    rec.fetchone_queue = [{"open_deals": 3, "computed_deals": 3, "touched_deals": 2}]
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
    rec.fetchone_queue = [{"open_deals": 5, "computed_deals": 5, "touched_deals": 0}]
    service.get_weekly_touches()

    for sql in (rec.sql_containing("FILTER"), rec.sql_containing("LEFT JOIN companies")):
        assert "<> " in sql and "created_at" in sql


def test_computed_deals_is_not_scoped_to_the_window(rec):
    """The gate must stay window-INDEPENDENT. If computed_deals ever gets pulled
    inside the FILTER that scopes touched_deals, "no provider configured" silently
    becomes "no touches this week" and the card vanishes during a quiet week on a
    fully-configured install — the exact failure the hidden-affordance rule forbids."""
    rec.fetchone_queue = [{"open_deals": 5, "computed_deals": 5, "touched_deals": 0}]
    service.get_weekly_touches()

    totals_sql = rec.sql_containing("FILTER")
    assert totals_sql.index("computed_deals") < totals_sql.index("FILTER")


def test_computed_deals_is_the_zero_keys_gate(rec):
    """No provider configured → the worker never ran → every ai_touch_count is NULL.
    The touch COUNTS stay real (membership is keyless); it is computed_deals == 0 that
    tells the client to hide the card rather than render rows of blank estimates."""
    rec.fetchone_queue = [{"open_deals": 12, "computed_deals": 0, "touched_deals": 4}]
    out = service.get_weekly_touches()

    assert out["computed_deals"] == 0
    # Both sides of the ratio still report honestly — the CRM has deals and real
    # touches, just no AI estimates to put beside them.
    assert (out["total_touches"], out["total_open_deals"]) == (4, 12)


def test_null_scalars_degrade_to_zero_not_none(rec):
    """model_dump/SQL NULLs make the key PRESENT but None, so .get(k, 0) wouldn't
    fire — the shaper must use `or 0` or the UI receives None."""
    rec.fetchone_queue = [{"open_deals": None, "computed_deals": None, "touched_deals": None}]
    out = service.get_weekly_touches()
    assert (out["total_open_deals"], out["computed_deals"], out["total_touches"]) == (0, 0, 0)


def test_missing_totals_row_degrades_to_zeros(rec):
    """pg_fetchone returning None (empty table / mock) must not raise."""
    rec.fetchone_queue = []
    out = service.get_weekly_touches()
    assert out["total_open_deals"] == 0
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
