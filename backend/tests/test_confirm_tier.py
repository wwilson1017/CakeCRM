"""The routine confirmation tier (issue #180) — source guard + registry predicate.

Two of the three layers #180 asks for; the third (the live SSE loop) lives beside the
other gate tests in ``test_assistant_engine.py`` and ``test_gmail_engine_taint.py``.

Layer 1 is an AST sweep over ``backend/``: every ``confirm_tier`` anywhere in the
source must be the shared constant, must sit on a ``writes: True`` def, must not land
on a context-file tool, and the set of tools carrying it must be exactly the twenty
classified so far — #180's sixteen ``crm_*`` writes plus #186's four ``todo_*`` ones.
It is a source guard because the failure it exists to catch is a
*future* def quietly exempting itself — the registry can only check the defs that
exist. The sweep is falsifiable: ``test_the_detector_flags_synthetic_violations``
feeds it hand-written bad defs, and ``test_the_sweep_reached_the_real_defs`` fails if
it ever stops finding the real ones.

Layer 2 drives the real ``ToolRegistry``: the predicate answers False for unknown
names, reads, aliases and every write nobody classified, and construction fails loud
on a mistyped tier.
"""

import ast
from pathlib import Path

import pytest

from assistant import confirm_tier
from assistant.confirm_tier import ROUTINE
from assistant.registry import ToolRegistry
from context_files import tools as context_file_tools

BACKEND = Path(__file__).resolve().parent.parent

# The classified set, spelled out here rather than imported: this literal is the pin.
# Importing it from the source would make the test agree with any future edit, which is
# the one thing it must not do. Changing this set is a deliberate act with a review.
#
# Split by DEFINING MODULE, because no single registry ever holds all twenty: normal
# mode loads the sixteen and no todo tools at all, while GTD mode (the product default)
# hides three of the sixteen and adds the four. The union is what the SOURCE declares.
ROUTINE_CRM_TOOLS = frozenset({
    "crm_create_contact", "crm_update_contact",
    "crm_create_company", "crm_update_company",
    "crm_create_deal", "crm_update_deal", "crm_update_deal_stage",
    "crm_mark_deal_won", "crm_mark_deal_lost",
    "crm_log_activity",
    "crm_create_task", "crm_update_task", "crm_complete_task",
    "crm_set_contact_fields", "crm_set_company_fields", "crm_set_deal_fields",
})
# #186. The other six todo tools stay unclassified: `todo_bulk_update` (rule 4, bulk),
# `todo_delete` / `todo_delete_project` (rule 3, a hard DELETE), and the three reads.
ROUTINE_TODO_TOOLS = frozenset({
    "todo_create", "todo_update", "todo_create_project", "todo_update_project",
})
ROUTINE_TOOLS = ROUTINE_CRM_TOOLS | ROUTINE_TODO_TOOLS

_CONTEXT_FILE_TOOLS = frozenset(d["name"] for d in context_file_tools.get_context_file_tools()[0])


# ── Layer 1: the AST sweep ────────────────────────────────────────────────────

def _dict_keys(node: ast.Dict) -> dict[str, ast.expr]:
    """String-literal keys of a dict literal → their value nodes."""
    out: dict[str, ast.expr] = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out[k.value] = v
    return out


def _violations(tree: ast.AST, rel: str) -> tuple[list[str], set[str]]:
    """``(violations, names carrying the tier)`` for one parsed module."""
    problems: list[str] = []
    declared: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = _dict_keys(node)
        if "confirm_tier" not in keys:
            continue
        where = f"{rel}:{node.lineno}"
        value = keys["confirm_tier"]
        # The constant, never a bare string: a literal "routine" spelled by hand is the
        # drift assistant/confirm_tier.py exists to prevent.
        is_constant = (isinstance(value, ast.Name) and value.id == "ROUTINE") or (
            isinstance(value, ast.Attribute) and value.attr == "ROUTINE"
        )
        if not is_constant:
            problems.append(f"{where}: confirm_tier must be the ROUTINE constant, not {ast.dump(value)}")
        writes = keys.get("writes")
        if not (isinstance(writes, ast.Constant) and writes.value is True):
            problems.append(f"{where}: confirm_tier is only valid beside writes: True")
        name = keys.get("name")
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            declared.add(name.value)
            if name.value in _CONTEXT_FILE_TOOLS:
                problems.append(f"{where}: {name.value} is a context-file tool and can never be routine")
        else:
            problems.append(f"{where}: a confirm_tier def must carry a literal name")
        if rel.startswith("context_files/"):
            problems.append(f"{where}: context_files/ may not declare a confirm_tier")
    return problems, declared


