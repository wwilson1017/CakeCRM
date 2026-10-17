"""The /mcp tool surface (#322): what a connected AI tool may see and call.

The golden file pins the exact advertised list, with every hint, in both todo modes. A
new or reclassified Baker tool therefore shows up as a diff a person reviews, instead of
quietly appearing on someone's ChatGPT. The guards below hold the hard lines structurally:
no Gmail tool for any seat, no hard contact delete, no write to Baker's memory or
context files, and no write at all on a read-only connection.
"""

import json
import os
from pathlib import Path

import pytest
from conftest import fake_admin, fake_member

from assistant.registry import ToolRegistry
from crm import gtd_tools, provenance_service
from gmail import tools as gmail_tools
from mcp_endpoint import surface

GOLDEN = Path(__file__).parent / "golden" / "mcp_surface.json"
REGENERATE = "cd backend && MCP_SURFACE_REGENERATE=1 python -m pytest -q tests/test_mcp_surface.py"
NEVER = {
    "gmail_search", "gmail_read_thread", "gmail_create_draft", "notify_user",
    "crm_delete_contact", "memory_add_fact", "memory_invalidate_fact",
    "write_context_file", "delete_context_file", "append_daily_note",
}


def _registry(monkeypatch, mode: str, user=None) -> ToolRegistry:
    from crm import tools
    monkeypatch.setattr(gtd_tools.service, "get_todo_mode", lambda: mode)
    monkeypatch.setattr(tools.crm, "get_todo_mode", lambda: mode)
    # Gmail connected and shared, so the Gmail tools are REALLY in the registry and the
    # exclusion is what keeps them off the surface.
    monkeypatch.setattr(gmail_tools.store, "is_connected", lambda: True)
    monkeypatch.setattr(gmail_tools.store, "sharing_enabled", lambda: True)
    return ToolRegistry(user=user or fake_admin())


def _snapshot(registry) -> list[dict]:
    out = []
    for tool in surface.advertised(registry, read_only=False):
        a = tool.annotations
        out.append({
            "name": tool.name,
            "title": tool.title,
            "readOnlyHint": a.read_only_hint,
            "destructiveHint": a.destructive_hint,
            "openWorldHint": a.open_world_hint,
        })
    return sorted(out, key=lambda t: t["name"])


def _both_modes(monkeypatch) -> dict:
    return {mode: _snapshot(_registry(monkeypatch, mode)) for mode in ("gtd", "normal")}


def test_golden_surface_matches_in_both_todo_modes(monkeypatch):
    live = _both_modes(monkeypatch)
    if os.environ.get("MCP_SURFACE_REGENERATE") == "1":
        # Rewrites the pin from the live registry. Review the diff before committing.
        GOLDEN.write_text("{\n" + ",\n".join(
            f' "{mode}": [\n' + ",\n".join(f"  {json.dumps(t)}" for t in tools) + "\n ]"
            for mode, tools in live.items()
        ) + "\n}\n")
    assert live == json.loads(GOLDEN.read_text()), (
        "The tools a connected AI tool sees changed. If that is intended, regenerate with\n"
        f"  {REGENERATE}\nand review the diff: every new tool reaches people's ChatGPT/Claude."
    )


@pytest.mark.parametrize("mode", ["gtd", "normal"])
def test_the_surface_reached_the_real_registry(monkeypatch, mode):
    registry = _registry(monkeypatch, mode)
    names = {t.name for t in surface.advertised(registry, read_only=False)}
    assert len(names) >= 50
    assert {"crm_find_contact", "reminder_create", "help_search", "read_context_file"} <= names
    assert ("todo_create" if mode == "gtd" else "crm_create_todo") in names


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("user", [fake_admin, fake_member])
def test_nothing_on_the_never_list_is_advertised_or_offered(monkeypatch, user, read_only):
    registry = _registry(monkeypatch, "gtd", user=user())
    present = NEVER & registry.writes_map.keys()
    # The registry must really carry them (Gmail shared with all seats, so a member too),
    # or the assertions below would pass without testing anything.
    assert present == NEVER - {"notify_user"}
    advertised = {t.name for t in surface.advertised(registry, read_only=read_only)}
    assert not (advertised & NEVER)
    assert not (surface.offered_names(registry, read_only=read_only) & NEVER)


