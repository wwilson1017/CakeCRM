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


def test_execute_tool_sync_wraps_executor_exceptions():
    reg = ToolRegistry()
    reg.executors = {"boom": lambda **k: (_ for _ in ()).throw(RuntimeError("kaboom"))}
    out = reg.execute_tool_sync("boom", {})
    assert out["error"].startswith("Tool error: ") and "kaboom" in out["error"]


def test_execute_tool_sync_success_passes_args():
    reg = ToolRegistry()
    reg.executors = {"echo": lambda **k: {"ok": True, "got": k}}
    assert reg.execute_tool_sync("echo", {"a": 1}) == {"ok": True, "got": {"a": 1}}


@pytest.mark.asyncio
async def test_execute_tool_offloads_to_thread():
    reg = ToolRegistry()
    reg.executors = {"echo": lambda **k: {"ran": True}}
    assert await reg.execute_tool("echo", {}) == {"ran": True}
