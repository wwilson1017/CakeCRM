"""Todo-GTD tool-surface tests: mode gating and the confirmation contract (#70).

The two task vocabularies must never both be advertised — the model would mix them
mid-conversation and act on one store through two sets of names.
"""

from crm import gtd_tools, tools
from crm.gtd_tools import GTD_TOOL_DEFS, GTD_TOOL_EXECUTORS, get_gtd_tools

_TASK_TOOLS = {"crm_create_task", "crm_list_tasks", "crm_complete_task",
               "crm_update_task", "crm_delete_task"}
_GTD_WRITE_TOOLS = {"todo_create", "todo_update", "todo_bulk_update", "todo_delete",
                    "todo_create_project", "todo_update_project", "todo_delete_project"}
_GTD_READ_TOOLS = {"todo_list", "todo_get", "todo_list_projects"}
# The four writes #186 classified ROUTINE. Spelled out, not derived: this is the pin.
_GTD_ROUTINE_TOOLS = {"todo_create", "todo_update",
                      "todo_create_project", "todo_update_project"}


def test_normal_mode_hides_every_todo_tool(task_mode):
    task_mode("normal")
    defs, executors = get_gtd_tools()
    assert defs == [] and executors == {}


def test_normal_mode_still_advertises_the_task_tools(task_mode):
    task_mode("normal")
    names = {d["name"] for d in tools.get_crm_tools()[0]}
    assert _TASK_TOOLS <= names


def test_gtd_mode_advertises_the_todo_tools(task_mode):
    task_mode("gtd")
    defs, executors = get_gtd_tools()
    names = {d["name"] for d in defs}
    assert _GTD_WRITE_TOOLS | _GTD_READ_TOOLS <= names
    assert set(executors) == names


def test_gtd_mode_hides_the_task_tools(task_mode):
    """Two vocabularies for one store is how the model ends up calling both."""
    task_mode("gtd")
    names = {d["name"] for d in tools.get_crm_tools()[0]}
    assert not (_TASK_TOOLS & names)


def test_gtd_mode_keeps_every_non_task_crm_tool(task_mode):
    task_mode("gtd")
    normal = {d["name"] for d in tools.CRM_TOOL_DEFS}
    gtd = {d["name"] for d in tools.get_crm_tools()[0]}
    assert normal - gtd == _TASK_TOOLS


def test_executors_stay_reachable_in_gtd_mode(task_mode):
    """Advertisement is what steers the model; keeping executors reachable means a
    call proposed just before a mode flip still resolves instead of erroring at
    confirmation time."""
    task_mode("gtd")
    _, executors = tools.get_crm_tools()
    assert _TASK_TOOLS <= set(executors)


# ── The fail-safe follows the product default (#102) ──────────────────────────

def test_an_unreadable_mode_degrades_to_the_gtd_default(monkeypatch):
    """get_task_mode is fail-safe, so a registry built with no database (which the
    hermetic suite does on every run) gets an answer rather than an exception.

    Since #102 that answer is GTD: a row we cannot read says nothing about what the
    user chose, so the honest guess is the experience a new install gets.
    """
    from crm.service import get_task_mode

    def _boom(*args, **kwargs):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr("crm.service.pg_fetchone", _boom)
    assert get_task_mode() == "gtd"


def test_an_unmigrated_or_missing_row_reads_as_the_gtd_default(monkeypatch):
    monkeypatch.setattr("crm.service.pg_fetchone", lambda *a, **k: None)
    from crm.service import get_task_mode
    assert get_task_mode() == "gtd"


def test_an_out_of_range_value_reads_as_the_gtd_default(monkeypatch):
    """The column carries a CHECK, so this is belt-and-braces — but the normalizer
    must not leak a junk value into the mode comparisons that gate the tool surface."""
    monkeypatch.setattr("crm.service.pg_fetchone", lambda *a, **k: {"task_mode": "kanban"})
    from crm.service import get_task_mode
    assert get_task_mode() == "gtd"