def _scan_backend() -> tuple[list[str], dict[str, int]]:
    """Return ``(violations, {module: declarations found})`` over the real source."""
    problems: list[str] = []
    found: dict[str, int] = {}
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in ("tests", ".venv", "venv", "migrations"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            # Fail, never skip: a file this sweep could not read is a file whose defs
            # are unchecked, which is exactly what the sweep exists to prevent.
            problems.append(f"{rel}: could not be scanned ({exc.__class__.__name__}: {exc})")
            continue
        module_problems, declared = _violations(tree, str(rel))
        problems.extend(module_problems)
        if declared:
            found[str(rel)] = len(declared)
    return problems, found


def test_every_confirm_tier_in_backend_is_the_constant_on_a_write():
    problems, _ = _scan_backend()
    assert problems == []


def test_the_routine_set_is_exactly_the_twenty(task_mode):
    """A twenty-first tool cannot slip in, and none of the twenty can slip out."""
    declared = set()
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in ("tests", ".venv", "venv", "migrations"):
            continue
        _, names = _violations(ast.parse(path.read_text(encoding="utf-8")), str(rel))
        declared |= names
    assert declared == ROUTINE_TOOLS
    assert len(declared) == 20
    # And the source agrees with what a registry actually loads, in BOTH modes — each
    # loads a subset, and between them they cover every declaration.
    task_mode("normal")
    normal = ToolRegistry().routine_writes
    task_mode("gtd")
    gtd = ToolRegistry().routine_writes
    assert normal <= ROUTINE_TOOLS and gtd <= ROUTINE_TOOLS
    assert normal | gtd == ROUTINE_TOOLS


def test_the_sweep_reached_the_real_defs():
    """Guard against a vacuous pass: the sweep must still be finding both sources.

    Per module, never one repo-wide total — the larger file would satisfy a total on
    its own, and #186's four declarations are the ones a prefix-shaped sweep drops.
    """
    _, found = _scan_backend()
    assert found.get("crm/tools.py") == 16, found
    assert found.get("crm/gtd_tools.py") == 4, found


@pytest.mark.parametrize(
    "source, expect_flagged",
    [
        # A hand-spelled literal instead of the constant.
        ('D = {"name": "crm_x", "writes": True, "confirm_tier": "routine"}', True),
        # The tier on a read.
        ('D = {"name": "crm_x", "writes": False, "confirm_tier": ROUTINE}', True),
        # The tier on a protected context-file tool.
        ('D = {"name": "write_context_file", "writes": True, "confirm_tier": ROUTINE}', True),
        # No `writes` key at all.
        ('D = {"name": "crm_x", "confirm_tier": ROUTINE}', True),
        # A clean declaration.
        ('D = {"name": "crm_x", "writes": True, "confirm_tier": ROUTINE}', False),
        # A def with no tier is none of the sweep's business.
        ('D = {"name": "crm_x", "writes": True}', False),
    ],
)
def test_the_detector_flags_synthetic_violations(source, expect_flagged):
    """The sweep's own self-test — a detector nobody has seen fail proves nothing."""
    problems, _ = _violations(ast.parse(source), "crm/synthetic.py")
    assert bool(problems) is expect_flagged, problems


# ── Layer 2: the registry predicate ───────────────────────────────────────────

def test_every_routine_tool_is_a_declared_write(task_mode):
    task_mode("normal")
    reg = ToolRegistry()
    for name in ROUTINE_CRM_TOOLS:
        assert reg.is_write(name) is True, name
        assert reg.is_routine_write(name) is True, name
    # The todo tools do not exist in normal mode, so this registry holds the sixteen.
    assert reg.routine_writes == ROUTINE_CRM_TOOLS


def test_the_gtd_registry_holds_the_thirteen_plus_the_four(task_mode):
    """GTD mode (the product default) is where the routine tier must actually work."""
    from crm.tools import _TASK_TOOL_NAMES
    task_mode("gtd")
    reg = ToolRegistry()
    expected = (ROUTINE_CRM_TOOLS - _TASK_TOOL_NAMES) | ROUTINE_TODO_TOOLS
    assert len(expected) == 17
    for name in expected:
        assert reg.is_write(name) is True, name
        assert reg.is_routine_write(name) is True, name
    assert reg.routine_writes == expected


def test_an_unknown_name_is_not_routine(task_mode):
    task_mode("normal")
    reg = ToolRegistry()
    assert reg.is_routine_write("nope_not_a_tool") is False
    # crm_log_note is an executor-only alias with no def, so it has no tier to read.
    assert "crm_log_note" in reg.executors
    assert reg.is_routine_write("crm_log_note") is False


def test_no_read_is_routine(task_mode):
    task_mode("normal")
    reg = ToolRegistry()
    reads = [n for n, w in reg.writes_map.items() if not w]
    assert reads  # vacuity guard
    for name in reads:
        assert reg.is_routine_write(name) is False, name


@pytest.mark.parametrize("name", [
    "crm_delete_contact", "crm_delete_task", "crm_archive_deal", "crm_merge_deals",
    "crm_bulk_move_deals", "crm_recompute_lead_scores", "crm_add_note",
    "memory_add_fact", "memory_invalidate_fact",
    "write_context_file", "delete_context_file", "append_daily_note",
])
def test_unclassified_writes_keep_their_card(task_mode, name):
    task_mode("normal")
    reg = ToolRegistry()
    assert reg.is_write(name) is True, name  # vacuity: it really is a write
    assert reg.is_routine_write(name) is False, name


def test_the_gmail_draft_is_never_routine(task_mode, monkeypatch):
    from conftest import fake_admin

    from gmail import tools as gmail_tools

    monkeypatch.setattr(gmail_tools.store, "is_connected", lambda: True)
    task_mode("normal")
    # An admin seat: since #194 the Gmail tools are seat-gated, so a bare registry would
    # not carry the draft tool and this test would pass vacuously.
    reg = ToolRegistry(user=fake_admin())
    assert reg.is_write("gmail_create_draft") is True
    assert reg.is_routine_write("gmail_create_draft") is False


def test_notify_user_is_never_routine(task_mode):
    task_mode("normal")
    reg = ToolRegistry(background=True)
    assert reg.is_write("notify_user") is True
    assert reg.is_routine_write("notify_user") is False


def test_only_the_four_classified_todo_tools_are_routine(task_mode):
    """#186's answer for the whole family, asserted in BOTH directions.

    The six that still deny are the reason this is not "every todo write is routine":
    `todo_bulk_update` is bulk, the two deletes remove a record, and the three reads
    cannot carry a tier at all.
    """
    task_mode("gtd")
    reg = ToolRegistry()
    todos = [n for n in reg.writes_map if n.startswith("todo_")]
    assert len(todos) == 10, todos  # vacuity guard
    for name in todos:
        expected = name in ROUTINE_TODO_TOOLS
        assert reg.is_routine_write(name) is expected, name
    for name in ("todo_bulk_update", "todo_delete", "todo_delete_project"):
        assert reg.is_write(name) is True, name  # vacuity: they really are writes
        assert reg.is_routine_write(name) is False, name
    for name in ("todo_list", "todo_get", "todo_list_projects"):
        assert reg.is_write(name) is False, name
        assert reg.is_routine_write(name) is False, name


def test_gtd_mode_hides_the_three_task_tools_and_they_still_deny(task_mode):
    """Hidden from the model, so still denied by name — #186 classified their GTD
    replacements instead, which is what closes the gap this used to document."""
    task_mode("gtd")
    reg = ToolRegistry()
    for name in ("crm_create_task", "crm_update_task", "crm_complete_task"):
        assert name not in reg.writes_map, name
        assert reg.is_routine_write(name) is False, name


def test_no_routine_tool_is_a_targeted_context_file_write(task_mode):
    task_mode("normal")
    reg = ToolRegistry()
    assert reg.routine_writes.isdisjoint(context_file_tools._TARGETED_WRITE_TOOLS)


def test_confirm_tier_never_reaches_the_provider(task_mode):
    task_mode("normal")
    reg = ToolRegistry()
    assert any("confirm_tier" in d for d in reg.tool_defs)  # vacuity guard
    for mode in ("read-only", "normal", "power"):
        for t in reg.provider_tools(mode):
            assert "confirm_tier" not in t, (mode, t["name"])


# ── Layer 2b: construction fails loud on a bad tier ───────────────────────────

def _one_tool_source(**extra):
    """A (defs, executors) source carrying a single synthetic tool."""
    d = {"name": "synthetic_tool", "description": "d", "input_schema": {}, "kind": "test"}
    d.update(extra)
    return [d], {"synthetic_tool": lambda **kw: {"ok": True}}


@pytest.mark.parametrize("bad", ["Routine", "routine ", "ROUTINE", "", None, True, 1])
def test_a_typoed_tier_value_fails_construction(monkeypatch, bad):
    import assistant.registry as reg_mod
    monkeypatch.setattr(reg_mod, "get_memory_tools",
                        lambda: _one_tool_source(writes=True, confirm_tier=bad))
    with pytest.raises(ValueError, match="confirm_tier must be"):
        ToolRegistry()


def test_a_tier_on_a_read_fails_construction(monkeypatch):
    import assistant.registry as reg_mod
    monkeypatch.setattr(reg_mod, "get_memory_tools",
                        lambda: _one_tool_source(writes=False, confirm_tier=ROUTINE))
    with pytest.raises(ValueError, match="only valid on a write"):
        ToolRegistry()


def test_the_constant_is_accepted(monkeypatch):
    """Positive control for the two failure tests above."""
    import assistant.registry as reg_mod
    monkeypatch.setattr(reg_mod, "get_memory_tools",
                        lambda: _one_tool_source(writes=True, confirm_tier=ROUTINE))
    assert ToolRegistry().is_routine_write("synthetic_tool") is True


# ── The argument-level "removes from view" carve-out ──────────────────────────

@pytest.mark.parametrize("name, args, expected", [
    ("crm_update_contact", {"status": "archived"}, True),
    ("crm_update_contact", {"status": "ARCHIVED "}, True),
    ("crm_update_company", {"status": "archived"}, True),
    # `status` is undeclared on crm_update_task's schema, but arguments are not
    # validated against the schema at runtime and the executor forwards **kwargs into
    # service.update_task, whose allow-list accepts `status`. A dropped task is
    # filtered out of list_tasks unconditionally, so this is a soft delete.
    ("crm_update_task", {"status": "dropped"}, True),
    ("crm_update_task", {"status": "Dropped "}, True),
    ("crm_update_contact", {"status": "active"}, False),
    ("crm_update_contact", {"name": "New Name"}, False),
    ("crm_update_company", {}, False),
    # Completion is not removal from view — crm_complete_task is routine by design.
    ("crm_update_task", {"status": "done"}, False),
    ("crm_update_task", {"completed": True}, False),
    # Rule 3 does not reach tools that cannot hide a record through an argument.
    # Deals archive via `archived_at`, which _DEAL_USER_WRITABLE excludes.
    ("crm_update_deal", {"status": "archived"}, False),
    ("crm_create_contact", {"status": "archived"}, False),
    # Fails closed on argument shapes a provider can decode from malformed JSON.
    ("crm_update_contact", None, True),
    ("crm_update_contact", ["archived"], True),
    ("crm_update_contact", "archived", True),
    ("crm_update_contact", {"status": 7}, True),
    ("crm_update_task", None, True),
    # #186. `status` IS advertised on both todo update tools, and dropping is the
    # product's delete gesture — `todo_delete`'s own description sends the model here.
    ("todo_update", {"status": "dropped"}, True),
    ("todo_update", {"status": " DROPPED "}, True),
    ("todo_update_project", {"status": "dropped"}, True),
    # Completion is not removal, on a todo or on a project.
    ("todo_update", {"status": "done"}, False),
    ("todo_update_project", {"status": "completed"}, False),
    # Filing between working lists is not removal either — each has its own GTD page.
    ("todo_update", {"status": "someday_maybe"}, False),
    ("todo_update", {"status": "next_action"}, False),
    ("todo_update_project", {"status": "someday"}, False),
    ("todo_update_project", {"status": "active"}, False),
    ("todo_update", {"title": "New title"}, False),
    ("todo_update_project", {"name": "Renamed"}, False),
    # Creating an already-dropped row hides nothing that was visible — the same call
    # #180 made for crm_create_contact(status='archived') two rows above.
    ("todo_create", {"status": "dropped"}, False),
    ("todo_create_project", {"status": "dropped"}, False),
    # The unclassified todo writes never reach the carve-out; they confirm by name.
    ("todo_delete", {"todo_id": 1}, False),
    ("todo_bulk_update", {"ids": [1], "fields": {"status": "dropped"}}, False),
    # Fails closed on malformed argument shapes, exactly as the crm_* keys do.
    ("todo_update", None, True),
    ("todo_update", "dropped", True),
    ("todo_update_project", {"status": 7}, True),
])
def test_removes_from_view(name, args, expected):
    assert confirm_tier.removes_from_view(name, args) is expected


def test_the_hiding_statuses_are_real_values_the_services_accept():
    """A carve-out keyed on a value nothing can store would silently protect nothing."""
    from crm import gtd_common, service as crm_service
    hiding = confirm_tier._HIDING_STATUS
    assert hiding["crm_update_contact"] <= set(crm_service.CONTACT_STATUSES)
    assert hiding["crm_update_company"] <= set(crm_service.COMPANY_STATUSES)
    assert hiding["crm_update_task"] <= set(gtd_common.TODO_STATUSES)
    assert hiding["todo_update"] <= set(gtd_common.TODO_STATUSES)
    assert hiding["todo_update_project"] <= set(gtd_common.PROJECT_STATUSES)
    # And `status` really does reach the column on every one of those write paths.
    assert "status" in crm_service._TASK_UPDATE_FIELDS
    assert "status" in gtd_common.TODO_FIELDS
    assert "status" in gtd_common.PROJECT_FIELDS


def test_status_is_the_only_hiding_argument_the_todo_updates_accept():
    """The carve-out is keyed on ONE argument, so nothing else may be able to hide.

    Tool arguments are never validated against the schema at runtime, so the question
    is what the SERVICE accepts, not what the def advertises. `gtd_service._check_fields`
    RAISES on anything outside these sets — unlike `service.update_task`, which silently
    filters — so the reachable arguments are exactly their members. `deal_id` is the one
    other column that can hide a todo (`list_todos` drops todos on archived deals via
    `LIVE_PREDICATE_D`), and it is not reachable here.
    """
    from crm import gtd_common, gtd_service
    from crm.gtd_common import ValidationError

    assert gtd_common.PROJECT_FIELDS == {"name", "notes", "status"}
    assert gtd_common.TODO_FIELDS.isdisjoint(
        {"deal_id", "contact_id", "owner_id", "completed", "archived_at"}
    )
    # Not merely absent from a constant — actually refused, before any connection opens.
    for fields in ({"deal_id": 7}, {"completed": True}, {"archived_at": "2026-01-01"}):
        with pytest.raises(ValidationError):
            gtd_service.update_todo(1, fields)
    with pytest.raises(ValidationError):
        gtd_service.update_project(1, {"archived": True})


def test_only_routine_tools_need_the_carve_out(task_mode):
    """The carve-out exists to narrow the tier, so it must sit on tools IN the tier.

    Checked against the UNION of both registries: since #186 the map spans two tool
    modules, and no single registry holds every key — normal mode has no `todo_update`
    and GTD mode hides `crm_update_task`.
    """
    task_mode("normal")
    routine = ToolRegistry().routine_writes
    task_mode("gtd")
    routine |= ToolRegistry().routine_writes
    for name in confirm_tier._HIDING_STATUS:
        assert name in routine, name
