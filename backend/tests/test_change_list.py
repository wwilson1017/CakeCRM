"""Change lists (#371), hermetic: the per-field rules, the bounds and the tool binding.

The transaction, the stale-row check and Undo run against real Postgres in
``test_integration_change_list_pg.py``.
"""

import json

import pytest

from crm import change_list as cl, service, tools
from crm.tools import CRM_TOOL_DEFS, get_crm_tools

DEAL = {"archived_at": None, "stage": "lead"}
TODO = {"status": "next_action", "repeat": ""}


@pytest.mark.parametrize("kind, field, value, stored", [
    ("deal", "notes", "Long notes", "Long notes"),
    ("deal", "owner_id", 7, 7),
    ("todo", "owner_id", None, None),
    ("todo", "description", None, ""),
    ("todo", "tags", [" first ", "", "second"], ["first", "second"]),
    ("todo", "tags", None, []),
    ("deal", "title", "  Renewal  ", "Renewal"),
    ("deal", "stage", "proposal", "proposal"),
    ("deal", "value", 5000, 5000.0),
    ("deal", "expected_close_date", "2026-11-30", "2026-11-30"),
    ("deal", "expected_close_date", "", ""),
    ("deal", "probability", 140, 100),
    ("deal", "currency", " eur ", "EUR"),
    ("deal", "deal_temperature", "HOT", "hot"),
    ("deal", "deal_temperature", None, None),
    ("todo", "title", " Call the venue ", "Call the venue"),
    ("todo", "status", "someday_maybe", "someday_maybe"),
    ("todo", "due_date", None, ""),
    ("todo", "bring_back_on", "", None),
    ("todo", "star", True, True),
    ("todo", "context", " @calls ", "@calls"),
    ("todo", "priority", "high", "high"),
])
def test_a_value_is_stored_as_its_write_path_would_store_it(kind, field, value, stored):
    assert cl._normalize(kind, field, value) == stored


@pytest.mark.parametrize("kind, field, value", [
    ("deal", "notes", "x" * 20001), ("todo", "description", "x" * 20001),
    ("deal", "owner_id", True), ("deal", "owner_id", 0), ("todo", "owner_id", "7"),
    ("todo", "owner_id", -7), ("todo", "tags", "first"), ("todo", "tags", [1]),
    ("deal", "title", "   "), ("deal", "title", 5),
    ("deal", "stage", "won"), ("deal", "stage", "lost"), ("deal", "stage", "nope"),
    ("deal", "value", -1), ("deal", "value", "5000"), ("deal", "value", True),
    ("deal", "value", float("nan")), ("deal", "value", float("inf")),
    ("deal", "expected_close_date", "30/11/2026"), ("deal", "expected_close_date", "2026-13-40"),
    ("deal", "probability", 50.5), ("deal", "probability", True), ("deal", "probability", "50"),
    ("deal", "currency", "dollars"), ("deal", "currency", 5),
    ("deal", "deal_temperature", "tepid"),
    ("todo", "title", ""), ("todo", "status", "finished"), ("todo", "status", ["done"]),
    ("todo", "due_date", "tomorrow"), ("todo", "bring_back_on", 5),
    ("todo", "star", "false"), ("todo", "priority", "urgent"), ("todo", "context", "x" * 501),
])
def test_a_bad_value_is_refused_with_a_reason(kind, field, value):
    with pytest.raises(ValueError):
        cl._normalize(kind, field, value)


def test_the_field_lists_stay_inside_the_write_paths_own_allowlists():
    assert set(cl.FIELDS["deal"]) <= service._DEAL_USER_WRITABLE
    assert set(cl.FIELDS["todo"]) <= service._TODO_UPDATE_FIELDS
    # Left out on purpose: re-links and anything whose write has a side
    # effect Undo cannot take back.
    assert not set(cl.FIELDS["deal"]) & {"contact_id", "company_id"}
    assert not set(cl.FIELDS["todo"]) & {
        "contact_id", "deal_id", "completed", "repeat",
        "auto_star_on_due", "project", "project_id"}


@pytest.mark.parametrize("kind, record, field, value, word", [
    ("deal", None, "title", "x", "no such record"),
    ("deal", {**DEAL, "archived_at": "2026-11-01T00:00:00+00:00"}, "title", "x", "archived"),
    ("deal", {**DEAL, "stage": "won"}, "stage", "lead", "won deal"),
    ("deal", {**DEAL, "stage": "lost"}, "stage", "lead", "lost deal"),
    ("todo", None, "star", True, "no such record"),
    ("todo", {**TODO, "repeat": "weekly"}, "status", "done", "repeating"),
    ("todo", {"status": "done", "repeat": "weekly"}, "status", "next_action", "repeating"),
])
def test_a_record_that_cannot_take_the_change_says_why(kind, record, field, value, word):
    assert word in cl._blocked(kind, record, field, value)


@pytest.mark.parametrize("kind, record, field, value", [
    ("deal", DEAL, "stage", "proposal"),
    ("deal", {**DEAL, "stage": "won"}, "value", 5.0),  # a closed deal still takes other edits
    ("todo", TODO, "status", "done"),
    ("todo", {**TODO, "repeat": "weekly"}, "status", "waiting_for"),
    ("todo", {**TODO, "repeat": "weekly"}, "star", True),
])
def test_an_ordinary_change_is_not_blocked(kind, record, field, value):
    assert cl._blocked(kind, record, field, value) is None


