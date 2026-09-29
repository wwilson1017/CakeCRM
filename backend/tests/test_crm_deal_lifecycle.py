"""Deal lifecycle + search + the archived-deal sweep (issue #22).

Hermetic: ``crm.service``'s pg helpers are monkeypatched with the shared Recorder and
multi-statement writes go through the ``fake_conn`` fixture, so these assert the SQL
SHAPE and the branching. The real queries are exercised against Postgres in
``test_integration_crm_lifecycle_pg.py``.

The archived-deal assertions are the important ones: `deals.archived_at` is only
meaningful if EVERY read filters on it, and a missed query site is invisible until a
user notices an archived deal inflating their pipeline value.
"""

import pytest

from crm import chatter_service, scoring_service, service
from tests.test_crm_service import Recorder


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(service, "pg_execute", r.execute)
    return r


@pytest.fixture
def no_field_embed(monkeypatch):
    """get_deal_detail/search_deals embed custom fields via field_service; stub it so
    these tests assert deal SQL, not the EAV batch query."""
    from crm import field_service
    monkeypatch.setattr(field_service, "get_field_values_batch", lambda *a, **k: {})


# ── _write_deal_update: stage events + stale lost_reason ─────────────────────

def test_stage_change_writes_a_stage_event_in_the_same_transaction(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None)])
    rec.fetchone_queue = [{"id": 1, "stage": "qualified"}]
    service.update_deal_stage(1, "qualified")

    stmts = [s for s, _ in conn.executed]
    assert any("SELECT stage, archived_at, contact_id FROM deals WHERE id = %s FOR UPDATE" in s
               for s in stmts)
    assert any("UPDATE deals SET stage = %s" in s for s in stmts)
    event = next((s, p) for s, p in conn.executed if "INSERT INTO deal_stage_events" in s)
    assert event[1] == (1, "lead", "qualified")


def test_no_stage_event_when_the_stage_does_not_change(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, value=500)
    assert not any("deal_stage_events" in s for s, _ in conn.executed)


