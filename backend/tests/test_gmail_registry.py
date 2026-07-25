"""Gmail tools integrated into the assistant ToolRegistry (issue #8).

Asserts the PUBLIC registry surface (is_write / provider_tools / execute_tool_sync)
— stable across any internal composition refactor — with Gmail connected vs not.
A separate file from test_assistant_registry.py so concurrent registry work stays
conflict-free."""

from __future__ import annotations

import gmail.store as gmail_store
from assistant.registry import ToolRegistry

_GMAIL = {"gmail_search", "gmail_read_thread", "gmail_create_draft"}


def test_gmail_tools_present_and_write_flagged_when_connected(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    reg = ToolRegistry()
    names = {d["name"] for d in reg.tool_defs}
    assert _GMAIL <= names
    assert reg.is_write("gmail_create_draft") is True
    assert reg.is_write("gmail_search") is False
    assert reg.is_write("gmail_read_thread") is False


def test_read_only_mode_hides_draft_keeps_reads(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    reg = ToolRegistry()
    ro_names = {t["name"] for t in reg.provider_tools("read-only")}
    assert "gmail_search" in ro_names
    assert "gmail_read_thread" in ro_names
    assert "gmail_create_draft" not in ro_names  # write hidden in read-only
    # Internal bookkeeping keys are stripped from provider-facing defs.
    for t in reg.provider_tools("normal"):
        assert "writes" not in t and "kind" not in t


def test_gmail_tools_absent_when_disconnected(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: False)
    reg = ToolRegistry()
    names = {d["name"] for d in reg.tool_defs}
    assert not (_GMAIL & names)  # model never sees Gmail tools
    assert "gmail_create_draft" not in reg.executors


def test_disconnected_draft_call_fails_closed(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: False)
    reg = ToolRegistry()
    # A stale/replayed tool_use degrades to an error dict, never a raise.
    out = reg.execute_tool_sync("gmail_create_draft", {"to": "x", "subject": "y", "body": "z"})
    assert out == {"error": "Unknown tool: gmail_create_draft"}
