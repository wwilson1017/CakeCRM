"""CRM agent-tools contract: unconditional, complete, well-formed.

The issue requires the ~18 crm_* tools to be collected unconditionally (no
enable gate). This pins that: 17 schema defs, 18 executors (incl. the
crm_log_note back-compat alias), every def has an executor, and get_crm_tools()
returns the full set with no gating.
"""

import inspect

from crm import tools
from crm.tools import CRM_TOOL_DEFS, TOOL_EXECUTORS, get_crm_tools


def test_seventeen_defs_eighteen_executors():
    assert len(CRM_TOOL_DEFS) == 17
    assert len(TOOL_EXECUTORS) == 18


def test_def_names_unique_prefixed_and_schema_shaped():
    names = [d["name"] for d in CRM_TOOL_DEFS]
    assert len(names) == len(set(names))  # unique
    for d in CRM_TOOL_DEFS:
        assert d["name"].startswith("crm_"), d["name"]
        assert d["kind"] == "integration"
        assert isinstance(d.get("description"), str) and d["description"]
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)


def test_every_def_has_executor_and_alias_is_the_extra():
    def_names = {d["name"] for d in CRM_TOOL_DEFS}
    for name in def_names:
        assert name in TOOL_EXECUTORS, f"{name} has no executor"
        assert callable(TOOL_EXECUTORS[name])
    # the only executor without a schema def is the back-compat alias
    assert set(TOOL_EXECUTORS) == def_names | {"crm_log_note"}


def test_get_crm_tools_returns_full_set_unconditionally():
    defs, execs = get_crm_tools()
    assert defs is CRM_TOOL_DEFS
    assert execs is TOOL_EXECUTORS
    # No enable gate: the accessor takes no required args and never filters.
    sig = inspect.signature(get_crm_tools)
    assert not [p for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty and p.kind in
                (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)]


def test_no_enable_gate_in_source():
    src = inspect.getsource(tools)
    assert "is_enabled" not in src  # chatty's crm_lite gate must not survive the port


# ── writes flags (issue #4 — the assistant confirmation-gate linchpin) ────────
# INVARIANT, not a count: every def (including ones later issues append) must
# self-classify as a write or a read, or the confirmation gate silently lets a
# mutation through. Deliberately no exact-count / exact-set assertions here so
# sibling issues adding tools (companies, chatter) pass through untouched.

_OWNED_WRITE_TOOLS = {
    "crm_create_contact", "crm_update_contact", "crm_delete_contact",
    "crm_create_deal", "crm_update_deal", "crm_update_deal_stage",
    "crm_log_activity", "crm_create_task", "crm_complete_task",
}
_OWNED_READ_TOOLS = {
    "crm_find_contact", "crm_get_contact", "crm_list_contacts", "crm_get_pipeline",
    "crm_get_deal", "crm_get_activity_log", "crm_list_tasks", "crm_dashboard",
}


def test_every_def_declares_a_boolean_writes_flag():
    """Fail loud if ANY tool def is missing a boolean ``writes`` — a missing flag
    defaults to read-only and would bypass the assistant's confirmation gate."""
    missing = [d["name"] for d in CRM_TOOL_DEFS if not isinstance(d.get("writes"), bool)]
    assert not missing, f"tool defs missing a boolean 'writes' flag: {missing}"


def test_owned_write_and_read_tools_are_classified_correctly():
    writes = {d["name"]: d.get("writes") for d in CRM_TOOL_DEFS}
    for name in _OWNED_WRITE_TOOLS:
        assert writes.get(name) is True, f"{name} must be writes=True"
    for name in _OWNED_READ_TOOLS:
        assert writes.get(name) is False, f"{name} must be writes=False"
