"""Assistant tool registry — writes classification, provider-tool shaping, dispatch.

Hermetic: ToolRegistry() reads the real CRM defs/executors (no DB at construction).
Membership assertions only, so sibling issues adding tools don't break these.
"""

import pytest

from assistant.registry import ToolRegistry

_WRITE_TOOLS = {
    "crm_create_contact", "crm_update_contact", "crm_delete_contact",
    "crm_create_deal", "crm_update_deal", "crm_update_deal_stage",
    "crm_log_activity", "crm_create_task", "crm_complete_task",
}
_READ_TOOLS = {"crm_find_contact", "crm_get_contact", "crm_list_contacts", "crm_dashboard"}


def test_writes_map_matches_owned_tools():
    reg = ToolRegistry()
    for name in _WRITE_TOOLS:
        assert reg.is_write(name) is True, name
    for name in _READ_TOOLS:
        assert reg.is_write(name) is False, name


def test_provider_tools_strip_internal_keys():
    reg = ToolRegistry()
    for t in reg.provider_tools("normal"):
        assert "kind" not in t and "writes" not in t
        assert set(t) <= {"name", "description", "input_schema"}


def test_read_only_mode_hides_write_tools():
    reg = ToolRegistry()
    normal_names = {t["name"] for t in reg.provider_tools("normal")}
    readonly_names = {t["name"] for t in reg.provider_tools("read-only")}
    # every owned write tool is present in normal, absent in read-only
    assert _WRITE_TOOLS <= normal_names
    assert not (_WRITE_TOOLS & readonly_names)
    # reads survive in both
    assert _READ_TOOLS <= readonly_names


def test_execute_tool_sync_unknown_returns_error():
    reg = ToolRegistry()
    assert reg.execute_tool_sync("nope_not_a_tool", {}) == {"error": "Unknown tool: nope_not_a_tool"}


def test_execute_tool_sync_rejects_undeclared_executor_alias():
    """crm_log_note is an executor-only alias with no def — it must NOT be
    executable via the assistant (fail closed on anything without a writes flag)."""
    reg = ToolRegistry()
    assert "crm_log_note" in reg.executors  # the alias exists in the executor map
    assert reg.execute_tool_sync("crm_log_note", {}) == {"error": "Unknown tool: crm_log_note"}


def test_execute_tool_sync_wraps_executor_exceptions():
    reg = ToolRegistry()
    # Override a DECLARED tool's executor so the exception path (not the
    # undeclared-name guard) is exercised.
    reg.executors["crm_create_contact"] = lambda **k: (_ for _ in ()).throw(ValueError("boom"))
    out = reg.execute_tool_sync("crm_create_contact", {})
    assert "error" in out and "crm_create_contact" in out["error"]
    assert "boom" not in out["error"]  # internal detail not leaked


def test_execute_tool_sync_success_passes_args():
    reg = ToolRegistry()
    reg.executors["crm_create_contact"] = lambda **k: {"ok": True, "got": k}
    assert reg.execute_tool_sync("crm_create_contact", {"a": 1}) == {"ok": True, "got": {"a": 1}}


@pytest.mark.asyncio
async def test_execute_tool_offloads_to_thread():
    reg = ToolRegistry()
    reg.executors["crm_create_contact"] = lambda **k: {"ran": True}
    assert await reg.execute_tool("crm_create_contact", {}) == {"ran": True}


# ── Memory tool family (issue #5) merged into the registry ────────────────────

def test_memory_tools_present_and_classified():
    reg = ToolRegistry()
    names = {t["name"] for t in reg.tool_defs}
    assert {"memory_search", "memory_add_fact", "memory_query_facts",
            "memory_invalidate_fact"} <= names
    assert reg.is_write("memory_add_fact") is True
    assert reg.is_write("memory_invalidate_fact") is True
    assert reg.is_write("memory_search") is False
    assert reg.is_write("memory_query_facts") is False


def test_memory_write_tools_hidden_in_read_only():
    reg = ToolRegistry()
    normal = {t["name"] for t in reg.provider_tools("normal")}
    readonly = {t["name"] for t in reg.provider_tools("read-only")}
    assert "memory_add_fact" in normal and "memory_add_fact" not in readonly
    assert "memory_search" in readonly   # reads survive read-only mode


def test_provider_tools_strip_internal_keys_from_memory_defs():
    reg = ToolRegistry()
    mem = [t for t in reg.provider_tools("normal") if t["name"].startswith("memory_")]
    assert mem
    for t in mem:
        assert set(t) <= {"name", "description", "input_schema"}


def test_crm_alias_still_non_executable_after_merge():
    # Merging a second source must not make the def-less crm_log_note alias runnable.
    reg = ToolRegistry()
    assert "crm_log_note" in reg.executors
    assert reg.execute_tool_sync("crm_log_note", {}) == {"error": "Unknown tool: crm_log_note"}


def _fake_source(name, writes=False):
    defs = [{"name": name, "writes": writes, "kind": "memory", "description": "x",
             "input_schema": {"type": "object", "properties": {}}}]
    return defs, {name: (lambda **k: {})}


def test_duplicate_tool_name_across_sources_raises(monkeypatch):
    from assistant import registry as reg_mod
    monkeypatch.setattr(reg_mod, "get_memory_tools", lambda: _fake_source("crm_dashboard"))
    with pytest.raises(ValueError, match="duplicate tool name across sources"):
        reg_mod.ToolRegistry()


def test_non_bool_writes_flag_raises(monkeypatch):
    from assistant import registry as reg_mod
    bad = ([{"name": "memory_bad", "writes": "yes", "kind": "memory", "description": "x",
             "input_schema": {"type": "object", "properties": {}}}], {"memory_bad": (lambda **k: {})})
    monkeypatch.setattr(reg_mod, "get_memory_tools", lambda: bad)
    with pytest.raises(ValueError, match="must carry a boolean 'writes' flag"):
        reg_mod.ToolRegistry()


def test_duplicate_executor_across_sources_raises(monkeypatch):
    from assistant import registry as reg_mod
    # A unique def name but an executor key colliding with an existing CRM executor
    # (the alias crm_log_note) exercises the executor-collision branch specifically.
    defs = [{"name": "memory_probe", "writes": False, "kind": "memory", "description": "x",
             "input_schema": {"type": "object", "properties": {}}}]
    execs = {"memory_probe": (lambda **k: {}), "crm_log_note": (lambda **k: {})}
    monkeypatch.setattr(reg_mod, "get_memory_tools", lambda: (defs, execs))
    with pytest.raises(ValueError, match="duplicate executor across sources"):
        reg_mod.ToolRegistry()
