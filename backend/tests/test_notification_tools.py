"""Delivery evidence for heartbeat snapshots, separate from the once-per-run guard."""

from types import SimpleNamespace

import pytest

from notifications import tools


@pytest.mark.parametrize("result,delivered", [
    ({"logged": True, "channels_sent": []}, True),
    ({"logged": False, "channels_sent": ["telegram"]}, True),
    ({"logged": False, "channels_sent": []}, False),
])
def test_notify_user_records_actual_delivery(monkeypatch, result, delivered):
    monkeypatch.setattr(tools.delivery, "deliver_notification", lambda *a: result)
    reg = SimpleNamespace()
    _, executors = tools.get_notification_tools(reg)
    executors["notify_user"](title="New overdue todo", message="Call today")
    assert reg._notify_user_called is True
    assert reg._notify_user_delivered is delivered