def test_leaving_lost_clears_the_lost_reason(monkeypatch, rec, fake_conn):
    """cake_os shipped a stale-lost_reason bug and fixed it later; the fixed behavior
    is what we port. A reopened deal must not carry 'budget cut' into win/loss reads."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lost", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal_stage(1, "negotiation")
    sql, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert "lost_reason = %s" in sql
    assert "" in params


def test_entering_and_leaving_won_each_write_a_stage_event(monkeypatch, rec, fake_conn):
    """Weekly Touches (#179) derives a deal's win instant from deal_stage_events rather
    than from a closed_at column, so the journal must see every move INTO 'won' and every
    move back OUT of it. _classify_deal_update has no directional rule today; this pins
    that it never grows one. The neighbouring stage-event test moves lead to qualified,
    so it stays green under exactly the mutation this one catches."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("negotiation", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.mark_deal_won(1)
    entering = next(p for s, p in conn.executed if "INSERT INTO deal_stage_events" in s)
    assert entering == (1, "negotiation", "won")

    conn = fake_conn(monkeypatch, service, fetchone_results=[("won", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal_stage(1, "negotiation")
    leaving = next(p for s, p in conn.executed if "INSERT INTO deal_stage_events" in s)
    assert leaving == (1, "won", "negotiation")


def test_staying_lost_keeps_the_lost_reason(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lost", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, notes="still lost")
    sql, _ = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert "lost_reason" not in sql


def test_write_on_a_missing_deal_returns_none_without_updating(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[None])
    assert service.update_deal(99, value=1) is None
    assert not any("UPDATE deals SET" in s for s, _ in conn.executed)


# ── No-op writes leave the staleness clock alone (issue #96) ─────────────────
#
# LAST_TOUCH_SQL reads deals.updated_at as a touch, so a write that changes nothing must
# not happen at all. The decision is made by Postgres (an IS DISTINCT FROM test on the
# very columns being SET), so these assert the SQL shape and the branching; the real
# semantics are exercised against Postgres in test_integration_crm_lifecycle_pg.py.
#
# `rowcounts={"UPDATE deals SET": 0}` makes the conditional UPDATE report "matched no
# row" — i.e. Postgres found nothing to change. It is keyed on the statement rather than
# its position, so adding a query to the flow can't silently re-target it.

def test_the_update_only_fires_when_a_column_would_actually_change(monkeypatch, rec, fake_conn):
    """Without the IS DISTINCT FROM test, a redundant call bumps updated_at and silently
    drops the deal out of get_stale_deals and the heartbeat nudges for a whole window."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, value=500)
    sql, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert "value IS DISTINCT FROM %s" in sql
    # Bound twice: once to SET the column, once to compare it. updated_at is SET only —
    # the question the WHERE asks is whether anything ELSE changed.
    assert params[0] == 500 and params[-1] == 500
    assert "updated_at IS DISTINCT FROM" not in sql


def test_every_written_column_is_compared_and_cast(monkeypatch, rec, fake_conn):
    """Two ways to re-open #96 in one assertion. A column that is SET but never compared
    makes any write carrying it a guaranteed match (the PUT path sends the whole form, so
    one gap is enough). A comparison left UNCAST is just as bad: assignment and comparison
    contexts disagree, so an uncast INTEGER silently bumps on a fractional no-op and an
    uncast TEXT raises outright."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, title="T", value=5, notes="n", probability=20,
                        expected_close_date="2026-09-01", currency="USD",
                        contact_id=None, company_id=3, owner_id=None)
    sql, _ = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    written = {c.split(" = ")[0] for c in sql.split(" SET ")[1].split(" WHERE ")[0].split(", ")}
    assert written - {"updated_at"}, "the test wrote no deal columns"
    for col in written - {"updated_at"}:
        cast = service._DEAL_COLUMN_TYPES[col]
        assert f"{col} IS DISTINCT FROM %s::{cast}" in sql, f"{col} SET but not compared-and-cast"


def test_every_user_writable_column_has_a_declared_type():
    """The coupling between the two column lists, pinned in the direction that is SAFE.

    `_DEAL_USER_WRITABLE` is the security boundary — what `crm_update_deal`'s raw model
    kwargs and `PUT /api/crm/deals/{id}`'s body may write — and it is hand-maintained and
    default-closed. `_DEAL_COLUMN_TYPES` is the wider set the write chokepoint can be asked
    to write, internal-only columns included. Deriving the boundary FROM the map (as this
    briefly did) is default-OPEN: declaring a type for a new internal column would silently
    make it writable by unvalidated input in the same commit.

    So the guarantee runs one way only — every user-writable column must have a declared
    type — and it is asserted here, in the hermetic suite that CI actually runs, rather
    than in the integration suite that is deselected by default.
    """
    undeclared = service._DEAL_USER_WRITABLE - service._DEAL_COLUMN_TYPES.keys()
    assert not undeclared, f"user-writable but no declared type: {sorted(undeclared)}"


def test_update_deal_refuses_the_columns_unvalidated_input_must_never_reach(
    monkeypatch, rec, fake_conn):
    """Asserted through `update_deal` rather than against the constant, because the hazard
    is HOW the allowlist is computed, not what the frozenset happens to contain: a later
    change deriving `allowed` from `_DEAL_COLUMN_TYPES` again would widen the boundary
    while leaving the frozenset untouched. `crm_update_deal` forwards the model's raw
    kwargs here, so these are reachable from an unvalidated caller.

    `lead_score` is never user/tool/assistant-writable (#18) and `archived_at` is owned by
    `archive_deal`; `lost_reason` has its own test above.
    """
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, lead_score=99, archived_at="2026-01-01T00:00:00+00:00")
    assert not any("UPDATE deals SET" in s for s, _ in conn.executed)
    assert not any("lead_score" in s or "archived_at" in s for s, _ in conn.executed)


def test_a_same_stage_move_cannot_match_its_own_row(monkeypatch, rec, fake_conn):
    """crm_update_deal_stage re-asserting a deal's current stage — the assistant
    redundancy #96 was filed for. The UPDATE is still ISSUED (Postgres does the
    deciding), so what has to hold hermetically is that its WHERE compares `stage`
    against the value already stored, which no row can satisfy."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("qualified", None, None)],
                     rowcounts={"UPDATE deals SET": 0})
    rec.fetchone_queue = [{"id": 1, "stage": "qualified"}]
    service.update_deal_stage(1, "qualified")
    sql, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert "stage IS DISTINCT FROM %s" in sql
    assert params[-1] == "qualified"     # compared against the stage the deal already has
    assert not any("deal_stage_events" in s for s, _ in conn.executed)


def test_no_stage_event_is_logged_when_the_update_matched_no_row(monkeypatch, rec, fake_conn):
    """Belt-and-braces on the audit log. A real stage change always differs, so the
    UPDATE always fires — gating the INSERT on rowcount too makes "no write, no history"
    structural rather than something you have to re-derive from the classifier."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None)],
                     rowcounts={"UPDATE deals SET": 0})
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal_stage(1, "won")
    assert any("UPDATE deals SET" in s for s, _ in conn.executed)
    assert not any("deal_stage_events" in s for s, _ in conn.executed)


# The next two do NOT guard the #96 fix — both invariants predate it and survive a
# revert. They guard the two plausible ways someone EXTENDS `changed` too far now that
# the flag exists, which is a live hazard precisely because the flag is new.

def test_the_changed_flag_must_not_reach_the_return_value(monkeypatch, rec, fake_conn):
    """False from _write_deal_update means "no such deal" and every caller turns it into
    None — a 404 / a tool error. Wiring the new `changed` flag into the return would make
    a harmless redundant call start reporting the deal missing."""
    fake_conn(monkeypatch, service, fetchone_results=[("won", None, None)],
              rowcounts={"UPDATE deals SET": 0})
    rec.fetchone_queue = [{"id": 1, "stage": "won"}]
    assert service.mark_deal_won(1) == {"id": 1, "stage": "won"}


def test_the_changed_flag_must_not_gate_the_rescore(monkeypatch, rec, fake_conn):
    """The rescore is deliberately unconditional. score_on_event is swallowed on failure
    and the daily refresh skips terminal deals that already carry a score, so re-calling
    mark_deal_won is the only repair route for a won deal whose rescore failed — gating
    it on `changed` would close that route."""
    from crm import scoring_service
    calls = []
    monkeypatch.setattr(scoring_service, "score_on_event", lambda **kw: calls.append(kw))
    fake_conn(monkeypatch, service, fetchone_results=[("won", None, None)],
              rowcounts={"UPDATE deals SET": 0})
    rec.fetchone_queue = [{"id": 1, "stage": "won"}]
    service.mark_deal_won(1)
    assert calls == [{"deal_ids": (1,), "contact_ids": (None, None)}]


def test_update_deal_ignores_a_model_supplied_lost_reason(monkeypatch, rec, fake_conn):
    """mark_deal_lost is lost_reason's only writer, so the reason always arrives with
    the close (and its timeline note) and can't be set on a deal that isn't lost."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, lost_reason="sneaky")
    assert not any("lost_reason" in s for s, _ in conn.executed)


# ── mark won / lost ──────────────────────────────────────────────────────────

def test_mark_deal_won_sets_stage_and_full_probability(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("negotiation", None, None)])
    rec.fetchone_queue = [{"id": 1, "stage": "won"}]
    assert service.mark_deal_won(1) == {"id": 1, "stage": "won"}
    _, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert "won" in params and 100 in params


def test_mark_deal_lost_records_reason_and_a_timeline_note(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("proposal", None, None)])
    rec.fetchone_queue = [{"id": 1, "stage": "lost"}]
    notes = []
    monkeypatch.setattr(chatter_service, "add_note",
                        lambda t, i, m, author_id=None: notes.append((t, i, m, author_id)))
    service.mark_deal_lost(1, lost_reason="chose a competitor")
    _, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert "lost" in params and 0 in params and "chose a competitor" in params
    # NULL author: the assistant tool is this default's caller, and Phase A does not thread
    # identity into tool executors, so "unattributed" is the honest record there.
    assert notes == [("deal", 1, "Deal lost — chose a competitor", None)]


def test_mark_deal_lost_credits_the_author_when_one_is_given(monkeypatch, rec, fake_conn):
    """A reason a REP typed must credit that rep (issue #128).

    `mark_deal_lost` swallows chatter failures by design, so a signature mismatch here
    degrades to a silently missing note rather than an error — which is exactly how this
    would rot unnoticed if nothing asserted the author reaches `add_note`.
    """
    fake_conn(monkeypatch, service, fetchone_results=[("proposal", None, None)])
    rec.fetchone_queue = [{"id": 1, "stage": "lost"}]
    notes = []
    monkeypatch.setattr(chatter_service, "add_note",
                        lambda t, i, m, author_id=None: notes.append((t, i, m, author_id)))
    service.mark_deal_lost(1, lost_reason="price", author_id=42)
    assert notes == [("deal", 1, "Deal lost — price", 42)]


def test_mark_deal_lost_without_a_reason_writes_no_note(monkeypatch, rec, fake_conn):
    fake_conn(monkeypatch, service, fetchone_results=[("proposal", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    called = []
    monkeypatch.setattr(chatter_service, "add_note",
                        lambda *a, **kw: called.append(a))
    service.mark_deal_lost(1)
    assert called == []


def test_mark_deal_lost_survives_a_note_failure(monkeypatch, rec, fake_conn):
    """The close is committed before the note is attempted — a chatter failure must
    never leave the deal un-closed."""
    fake_conn(monkeypatch, service, fetchone_results=[("proposal", None, None)])
    rec.fetchone_queue = [{"id": 1, "stage": "lost"}]

    # **kw, not just *a: mark_deal_lost passes author_id as a KEYWORD, and its blanket
    # `except Exception` would swallow the resulting TypeError — the RuntimeError below
    # would never be reached and this test would keep passing while proving nothing.
    def boom(*a, **kw):
        raise RuntimeError("chatter down")
    monkeypatch.setattr(chatter_service, "add_note", boom)
    assert service.mark_deal_lost(1, lost_reason="price")["stage"] == "lost"


def test_lost_reason_is_length_bounded(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("proposal", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    monkeypatch.setattr(chatter_service, "add_note", lambda *a, **kw: None)
    service.mark_deal_lost(1, lost_reason="x" * 5000)
    _, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert any(isinstance(v, str) and len(v) == service.MAX_LOST_REASON for v in params)


# ── archive / restore (who and why: #239) ───────────────────────────────────

_LIVE = (None, 7)                               # locked pre-image: archived_at, contact_id
_ARCHIVED = ("2026-02-01T00:00:00+00:00", 7)


def _stmts(conn):
    return [s for s, _ in conn.executed]


def test_archiving_a_live_deal_records_who_and_why_in_one_transaction(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[_LIVE])
    rec.fetchone_queue = [{"id": 1}]  # get_deal
    service.archive_deal(1, reason="  duplicate of #12  ", actor_id=5)
    stmts = _stmts(conn)
    assert stmts[0] == "SELECT archived_at, contact_id FROM deals WHERE id = %s FOR UPDATE"
    update = next(p for s, p in conn.executed if s.startswith("UPDATE deals SET"))
    assert update[1:] == (5, "duplicate of #12", 1)  # trimmed reason, actor, id
    note_sql, note = next((s, p) for s, p in conn.executed if "INSERT INTO crm_chatter" in s)
    assert "author_id" in note_sql
    assert note[:3] == ("deal", 1, "Archived — duplicate of #12") and note[4] == 5
    # The state change and its audit note ride ONE cursor in ONE transaction.
    assert conn.entries == 1 and len(set(conn.executed_by)) == 1


def test_archiving_needs_a_reason_and_writes_nothing_without_one(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[_LIVE])
    with pytest.raises(ValueError, match="reason is required"):
        service.archive_deal(1, reason="   ")
    assert not any(s.startswith(("UPDATE", "INSERT")) for s in _stmts(conn))


def test_re_archiving_is_a_no_op_even_without_a_reason(monkeypatch, rec, fake_conn):
    """Idempotent on the locked pre-image: the first actor/reason/date survive and no
    second note is written."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[_ARCHIVED])
    rec.fetchone_queue = [{"id": 1}]
    service.archive_deal(1)
    assert not any(s.startswith(("UPDATE", "INSERT")) for s in _stmts(conn))


def test_restore_clears_who_and_why_and_writes_a_restored_note(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[_ARCHIVED])
    rec.fetchone_queue = [{"id": 1}]
    service.archive_deal(1, archived=False, actor_id=5)
    assert ("UPDATE deals SET archived_at = NULL, archived_by = NULL, archived_reason = NULL "
            "WHERE id = %s") in _stmts(conn)
    note = next(p for s, p in conn.executed if "INSERT INTO crm_chatter" in s)
    assert note[2] == scoring_service.RESTORE_NOTE and note[4] == 5


def test_restoring_a_live_deal_writes_nothing(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[_LIVE])
    rec.fetchone_queue = [{"id": 1}]
    service.archive_deal(1, archived=False)
    assert not any(s.startswith(("UPDATE", "INSERT")) for s in _stmts(conn))


def test_archive_reason_is_length_bounded(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[_LIVE])
    rec.fetchone_queue = [{"id": 1}]
    service.archive_deal(1, reason="x" * 5000)
    update = next(p for s, p in conn.executed if s.startswith("UPDATE deals SET"))
    assert len(update[2]) == service.MAX_ARCHIVE_REASON


def test_archive_missing_deal_returns_none(monkeypatch, rec, fake_conn):
    fake_conn(monkeypatch, service, fetchone_results=[None])
    assert service.archive_deal(999, reason="x") is None


# ── merge ────────────────────────────────────────────────────────────────────

def test_merge_rejects_a_self_merge():
    with pytest.raises(ValueError, match="into itself"):
        service.merge_deals(5, 5)


def test_merge_rejects_a_missing_deal(monkeypatch, fake_conn):
    fake_conn(monkeypatch, service, fetchall_results=[[(1, "Kept", None, None)]])
    with pytest.raises(ValueError, match="Deal not found: 2"):
        service.merge_deals(1, 2)


def test_merge_repoints_moves_copies_and_archives_the_source(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchall_results=[[(1, "Kept", None, None), (2, "Dupe", None, None)]])
    rec.fetchone_queue = [{"id": 1}]
    scheduled = []
    monkeypatch.setattr(service.touch_count_service, "schedule_recompute",
                        lambda did, force_write=False: scheduled.append((did, force_write)))

    out = service.merge_deals(1, 2)

    stmts = [s for s, _ in conn.executed]
    joined = " || ".join(stmts)
    # Both rows locked in id order so concurrent merges queue instead of deadlocking.
    assert "SELECT id, title, archived_at, contact_id FROM deals WHERE id IN (%s, %s) ORDER BY id FOR UPDATE" in joined
    # Dated interactions + open work MOVE to the target.
    assert any("UPDATE activity_log SET deal_id = %s WHERE deal_id = %s" in s for s in stmts)
    assert any("UPDATE todos SET deal_id = %s" in s for s in stmts)
    # Notes are COPIED (source keeps its own thread for the restore case).
    assert any("INSERT INTO crm_chatter" in s and "left(%s || message, %s)" in s for s in stmts)
    # Custom fields gap-fill only — the target's own SET values must win, but a
    # CLEARED target field (an existing row holding '') must still be filled.
    upsert = next(s for s in stmts if "INSERT INTO crm_field_values" in s)
    assert "DO UPDATE SET value = EXCLUDED.value" in upsert
    assert "WHERE crm_field_values.value IS NULL OR crm_field_values.value = ''" in upsert
    # Source is archived, never deleted — and records why (#239).
    src = next(p for s, p in conn.executed if s.startswith("UPDATE deals SET archived_at"))
    assert src[2] == "Merged into deal #1"
    assert not any("DELETE FROM deals" in s for s in stmts)
    assert scheduled == [(1, True)]
    assert out == {"id": 1}


def test_merge_never_touches_the_targets_own_columns(monkeypatch, rec, fake_conn):
    """A merge consolidates history; it must not silently rewrite the surviving
    deal's title/value/stage."""
    conn = fake_conn(monkeypatch, service, fetchall_results=[[(1, "Kept", None, None), (2, "Dupe", None, None)]])
    rec.fetchone_queue = [{"id": 1}]
    monkeypatch.setattr(service.touch_count_service, "schedule_recompute",
                        lambda *a, **k: None)
    service.merge_deals(1, 2)
    deal_updates = [s for s, _ in conn.executed if s.startswith("UPDATE deals SET")]
    assert deal_updates == [
        "UPDATE deals SET archived_at = %s, archived_by = %s, archived_reason = %s, "
        "updated_at = %s WHERE id = %s"
    ]


def test_merge_records_the_actor_and_keeps_the_source_note_off_the_target(
    monkeypatch, rec, fake_conn,
):
    """#239: the source says why it vanished, by whom — and that audit note is written
    AFTER the note-copy step, so it stays on the source instead of being copied across."""
    conn = fake_conn(monkeypatch, service, fetchall_results=[[(1, "Kept", None, None), (2, "Dupe", None, None)]])
    rec.fetchone_queue = [{"id": 1}]
    monkeypatch.setattr(service.touch_count_service, "schedule_recompute", lambda *a, **k: None)
    service.merge_deals(1, 2, actor_id=5)
    stmts = _stmts(conn)
    copy_at = next(i for i, s in enumerate(stmts) if "left(%s || message, %s)" in s)
    notes = [(i, p) for i, (s, p) in enumerate(conn.executed)
             if s.startswith("INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, author_id)")]
    target_note = next(p for _, p in notes if p[0] == 1)
    assert target_note[-1] == 5  # the "Merged deal #2" note credits the actor
    src_at, src_note = next((i, p) for i, p in notes if p[1] == 2)
    assert src_at > copy_at
    assert src_note[2] == "Archived — Merged into deal #1" and src_note[4] == 5
    src_update = next(p for s, p in conn.executed if s.startswith("UPDATE deals SET archived_at"))
    assert src_update[1] == 5


# ── search_deals ─────────────────────────────────────────────────────────────

def test_search_deals_matches_title_notes_contact_and_company(rec, no_field_embed):
    rec.fetchall_queue = [[]]
    service.search_deals(search="acme")
    sql = rec.sql_containing("FROM deals d")
    assert "d.title ILIKE %s OR d.notes ILIKE %s OR c.name ILIKE %s OR co.name ILIKE %s" in sql
    assert rec.params_for("FROM deals d")[:4] == ["%acme%"] * 4


def test_search_deals_sort_is_allowlisted(rec, no_field_embed):
    rec.fetchall_queue = [[], []]
    service.search_deals(sort_by="value; DROP TABLE deals", sort_dir="sideways")
    sql = rec.sql_containing("FROM deals d")
    assert "DROP TABLE" not in sql
    assert "ORDER BY d.updated_at DESC" in sql  # unknown sort + dir fall back


def test_search_deals_honors_a_valid_sort(rec, no_field_embed):
    rec.fetchall_queue = [[]]
    service.search_deals(sort_by="value", sort_dir="asc")
    assert "ORDER BY d.value ASC, d.id ASC" in rec.sql_containing("FROM deals d")


def test_search_deals_custom_field_filter_uses_an_exists_join(rec, no_field_embed):
    rec.fetchall_queue = [[]]
    service.search_deals(custom_field_filters={"region": "north"})
    sql = rec.sql_containing("FROM deals d")
    assert "EXISTS (SELECT 1 FROM crm_field_values v" in sql
    assert "lower(v.value) = lower(%s)" in sql
    params = rec.params_for("FROM deals d")
    assert "region" in params and "north" in params


def test_search_deals_clamps_the_limit(rec, no_field_embed):
    # rec.params_for returns the FIRST match, so read the latest call directly.
    rec.fetchall_queue = [[], []]
    service.search_deals(limit=99999)
    assert rec.calls[-1][1][-1] == service.MAX_DEAL_SEARCH_LIMIT
    service.search_deals(limit="not a number")
    assert rec.calls[-1][1][-1] == 25


def test_search_deals_embeds_custom_field_values(rec, monkeypatch):
    from crm import field_service
    rec.fetchall_queue = [[{"id": 7, "title": "D"}]]
    monkeypatch.setattr(field_service, "get_field_values_batch",
                        lambda et, ids: {7: {"region": "north"}})
    out = service.search_deals(search="d")
    assert out[0]["custom_fields"] == {"region": "north"}


def test_get_deal_detail_embeds_custom_fields(rec, monkeypatch):
    from crm import field_service
    rec.fetchone_queue = [{"id": 7, "title": "D"}]
    rec.fetchall_queue = [[]]
    monkeypatch.setattr(field_service, "get_field_values_batch",
                        lambda et, ids: {7: {"region": "north"}})
    assert service.get_deal_detail(7)["custom_fields"] == {"region": "north"}


# ── the archived-deal sweep ──────────────────────────────────────────────────
# One test per read site. If a new deal-reading query is added without the
# predicate, archived deals leak back into that surface silently.

def test_pipeline_excludes_archived_deals(rec):
    rec.fetchall_queue = [[], []]
    service.get_pipeline()
    assert "d.archived_at IS NULL" in rec.sql_containing("last_activity_at")
    assert "archived_at IS NULL" in rec.sql_containing("GROUP BY stage")


def test_pipeline_include_archived_opens_only_the_deals_query(rec):
    """Issue #83's opt-in hole is exactly one query wide. The board may SHOW an archived
    deal (or an accidental archive is unrecoverable without an AI provider), but
    stage_summary must never COUNT one — won + archived would book revenue no report can
    see. A regression here is silent: the board looks right and the totals lie."""
    rec.fetchall_queue = [[], []]
    service.get_pipeline(include_archived=True)
    assert "archived_at IS NULL" not in rec.sql_containing("last_activity_at")
    assert "archived_at IS NULL" in rec.sql_containing("GROUP BY stage")


def test_pipeline_include_archived_still_binds_the_stage_filter(rec):
    """Dropping the predicate rebuilt the WHERE clause — the stage param must survive it."""
    rec.fetchall_queue = [[], []]
    service.get_pipeline(stage="lead", include_archived=True)
    sql = rec.sql_containing("last_activity_at")
    assert "d.stage = %s" in sql
    assert "archived_at IS NULL" not in sql
    assert list(rec.calls[0][1]) == ["lead"]


def test_pipeline_stage_filter_alone_keeps_the_sweep(rec):
    """The rebuilt WHERE must AND both conditions when only `stage` is given."""
    rec.fetchall_queue = [[], []]
    service.get_pipeline(stage="lead")
    sql = rec.sql_containing("last_activity_at")
    assert "d.archived_at IS NULL" in sql and "d.stage = %s" in sql


def test_list_deals_excludes_archived(rec):
    rec.fetchall_queue = [[]]
    service.list_deals()
    assert "d.archived_at IS NULL" in rec.sql_containing("FROM deals d")


def test_dashboard_excludes_archived(rec):
    rec.fetchone_queue = [{"cnt": 0}, {"cnt": 0}, {"cnt": 0}]
    rec.fetchall_queue = [[], [], [], []]
    service.get_dashboard_stats()
    assert "archived_at IS NULL" in rec.sql_containing("GROUP BY stage")
    assert "d.archived_at IS NULL" in rec.sql_containing("ORDER BY d.value DESC")


def test_analytics_excludes_archived(rec):
    rec.fetchone_queue = [{}]
    rec.fetchall_queue = [[], [], []]
    service.get_analytics()
    assert "archived_at IS NULL" in rec.sql_containing("AS avg_days_to_close")
    assert "d.archived_at IS NULL" in rec.sql_containing("AS days_since_touch")


def test_contact_and_company_detail_exclude_archived_deals(rec):
    rec.fetchone_queue = [{"id": 1, "name": "Ana"}]
    rec.fetchall_queue = [[], [], []]
    service.get_contact_detail(1)
    assert "archived_at IS NULL" in rec.sql_containing("WHERE contact_id = %s")

    rec2_calls = len(rec.calls)
    rec.fetchone_queue = [{"id": 2, "name": "Acme"}]
    rec.fetchall_queue = [[], [], []]
    service.get_company_detail(2)
    later = [s for s, _ in rec.calls[rec2_calls:]]
    assert any("d.archived_at IS NULL" in s for s in later)


def test_get_deal_still_resolves_an_archived_deal(rec):
    """Fetch-by-id must NOT filter: an archived deal has to stay readable so it can be
    shown, restored, or merged."""
    rec.fetchone_queue = [{"id": 1, "archived_at": "2026-01-01"}]
    service.get_deal(1)
    assert "archived_at IS NULL" not in rec.sql_containing("FROM deals d")


def test_is_crm_empty_still_counts_archived_deals(rec):
    """An archived deal is data, not absence of data — the first-run seed must not
    fire into a CRM that has one."""
    rec.fetchone_queue = [{"total": 1}]
    service.is_crm_empty()
    assert "archived_at" not in rec.sql_containing("SELECT COUNT(*) FROM deals")


def test_write_deal_update_rejects_an_empty_column_map():
    """No caller reaches this today; the guard exists so a future one can't emit
    `SET , updated_at = ...`."""
    with pytest.raises(ValueError, match="at least one column"):
        service._write_deal_update(1, {})


def test_merge_refuses_an_archived_deal(monkeypatch, fake_conn):
    """Merging into an archived target would move the source's whole history onto a
    deal every view already hides — the user would watch both deals disappear."""
    fake_conn(monkeypatch, service,
              fetchall_results=[[(1, "Kept", None, None), (2, "Dupe", "2026-02-01T00:00:00+00:00", None)]])
    with pytest.raises(ValueError, match=r"Cannot merge: deal #2 is archived"):
        service.merge_deals(1, 2)


def test_search_deals_normalizes_boolean_filter_values(rec, no_field_embed):
    """Boolean custom fields are stored '1'/'0'. A raw str(True) -> 'True' would match
    nothing, silently returning an empty result for every boolean filter."""
    rec.fetchall_queue = [[], []]
    service.search_deals(custom_field_filters={"is_key_account": True})
    assert "1" in rec.calls[-1][1]
    service.search_deals(custom_field_filters={"is_key_account": False})
    assert "0" in rec.calls[-1][1]


def test_search_deals_caps_the_number_of_custom_field_filters(rec, no_field_embed):
    """Every other model-supplied bound in search_deals is clamped; the filter count
    is one too, or an LLM can build a query with hundreds of EXISTS subqueries."""
    rec.fetchall_queue = [[]]
    service.search_deals(custom_field_filters={f"k{i}": "v" for i in range(50)})
    sql = rec.calls[-1][0]
    assert sql.count("EXISTS (SELECT 1 FROM crm_field_values") == service.MAX_CUSTOM_FIELD_FILTERS


def test_closing_via_the_generic_stage_path_still_settles_probability(monkeypatch, rec, fake_conn):
    """The Kanban drag and crm_update_deal_stage close deals too. Normalizing at the
    single write chokepoint keeps a 'won' deal from showing 30% win probability."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("negotiation", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal_stage(1, "won")
    _, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert 100 in params


def test_closing_overrides_even_an_explicit_probability(monkeypatch, rec, fake_conn):
    """Deliberate override: probability means "chance of winning", so a decided deal
    has exactly one correct value — and the edit form posts the stale 30% right
    alongside stage='won'."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("negotiation", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, stage="won", probability=80)
    _, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert 100 in params and 80 not in params


def test_editing_probability_on_an_already_closed_deal_is_respected(monkeypatch, rec, fake_conn):
    """No stage transition -> no normalization; the user's edit stands."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[("won", None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, probability=55)
    _, params = next((s, p) for s, p in conn.executed if "UPDATE deals SET" in s)
    assert 55 in params and 100 not in params


def test_archiving_does_not_reset_the_staleness_clock(monkeypatch, rec, fake_conn):
    """LAST_TOUCH_SQL treats updated_at as a touch, so bumping it here would make an
    archive→restore round-trip silently drop the deal out of get_stale_deals and the
    heartbeat's nudges."""
    conn = fake_conn(monkeypatch, service, fetchone_results=[(None, None), ("x", None)])
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.archive_deal(1, reason="junk")
    service.archive_deal(1, archived=False)
    assert not any("updated_at" in s for s in _stmts(conn))


def test_a_stage_change_on_an_archived_deal_is_refused(monkeypatch, rec, fake_conn):
    """won + archived would book revenue no report can see."""
    fake_conn(monkeypatch, service,
              fetchone_results=[("negotiation", "2026-02-01T00:00:00+00:00", None)])
    with pytest.raises(ValueError, match="restore it first"):
        service.mark_deal_won(1)


def test_a_non_stage_edit_on_an_archived_deal_is_still_allowed(monkeypatch, rec, fake_conn):
    conn = fake_conn(monkeypatch, service,
                     fetchone_results=[("lead", "2026-02-01T00:00:00+00:00", None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, notes="tidy up")
    assert any("UPDATE deals SET" in s for s, _ in conn.executed)


def test_create_deal_settles_probability_when_created_closed(rec):
    """The edit form's stage select offers won/lost, so create-as-closed is reachable."""
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_deal("Won on arrival", stage="won", probability=0)
    assert 100 in rec.params_for("INSERT INTO deals")


def test_todos_on_archived_deals_drop_out_but_standalone_todos_do_not(rec):
    rec.fetchall_queue = [[]]
    service.list_todos()
    sql = rec.sql_containing("FROM todos t")
    assert "(t.deal_id IS NULL OR d.archived_at IS NULL)" in sql


def test_activity_history_is_never_swept(rec):
    """Reviewing an archived deal's history is exactly what you need before restoring
    it — an activity row is a record of something that happened, not open work."""
    rec.fetchall_queue = [[]]
    service.get_activity_log(deal_id=5)
    assert "archived_at" not in rec.sql_containing("FROM activity_log a")


def test_close_date_sort_puts_undated_deals_last(rec, no_field_embed):
    rec.fetchall_queue = [[]]
    service.search_deals(sort_by="expected_close_date", sort_dir="asc")
    assert "NULLIF(d.expected_close_date, '') ASC NULLS LAST" in rec.calls[-1][0]


def test_search_can_surface_archived_deals_on_request(rec, no_field_embed):
    """The assistant's read that can find an archived deal. Since issue #83 it is no
    longer the ONLY one — get_pipeline(include_archived=True) is the keyless route back —
    but it is still the only way to find one by keyword."""
    rec.fetchall_queue = [[], []]
    service.search_deals(search="junk")
    assert "d.archived_at IS NULL" in rec.calls[-1][0]
    service.search_deals(search="junk", include_archived=True)
    assert "archived_at" not in rec.calls[-1][0]
