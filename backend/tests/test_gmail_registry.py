"""Gmail tools integrated into the assistant ToolRegistry (issue #8).

Asserts the PUBLIC registry surface (is_write / provider_tools / execute_tool_sync)
— stable across any internal composition refactor — with Gmail connected vs not.
A separate file from test_assistant_registry.py so concurrent registry work stays
conflict-free.

Since #194 the tools are seat-gated too, so a registry that expects them must be built
for an ADMIN seat; the seat gate's own truth table lives in test_gmail_tools.py.
"""

from __future__ import annotations

from conftest import fake_admin, fake_member

import gmail.store as gmail_store
from assistant.registry import ToolRegistry

_GMAIL = {"gmail_search", "gmail_read_thread", "gmail_create_draft"}


def test_gmail_tools_present_and_write_flagged_when_connected(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    reg = ToolRegistry(user=fake_admin())
    names = {d["name"] for d in reg.tool_defs}
    assert _GMAIL <= names
    assert reg.is_write("gmail_create_draft") is True
    assert reg.is_write("gmail_search") is False
    assert reg.is_write("gmail_read_thread") is False


def test_read_only_mode_hides_draft_keeps_reads(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    reg = ToolRegistry(user=fake_admin())
    ro_names = {t["name"] for t in reg.provider_tools("read-only")}
    assert "gmail_search" in ro_names
    assert "gmail_read_thread" in ro_names
    assert "gmail_create_draft" not in ro_names  # write hidden in read-only
    # Internal bookkeeping keys are stripped from provider-facing defs.
    for t in reg.provider_tools("normal"):
        assert "writes" not in t and "kind" not in t


def test_gmail_tools_absent_when_disconnected(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: False)
    reg = ToolRegistry(user=fake_admin())
    names = {d["name"] for d in reg.tool_defs}
    assert not (_GMAIL & names)  # model never sees Gmail tools
    assert "gmail_create_draft" not in reg.executors


def test_disconnected_draft_call_fails_closed(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: False)
    reg = ToolRegistry(user=fake_admin())
    # A stale/replayed tool_use degrades to an error dict, never a raise.
    out = reg.execute_tool_sync("gmail_create_draft", {"to": "x", "subject": "y", "body": "z"})
    assert out == {"error": "Unknown tool: gmail_create_draft"}


# ── the seat gate reaches the registry surface (issue #194) ───────────────────

def test_member_registry_carries_no_gmail_tools_when_not_shared(monkeypatch):
    """The whole point of #194: a member's registry never advertises the admin's mailbox,
    so the model cannot even name those tools on that seat."""
    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    monkeypatch.setattr(gmail_store, "sharing_enabled", lambda: False)
    reg = ToolRegistry(user=fake_member())
    assert not (_GMAIL & {d["name"] for d in reg.tool_defs})
    assert not (_GMAIL & set(reg.executors))


def test_member_registry_carries_gmail_tools_when_the_install_shares(monkeypatch):
    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    monkeypatch.setattr(gmail_store, "sharing_enabled", lambda: True)
    reg = ToolRegistry(user=fake_member())
    assert _GMAIL <= {d["name"] for d in reg.tool_defs}
    assert reg.is_write("gmail_create_draft") is True


def test_a_members_stale_draft_call_fails_closed(monkeypatch):
    """A draft proposed while sharing was on, approved after it was turned off: the
    confirmation path rebuilds the registry for the same seat, so the executor is simply
    gone and dispatch hits the existing unknown-tool path. No new authorization layer."""
    monkeypatch.setattr(gmail_store, "is_connected", lambda: True)
    monkeypatch.setattr(gmail_store, "sharing_enabled", lambda: False)
    reg = ToolRegistry(user=fake_member())
    out = reg.execute_tool_sync("gmail_create_draft", {"to": "x", "subject": "y", "body": "z"})
    assert out == {"error": "Unknown tool: gmail_create_draft"}
