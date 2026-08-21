"""Bulk deal stage moves (issue #55) — the set-based write and its single-deal parity.

Hermetic: multi-statement writes go through the shared ``fake_conn`` fixture, so these
assert the SQL SHAPE, the grouping, and the per-deal branching. The real queries run
against Postgres in ``test_integration_crm_lifecycle_pg.py``.

The load-bearing assertions here are the ones that would let bulk drift from single:
that the rules come from ``_classify_deal_update`` (the shared classifier) rather than a
second copy, and that the stage-event INSERT rides the same cursor as the deal UPDATEs.
"""

import pytest

from crm import scoring_service, service


@pytest.fixture
def no_scoring(monkeypatch):
    """Capture the post-commit rescore instead of letting it hit the DB."""
    calls = []
    monkeypatch.setattr(
        scoring_service, "score_on_event",
        lambda deal_ids=(), contact_ids=(): calls.append((list(deal_ids), list(contact_ids))),
    )
    return calls


def _rows(*specs):
    """One fetchall payload of (id, stage, archived_at, contact_id) tuples."""
    return [list(specs)]


def _updates(conn):
    return [(s, p) for s, p in conn.executed if "UPDATE deals SET" in s]


# ── Whole-request refusals: no DB access at all ──────────────────────────────

def test_invalid_stage_is_refused_without_touching_the_database(monkeypatch, no_scoring):
    def explode(*a, **k):
        raise AssertionError("bulk_move_deals opened a connection for a bad stage")
    monkeypatch.setattr(service, "get_connection", explode)
    result = service.bulk_move_deals([1, 2], "nonsense")
    assert result == {"ok": False, "updated": 0, "updated_ids": [],
                     "errors": ["Invalid stage: nonsense"]}
    assert no_scoring == []


