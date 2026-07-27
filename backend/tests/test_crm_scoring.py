"""Hermetic lead-scoring tests (issue #18).

The pure band/compose helpers need no DB. score_deal/score_contact monkeypatch the
module's pg_fetchone/pg_fetchall by name; recompute_* + the refresh go through
get_connection and use the shared fake_conn fixture (with row_to_dict stubbed to
identity so a dict queued on the fake cursor passes straight through).
"""

from datetime import datetime, timedelta, timezone

import pytest

from crm import scoring_service as ss

NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


def _ago(days):
    return (NOW - timedelta(days=days)).isoformat()


# ── Pure band helpers: exact boundaries ──────────────────────────────────────

@pytest.mark.parametrize("count,mult", [(0, 0.6), (1, 0.9), (2, 0.9), (3, 1.05), (5, 1.05), (6, 1.15), (10, 1.15), (11, 1.25), (99, 1.25)])
def test_engagement_bands(count, mult):
    assert ss._engagement_multiplier(count) == mult


@pytest.mark.parametrize("value,mult", [(0, 0.7), (5_000, 0.9), (9_999, 0.9), (10_000, 1.05), (49_999, 1.05), (50_000, 1.15), (99_999, 1.15), (100_000, 1.2), (5_000_000, 1.2)])
def test_value_bands_monotonic(value, mult):
    assert ss._value_multiplier(value) == mult


def test_value_bands_are_non_decreasing():
    vals = [0, 9_999, 10_000, 49_999, 50_000, 99_999, 100_000, 1_000_000]
    mults = [ss._value_multiplier(v) for v in vals]
    assert mults == sorted(mults), "a larger deal must never score lower on value"


@pytest.mark.parametrize("hc,cc,mult", [(True, True, 1.15), (True, False, 1.0), (False, True, 1.0), (False, False, 0.6)])
def test_relationship_bands(hc, cc, mult):
    assert ss._relationship_multiplier(hc, cc) == mult


@pytest.mark.parametrize("days,mult", [(0, 1.1), (7, 1.1), (8, 1.05), (14, 1.05), (30, 1.0), (60, 0.85), (90, 0.7), (91, 0.5), (None, 0.7)])
def test_deal_recency_bands(days, mult):
    assert ss._recency_multiplier(days) == mult


@pytest.mark.parametrize("days,mult", [(0, 1.05), (29, 1.05), (30, 1.0), (90, 1.0), (91, 0.9), (180, 0.9), (181, 0.75), (365, 0.75), (366, 0.6), (None, 1.0)])
def test_age_bands(days, mult):
    assert ss._age_multiplier(days) == mult


@pytest.mark.parametrize("status,base", [("active", 30), ("inactive", 15), ("archived", 5), ("", 15), ("bogus", 15), (None, 15)])
def test_contact_status_base_unknown_is_conservative(status, base):
    assert ss._contact_status_base(status) == base


@pytest.mark.parametrize("count,mult", [(0, 0.6), (2, 0.9), (5, 1.1), (10, 1.2), (11, 1.3)])
def test_interactions_bands(count, mult):
    assert ss._interactions_multiplier(count) == mult


@pytest.mark.parametrize("days,mult", [(7, 1.15), (30, 1.0), (90, 0.8), (91, 0.55), (None, 0.55)])
def test_contact_recency_bands(days, mult):
    assert ss._contact_recency_multiplier(days) == mult


@pytest.mark.parametrize("stages,mult", [
    (["negotiation", "lead"], 1.6),
    (["proposal"], 1.45),
    (["qualified"], 1.25),
    (["lead"], 1.1),
    (["won", "lost"], 1.5),          # a won deal beats a lost-only history
    (["lost", "lost"], 0.65),        # only-lost is the weakest (a died opportunity)
    ([], 0.75),                      # no deals yet — untapped, not dead
])
def test_deal_link_bands(stages, mult):
    assert ss._deal_link_multiplier(stages) == mult


@pytest.mark.parametrize("cid,ctext,mult", [(5, "", 1.15), (None, "Acme", 1.05), (None, "   ", 0.9), (None, None, 0.9)])
def test_company_link_bands(cid, ctext, mult):
    assert ss._company_link_multiplier(cid, ctext) == mult


@pytest.mark.parametrize("email,phone,mult", [("a@b.c", "555", 1.1), ("a@b.c", "", 1.0), ("", "555", 1.0), ("", "", 0.85), (None, None, 0.85)])
def test_completeness_bands(email, phone, mult):
    assert ss._completeness_multiplier(email, phone) == mult


# ── clamp / days_since / safe_value ──────────────────────────────────────────

