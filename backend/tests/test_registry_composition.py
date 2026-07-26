"""ToolRegistry composition + collision checks + background gating (issue #6).

Hermetic: ToolRegistry reads the real CRM + reminder defs (no DB at construction).
"""

import pytest

from assistant import registry as registry_mod
from assistant.registry import ToolRegistry


def test_interactive_registry_has_crm_and_reminder_tools_not_notify():
    reg = ToolRegistry()
    names = {t["name"] for t in reg.tool_defs}
    assert "crm_create_contact" in names          # crm source
    assert {"create_reminder", "list_reminders", "cancel_reminder"} <= names  # reminder source
    assert "notify_user" not in names             # background-only


def test_background_registry_adds_notify_user():
    reg = ToolRegistry(background=True)
    names = {t["name"] for t in reg.tool_defs}
    assert "notify_user" in names
    assert reg.is_write("notify_user") is True


def test_every_composed_def_carries_bool_writes():
    for reg in (ToolRegistry(), ToolRegistry(background=True)):
        for t in reg.tool_defs:
            assert isinstance(t.get("writes"), bool), t["name"]


def test_provider_tools_allow_filters_to_allowlist():
    reg = ToolRegistry(background=True)
    allow = {"crm_dashboard", "notify_user"}
    names = {t["name"] for t in reg.provider_tools("power", allow=allow)}
    assert names == allow


def test_provider_tools_no_allow_returns_all_non_internal():
    reg = ToolRegistry()
    for t in reg.provider_tools("normal"):
        assert "writes" not in t and "kind" not in t


def test_notify_user_one_per_run_guard(monkeypatch):
    # deliver_notification is mocked so no DB is touched.
    from notifications import tools as notif_tools
    monkeypatch.setattr(notif_tools.delivery, "deliver_notification",
                        lambda title, message: {"notification_id": "n1", "channels_sent": ["web_push"]})
    reg = ToolRegistry(background=True)
    first = reg.executors["notify_user"](title="T", message="M")
    assert first["ok"] is True
    second = reg.executors["notify_user"](title="T2", message="M2")
    assert "already called" in second["error"]


def test_duplicate_tool_name_across_sources_raises(monkeypatch):
    # A source that redefines an existing crm tool name must fail construction.
    def bad_source():
        return ([{"name": "crm_dashboard", "writes": False, "description": "dup",
                  "input_schema": {"type": "object", "properties": {}}, "kind": "reminder"}],
                {"crm_dashboard": lambda: {}})
    monkeypatch.setattr(registry_mod, "get_reminder_tools", bad_source)
    with pytest.raises(ValueError, match="duplicate tool name"):
        ToolRegistry()


def test_non_bool_writes_raises(monkeypatch):
    def bad_source():
        return ([{"name": "reminder_x", "writes": "yes", "description": "d",
                  "input_schema": {"type": "object", "properties": {}}, "kind": "reminder"}],
                {"reminder_x": lambda: {}})
    monkeypatch.setattr(registry_mod, "get_reminder_tools", bad_source)
    with pytest.raises(ValueError, match="boolean 'writes'"):
        ToolRegistry()


def test_def_without_executor_raises(monkeypatch):
    def bad_source():
        return ([{"name": "reminder_y", "writes": False, "description": "d",
                  "input_schema": {"type": "object", "properties": {}}, "kind": "reminder"}],
                {})  # no executor for reminder_y
    monkeypatch.setattr(registry_mod, "get_reminder_tools", bad_source)
    with pytest.raises(ValueError, match="no executor"):
        ToolRegistry()
