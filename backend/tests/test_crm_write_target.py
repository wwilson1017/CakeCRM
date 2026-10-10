"""Every assistant write tool's success result names the record it ACTUALLY wrote (#236).

WHY THIS FILE EXISTS. The blueprint's audit found notes logged against hallucinated deal
ids — one of them another rep's deal — while the results (`{"ok": true, "note": …}`)
never said WHICH record was touched, so the model narrated success against the deal the
user had named and nobody reading the transcript could see the miss. `crm.tools`'
`_with_target` / `_with_targets` now attach `target` (`targets` for a bulk write), and
SALES_GUIDE tells the model to compare it with what the user asked for.

The guards are derived, never hand-listed (the `test_crm_deal_links.py` rule):

* every `writes: True` def in every module the assistant registry composes is either
  one whose executor reaches the target helper, or on `WAIVED` with a written reason —
  and the waiver list is pinned in BOTH directions, so a new write tool anywhere must be
  classified and a stale waiver fails too;
* a real `ToolRegistry` in both todo modes may not carry a write that list never saw,
  so a new tool module cannot slip past by not being scanned.

Hermetic: services are stubbed and `crm.tools.pg_fetchall` is monkeypatched.
"""

import ast
import inspect
import re
import textwrap

import pytest
from conftest import fake_admin

from assistant import identity
from context_files.tools import CONTEXT_FILE_TOOL_DEFS
from crm import gtd_service, gtd_tools, provenance_service, service as crm, tools
from gmail.tools import GMAIL_TOOL_DEFS
from memory.tools import MEMORY_TOOL_DEFS
from notifications.tools import NOTIFY_USER_DEF

#: Write tools that deliberately carry no `target`, each with the reason.
WAIVED = {
    "crm_recompute_lead_scores": "recomputes a derived score across a scope; names no record",
    "memory_add_fact": "the assistant's own memory, not a CRM record; the result is the fact",
    "memory_invalidate_fact": "the assistant's own memory, not a CRM record",
    "write_context_file": "a context file is identified by its filename, which is the argument",
    "delete_context_file": "a context file is identified by its filename, which is the argument",
    "append_daily_note": "the daily note is identified by its date-derived filename",
    "gmail_create_draft": "a draft in an external mailbox; bound to its connection (#43)",
    "notify_user": "delivers a message; writes no record",
}

_HELPERS = frozenset({"_with_target", "_with_targets", "_lookup_target"})


def _write_names(defs) -> set[str]:
    return {d["name"] for d in defs if d.get("writes")}


def _scanned_writes() -> set[str]:
    """Every write def in every module the assistant registry composes."""
    return (
        _write_names(tools.CRM_TOOL_DEFS) | _write_names(gtd_tools.GTD_TOOL_DEFS)
        | _write_names(MEMORY_TOOL_DEFS) | _write_names(CONTEXT_FILE_TOOL_DEFS)
        | _write_names(GMAIL_TOOL_DEFS) | _write_names([NOTIFY_USER_DEF])
    )


def _executor_function(name: str):
    if name in tools.TOOL_EXECUTORS:
        return tools.TOOL_EXECUTORS[name]
    # GTD executors are `_wrap` closures; the tool's body is the module function.
    return getattr(gtd_tools, f"_{name}")