def test_clamp_range():
    assert ss._clamp(0.1) == 1       # floor is 1 (0 is terminal-only)
    assert ss._clamp(1000) == 99     # ceiling is 99 (100 is terminal-only)
    assert ss._clamp(41.4) == 41


def test_days_since_none_and_future_and_iso():
    assert ss._days_since(None, NOW) is None
    assert ss._days_since(_ago(0), NOW) == 0.0
    assert ss._days_since((NOW + timedelta(days=5)).isoformat(), NOW) == 0.0  # future clamps to 0
    assert round(ss._days_since(_ago(10), NOW)) == 10


def test_days_since_naive_datetime_treated_utc():
    naive = datetime(2026, 7, 16, 12, 0, 0)  # 10 days before NOW, no tzinfo
    assert round(ss._days_since(naive, NOW)) == 10


@pytest.mark.parametrize("value,out", [(None, 0.0), (-5, 0.0), ("garbage", 0.0), (12.5, 12.5), (0, 0.0)])
def test_safe_value_normalizes(value, out):
    assert ss._safe_value(value) == out


# ── Deal composition ─────────────────────────────────────────────────────────

def test_compose_deal_terminal_short_circuits():
    won = ss._compose_deal({"stage": "won"}, 999, _ago(0), NOW)
    assert won["score"] == 100 and won["factors"] == {"terminal": {"value": "won", "multiplier": None}}
    lost = ss._compose_deal({"stage": "lost"}, 0, None, NOW)
    assert lost["score"] == 0 and lost["factors"]["terminal"]["value"] == "lost"


def test_compose_deal_unknown_stage_uses_lead_baseline():
    r = ss._compose_deal({"stage": "weird", "value": 0, "contact_id": None, "company_id": None, "created_at": _ago(1)}, 0, None, NOW)
    # baseline 8 (lead) × 0.6 eng × 0.7 val × 0.6 rel × 0.7 rec(no touch) × 1.05 age -> clamps to 1..99
    assert 1 <= r["score"] <= 99
    assert r["factors"]["stage_baseline"]["multiplier"] == 8


def test_compose_deal_hot_negotiation_has_headroom_below_clamp():
    deal = {"stage": "negotiation", "value": 75_000, "contact_id": 3, "company_id": 9, "created_at": _ago(10)}
    r = ss._compose_deal(deal, 8, _ago(2), NOW)   # 8 notes, touched 2 days ago
    assert 70 <= r["score"] < 99, f"hot deal should sort high but leave headroom below the clamp, got {r['score']}"


def test_compose_deal_distinguishes_two_hot_deals():
    base = {"stage": "negotiation", "contact_id": 1, "company_id": 2, "created_at": _ago(20)}
    hotter = ss._compose_deal({**base, "value": 120_000}, 12, _ago(1), NOW)["score"]
    cooler = ss._compose_deal({**base, "value": 5_000}, 1, _ago(45), NOW)["score"]
    assert hotter > cooler  # the top of the scale is not saturated


# ── Contact composition ──────────────────────────────────────────────────────

def test_compose_contact_deal_linkage_reads_raw_stages():
    c = {"status": "active", "company_id": 1, "company": "", "email": "a@b.c", "phone": "5", "created_at": _ago(3)}
    strong = ss._compose_contact(c, 6, _ago(2), ["negotiation"], NOW)["score"]
    weak = ss._compose_contact(c, 6, _ago(2), ["lost"], NOW)["score"]
    none = ss._compose_contact(c, 6, _ago(2), [], NOW)["score"]
    assert strong > none and none > weak  # open-negotiation > no-deals > lost-only


def test_compose_contact_recency_falls_back_to_created_at():
    c = {"status": "active", "company_id": None, "company": "", "email": "", "phone": "", "created_at": _ago(200)}
    r = ss._compose_contact(c, 0, None, [], NOW)  # no interactions, no touch
    assert r["factors"]["recency"]["value"] == pytest.approx(200, abs=1)


# ── recompute_* : advisory lock + only score cols written, no updated_at ──────

@pytest.fixture
def _identity_row_to_dict(monkeypatch):
    monkeypatch.setattr(ss, "row_to_dict", lambda cur, row: row)


def test_recompute_deal_locks_and_writes_only_score_cols(monkeypatch, fake_conn, _identity_row_to_dict):
    conn = fake_conn(monkeypatch, ss, fetchone_results=[
        {"stage": "qualified", "value": 20_000, "contact_id": 3, "company_id": None, "created_at": _ago(5)},
        {"cnt": 4, "newest": _ago(2)},   # chatter agg
        {"newest": _ago(9)},             # activity max
    ])
    score = ss.recompute_deal(1, now=NOW)
    assert isinstance(score, int) and 1 <= score <= 99
    stmts = [s for s, _ in conn.executed]
    assert any("pg_advisory_xact_lock" in s for s in stmts)
    upd = next(s for s in stmts if "UPDATE deals SET" in s)
    assert "lead_score = %s" in upd and "lead_score_at = %s" in upd
    assert "updated_at" not in upd  # a score write must never reorder the default sort