def test_empty_id_list_is_refused_without_touching_the_database(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("bulk_move_deals opened a connection for an empty list")
    monkeypatch.setattr(service, "get_connection", explode)
    assert service.bulk_move_deals([], "qualified")["errors"] == ["No deal IDs provided"]


def test_over_the_cap_is_refused_with_a_renderable_message(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("bulk_move_deals opened a connection over the cap")
    monkeypatch.setattr(service, "get_connection", explode)
    result = service.bulk_move_deals(list(range(1, service.BULK_MOVE_MAX + 2)), "qualified")
    assert result["ok"] is False
    assert result["errors"] == [
        f"Too many deals ({service.BULK_MOVE_MAX + 1}); max {service.BULK_MOVE_MAX} per bulk move"
    ]


# ── The set-based write ──────────────────────────────────────────────────────

def test_rows_are_locked_in_ascending_id_order(monkeypatch, fake_conn, no_scoring):
    """Ascending-id FOR UPDATE is merge_deals' documented deadlock rule; two concurrent
    bulks must queue rather than deadlock."""
    conn = fake_conn(monkeypatch, service,
                     fetchall_results=_rows((1, "lead", None, None)))
    service.bulk_move_deals([1], "qualified")
    select = next(s for s, _ in conn.executed if "SELECT id, stage" in s)
    assert "WHERE id = ANY(%s) ORDER BY id FOR UPDATE" in select


def test_a_plain_batch_collapses_to_one_update_and_one_event_insert(monkeypatch, fake_conn, no_scoring):
    conn = fake_conn(monkeypatch, service, fetchall_results=_rows(
        (1, "lead", None, 10), (2, "lead", None, 11), (3, "proposal", None, None),
    ))
    result = service.bulk_move_deals([1, 2, 3], "qualified")

    updates = _updates(conn)
    assert len(updates) == 1, "three deals with the same field map must share one UPDATE"
    sql, params = updates[0]
    assert sql == "UPDATE deals SET stage = %s, updated_at = %s WHERE id = ANY(%s)"
    assert params[0] == "qualified" and sorted(params[-1]) == [1, 2, 3]

    inserts = [(s, p) for s, p in conn.executed if "deal_stage_events" in s]
    assert len(inserts) == 1, "the whole batch's audit must be ONE multi-row INSERT"
    assert "unnest(%s::int[], %s::text[], %s::text[])" in inserts[0][0]
    assert inserts[0][1] == ([1, 2, 3], ["lead", "lead", "proposal"],
                            ["qualified", "qualified", "qualified"])
    assert result == {"ok": True, "updated": 3, "updated_ids": [1, 2, 3], "errors": []}


def test_the_audit_insert_rides_the_same_cursor_as_the_deal_updates(monkeypatch, fake_conn, no_scoring):
    """#1274: deals and their stage history commit together or not at all. Both
    statements landing on the one recorded cursor IS that guarantee, hermetically."""
    conn = fake_conn(monkeypatch, service, fetchall_results=_rows((1, "lead", None, None)))
    service.bulk_move_deals([1], "won")
    kinds = [s for s, _ in conn.executed]
    assert any("UPDATE deals SET" in s for s in kinds)
    assert any("INSERT INTO deal_stage_events" in s for s in kinds)


def test_groups_split_when_a_deal_leaves_lost_and_clears_its_reason(monkeypatch, fake_conn, no_scoring):
    conn = fake_conn(monkeypatch, service, fetchall_results=_rows(
        (1, "lead", None, None), (2, "lost", None, None),
    ))
    service.bulk_move_deals([1, 2], "qualified")

    updates = _updates(conn)
    assert len(updates) == 2, "different field maps must not share a statement"
    clearing = next((s, p) for s, p in updates if "lost_reason" in s)
    assert "" in clearing[1], "a deal leaving 'lost' clears its reason"
    assert clearing[1][-1] == [2]


def test_probability_is_settled_on_a_closing_transition(monkeypatch, fake_conn, no_scoring):
    for stage, expected in (("won", 100), ("lost", 0)):
        conn = fake_conn(monkeypatch, service, fetchall_results=_rows((1, "lead", None, None)))
        service.bulk_move_deals([1], stage)
        sql, params = _updates(conn)[0]
        assert "probability = %s" in sql
        assert expected in params


def test_a_deal_already_in_the_target_stage_is_skipped_entirely(monkeypatch, fake_conn, no_scoring):
    """No write at all — bumping updated_at would reset the deal's staleness clock
    (LAST_TOUCH_SQL reads updated_at as a touch) for a deal nothing changed."""
    conn = fake_conn(monkeypatch, service, fetchall_results=_rows(
        (1, "qualified", None, None), (2, "lead", None, None),
    ))
    result = service.bulk_move_deals([1, 2], "qualified")

    updates = _updates(conn)
    assert len(updates) == 1 and updates[0][1][-1] == [2]
    events = next(p for s, p in conn.executed if "deal_stage_events" in s)
    assert events[0] == [2], "the skipped deal logs no transition"
    assert result["updated"] == 1 and result["updated_ids"] == [2]
    assert result["errors"] == [], "a same-stage deal is a silent no-op, not an error"


def test_duplicate_ids_are_deduped_preserving_request_order(monkeypatch, fake_conn, no_scoring):
    conn = fake_conn(monkeypatch, service, fetchall_results=_rows(
        (5, "lead", None, None), (9, "lead", None, None),
    ))
    result = service.bulk_move_deals([9, 5, 9], "qualified")
    assert result["updated_ids"] == [9, 5]
    assert sorted(_updates(conn)[0][1][-1]) == [5, 9]


# ── Per-deal isolation ───────────────────────────────────────────────────────

def test_missing_and_archived_deals_do_not_sink_the_batch(monkeypatch, fake_conn, no_scoring):
    """The bulk-vs-single contract difference: _write_deal_update raises, bulk collects.
    One archived deal in a selection must not block the deals that CAN move."""
    conn = fake_conn(monkeypatch, service, fetchall_results=_rows(
        (3, "lead", None, None), (9, "lead", "2026-01-01T00:00:00", None),
    ))
    result = service.bulk_move_deals([3, 7, 9], "qualified")

    assert result["ok"] is True
    assert result["updated"] == 1 and result["updated_ids"] == [3]
    assert result["errors"] == [
        "Deal 7 not found",
        "Cannot change the stage of archived deal #9 — restore it first",
    ]
    assert _updates(conn)[0][1][-1] == [3]


def test_the_archived_message_is_the_single_deal_path_verbatim(monkeypatch, fake_conn, no_scoring):
    """Both paths raise from the same classifier, so the copy cannot diverge."""
    with pytest.raises(ValueError) as single:
        service._classify_deal_update(9, "lead", "2026-01-01T00:00:00", {"stage": "won"})
    conn = fake_conn(monkeypatch, service,
                     fetchall_results=_rows((9, "lead", "2026-01-01T00:00:00", None)))
    bulk = service.bulk_move_deals([9], "won")
    assert bulk["errors"] == [str(single.value)]
    assert _updates(conn) == []


# ── Post-commit rescore ──────────────────────────────────────────────────────

def test_rescore_runs_once_over_the_updated_deals_and_their_contacts(monkeypatch, fake_conn, no_scoring):
    fake_conn(monkeypatch, service, fetchall_results=_rows(
        (1, "lead", None, 10), (2, "lead", None, 11),
        (3, "qualified", None, 12), (4, "lead", "2026-01-01T00:00:00", 13),
    ))
    service.bulk_move_deals([1, 2, 3, 4], "qualified")

    assert len(no_scoring) == 1, "one rescore call for the whole batch, after commit"
    deal_ids, contact_ids = no_scoring[0]
    assert deal_ids == [1, 2], "only deals that actually moved"
    assert contact_ids == [10, 11], "skipped and refused deals contribute no contact"


def test_a_batch_that_moves_nothing_still_answers_ok(monkeypatch, fake_conn, no_scoring):
    conn = fake_conn(monkeypatch, service, fetchall_results=_rows((1, "qualified", None, None)))
    result = service.bulk_move_deals([1], "qualified")
    assert result == {"ok": True, "updated": 0, "updated_ids": [], "errors": []}
    assert _updates(conn) == []


# ── The shared classifier itself ─────────────────────────────────────────────

@pytest.mark.parametrize("old_stage,archived_at,filtered,expect_fields,expect_event", [
    ("lead", None, {"stage": "qualified"}, {"stage": "qualified"}, ("lead", "qualified")),
    ("lead", None, {"stage": "lead"}, {"stage": "lead"}, None),
    ("lost", None, {"stage": "lead"}, {"stage": "lead", "lost_reason": ""}, ("lost", "lead")),
    ("lost", None, {"stage": "lead", "lost_reason": "keep"},
     {"stage": "lead", "lost_reason": "keep"}, ("lost", "lead")),
    ("lead", None, {"stage": "won"}, {"stage": "won", "probability": 100}, ("lead", "won")),
    ("lead", None, {"stage": "lost"}, {"stage": "lost", "probability": 0}, ("lead", "lost")),
    # Already closed: editing probability on a decided deal stays the caller's call.
    ("won", None, {"probability": 30}, {"probability": 30}, None),
    # A non-stage edit on an archived deal is allowed; only a stage change is refused.
    ("lead", "2026-01-01T00:00:00", {"value": 5}, {"value": 5}, None),
])
def test_classifier_table(old_stage, archived_at, filtered, expect_fields, expect_event):
    fields, event = service._classify_deal_update(1, old_stage, archived_at, filtered)
    assert fields == expect_fields
    assert event == expect_event


def test_classifier_refuses_a_stage_change_on_an_archived_deal():
    with pytest.raises(ValueError, match="restore it first"):
        service._classify_deal_update(4, "lead", "2026-01-01T00:00:00", {"stage": "won"})


def test_classifier_never_mutates_its_input():
    original = {"stage": "lead"}
    service._classify_deal_update(1, "lost", None, original)
    assert original == {"stage": "lead"}, "callers reuse their dict; copies only"