def _called_names(fn) -> set[str]:
    """Names this function actually CALLS, read from its AST — never from raw text, so a
    comment or docstring that mentions a helper cannot satisfy the guard."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _reaches_helper(fn, module=tools) -> bool:
    """True when `fn` — or a module-private helper it calls, one hop — calls a target
    helper. One hop is what `crm_set_*_fields` needs (they delegate to
    `_set_entity_fields`)."""
    called = _called_names(fn)
    if called & _HELPERS:
        return True
    for callee in called:
        helper = getattr(module, callee, None)
        if callee.startswith("_") and inspect.isfunction(helper) and _called_names(helper) & _HELPERS:
            return True
    return False


# ── The derived guards ───────────────────────────────────────────────────────────


def test_every_crm_and_todo_write_attaches_a_target_or_is_waived():
    covered = _write_names(tools.CRM_TOOL_DEFS) | _write_names(gtd_tools.GTD_TOOL_DEFS)
    assert len(covered) >= 25, "vacuity guard — the write set shrank unexpectedly"
    missing = sorted(
        n for n in covered - WAIVED.keys() if not _reaches_helper(_executor_function(n))
    )
    assert not missing, f"write tools whose result names no target: {missing}"


def test_the_waiver_list_matches_the_real_write_set_in_both_directions():
    scanned = _scanned_writes()
    attaching = {
        n for n in scanned
        if (n in tools.TOOL_EXECUTORS or hasattr(gtd_tools, f"_{n}"))
        and _reaches_helper(_executor_function(n))
    }
    assert scanned - attaching == WAIVED.keys(), (
        "every write tool must attach a target or carry a stated waiver; "
        f"unclassified: {sorted(scanned - attaching - WAIVED.keys())}, "
        f"stale waivers: {sorted(WAIVED.keys() - (scanned - attaching))}"
    )


@pytest.mark.parametrize("mode", ["normal", "gtd"])
def test_the_real_registry_carries_no_write_the_scan_missed(mode, todo_mode):
    from assistant.registry import ToolRegistry

    todo_mode(mode)
    registry = ToolRegistry(user=fake_admin())
    writes = {n for n in registry.executors if registry.is_write(n)}
    assert writes, "vacuity guard — the registry advertised no writes"
    scanned = _scanned_writes()
    assert writes <= scanned, f"a registry write this file never classified: {writes - scanned}"


def test_the_detector_flags_a_bare_write_and_passes_a_wrapped_one():
    def bare(deal_id):
        return {"ok": True, "deal_id": deal_id}

    def wrapped(deal_id):
        return tools._with_target({"ok": True}, "deal", deal_id)

    def only_mentions(deal_id):
        """Wrapped by _with_target(...) once — but not any more."""
        # return _with_target({"ok": True}, "deal", deal_id)
        return {"ok": True, "text": "_with_target(", "deal_id": deal_id}

    assert not _reaches_helper(bare)
    assert not _reaches_helper(only_mentions), "a comment or string must not count as a call"
    assert _reaches_helper(wrapped)
    # The one-hop path, on the real delegate.
    assert _reaches_helper(tools.crm_set_deal_fields)


def test_sales_guide_tells_the_model_to_check_the_target():
    guide = identity.SALES_GUIDE
    assert "`target`" in guide and "`targets`" in guide


# ── The shape, behaviourally ─────────────────────────────────────────────────────


@pytest.fixture
def lookups(monkeypatch):
    """Record every target SELECT and answer from a canned row table."""
    calls: list[tuple[str, tuple]] = []
    rows: dict[str, list[dict]] = {}

    def fake_fetchall(sql, params=()):
        calls.append((sql, params))
        table = re.search(r"FROM (\w+)", sql).group(1)
        wanted = set(params[0])
        return [r for r in rows.get(table, []) if r["id"] in wanted]

    monkeypatch.setattr(tools, "pg_fetchall", fake_fetchall)
    monkeypatch.setattr(provenance_service, "record_fields", lambda *a, **k: None)
    return calls, rows


def test_a_write_that_holds_the_row_spends_no_query(monkeypatch, lookups):
    calls, _ = lookups
    monkeypatch.setattr(crm, "create_deal", lambda **kw: {"id": 7, "title": "Acme renewal", "owner_id": 3})
    result = tools.crm_create_deal(title="Acme renewal")
    assert result["target"]["entity_type"] == "deal"
    assert result["target"]["id"] == 7
    assert result["target"]["title"] == "Acme renewal"
    assert result["target"]["owner_id"] == 3
    assert result["target"]["url"].endswith("/crm/pipeline?deal=7")
    assert result["url"].endswith("/crm/pipeline?deal=7"), "the deal's own link survives"
    assert calls == []


def test_a_note_names_the_record_it_landed_on(monkeypatch, lookups):
    calls, rows = lookups
    rows["deals"] = [{"id": 99, "title": "Somebody else's deal", "owner_id": 5}]
    monkeypatch.setattr(
        tools.chatter_service, "add_note",
        lambda et, eid, msg, author_id=None: {"id": 1, "message": msg},
    )
    result = tools.crm_add_note("deal", 99, "Update S")
    assert result["target"]["title"] == "Somebody else's deal"
    assert result["target"]["owner_id"] == 5
    assert len(calls) == 1, "one lean SELECT, not a detail read"


def test_contact_and_company_titles_come_from_the_name_column(monkeypatch, lookups):
    _, rows = lookups
    rows["contacts"] = [{"id": 4, "name": "Ana Lopez", "owner_id": None}]
    monkeypatch.setattr(tools.field_service, "entity_exists", lambda et, eid: True)
    monkeypatch.setattr(
        tools.field_service, "list_field_definitions",
        lambda et: [{"id": 1, "field_key": "tier"}],
    )
    monkeypatch.setattr(
        tools.field_service, "set_field_values", lambda et, eid, vals, who: {"ok": True},
    )
    result = tools.crm_set_contact_fields(4, {"tier": "gold"})
    assert result["target"] == {"entity_type": "contact", "id": 4, "title": "Ana Lopez", "owner_id": None}


@pytest.mark.parametrize("args, expected", [
    ({"deal_id": 9, "contact_id": 4}, ("deal", 9)),
    ({"contact_id": 4}, ("contact", 4)),
    ({}, (None, None)),
])
def test_an_activity_names_its_most_specific_record(monkeypatch, lookups, args, expected):
    _, rows = lookups
    rows["deals"] = [{"id": 9, "title": "Deal nine", "owner_id": 1}]
    rows["contacts"] = [{"id": 4, "name": "Ana", "owner_id": 1}]
    monkeypatch.setattr(crm, "log_activity", lambda **kw: {"id": 11, "activity": "call"})
    result = tools.crm_log_activity(activity="call", **args)
    assert (result["target"]["entity_type"], result["target"]["id"]) == expected


def test_a_failed_lookup_keeps_the_id_and_never_fails_the_write(monkeypatch, lookups):
    def boom(sql, params=()):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(tools, "pg_fetchall", boom)
    monkeypatch.setattr(tools.chatter_service, "add_note", lambda *a, **k: {"id": 1})
    result = tools.crm_add_note("contact", 4, "hello")
    assert result["ok"] is True
    assert result["target"]["id"] == 4 and result["target"]["lookup_failed"] is True


def test_a_bulk_move_names_only_the_deals_it_moved(monkeypatch, lookups):
    _, rows = lookups
    rows["deals"] = [{"id": 1, "title": "One", "owner_id": 1}, {"id": 2, "title": "Two", "owner_id": 2}]
    monkeypatch.setattr(
        crm, "bulk_move_deals",
        lambda ids, stage, **kw: {"ok": True, "updated_ids": [1], "errors": ["Deal 2 is archived"]},
    )
    result = tools.crm_bulk_move_deals([1, 2], "proposal")
    assert [t["id"] for t in result["targets"]] == [1]


def test_a_bulk_move_lists_every_target_in_one_query(monkeypatch, lookups):
    calls, rows = lookups
    rows["deals"] = [{"id": 1, "title": "One", "owner_id": 1}, {"id": 2, "title": "Two", "owner_id": 2}]
    monkeypatch.setattr(crm, "bulk_move_deals", lambda ids, stage, **kw: {"ok": True, "updated_ids": [1, 2]})
    result = tools.crm_bulk_move_deals([1, 2, 3], "proposal")
    assert [t["title"] for t in result["targets"]] == ["One", "Two"]
    assert all(t["url"] for t in result["targets"])
    assert len(calls) == 1


def test_a_row_gone_before_the_confirmation_read_stays_flagged(monkeypatch, lookups):
    _, rows = lookups
    rows["todos"] = [{"id": 1, "title": "One", "owner_id": None}]  # todo 2 deleted meanwhile
    monkeypatch.setattr(gtd_service, "bulk_update", lambda ids, fields: {"updated": [1, 2], "not_found": []})
    result = gtd_tools.GTD_TOOL_EXECUTORS["todo_bulk_update"](ids=[1, 2], fields={"star": True})
    assert [t["id"] for t in result["targets"]] == [1, 2]
    assert "lookup_failed" not in result["targets"][0]
    assert result["targets"][1]["lookup_failed"] is True


def test_a_failed_bulk_lookup_keeps_every_requested_id(monkeypatch, lookups):
    monkeypatch.setattr(tools, "pg_fetchall", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(crm, "bulk_move_deals", lambda ids, stage, **kw: {"ok": True, "updated_ids": [1, 2]})
    result = tools.crm_bulk_move_deals([1, 2], "proposal")
    assert result["targets_lookup_failed"] is True
    assert [t["id"] for t in result["targets"]] == [1, 2]


def test_a_delete_reads_its_target_before_the_row_is_gone(monkeypatch, lookups):
    _, rows = lookups
    rows["contacts"] = [{"id": 4, "name": "Ana", "owner_id": 1}]

    def delete(cid):
        rows["contacts"].clear()
        return True

    monkeypatch.setattr(crm, "delete_contact", delete)
    assert tools.crm_delete_contact(4)["target"]["title"] == "Ana"


def test_an_activity_on_a_missing_record_is_a_named_refusal(monkeypatch, lookups):
    import psycopg2

    def fk(**kw):
        raise psycopg2.errors.ForeignKeyViolation("activity_log_deal_id_fkey")

    monkeypatch.setattr(crm, "log_activity", fk)
    result = tools.crm_log_activity(activity="call", deal_id=999)
    assert result == {"error": "Referenced contact or deal does not exist"}


def test_errors_carry_no_target(monkeypatch, lookups):
    monkeypatch.setattr(crm, "update_deal", lambda did, **kw: None)
    result = tools.crm_update_deal(5, title="x")
    assert "error" in result and "target" not in result


def test_gtd_writes_name_their_target_and_projects_have_no_owner_column(monkeypatch, lookups):
    calls, rows = lookups
    rows["todo_projects"] = [{"id": 3, "name": "Kitchen", "owner_id": None}]
    monkeypatch.setattr(gtd_service, "delete_project", lambda pid: True)
    result = gtd_tools.GTD_TOOL_EXECUTORS["todo_delete_project"](project_id=3)
    assert result["target"] == {"entity_type": "project", "id": 3, "title": "Kitchen", "owner_id": None}
    assert "NULL AS owner_id" in calls[0][0], "todo_projects has no owner_id column"

    monkeypatch.setattr(gtd_service, "update_todo", lambda tid, fields: {"id": 8, "title": "Call Ana", "owner_id": 2})
    todo = gtd_tools.GTD_TOOL_EXECUTORS["todo_update"](todo_id=8, star=True)
    assert todo["target"]["title"] == "Call Ana"