def test_all_four_fail_safes_agree_on_one_product_default(monkeypatch):
    """Four modules read the task mode and each carries its own fallback. They must
    name the SAME default — a split would give the assistant one task vocabulary and
    the heartbeat another on the very install that can least afford the confusion.

    Each wrapper's own `except` is exercised by making the lazy import fail, which is
    the only thing those handlers can actually catch (get_task_mode never raises).
    """
    import builtins

    from assistant import identity
    from crm.service import get_task_mode
    from heartbeat import service as heartbeat_service
    from telegram import service as telegram_service

    def _boom(*args, **kwargs):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr("crm.service.pg_fetchone", _boom)

    real_import = builtins.__import__

    def _no_crm_service(name, *args, **kwargs):
        if name == "crm.service":
            raise ImportError("simulated import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_crm_service)
    wrappers = [identity._task_mode(), heartbeat_service._task_mode(),
                telegram_service._task_mode()]
    monkeypatch.undo()

    monkeypatch.setattr("crm.service.pg_fetchone", _boom)
    assert set(wrappers) == {get_task_mode()} == {"gtd"}


def test_a_no_database_registry_advertises_the_gtd_vocabulary():
    """End-to-end proof that the flipped fail-safe reaches the tool surface: with no
    pool initialised (the hermetic default — no monkeypatching here on purpose), the
    todo tools are advertised and the normal task tools are not."""
    gtd_defs, _ = get_gtd_tools()
    crm_defs, _ = tools.get_crm_tools()
    advertised = {d["name"] for d in gtd_defs}
    assert _GTD_WRITE_TOOLS | _GTD_READ_TOOLS <= advertised
    assert not (_TASK_TOOLS & {d["name"] for d in crm_defs})


def test_every_def_declares_a_boolean_writes_flag():
    missing = [d["name"] for d in GTD_TOOL_DEFS if not isinstance(d.get("writes"), bool)]
    assert not missing, f"tool defs missing a boolean 'writes' flag: {missing}"


def test_the_writes_flags_match_the_intended_split():
    writes = {d["name"]: d["writes"] for d in GTD_TOOL_DEFS}
    for name in _GTD_WRITE_TOOLS:
        assert writes[name] is True, f"{name} must be writes=True"
    for name in _GTD_READ_TOOLS:
        assert writes[name] is False, f"{name} must be writes=False"


def test_the_confirm_tiers_match_the_intended_split():
    """#186's classification, pinned at the DEF (the registry pin lives in
    tests/test_confirm_tier.py). A tier here is an auto-approved write, so a def
    growing one by accident must fail a test in its own module too."""
    from assistant.confirm_tier import ROUTINE
    tiered = {d["name"] for d in GTD_TOOL_DEFS if "confirm_tier" in d}
    assert tiered == _GTD_ROUTINE_TOOLS
    for d in GTD_TOOL_DEFS:
        if d["name"] in _GTD_ROUTINE_TOOLS:
            assert d["confirm_tier"] is ROUTINE, d["name"]
            assert d["writes"] is True, d["name"]
    # The other six — bulk, both deletes, and the three reads — stay unclassified,
    # so they keep their Approve card. Absence of the key is the deny state.
    for name in (_GTD_WRITE_TOOLS | _GTD_READ_TOOLS) - _GTD_ROUTINE_TOOLS:
        assert name not in {d["name"] for d in GTD_TOOL_DEFS if "confirm_tier" in d}, name


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


def test_the_registry_composes_without_duplicate_names_in_both_modes(task_mode):
    from assistant.registry import ToolRegistry
    for value in ("normal", "gtd"):
        task_mode(value)
        registry = ToolRegistry()
        names = [d["name"] for d in registry.tool_defs]
        assert len(names) == len(set(names)), f"duplicate tool names in {value} mode"
