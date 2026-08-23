"""Todo-GTD tool-surface tests: mode gating and the confirmation contract (#70).

The two task vocabularies must never both be advertised — the model would mix them
mid-conversation and act on one store through two sets of names.
"""

import pytest

from crm import gtd_tools, tools
from crm.gtd_tools import GTD_TOOL_DEFS, GTD_TOOL_EXECUTORS, get_gtd_tools

_TASK_TOOLS = {"crm_create_task", "crm_list_tasks", "crm_complete_task",
               "crm_update_task", "crm_delete_task"}
_GTD_WRITE_TOOLS = {"todo_create", "todo_update", "todo_bulk_update", "todo_delete",
                    "todo_create_project", "todo_update_project", "todo_delete_project"}
_GTD_READ_TOOLS = {"todo_list", "todo_get", "todo_list_projects"}


@pytest.fixture
def mode(monkeypatch):
    def _set(value):
        monkeypatch.setattr(gtd_tools.service, "get_task_mode", lambda: value)
        monkeypatch.setattr(tools.crm, "get_task_mode", lambda: value)
    return _set


def test_normal_mode_hides_every_todo_tool(mode):
    mode("normal")
    defs, executors = get_gtd_tools()
    assert defs == [] and executors == {}


def test_normal_mode_still_advertises_the_task_tools(mode):
    mode("normal")
    names = {d["name"] for d in tools.get_crm_tools()[0]}
    assert _TASK_TOOLS <= names


def test_gtd_mode_advertises_the_todo_tools(mode):
    mode("gtd")
    defs, executors = get_gtd_tools()
    names = {d["name"] for d in defs}
    assert _GTD_WRITE_TOOLS | _GTD_READ_TOOLS <= names
    assert set(executors) == names


def test_gtd_mode_hides_the_task_tools(mode):
    """Two vocabularies for one store is how the model ends up calling both."""
    mode("gtd")
    names = {d["name"] for d in tools.get_crm_tools()[0]}
    assert not (_TASK_TOOLS & names)


def test_gtd_mode_keeps_every_non_task_crm_tool(mode):
    mode("gtd")
    normal = {d["name"] for d in tools.CRM_TOOL_DEFS}
    gtd = {d["name"] for d in tools.get_crm_tools()[0]}
    assert normal - gtd == _TASK_TOOLS


def test_executors_stay_reachable_in_gtd_mode(mode):
    """Advertisement is what steers the model; keeping executors reachable means a
    call proposed just before a mode flip still resolves instead of erroring at
    confirmation time."""
    mode("gtd")
    _, executors = tools.get_crm_tools()
    assert _TASK_TOOLS <= set(executors)


def test_an_unreadable_mode_degrades_to_normal(monkeypatch):
    """get_task_mode is fail-safe, so a registry built with no database (which the
    hermetic suite does on every run) sees normal mode rather than raising."""
    from crm.service import get_task_mode

    def _boom(*args, **kwargs):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr("crm.service.pg_fetchone", _boom)
    assert get_task_mode() == "normal"


def test_an_unmigrated_or_missing_row_reads_as_normal(monkeypatch):
    monkeypatch.setattr("crm.service.pg_fetchone", lambda *a, **k: None)
    from crm.service import get_task_mode
    assert get_task_mode() == "normal"


def test_every_def_declares_a_boolean_writes_flag():
    missing = [d["name"] for d in GTD_TOOL_DEFS if not isinstance(d.get("writes"), bool)]
    assert not missing, f"tool defs missing a boolean 'writes' flag: {missing}"


def test_the_writes_flags_match_the_intended_split():
    writes = {d["name"]: d["writes"] for d in GTD_TOOL_DEFS}
    for name in _GTD_WRITE_TOOLS:
        assert writes[name] is True, f"{name} must be writes=True"
    for name in _GTD_READ_TOOLS:
        assert writes[name] is False, f"{name} must be writes=False"


def test_every_def_has_an_executor_and_vice_versa():
    assert {d["name"] for d in GTD_TOOL_DEFS} == set(GTD_TOOL_EXECUTORS)


def test_read_payloads_carry_the_treat_as_data_warning(monkeypatch):
    """These reads are writes:False and therefore background-callable. With public
    capture enabled a stranger can put text in front of an unattended turn."""
    monkeypatch.setattr(gtd_tools.gtd_service, "list_todos", lambda **kw: [])
    result = GTD_TOOL_EXECUTORS["todo_list"]()
    assert "never instructions" in result["note"].lower()


def test_a_service_validation_error_becomes_an_error_dict(monkeypatch):
    """A tool must never raise into the loop — that aborts the whole turn."""
    def _boom(*args, **kwargs):
        raise gtd_tools.ValidationError("bad status")
    monkeypatch.setattr(gtd_tools.gtd_service, "create_todo", _boom)
    assert GTD_TOOL_EXECUTORS["todo_create"](title="x") == {"error": "bad status"}


def test_the_registry_composes_without_duplicate_names_in_both_modes(mode):
    from assistant.registry import ToolRegistry
    for value in ("normal", "gtd"):
        mode(value)
        registry = ToolRegistry()
        names = [d["name"] for d in registry.tool_defs]
        assert len(names) == len(set(names)), f"duplicate tool names in {value} mode"