def test_recompute_deal_missing_returns_none_no_update(monkeypatch, fake_conn, _identity_row_to_dict):
    conn = fake_conn(monkeypatch, ss, fetchone_results=[None])  # deal row missing
    assert ss.recompute_deal(999, now=NOW) is None
    assert not any("UPDATE deals" in s for s, _ in conn.executed)


def test_recompute_contact_writes_only_score_cols(monkeypatch, fake_conn, _identity_row_to_dict):
    conn = fake_conn(
        monkeypatch, ss,
        fetchone_results=[
            {"status": "active", "company_id": 1, "company": "", "email": "a@b.c", "phone": "5", "created_at": _ago(3)},
            {"cnt": 2, "newest": _ago(1)},   # chatter agg
            {"cnt": 1, "newest": _ago(4)},   # activity agg
        ],
        fetchall_results=[[{"stage": "proposal"}]],  # linked deal stages
    )
    score = ss.recompute_contact(7, now=NOW)
    assert isinstance(score, int)
    upd = next(s for s, _ in conn.executed if "UPDATE contacts SET" in s)
    assert "lead_score = %s" in upd and "lead_score_at = %s" in upd and "updated_at" not in upd


# ── score_on_event facade: never raises, per-id isolation, dedupe, skip falsy ─

def test_score_on_event_isolates_per_id_failures(monkeypatch):
    seen = []

    def boom(deal_id, now=None):
        seen.append(deal_id)
        if deal_id == 1:
            raise RuntimeError("scoring blew up")
        return 50

    monkeypatch.setattr(ss, "recompute_deal", boom)
    monkeypatch.setattr(ss, "recompute_contact", lambda cid, now=None: None)
    # id 1 raises but must not stop id 2; the whole call must not raise.
    ss.score_on_event(deal_ids=(1, 2, 0, None, 2))  # 0/None skipped, 2 deduped
    assert seen == [1, 2]


