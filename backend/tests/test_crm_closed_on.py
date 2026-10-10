"""Closed on (#279): the day a Won deal actually closed.

Hermetic: the rule, the resolver, and every writer's SQL shape through the shared
``fake_conn``. The real column, the migration backfill and the notes run against Postgres
in ``test_integration_crm_lifecycle_pg.py``.
"""

from datetime import date, datetime

import pytest

from crm import scoring_service, service, tools
from tests.conftest import FAKE_MEMBER
from tests.test_crm_service import Recorder

TODAY = date(2026, 10, 10)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(service, "today_local", lambda: TODAY)
    monkeypatch.setattr(scoring_service, "score_on_event", lambda **kw: None)
    rec = Recorder()
    monkeypatch.setattr(service, "pg_fetchone", rec.fetchone)
    monkeypatch.setattr(service, "pg_execute", rec.execute)
    return rec


def _update(conn):
    return next((s, p) for s, p in conn.executed if s.startswith("UPDATE deals SET"))


def _notes(conn):
    return [(s, p) for s, p in conn.executed if "INSERT INTO crm_chatter" in s]


# ── The rule and the resolver ────────────────────────────────────────────────

@pytest.mark.parametrize("old,new,closed,expect", [
    ("lead", "won", None, "set"),
    (None, "won", None, "set"),            # create_deal
    ("won", "lead", date(2026, 9, 1), "clear"),
    ("won", "lost", "2026-09-01", "clear"),
    ("won", "lead", None, None),           # undated: nothing to clear
    ("won", "won", date(2026, 9, 1), None),  # won -> won keeps its date
    ("lead", "qualified", None, None),
    ("lead", None, None, None),
])
def test_closed_on_transition(old, new, closed, expect):
    assert service.closed_on_transition(old, new, closed) == expect


def test_resolve_closed_on_defaults_to_today_and_allows_the_past():
    assert service.resolve_closed_on(None, TODAY) == TODAY
    assert service.resolve_closed_on("", TODAY) == TODAY
    assert service.resolve_closed_on("2026-10-10", TODAY) == TODAY
    assert service.resolve_closed_on("2025-01-31", TODAY) == date(2025, 1, 31)
    assert service.resolve_closed_on(date(2026, 9, 1), TODAY) == date(2026, 9, 1)


@pytest.mark.parametrize("bad", [
    "2026-10-11", date(2026, 10, 11),                   # future
    "20261001", "2026-W40-1", "2026-1-1", "2026-10-01T00:00", "tomorrow", "2026-02-30",
    20261001, datetime(2026, 10, 1),
])
def test_resolve_closed_on_refuses_the_future_and_anything_not_a_plain_day(bad):
    with pytest.raises(ValueError, match="Closed on"):
        service.resolve_closed_on(bad, TODAY)


def test_closed_on_is_typed_but_not_user_writable():
    assert service._DEAL_COLUMN_TYPES["closed_on"] == "date"
    assert "closed_on" not in service._DEAL_USER_WRITABLE


def test_the_note_renders_dates_strings_and_none():
    assert service._closed_on_note(None, date(2026, 10, 1)) == "Closed on: none → 2026-10-01"
    assert service._closed_on_note("2026-10-01", None) == "Closed on: 2026-10-01 → none"
    assert service._closed_on_note(None, "x").startswith(scoring_service.CLOSED_ON_NOTE_PREFIX)


# ── Single-deal writers ──────────────────────────────────────────────────────

def test_mark_won_dates_today_and_writes_the_note_on_the_same_cursor(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("negotiation", None, None, None)])
    service.mark_deal_won(1, actor_id=5)
    sql, params = _update(conn)
    assert "closed_on = %s" in sql and "closed_on IS DISTINCT FROM %s::date" in sql
    assert TODAY in params
    [(note_sql, note_params)] = _notes(conn)
    assert note_params[2] == "Closed on: none → 2026-10-10" and note_params[4] == 5
    assert conn.executed_by[-1] == conn.executed_by[0], "note rides the write's cursor"