def test_no_memory_or_context_file_write_crosses_by_kind(monkeypatch):
    registry = _registry(monkeypatch, "gtd")
    mind_writes = {d["name"] for d in registry.tool_defs
                   if d["kind"] in ("memory", "context") and d["writes"]}
    mind_reads = {d["name"] for d in registry.tool_defs
                  if d["kind"] in ("memory", "context") and not d["writes"]}
    assert mind_writes and mind_reads
    offered = surface.offered_names(registry, read_only=False)
    assert not (offered & mind_writes)
    assert mind_reads <= offered


def test_the_exclusion_flags_synthetic_defs_and_passes_a_crm_read():
    assert surface.is_excluded({"name": "gmail_anything", "kind": "gmail", "writes": False})
    assert surface.is_excluded({"name": "x", "kind": "notification", "writes": True})
    assert surface.is_excluded({"name": "x", "kind": "memory", "writes": True})
    assert surface.is_excluded({"name": "x", "kind": "context", "writes": True})
    assert surface.is_excluded({"name": "crm_delete_contact", "kind": "integration", "writes": True})
    assert not surface.is_excluded({"name": "crm_find_contact", "kind": "integration", "writes": False})
    assert not surface.is_excluded({"name": "read_context_file", "kind": "context", "writes": False})


@pytest.mark.parametrize("mode", ["gtd", "normal"])
def test_a_read_only_connection_sees_and_may_call_only_reads(monkeypatch, mode):
    registry = _registry(monkeypatch, mode)
    tools = surface.advertised(registry, read_only=True)
    assert tools and all(t.annotations.read_only_hint for t in tools)
    assert all(not registry.is_write(n) for n in surface.offered_names(registry, read_only=True))


@pytest.mark.parametrize("mode", ["gtd", "normal"])
def test_hints_follow_the_tier_rules(monkeypatch, mode):
    from assistant.confirm_tier import HIDE_CAPABLE_TOOLS

    registry = _registry(monkeypatch, mode)
    for tool in surface.advertised(registry, read_only=False):
        a = tool.annotations
        assert tool.title and a.title == tool.title
        assert a.open_world_hint is False
        writes = registry.is_write(tool.name)
        assert a.read_only_hint is (not writes)
        routine = registry.is_routine_write(tool.name) and tool.name not in HIDE_CAPABLE_TOOLS
        assert a.destructive_hint is (writes and not routine), tool.name


def test_titles_read_as_words():
    assert surface.title_for("crm_find_contact") == "Find contact"
    assert surface.title_for("todo_create") == "Todo create"
    assert surface.title_for("list_context_files") == "List context files"


def test_instructions_explain_both_fences():
    assert 'source="public_capture"' in surface.INSTRUCTIONS
    assert "<recorded_context" in surface.INSTRUCTIONS


def test_a_todo_made_through_mcp_is_stamped_mcp(monkeypatch, todo_mode):
    todo_mode("gtd")
    seen = []
    monkeypatch.setitem(gtd_tools.GTD_TOOL_EXECUTORS, "todo_create",
                        lambda **kw: seen.append(kw["source"]) or {"ok": True})
    token = provenance_service.WRITE_SOURCE.set(("mcp", "Claude"))
    try:
        _, executors = gtd_tools.get_gtd_tools(user=fake_member())
    finally:
        provenance_service.WRITE_SOURCE.reset(token)
    executors["todo_create"](title="x")
    _, baker = gtd_tools.get_gtd_tools(user=fake_member())
    baker["todo_create"](title="y")
    assert seen == ["mcp", "agent"]


def test_custom_field_attribution_names_the_connection():
    assert provenance_service.actor_label() == "assistant"
    token = provenance_service.WRITE_SOURCE.set(("mcp", "Claude Code"))
    try:
        assert provenance_service.actor_label() == "via Claude Code"
        assert provenance_service.current_source() == ("mcp", "Claude Code")
    finally:
        provenance_service.WRITE_SOURCE.reset(token)
