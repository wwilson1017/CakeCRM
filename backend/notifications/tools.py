"""The ``notify_user`` agent tool (issue #6).

Added ONLY to background registries (``ToolRegistry(background=True)``) — Chatty
gates it behind ``background_mode`` because it's meaningful only for autonomous
turns (heartbeat / reminder firing), where the assistant proactively alerts the
user. The executor is a closure over the registry so the "one notification per
run" guard hangs on the per-run registry instance (a fresh registry is built for
each background turn).
"""

import logging
from collections.abc import Callable

from notifications import delivery

logger = logging.getLogger(__name__)

NOTIFY_USER_DEF = {
    "name": "notify_user",
    "writes": True,   # an externally-visible action; counts against the write budget
    "description": (
        "Send a notification to the user. Use when you have important findings, a "
        "completed action, or time-sensitive information genuinely worth alerting "
        "them about. It appears in their notification log and pushes to their "
        "devices. Limited to ONE notification per background run."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short headline (e.g. 'Overdue tasks')"},
            "message": {"type": "string", "description": "The notification body with details"},
        },
        "required": ["title", "message"],
    },
    "kind": "notification",
}


def get_notification_tools(registry) -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """Return ([notify_user def], {notify_user: executor}) bound to ``registry``."""

    def _notify_user(title: str = "", message: str = "") -> dict:
        if not title or not message:
            return {"error": "Both title and message are required"}
        if getattr(registry, "_notify_user_called", False):
            return {"error": "notify_user already called this run — only one per run."}
        registry._notify_user_called = True
        result = delivery.deliver_notification(title, message)
        channels = result.get("channels_sent", [])
        return {
            "ok": True,
            "notification_id": result.get("notification_id"),
            "channels_sent": channels,
            "message": f"Notification sent via {', '.join(channels) if channels else 'notification log only'}.",
        }

    return [NOTIFY_USER_DEF], {"notify_user": _notify_user}