def test_mark_won_backdates(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None, None)])
    service.mark_deal_won(1, closed_on="2026-10-03")
    assert date(2026, 10, 3) in _update(conn)[1]


def test_a_future_day_is_refused_before_any_connection(monkeypatch):
    def boom():
        raise AssertionError("must refuse before opening a transaction")
    monkeypatch.setattr(service, "get_connection", boom)
    with pytest.raises(ValueError, match="future"):
        service.mark_deal_won(1, closed_on="2026-10-11")
    assert service.bulk_move_deals([1], "won", closed_on="2026-10-11")["ok"] is False


def test_re_marking_a_won_deal_keeps_its_date(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service,
                     fetchone_results=[("won", None, None, date(2026, 9, 1))])
    service.mark_deal_won(1)
    assert "closed_on" not in _update(conn)[0]
    assert _notes(conn) == []


def test_mark_won_with_a_day_on_a_won_deal_edits_it(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service,
                     fetchone_results=[("won", None, None, date(2026, 9, 1))])
    service.mark_deal_won(1, closed_on="2026-09-20")
    assert date(2026, 9, 20) in _update(conn)[1]
    assert _notes(conn)[0][1][2] == "Closed on: 2026-09-01 → 2026-09-20"


def test_leaving_won_clears_the_date_and_records_it(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service,
                     fetchone_results=[("won", None, None, date(2026, 9, 1))])
    service.update_deal_stage(1, "negotiation", actor_id=3)
    sql, params = _update(conn)
    assert "closed_on = %s" in sql and None in params
    assert _notes(conn)[0][1][2] == "Closed on: 2026-09-01 → none"
    assert _notes(conn)[0][1][4] == 3


def test_an_undated_won_deal_leaving_won_writes_no_date_and_no_note(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("won", None, None, None)])
    service.update_deal_stage(1, "negotiation")
    assert "closed_on" not in _update(conn)[0]
    assert _notes(conn) == []


def test_mark_lost_on_a_won_deal_clears_and_credits_the_author(monkeypatch, fake_conn):
    from crm import chatter_service
    monkeypatch.setattr(chatter_service, "add_note", lambda *a, **k: None)
    conn = fake_conn(monkeypatch, service,
                     fetchone_results=[("won", None, None, date(2026, 9, 1))])
    service.mark_deal_lost(1, "price", author_id=5)
    [(_, params)] = _notes(conn)
    assert params[2] == "Closed on: 2026-09-01 → none" and params[4] == 5


def test_no_note_when_the_update_matched_no_row(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None, None)],
                     rowcounts={"UPDATE deals SET": 0})
    service.mark_deal_won(1)
    assert _notes(conn) == []


def test_update_deal_edits_only_while_won(monkeypatch, fake_conn):
    fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None, None)])
    with pytest.raises(ValueError, match="only be set on a Won deal"):
        service.update_deal(1, closed_on="2026-10-01")
    conn = fake_conn(monkeypatch, service,
                     fetchone_results=[("won", None, None, date(2026, 9, 1))])
    service.update_deal(1, closed_on="2026-10-01", value=5, actor_id=2)
    sql, params = _update(conn)
    assert "value = %s" in sql and "closed_on = %s" in sql
    assert _notes(conn)[0][1][2] == "Closed on: 2026-09-01 → 2026-10-01"


def test_update_deal_never_blanks_it(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service,
                     fetchone_results=[("won", None, None, date(2026, 9, 1))])
    for blank in (None, ""):
        service.update_deal(1, closed_on=blank)
    assert conn.executed == []


def test_update_deal_moving_into_won_takes_the_given_day(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None, None)])
    service.update_deal(1, stage="won", closed_on="2026-10-02")
    assert date(2026, 10, 2) in _update(conn)[1]


