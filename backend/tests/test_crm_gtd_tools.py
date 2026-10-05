"""Todo-GTD tool-surface tests: mode gating and the confirmation contract (#70).

The two todo vocabularies must never both be advertised — the model would mix them
mid-conversation and act on one store through two sets of names.
"""

from crm import gtd_tools, tools
from crm.gtd_tools import GTD_TOOL_DEFS, GTD_TOOL_EXECUTORS, get_gtd_tools

_TODO_TOOLS = {"crm_create_todo", "crm_list_todos", "crm_complete_todo",
               "crm_update_todo", "crm_delete_todo"}
_GTD_WRITE_TOOLS = {"todo_create", "todo_update", "todo_bulk_update", "todo_delete",
                    "todo_create_project", "todo_update_project", "todo_delete_project"}
_GTD_READ_TOOLS = {"todo_list", "todo_get", "todo_list_projects", "todo_weekly_review"}
# The four writes #186 classified ROUTINE. Spelled out, not derived: this is the pin.
_GTD_ROUTINE_TOOLS = {"todo_create", "todo_update",
                      "todo_create_project", "todo_update_project"}


def test_normal_mode_hides_every_todo_tool(todo_mode):
    todo_mode("normal")
    defs, executors = get_gtd_tools()
    assert defs == [] and executors == {}


def test_normal_mode_still_advertises_the_todo_tools(todo_mode):
    todo_mode("normal")
    names = {d["name"] for d in tools.get_crm_tools()[0]}
    assert _TODO_TOOLS <= names


def test_gtd_mode_advertises_the_todo_tools(todo_mode):
    todo_mode("gtd")
    defs, executors = get_gtd_tools()
    names = {d["name"] for d in defs}
    assert _GTD_WRITE_TOOLS | _GTD_READ_TOOLS <= names
    assert set(executors) == names


def test_gtd_mode_hides_the_todo_tools(todo_mode):
    """Two vocabularies for one store is how the model ends up calling both."""
    todo_mode("gtd")
    names = {d["name"] for d in tools.get_crm_tools()[0]}
    assert not (_TODO_TOOLS & names)


def test_gtd_mode_keeps_every_non_todo_crm_tool(todo_mode):
    todo_mode("gtd")
    normal = {d["name"] for d in tools.CRM_TOOL_DEFS}
    gtd = {d["name"] for d in tools.get_crm_tools()[0]}
    assert normal - gtd == _TODO_TOOLS


def test_executors_stay_reachable_in_gtd_mode(todo_mode):
    """Advertisement is what steers the model; keeping executors reachable means a
    call proposed just before a mode flip still resolves instead of erroring at
    confirmation time."""
    todo_mode("gtd")
    _, executors = tools.get_crm_tools()
    assert _TODO_TOOLS <= set(executors)


# ── The fail-safe follows the product default (#102) ──────────────────────────

def test_an_unreadable_mode_degrades_to_the_gtd_default(monkeypatch):
    """get_todo_mode is fail-safe, so a registry built with no database (which the
    hermetic suite does on every run) gets an answer rather than an exception.

    Since #102 that answer is GTD: a row we cannot read says nothing about what the
    user chose, so the honest guess is the experience a new install gets.
    """
    from crm.service import get_todo_mode

    def _boom(*args, **kwargs):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr("crm.service.pg_fetchone", _boom)
    assert get_todo_mode() == "gtd"


def test_an_unmigrated_or_missing_row_reads_as_the_gtd_default(monkeypatch):
    monkeypatch.setattr("crm.service.pg_fetchone", lambda *a, **k: None)
    from crm.service import get_todo_mode
    assert get_todo_mode() == "gtd"


def test_an_out_of_range_value_reads_as_the_gtd_default(monkeypatch):
    """The column carries a CHECK, so this is belt-and-braces — but the normalizer
    must not leak a junk value into the mode comparisons that gate the tool surface."""
    monkeypatch.setattr("crm.service.pg_fetchone", lambda *a, **k: {"todo_mode": "kanban"})
    from crm.service import get_todo_mode
    assert get_todo_mode() == "gtd"


def test_all_four_fail_safes_agree_on_one_product_default(monkeypatch):
    """Four modules read the todo mode and each carries its own fallback. They must
    name the SAME default — a split would give the assistant one todo vocabulary and
    the heartbeat another on the very install that can least afford the confusion.

    Each wrapper's own `except` is exercised by making the lazy import fail, which is
    the only thing those handlers can actually catch (get_todo_mode never raises).
    """
    import builtins

    from assistant import identity
    from crm.service import get_todo_mode
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
    wrappers = [identity._todo_mode(), heartbeat_service._todo_mode(),
                telegram_service._todo_mode()]
    monkeypatch.undo()

    monkeypatch.setattr("crm.service.pg_fetchone", _boom)
    assert set(wrappers) == {get_todo_mode()} == {"gtd"}


def test_a_no_database_registry_advertises_the_gtd_vocabulary():
    """End-to-end proof that the flipped fail-safe reaches the tool surface: with no
    pool initialised (the hermetic default — no monkeypatching here on purpose), the
    todo tools are advertised and the normal todo tools are not."""
    gtd_defs, _ = get_gtd_tools()
    crm_defs, _ = tools.get_crm_tools()
    advertised = {d["name"] for d in gtd_defs}
    assert _GTD_WRITE_TOOLS | _GTD_READ_TOOLS <= advertised
    assert not (_TODO_TOOLS & {d["name"] for d in crm_defs})


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


def test_the_registry_composes_without_duplicate_names_in_both_modes(todo_mode):
    from assistant.registry import ToolRegistry
    for value in ("normal", "gtd"):
        todo_mode(value)
        registry = ToolRegistry()
        names = [d["name"] for d in registry.tool_defs]
        assert len(names) == len(set(names)), f"duplicate tool names in {value} mode"


def test_project_tools_advertise_purpose_and_outcome():
    """#262: the model can set both fields, and the service allowlist accepts them."""
    from crm import gtd_common

    by_name = {d["name"]: d for d in GTD_TOOL_DEFS}
    for name in ("todo_create_project", "todo_update_project"):
        props = by_name[name]["input_schema"]["properties"]
        assert {"purpose", "outcome"} <= set(props), name
    assert {"purpose", "outcome"} <= gtd_common.PROJECT_FIELDS


def test_update_project_accepts_purpose_and_outcome_and_still_refuses_strangers(monkeypatch):
    """`_check_fields` RAISES on unknown keys; the two new ones must get past it."""
    import pytest

    from crm import gtd_service
    from crm.gtd_common import ValidationError

    statements: list[tuple[str, tuple]] = []

    class _Cur:
        def execute(self, sql, params=()):
            statements.append((sql, tuple(params)))

        def fetchone(self):
            return (1,)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return _Cur()

    monkeypatch.setattr(gtd_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(gtd_service, "get_project", lambda pid: {"id": pid})
    gtd_service.update_project(1, {"purpose": " why\nnow ", "outcome": ""})
    update_sql, params = statements[-1]
    assert "purpose = %s" in update_sql and "outcome = %s" in update_sql
    assert params[:2] == ("why now", "")  # one line; clearing is a real save
    with pytest.raises(ValidationError):
        gtd_service.update_project(1, {"area_id": 3})
