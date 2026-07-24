"""CRM agent-tools contract: unconditional, complete, well-formed.

The issue requires the crm_* tools to be collected unconditionally (no enable
gate). This pins that: 19 schema defs, 20 executors (incl. the crm_log_note
back-compat alias), every def has an executor, and get_crm_tools() returns the
full set with no gating.
"""

import inspect

from crm import tools
from crm.tools import CRM_TOOL_DEFS, TOOL_EXECUTORS, get_crm_tools


def test_nineteen_defs_twenty_executors():
    assert len(CRM_TOOL_DEFS) == 19
    assert len(TOOL_EXECUTORS) == 20


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


def test_chatter_tools_present_and_shaped():
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    assert {"crm_add_note", "crm_get_chatter"} <= set(by_name)
    for name in ("crm_add_note", "crm_get_chatter"):
        props = by_name[name]["input_schema"]["properties"]
        assert props["entity_type"]["enum"] == ["deal", "contact"]
    assert by_name["crm_add_note"]["input_schema"]["required"] == ["entity_type", "entity_id", "message"]


def test_chatter_executors_wrap_validation_errors():
    # A bad entity_type is rejected in the service before any DB call; the tool
    # surfaces it as {"error": ...} rather than raising.
    assert "error" in tools.crm_add_note("company", 1, "hi")
    assert "error" in tools.crm_get_chatter("company", 1)