def test_create_deal_born_won_is_dated_today(pinned):
    pinned.fetchone_queue = [{"id": 9}]
    service.create_deal("x", stage="won")
    assert pinned.params_for("INSERT INTO deals")[-1] == TODAY
    pinned.calls.clear()
    pinned.fetchone_queue = [{"id": 9}]
    service.create_deal("x", stage="lead", closed_on="2026-10-01")
    assert pinned.params_for("INSERT INTO deals")[-1] is None
    with pytest.raises(ValueError, match="future"):
        service.create_deal("x", stage="won", closed_on="2026-10-11")


# ── Bulk ─────────────────────────────────────────────────────────────────────

def test_bulk_into_won_dates_and_notes_in_one_insert(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchall_results=[[
        (1, "lead", None, None, None), (2, "won", None, None, date(2026, 9, 1)),
    ]])
    result = service.bulk_move_deals([1, 2], "won", closed_on="2026-10-05", actor_id=7)
    assert result["updated_ids"] == [1]
    [(sql, params)] = _notes(conn)
    assert "unnest" in sql and params[1:] == (7, [1], ["Closed on: none → 2026-10-05"])
    stmts = [s for s, _ in conn.executed]
    assert stmts.index(sql) > next(i for i, s in enumerate(stmts) if "deal_stage_events" in s)


def test_bulk_out_of_won_clears_and_notes_only_dated_deals(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchall_results=[[
        (3, "won", None, None, date(2026, 9, 1)), (4, "won", None, None, None),
    ]])
    service.bulk_move_deals([3, 4], "lead")
    [(_, params)] = _notes(conn)
    assert params[2] == [3] and params[3] == ["Closed on: 2026-09-01 → none"]


def test_bulk_ignores_closed_on_on_a_non_won_move(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchall_results=[[(1, "lead", None, None, None)]])
    result = service.bulk_move_deals([1], "qualified", closed_on="not a date")
    assert result["ok"] is True
    assert "closed_on" not in _update(conn)[0]


# ── Tools ────────────────────────────────────────────────────────────────────

def test_mark_won_tool_forwards_the_day_and_binds_the_seat(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "mark_deal_won",
                        lambda d, **kw: seen.update(kw) or {"id": d, "stage": "won"})
    monkeypatch.setattr(tools, "_record_provenance", lambda *a, **k: None)
    tools._identity_executors(FAKE_MEMBER)["crm_mark_deal_won"](
        deal_id=4, closed_on="2026-10-01", actor_id=99)
    assert seen == {"closed_on": "2026-10-01", "actor_id": FAKE_MEMBER["id"]}


@pytest.mark.parametrize("name", [
    "crm_update_deal", "crm_update_deal_stage", "crm_bulk_move_deals",
])
def test_every_stage_moving_tool_binds_the_seat(name):
    bound = tools._identity_executors(FAKE_MEMBER)[name]
    assert bound is not tools.TOOL_EXECUTORS[name]


def test_only_mark_won_advertises_closed_on():
    defs = {d["name"]: d for d in tools.CRM_TOOL_DEFS}
    assert "closed_on" in defs["crm_mark_deal_won"]["input_schema"]["properties"]
    for name in ("crm_update_deal_stage", "crm_bulk_move_deals"):  # they refuse Won (#99)
        assert "closed_on" not in defs[name]["input_schema"]["properties"]


def test_an_assistant_written_closed_on_is_badged(monkeypatch):
    """crm_mark_deal_won's explicit day reaches provenance, so the deal panel can badge it."""
    from crm import provenance_service
    recorded = {}
    monkeypatch.setattr(service, "mark_deal_won",
                        lambda d, **kw: {"id": d, "stage": "won", "probability": 100,
                                         "closed_on": kw.get("closed_on")})
    monkeypatch.setattr(provenance_service, "record_fields",
                        lambda et, eid, fields, **kw: recorded.update(fields))
    tools.crm_mark_deal_won(4, closed_on="2026-10-01")
    assert recorded.get("closed_on") == "2026-10-01"