def test_lines_are_cut_to_the_budget_and_say_how_many_were_left_out():
    lines = [f"deal {i} stage: " + "x" * 80 for i in range(200)]
    out = cl._bounded({"headline": "h"}, "lines", lines, cl.SUMMARY_BUDGET, suffix="; the card lists every row")
    assert len(json.dumps(out)) <= cl.SUMMARY_BUDGET
    kept = out["lines"]
    assert kept[:-1] == lines[:len(kept) - 1]
    assert kept[-1] == f"…and {200 - (len(kept) - 1)} more; the card lists every row"
    assert cl._bounded({"headline": "h"}, "lines", lines[:2], cl.SUMMARY_BUDGET, suffix="; the card lists every row")["lines"] == lines[:2]
    assert cl._bounded({"headline": "h"}, "lines", [], cl.SUMMARY_BUDGET, suffix="; the card lists every row") == {"headline": "h"}
    # One absurd line cannot push the placeholder past the reload cap either.
    huge = cl._bounded({"headline": "h"}, "lines", ["x" * 5000, "y"], cl.SUMMARY_BUDGET, suffix="; the card lists every row")
    assert huge["lines"] == ["…and 2 more; the card lists every row"]


def test_the_headline_counts_and_never_adds_money():
    rows = [
        {"record_type": "deal", "id": 1, "field": "value", "old": 100.0, "new": 250.0, "state": "ready"},
        {"record_type": "deal", "id": 1, "field": "stage", "old": "lead", "new": "proposal", "state": "ready"},
        {"record_type": "deal", "id": 2, "field": "value", "old": 900.0, "new": 1000.0, "state": "ready"},
        {"record_type": "todo", "id": 5, "field": "star", "old": False, "new": True, "state": "ready"},
        {"record_type": "deal", "id": 3, "field": "value", "old": None, "new": 5, "state": "refused"},
    ]
    assert cl._headline(rows) == "Change 4 fields on 2 deals and 1 todo; 1 row will not change"
    assert cl._headline(rows[3:4]) == "Change 1 field on 1 todo"


def test_the_tool_is_a_write_that_never_runs_from_the_models_arguments(monkeypatch):
    tool = next(d for d in CRM_TOOL_DEFS if d["name"] == cl.TOOL)
    assert tool["writes"] is True and "confirm_tier" not in tool
    assert set(tool["input_schema"]["properties"]) == {"changes", "reason"}
    assert "batch_id" not in json.dumps(tool["input_schema"])
    # No registry (a direct call), or a registry no approval has bound: refused.
    assert tools.crm_apply_change_list(changes=[], batch_id="chg_x") == {"error": cl.ONLY_FROM_CARD}
    seen = []
    monkeypatch.setattr(cl, "apply", lambda registry: seen.append(registry) or {"ok": True})
    marker = object()
    _, executors = get_crm_tools(user={"id": 7}, registry=marker)
    # A model-supplied `registry` is dropped by the binding, like every server-bound argument.
    assert executors[cl.TOOL](changes=[], registry="model") == {"ok": True}
    assert seen == [marker]


def test_the_deal_guard_does_not_refuse_the_whole_call_for_a_nested_id():
    """The per-row check is the stager's; the call-level guard must not see nested ids."""
    from crm import deal_write_guard

    args = {"changes": [{"record_type": "deal", "id": 99, "field": "stage", "value": "lead"}]}
    assert deal_write_guard.target_deal_ids(args) == []


@pytest.mark.parametrize("error", ["LockNotAvailable", "QueryCanceled", "DeadlockDetected"])
def test_busy_rows_fail_the_whole_step_and_write_nothing(monkeypatch, error):
    import types
    from contextlib import contextmanager

    import psycopg2

    @contextmanager
    def busy():
        raise getattr(psycopg2.errors, error)()
        yield

    monkeypatch.setattr(cl, "view", lambda batch_id, *, user_id: {"rows": []})
    monkeypatch.setattr(cl, "get_connection", busy)
    registry = types.SimpleNamespace(change_batch_id="chg_1", user={"id": 7})
    assert cl.apply(registry) == {"error": cl.BUSY}
    with pytest.raises(ValueError, match="busy"):
        cl.undo("chg_1", user_id=7)


def test_no_card_bounds_do_not_promise_a_card():
    out = cl._bounded({"error": "Nothing to change"}, "refused", ["x" * 400] * 200, 1800)
    assert out["refused"][-1] == f"…and {200 - len(out['refused']) + 1} more"
    assert len(json.dumps(out)) <= 1800
    assert "card" not in json.dumps(out)


@pytest.mark.parametrize("old, new, currency, expected", [
    (None, "", None, "(empty) → (empty)"),
    (False, True, None, "no → yes"),
    (1000, 2500.5, ["USD", "CAD"], "1,000 USD → 2,500.5 CAD"),
    (["one", "two"], ["three"], None, "one,two → three"),
    ("x" * 50, "y" * 50, None, "x" * 39 + "… → " + "y" * 39 + "…"),
])
def test_summary_samples_render_like_the_card_and_bound_each_value(old, new, currency, expected):
    row = {"record_type": "deal", "id": 7, "field": "value", "old": old, "new": new}
    if currency:
        row["currency"] = currency
    assert cl._sample(row) == "deal 7 value: " + expected
