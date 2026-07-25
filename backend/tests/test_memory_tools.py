"""memory/tools.py — the memory tool family contract.

Parallels tests/test_crm_tools.py: unconditional collection, every def self-classifies
as read/write (the confirmation-gate linchpin), executors 1:1 with defs, and the
provider-facing ``object`` kwarg matches the schema property (the registry calls
``fn(**args)``, so a rename would TypeError every add_fact call).
"""

import inspect

from memory import service, tools
from memory.tools import MEMORY_TOOL_DEFS, MEMORY_TOOL_EXECUTORS, get_memory_tools
from memory.types import MEMORY_TYPES

_WRITE_TOOLS = {"memory_add_fact", "memory_invalidate_fact"}
_READ_TOOLS = {"memory_search", "memory_query_facts"}


def test_four_defs_four_executors():
    assert len(MEMORY_TOOL_DEFS) == 4
    assert len(MEMORY_TOOL_EXECUTORS) == 4


def test_def_names_unique_prefixed_kind_and_schema_shaped():
    names = [d["name"] for d in MEMORY_TOOL_DEFS]
    assert len(names) == len(set(names))
    for d in MEMORY_TOOL_DEFS:
        assert d["name"].startswith("memory_"), d["name"]
        assert d["kind"] == "memory"
        assert isinstance(d.get("description"), str) and d["description"]
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)


def test_every_def_declares_a_boolean_writes_flag():
    missing = [d["name"] for d in MEMORY_TOOL_DEFS if not isinstance(d.get("writes"), bool)]
    assert not missing, f"memory tool defs missing a boolean 'writes' flag: {missing}"


def test_writes_classification():
    writes = {d["name"]: d["writes"] for d in MEMORY_TOOL_DEFS}
    for name in _WRITE_TOOLS:
        assert writes[name] is True, name
    for name in _READ_TOOLS:
        assert writes[name] is False, name


def test_executors_match_defs_one_to_one_no_aliases():
    def_names = {d["name"] for d in MEMORY_TOOL_DEFS}
    assert set(MEMORY_TOOL_EXECUTORS) == def_names
    for name in def_names:
        assert callable(MEMORY_TOOL_EXECUTORS[name])


def test_get_memory_tools_unconditional_no_gate():
    defs, execs = get_memory_tools()
    assert defs is MEMORY_TOOL_DEFS and execs is MEMORY_TOOL_EXECUTORS
    sig = inspect.signature(get_memory_tools)
    assert not [p for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)]
    assert "is_enabled" not in inspect.getsource(tools)


def test_add_fact_executor_uses_object_kwarg():
    # The registry calls fn(**args); the schema property is 'object', so the executor
    # parameter must literally be 'object' (forwarded to service.add_fact as object_).
    params = inspect.signature(tools.memory_add_fact).parameters
    assert "object" in params
    assert MEMORY_TOOL_DEFS[1]["input_schema"]["properties"].get("object")  # def agrees


def test_add_fact_executor_forwards_to_service(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "add_fact",
                        lambda **kw: captured.update(kw) or {"ok": True})
    tools.memory_add_fact(subject="Dana", predicate="likes", object="tea")
    assert captured["object_"] == "tea"      # forwarded under the service's name
    assert captured["subject"] == "Dana"


def test_search_executor_shapes_result(monkeypatch):
    monkeypatch.setattr(service, "search_facts", lambda *a, **k: [{"id": 1}, {"id": 2}])
    assert tools.memory_search("acme") == {"query": "acme", "results": [{"id": 1}, {"id": 2}], "total": 2}


def test_query_facts_executor_shapes_result(monkeypatch):
    monkeypatch.setattr(service, "query_facts", lambda **k: [{"id": 1}])
    assert tools.memory_query_facts() == {"facts": [{"id": 1}], "total": 1}


def test_add_fact_schema_exposes_type_enum_and_valid_from():
    props = {d["name"]: d for d in MEMORY_TOOL_DEFS}["memory_add_fact"]["input_schema"]["properties"]
    assert set(props["memory_type"]["enum"]) == set(MEMORY_TYPES)
    assert "valid_from" in props


def test_add_fact_executor_forwards_valid_from(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "add_fact", lambda **kw: captured.update(kw) or {"ok": True})
    tools.memory_add_fact(subject="D", predicate="p", object="o", valid_from="2026-01-01")
    assert captured["valid_from"] == "2026-01-01"