def test_score_on_event_swallows_everything(monkeypatch):
    monkeypatch.setattr(ss, "recompute_deal", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    # Must not raise even if recompute always throws.
    ss.score_on_event(deal_ids=(1,))


# ── backfill_scores ──────────────────────────────────────────────────────────

def test_backfill_rejects_bad_scope():
    with pytest.raises(ValueError):
        ss.backfill_scores("everything")


def test_backfill_null_filters_unscored(monkeypatch):
    seen_sql = []

    def fake_fetchall(sql, params=()):
        seen_sql.append(" ".join(sql.split()))
        return []

    monkeypatch.setattr(ss, "pg_fetchall", fake_fetchall)
    out = ss.backfill_scores("null", now=NOW)
    assert out == {"deals_scored": 0, "contacts_scored": 0, "errors": 0, "capped": False}
    assert any("WHERE lead_score IS NULL" in s and "FROM deals" in s for s in seen_sql)
    assert any("WHERE lead_score IS NULL" in s and "FROM contacts" in s for s in seen_sql)


def test_backfill_all_has_no_where_and_counts_errors(monkeypatch):
    monkeypatch.setattr(ss, "pg_fetchall", lambda sql, params=(): [{"id": 1}, {"id": 2}] if "FROM deals" in sql else [{"id": 9}])

    def flaky_deal(did, now=None):
        if did == 2:
            raise RuntimeError("boom")
        return 40

    monkeypatch.setattr(ss, "recompute_deal", flaky_deal)
    monkeypatch.setattr(ss, "recompute_contact", lambda cid, now=None: 30)
    out = ss.backfill_scores("all", now=NOW)
    assert out["deals_scored"] == 1 and out["contacts_scored"] == 1 and out["errors"] == 1


# ── run_score_refresh_if_due: due-guard + session lock + completion stamp ─────

def test_refresh_lock_busy_returns_none(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, ss, fetchone_results=[(False,)])  # pg_try_advisory_lock -> False
    assert ss.run_score_refresh_if_due(now=NOW) is None
    assert not any("scores_refreshed_at" in s for s, _ in conn.executed)  # never even read the gate


def test_refresh_not_due_skips_scan(monkeypatch, fake_conn):
    # Coarse due-gate: last refresh 1h ago -> return None WITHOUT scanning any table.
    recent = NOW - timedelta(hours=1)
    conn = fake_conn(monkeypatch, ss, fetchone_results=[(True,), (recent,)])
    monkeypatch.setattr(ss, "_stale_ids", lambda *a: pytest.fail("must not scan when not due"))
    assert ss.run_score_refresh_if_due(now=NOW) is None
    assert any("pg_advisory_unlock" in s for s, _ in conn.executed)  # lock still released


def test_refresh_window_open_nothing_stale_resets_gate(monkeypatch, fake_conn):
    # Window open (last=48h ago) but nothing stale -> stamp to reset the 24h gate, return None.
    old = NOW - timedelta(hours=48)
    conn = fake_conn(monkeypatch, ss, fetchone_results=[(True,), (old,)], fetchall_results=[[], []])
    stamps = []
    monkeypatch.setattr(ss, "pg_execute", lambda sql, params=(): stamps.append(sql))
    monkeypatch.setattr(ss, "recompute_deal", lambda *a, **k: pytest.fail("nothing stale"))
    assert ss.run_score_refresh_if_due(now=NOW) is None
    assert any("UPDATE crm_meta" in s for s in stamps)
    assert any("pg_advisory_unlock" in s for s, _ in conn.executed)


def test_refresh_drains_stale_batch_and_stamps(monkeypatch, fake_conn):
    old = NOW - timedelta(hours=48)  # window open
    conn = fake_conn(monkeypatch, ss, fetchone_results=[(True,), (old,)],
                     fetchall_results=[[(1,), (2,)], [(9,)]])
    monkeypatch.setattr(ss, "recompute_deal", lambda i, now=None: 50)
    monkeypatch.setattr(ss, "recompute_contact", lambda i, now=None: 40)
    stamps = []
    monkeypatch.setattr(ss, "pg_execute", lambda sql, params=(): stamps.append(sql))
    out = ss.run_score_refresh_if_due(now=NOW)
    assert out == {"deals_scored": 2, "contacts_scored": 1, "batch": 3}
    assert any("UPDATE crm_meta SET scores_refreshed_at = %s WHERE id = 1" in s for s in stamps)  # drained
    assert any("pg_advisory_unlock" in s for s, _ in conn.executed)  # lock released in phase 1


def test_refresh_capped_does_not_stamp(monkeypatch, fake_conn):
    # A full batch means MORE stale rows remain -> do NOT stamp, so the next tick keeps draining.
    old = NOW - timedelta(hours=48)
    full = [(i,) for i in range(ss._REFRESH_BATCH)]
    fake_conn(monkeypatch, ss, fetchone_results=[(True,), (old,)], fetchall_results=[full, []])
    monkeypatch.setattr(ss, "recompute_deal", lambda i, now=None: 1)
    monkeypatch.setattr(ss, "recompute_contact", lambda i, now=None: 1)
    stamps = []
    monkeypatch.setattr(ss, "pg_execute", lambda sql, params=(): stamps.append(sql))
    out = ss.run_score_refresh_if_due(now=NOW)
    assert out["batch"] == ss._REFRESH_BATCH
    assert not stamps  # batch full -> not drained -> no stamp -> still due next tick


def test_refresh_is_bounded_to_batch(monkeypatch, fake_conn):
    # deals fill the whole batch; contacts then get the remainder (0) and are skipped.
    old = NOW - timedelta(hours=48)
    conn = fake_conn(monkeypatch, ss, fetchone_results=[(True,), (old,)],
                     fetchall_results=[[(i,) for i in range(ss._REFRESH_BATCH)], []])
    monkeypatch.setattr(ss, "recompute_deal", lambda i, now=None: 1)
    monkeypatch.setattr(ss, "recompute_contact", lambda i, now=None: 1)
    monkeypatch.setattr(ss, "pg_execute", lambda *a, **k: None)
    ss.run_score_refresh_if_due(now=NOW)
    deal_limit = next(p[-1] for s, p in conn.executed if "FROM deals WHERE lead_score_at" in s)
    assert deal_limit == ss._REFRESH_BATCH
    # contacts stale-query is skipped entirely when the deal batch is already full (limit 0)
    assert not any("FROM contacts WHERE lead_score_at" in s for s, _ in conn.executed)


def test_refresh_first_run_null_last_is_due(monkeypatch, fake_conn):
    # scores_refreshed_at NULL (never run) -> due; one stale deal gets processed.
    fake_conn(monkeypatch, ss, fetchone_results=[(True,), None], fetchall_results=[[(5,)], []])
    monkeypatch.setattr(ss, "recompute_deal", lambda i, now=None: 1)
    monkeypatch.setattr(ss, "pg_execute", lambda *a, **k: None)
    out = ss.run_score_refresh_if_due(now=NOW)
    assert out is not None and out["deals_scored"] == 1
