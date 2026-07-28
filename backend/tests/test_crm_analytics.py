"""Hermetic analytics tests (issue #20) — SQL shape, pure shapers, tool, router.

The service is split so the CI gate (which does NOT touch a database) exercises
all the logic: get_analytics() only runs four queries and delegates every ratio,
guard, bucket, sort, and Decimal→float coercion to pure module-level shapers.

pg helpers are NOT globally mocked — each get_analytics() test installs the
Recorder explicitly (mirroring test_crm_service.py); pure-shaper tests need no
mock; router/tool tests monkeypatch service.get_analytics/summarize_analytics.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import service, tools
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


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


def _open_deal(id, age_days, days_since_touch, stage="lead", value=100.0):
    return {
        "id": id, "title": f"Deal {id}", "value": value, "stage": stage,
        "contact_name": None, "company_name": None,
        "age_days": age_days, "days_since_touch": days_since_touch,
    }


# ── SQL shape (Recorder) ──────────────────────────────────────────────────────

def test_get_analytics_query_shapes(rec):
    rec.fetchone_queue = [{"won": 0, "lost": 0, "open_count": 0, "total_pipeline_value": 0,
                           "avg_won_deal_size": None, "avg_open_deal_size": None,
                           "avg_days_to_close": None}]
    rec.fetchall_queue = [[], [], []]  # open_rows, daily_rows, type_rows (call order)
    service.get_analytics()

    agg = rec.sql_containing("FILTER (WHERE stage = 'won')")
    assert "FILTER (WHERE stage = 'lost')" in agg
    assert "stage NOT IN ('won', 'lost')" in agg
    assert "EXTRACT(EPOCH FROM (updated_at - created_at)) / 86400.0" in agg
    assert "FROM deals" in agg

    opens = rec.sql_containing("GREATEST(")
    assert "ch.archived = 0" in opens
    assert "ch.entity_type = 'deal'" in opens
    assert "LEFT JOIN contacts" in opens and "LEFT JOIN companies" in opens
    assert "WHERE d.stage NOT IN ('won', 'lost')" in opens
    assert "MAX(a.created_at)" in opens  # activity_log last-touch subquery

    daily = rec.sql_containing("GROUP BY 1")
    assert "AT TIME ZONE 'UTC'" in daily and "::date" in daily
    assert "created_at >= %s AND created_at < %s" in daily  # half-open window
    by_type = rec.sql_containing("GROUP BY activity")
    assert "ORDER BY count DESC, activity ASC" in by_type
    assert "created_at >= %s AND created_at < %s" in by_type


def test_activity_queries_share_one_window(rec):
    """R1: daily + by_type must use the SAME UTC boundary param, so activity.total
    (sum of the daily series) can never disagree with sum(by_type)."""
    rec.fetchone_queue = [None]
    rec.fetchall_queue = [[], [], []]
    service.get_analytics(days=30)
    daily_params = rec.params_for("GROUP BY 1")
    type_params = rec.params_for("GROUP BY activity")
    # Half-open [start_dt, end_dt): BOTH bounds shared, so a today+1 row (UTC-midnight
    # straddle / DB-clock skew) is excluded from both queries, not just the daily frame.
    assert len(daily_params) == 2 and all(isinstance(p, datetime) for p in daily_params)
    assert daily_params == type_params            # identical window
    assert daily_params[0] < daily_params[1]      # start before exclusive end
    assert all(p.tzinfo is not None for p in daily_params)  # tz-aware


def test_get_analytics_clamps_inputs(rec):
    rec.fetchone_queue = [None]
    # 60 stale open deals to prove the stale_limit 50-cap actually truncates.
    stale_rows = [_open_deal(i, age_days=100, days_since_touch=100) for i in range(60)]
    rec.fetchall_queue = [stale_rows, [], []]
    out = service.get_analytics(days=1, stale_days=0, stale_limit=999)
    assert out["window_days"] == 7      # clamped up from 1
    assert out["stale_days"] == 1       # clamped up from 0
    assert out["aging"]["stale_count"] == 60          # count is limit-independent
    assert len(out["aging"]["stale_deals"]) == 50     # list clamped to 50

    # Upper clamp — the direction the crm_analytics "absurd LLM value is bounded"
    # safety comment is about; the tool path has no Query-level backstop, so this
    # server-side clamp is the only guard.
    rec.fetchone_queue = [None]
    rec.fetchall_queue = [[], [], []]
    hi = service.get_analytics(days=10**7, stale_days=10**7)
    assert hi["window_days"] == 365 and hi["stale_days"] == 365


# ── Pure shapers ──────────────────────────────────────────────────────────────

def test_shape_win_loss_guards_and_types():
    assert service._shape_win_loss(None) == {
        "deals_won": 0, "deals_lost": 0, "open_deals": 0, "win_rate_pct": None,
        "avg_won_deal_size": None, "avg_open_deal_size": None,  # None (not 0.0) = "no data"
        "avg_days_to_close": None, "total_pipeline_value": 0.0,
    }
    won = service._shape_win_loss({"won": 3, "lost": 1, "open_count": 5,
                                   "total_pipeline_value": 500, "avg_won_deal_size": Decimal("1234.5"),
                                   "avg_open_deal_size": None, "avg_days_to_close": Decimal("9.97")})
    assert won["win_rate_pct"] == 75.0
    assert won["avg_days_to_close"] == 10.0 and isinstance(won["avg_days_to_close"], float)
    assert isinstance(won["avg_won_deal_size"], float)
    assert won["avg_open_deal_size"] is None  # NULL avg → None, not 0.0
    # won+lost == 0 with open deals → None, not a misleading 0% record
    assert service._shape_win_loss({"won": 0, "lost": 0, "open_count": 4})["win_rate_pct"] is None


def test_bucket_deal_ages_boundaries():
    rows = [_open_deal(i, age_days=a, days_since_touch=0)
            for i, a in enumerate([7, 8, 30, 31, 90, 91, 7.9, -5])]
    got = {b["label"]: b["count"] for b in service._bucket_deal_ages(rows)}
    assert got == {"0-7": 3, "8-30": 2, "31-90": 2, "91+": 1}  # 7,7.9,-5→0 | 8,30 | 31,90 | 91
    empty = service._bucket_deal_ages([])
    assert [b["label"] for b in empty] == ["0-7", "8-30", "31-90", "91+"]
    assert all(b["count"] == 0 for b in empty)


def test_stale_open_deals_threshold_sort_and_count():
    rows = [
        _open_deal(1, age_days=50, days_since_touch=5),    # below threshold
        _open_deal(2, age_days=50, days_since_touch=30),
        _open_deal(3, age_days=50, days_since_touch=30),   # tie → id asc
        _open_deal(4, age_days=50, days_since_touch=45),
    ]
    stale, total = service._stale_open_deals(rows, stale_days=14, limit=2)
    assert total == 3                       # count before the limit
    assert [d["id"] for d in stale] == [4, 2]   # stalest first, id tiebreak, top-2
    assert all(isinstance(d["days_since_touch"], int) for d in stale)


def test_stale_threshold_is_inclusive():
    # days_since_touch == stale_days must count as stale (the `>=` boundary).
    rows = [_open_deal(1, age_days=20, days_since_touch=14),   # exactly at threshold
            _open_deal(2, age_days=20, days_since_touch=13)]   # just under
    stale, total = service._stale_open_deals(rows, stale_days=14, limit=10)
    assert total == 1 and [d["id"] for d in stale] == [1]


def test_stale_open_deals_clamps_negative_age():
    # _stale_open_deals clamps its own age_days output (a second clamp site).
    stale, _ = service._stale_open_deals(
        [_open_deal(1, age_days=-3, days_since_touch=30)], stale_days=14, limit=10)
    assert stale[0]["age_days"] == 0


def test_fill_activity_daily_frame_and_types():
    today = date(2026, 7, 26)
    rows = [{"day": date(2026, 7, 26), "count": 3}, {"day": "2026-07-24", "count": 2}]
    series = service._fill_activity_daily(rows, days=7, today=today)
    assert len(series) == 7
    assert series[0]["day"] == "2026-07-20" and series[-1]["day"] == "2026-07-26"
    counts = {d["day"]: d["count"] for d in series}
    assert counts["2026-07-26"] == 3 and counts["2026-07-24"] == 2 and counts["2026-07-25"] == 0


def test_shape_activity_types_normalizes():
    rows = [{"activity": "Call", "count": 3}, {"activity": "call ", "count": 2},
            {"activity": "", "count": 1}, {"activity": "email", "count": 5}]
    out = service._shape_activity_types(rows)
    # merged (Call+call → call=5), count desc, label asc tiebreak (call before email)
    assert out == [{"activity": "call", "count": 5}, {"activity": "email", "count": 5},
                   {"activity": "other", "count": 1}]


def test_get_analytics_zero_data_shape(rec):
    rec.fetchone_queue = [None]
    rec.fetchall_queue = [[], [], []]
    out = service.get_analytics()
    assert out["win_loss"]["win_rate_pct"] is None
    assert out["win_loss"]["total_pipeline_value"] == 0.0
    assert [b["count"] for b in out["aging"]["buckets"]] == [0, 0, 0, 0]
    assert out["aging"]["stale_count"] == 0 and out["aging"]["stale_deals"] == []
    assert out["activity"]["by_type"] == [] and out["activity"]["total"] == 0
    assert len(out["activity"]["daily"]) == 30 and all(d["count"] == 0 for d in out["activity"]["daily"])


# ── summarize_analytics (D5 trim) ─────────────────────────────────────────────

def test_summarize_analytics_trims_to_lean_shape():
    full = {
        "window_days": 30, "stale_days": 14,
        "win_loss": {"deals_won": 3, "deals_lost": 1, "open_deals": 5, "win_rate_pct": 75.0,
                     "avg_won_deal_size": 1200.0, "avg_open_deal_size": 800.0,
                     "avg_days_to_close": 10.0, "total_pipeline_value": 4000.0},
        "activity": {"daily": [{"day": "2026-07-26", "count": 9}] * 30,
                     "by_type": [{"activity": "call", "count": 9}], "total": 9},
        "aging": {"buckets": [{"label": "0-7", "min_days": 0, "max_days": 7, "count": 2},
                              {"label": "91+", "min_days": 91, "max_days": None, "count": 1}],
                  "stale_count": 6,
                  "stale_deals": [_open_deal(i, 100, 100) for i in range(8)]},
    }
    out = service.summarize_analytics(full)
    assert "daily" not in out and "activity" not in out  # raw series never leaks
    assert out["win_rate_pct"] == 75.0 and out["activity_total"] == 9
    assert out["activity_by_type"] == [{"activity": "call", "count": 9}]
    assert out["aging_buckets"] == {"0-7": 2, "91+": 1}
    assert out["stale_count"] == 6
    assert len(out["stale_deals"]) == 5  # top-5 only
    assert set(out["stale_deals"][0]) == {"id", "title", "days_since_touch"}


def test_summarize_analytics_caps_activity_by_type():
    # Free-text activity vocab → the tool trims to the top-N by count (D5 lean payload).
    full = {
        "window_days": 30, "stale_days": 14,
        "win_loss": {"deals_won": 0, "deals_lost": 0, "open_deals": 0, "win_rate_pct": None,
                     "avg_won_deal_size": None, "avg_open_deal_size": None,
                     "avg_days_to_close": None, "total_pipeline_value": 0.0},
        "activity": {"daily": [], "total": 120,
                     "by_type": [{"activity": f"k{i:02d}", "count": 100 - i} for i in range(15)]},
        "aging": {"buckets": [], "stale_count": 0, "stale_deals": []},
    }
    out = service.summarize_analytics(full)
    assert len(out["activity_by_type"]) == service._TOOL_MAX_ACTIVITY_TYPES  # capped at top-10
    assert out["activity_by_type"][0]["activity"] == "k00"  # highest-count first, order kept


# ── Tool ──────────────────────────────────────────────────────────────────────

def test_crm_analytics_tool_def_and_passthrough(monkeypatch):
    by_name = {d["name"]: d for d in tools.CRM_TOOL_DEFS}
    assert "crm_analytics" in by_name
    d = by_name["crm_analytics"]
    assert d["writes"] is False and d["kind"] == "integration"
    assert tools.TOOL_EXECUTORS["crm_analytics"] is tools.crm_analytics

    seen = {}
    monkeypatch.setattr(service, "get_analytics", lambda **kw: (seen.update(kw) or {"full": True}))
    monkeypatch.setattr(service, "summarize_analytics", lambda full: {"trimmed": full})
    out = tools.crm_analytics(stale_days=21)
    assert seen == {"stale_days": 21}          # forwarded to the service
    assert out == {"trimmed": {"full": True}}  # summarized, not the raw payload


# ── Router ────────────────────────────────────────────────────────────────────

def test_analytics_route_defaults_and_forwarding(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "get_analytics", lambda **kw: (seen.update(kw) or {"ok": 1}))
    assert client.get("/api/crm/analytics").json() == {"ok": 1}
    assert seen == {"days": 30, "stale_days": 14}      # route defaults
    client.get("/api/crm/analytics?days=90&stale_days=7")
    assert seen == {"days": 90, "stale_days": 7}        # query forwarded


def test_analytics_route_validates_bounds(client, monkeypatch):
    monkeypatch.setattr(service, "get_analytics", lambda **kw: {})
    assert client.get("/api/crm/analytics?days=1").status_code == 422   # ge=7
    assert client.get("/api/crm/analytics?days=999").status_code == 422  # le=365
    assert client.get("/api/crm/analytics?stale_days=0").status_code == 422  # ge=1
    assert client.get("/api/crm/analytics?stale_days=9999").status_code == 422  # le=365
