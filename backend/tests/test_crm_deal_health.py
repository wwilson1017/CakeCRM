"""Deal health + pipeline analytics (issue #22, Phase 2).

Hermetic: the pg helpers on ``crm.analytics_service`` are monkeypatched, so these pin
the query SHAPE and — more importantly — the pure shapers, which is where every ratio
and duration is actually computed. The real SQL (window functions, DISTINCT ON,
FILTER) runs against Postgres in the integration suite.

The sharpest assertions here are the two claims the tools make to the model and must
never quietly break: that #18 owns the scoring maths (we compose, never recompute),
and that a partial stage-history is reported as partial rather than presented as a
complete funnel.
"""

import pytest

from crm import analytics_service as az
from tests.test_crm_service import Recorder


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(az, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(az, "pg_fetchall", r.fetchall)
    return r


HEALTHY_ROW = {
    "id": 7, "title": "Renewal", "stage": "proposal", "value": 1000.0,
    "contact_id": 3, "company_id": 4, "archived": False,
    "days_since_touch": 1, "days_in_stage": 2, "age_days": 10,
    "open_todos": 1, "overdue_todos": 0,
}


# ── flags (pure) ──────────────────────────────────────────────────────────────

def test_healthy_deal_raises_no_flags():
    assert az._health_flags(HEALTHY_ROW, 14) == []


def test_every_flag_fires_on_a_neglected_deal():
    row = dict(HEALTHY_ROW, days_since_touch=40, days_in_stage=60,
               open_todos=0, overdue_todos=0, contact_id=None, company_id=None)
    assert set(az._health_flags(row, 14)) == {
        "stale", "stuck_in_stage", "no_next_step", "missing_contact", "missing_company",
    }


def test_overdue_todo_flag_is_independent_of_no_next_step():
    """A deal WITH an open todo can still have an overdue one — the two flags say
    different things and must not cancel each other out."""
    row = dict(HEALTHY_ROW, open_todos=2, overdue_todos=1)
    flags = az._health_flags(row, 14)
    assert "overdue_todo" in flags and "no_next_step" not in flags


def test_stale_uses_the_caller_supplied_threshold():
    row = dict(HEALTHY_ROW, days_since_touch=20)
    assert "stale" in az._health_flags(row, 14)
    assert "stale" not in az._health_flags(row, 30)


def test_null_timestamps_never_flag():
    """A deal with no touch/stage data must not be reported as stale or stuck — an
    unknown is not a problem, and a background nudge firing on NULL is pure noise."""
    row = dict(HEALTHY_ROW, days_since_touch=None, days_in_stage=None)
    flags = az._health_flags(row, 14)
    assert "stale" not in flags and "stuck_in_stage" not in flags


# ── get_deal_health ───────────────────────────────────────────────────────────

def test_deal_health_composes_the_18_score_and_never_recomputes_it(rec, monkeypatch):
    """#18 owns the scoring model. This tool must pass its score/factors straight
    through — a second copy of the maths here would drift the moment #18 is tuned."""
    monkeypatch.setattr(az.scoring_service, "score_deal",
                        lambda deal_id: {"score": 63, "factors": {"stage_baseline": {"multiplier": 32}}})
    rec.fetchone_queue = [HEALTHY_ROW]
    out = az.get_deal_health(7)
    assert out["score"] == 63
    assert out["factors"] == {"stage_baseline": {"multiplier": 32}}


def test_deal_health_returns_none_for_a_missing_deal(rec, monkeypatch):
    monkeypatch.setattr(az.scoring_service, "score_deal", lambda deal_id: None)
    rec.fetchone_queue = [None]
    assert az.get_deal_health(999) is None


def test_deal_health_survives_a_deal_that_vanished_mid_read(rec, monkeypatch):
    """score_deal reads live and can return None if the deal was deleted between our
    two queries. That is a null score, not a crash."""
    monkeypatch.setattr(az.scoring_service, "score_deal", lambda deal_id: None)
    rec.fetchone_queue = [HEALTHY_ROW]
    out = az.get_deal_health(7)
    assert out["score"] is None and out["factors"] == {}


def test_deal_health_resolves_archived_deals(rec, monkeypatch):
    """Fetch-by-id must still find an archived deal — same rule as get_deal. You need
    to be able to look at one to decide whether to restore it."""
    monkeypatch.setattr(az.scoring_service, "score_deal", lambda deal_id: {"score": 0, "factors": {}})
    rec.fetchone_queue = [dict(HEALTHY_ROW, archived=True)]
    out = az.get_deal_health(7)
    assert out is not None and out["deal"]["archived"] is True
    assert "d.archived_at IS NULL" not in rec.sql_containing("FROM deals d")


def test_deal_health_clamps_stale_days(rec, monkeypatch):
    monkeypatch.setattr(az.scoring_service, "score_deal", lambda deal_id: {"score": 1, "factors": {}})
    rec.fetchone_queue = [HEALTHY_ROW]
    out = az.get_deal_health(7, stale_days=99999)
    assert out["stale_days"] == 365


def test_deal_health_overdue_is_date_only_text_comparison(rec, monkeypatch):
    """due_date is TEXT. Comparing as text (not ::date) matches the dashboard and can
    never cast-error on a malformed row."""
    monkeypatch.setattr(az.scoring_service, "score_deal", lambda deal_id: {"score": 1, "factors": {}})
    rec.fetchone_queue = [HEALTHY_ROW]
    az.get_deal_health(7)
    sql = rec.sql_containing("FROM deals d")
    assert "t.due_date != '' AND t.due_date < %s" in sql
    assert "::date" not in sql


# ── pipeline analytics shapers (pure) ─────────────────────────────────────────

def test_median_handles_odd_even_and_empty():
    assert az._median([5.0]) == 5.0
    assert az._median([1.0, 2.0, 3.0]) == 2.0
    assert az._median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert az._median([]) is None


def test_stage_durations_return_every_stage_even_with_no_samples():
    """The UI and the model both benefit from a stable frame — a stage with no data
    reports samples=0 and null averages rather than disappearing."""
    out = az._shape_stage_durations([{"stage": "lead", "days": 2.0}])
    assert [d["stage"] for d in out] == list(az.OPEN_STAGES)
    lead = next(d for d in out if d["stage"] == "lead")
    assert lead["samples"] == 1 and lead["avg_days"] == 2.0
    qualified = next(d for d in out if d["stage"] == "qualified")
    assert qualified["samples"] == 0
    assert qualified["avg_days"] is None and qualified["median_days"] is None


def test_stage_durations_ignore_null_days():
    out = az._shape_stage_durations([{"stage": "lead", "days": None}, {"stage": "lead", "days": 4.0}])
    lead = next(d for d in out if d["stage"] == "lead")
    assert lead["samples"] == 1 and lead["avg_days"] == 4.0


def test_conversion_classifies_each_outcome_from_the_current_stage():
    rows = [
        {"stage": "lead", "current_stage": "lead"},        # still sitting there
        {"stage": "lead", "current_stage": "qualified"},   # advanced
        {"stage": "lead", "current_stage": "won"},
        {"stage": "lead", "current_stage": "lost"},
    ]
    lead = next(d for d in az._shape_conversion(rows) if d["stage"] == "lead")
    assert lead["entered"] == 4
    assert (lead["still_here"], lead["advanced"], lead["won"], lead["lost"]) == (1, 1, 1, 1)


def test_conversion_progression_counts_advanced_and_won_but_not_lost():
    """Progression means "got out of this stage in the right direction". A lost deal
    left the stage too, and counting it as progress would flatter every funnel."""
    rows = [
        {"stage": "qualified", "current_stage": "proposal"},
        {"stage": "qualified", "current_stage": "won"},
        {"stage": "qualified", "current_stage": "lost"},
        {"stage": "qualified", "current_stage": "qualified"},
    ]
    q = next(d for d in az._shape_conversion(rows) if d["stage"] == "qualified")
    assert q["progression_rate"] == 0.5     # advanced + won, out of 4
    assert q["win_rate"] == 0.25


def test_conversion_rates_are_none_not_zero_when_nothing_entered():
    """No data must not render as a 0% conversion rate — that reads as a broken
    pipeline instead of an empty one."""
    stage = next(d for d in az._shape_conversion([]) if d["stage"] == "lead")
    assert stage["entered"] == 0
    assert stage["progression_rate"] is None and stage["win_rate"] is None


def test_conversion_ignores_terminal_stages_as_entry_points():
    """won/lost are outcomes, not stages deals sit in — they never open a funnel row."""
    out = az._shape_conversion([{"stage": "won", "current_stage": "won"}])
    assert [d["stage"] for d in out] == list(az.OPEN_STAGES)
    assert all(d["entered"] == 0 for d in out)


# ── get_pipeline_analytics ────────────────────────────────────────────────────

def _queue_analytics(rec, *, duration_rows=None, conversion_rows=None,
                     velocity=None, history=None):
    rec.fetchall_queue = [duration_rows or [], conversion_rows or []]
    rec.fetchone_queue = [velocity or {"won_count": 0, "avg_days_to_won": None},
                          history or {"since": None, "days": None}]


def test_pipeline_analytics_clamps_the_window(rec):
    _queue_analytics(rec)
    assert az.get_pipeline_analytics(window_days=99999)["window_days"] == 365
    _queue_analytics(rec)
    assert az.get_pipeline_analytics(window_days=1)["window_days"] == 7
    _queue_analytics(rec)
    assert az.get_pipeline_analytics(window_days="ninety")["window_days"] == 90


def test_pipeline_analytics_reports_a_partial_history_as_partial(rec):
    """The stage log only started when Phase 1 landed. A 90-day window over 5 days of
    history must NOT read as a complete 90-day funnel — this flag is what the tool
    description tells the model to state."""
    _queue_analytics(rec, history={"since": "2026-08-16T00:00:00Z", "days": 5})
    out = az.get_pipeline_analytics(window_days=90)
    assert out["history_days"] == 5
    assert out["history_covers_window"] is False


def test_pipeline_analytics_reports_full_coverage_when_history_is_long_enough(rec):
    _queue_analytics(rec, history={"since": "2025-01-01T00:00:00Z", "days": 400})
    assert az.get_pipeline_analytics(window_days=90)["history_covers_window"] is True


def test_pipeline_analytics_handles_an_empty_stage_log(rec):
    """A brand-new install has no events at all. Every field must render a zero state
    rather than raise — this tool is reachable from an unattended background turn."""
    _queue_analytics(rec)
    out = az.get_pipeline_analytics()
    assert out["history_since"] is None
    assert out["history_covers_window"] is False
    assert out["velocity"] == {"won_in_window": 0, "avg_days_to_won": None}
    assert all(s["samples"] == 0 for s in out["time_in_stage"])


def test_pipeline_analytics_rounds_velocity(rec):
    _queue_analytics(rec, velocity={"won_count": 3, "avg_days_to_won": 12.3456})
    out = az.get_pipeline_analytics()
    assert out["velocity"] == {"won_in_window": 3, "avg_days_to_won": 12.3}


def test_pipeline_analytics_excludes_archived_deals_everywhere(rec):
    """An archived deal did not convert, it was put away. Every one of the three
    queries must carry the live predicate."""
    _queue_analytics(rec)
    az.get_pipeline_analytics()
    event_queries = [sql for sql, _ in rec.calls if "deal_stage_events" in sql]
    assert len(event_queries) == 4          # durations, conversion, velocity, history
    joined = [sql for sql in event_queries if "JOIN deals d" in sql]
    assert len(joined) == 3
    assert all("d.archived_at IS NULL" in sql for sql in joined)


def test_pipeline_analytics_open_interval_is_excluded_not_clamped(rec):
    """A deal still sitting in its stage has no exit time. Counting it as if it left
    now would drag every average downward, so those rows are filtered out in SQL."""
    _queue_analytics(rec)
    az.get_pipeline_analytics()
    sql = rec.sql_containing("LEAD(e.changed_at)")
    assert "WHERE next_at IS NOT NULL" in sql


def test_pipeline_analytics_counts_each_deal_once_per_stage(rec):
    """A deal that bounces back into a stage must not inflate that stage's denominator."""
    _queue_analytics(rec)
    az.get_pipeline_analytics()
    sql = rec.sql_containing("DISTINCT ON")
    assert "DISTINCT ON (e.deal_id, e.new_stage)" in sql


def test_tool_description_states_both_honesty_limits():
    """Found in real-app verification: a deal created directly into 'lead' writes no
    stage event, so lead reads entered=0 on a CRM full of leads. That is correct for a
    TRANSITION funnel but misleading unstated, so the description must say both this and
    the partial-history limit — those sentences are the fix, not decoration."""
    from crm.tools import CRM_TOOL_DEFS

    desc = next(d for d in CRM_TOOL_DEFS if d["name"] == "crm_get_pipeline_analytics")["description"]
    assert "history_covers_window" in desc
    assert "TRANSITIONS" in desc
    assert "created directly into a stage" in desc
