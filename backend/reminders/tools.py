"""Reminder agent tools — exposed to the assistant via the ToolRegistry.

``get_reminder_tools()`` returns ``(defs, executors)`` in the same shape as
``crm.tools.get_crm_tools()`` (every def carries a boolean ``writes`` flag — the
confirmation-gate source of truth). Registered on EVERY registry (interactive and
background), so the chat assistant can set/list/cancel reminders. Recurrence is
given as a natural-language string and parsed via ``reminders.recurrence``.
"""

from collections.abc import Callable

from reminders import recurrence as recurrence_rules, service

REMINDER_TOOL_DEFS: list[dict] = [
    {
        "name": "create_reminder",
        "writes": True,
        "description": (
            "Set a reminder that fires at a specific time and notifies the user. "
            "Use for follow-ups and time-based nudges. due_at is an ISO 8601 datetime. "
            "For a repeating reminder, pass recurrence like 'daily', 'weekly:mon,wed,fri', "
            "'monthly:15', 'every 4 hours', or 'cron:0 9 * * MON-FRI'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to remind about"},
                "due_at": {"type": "string", "description": "When to fire — ISO 8601 datetime (e.g. 2026-07-25T09:00:00Z)"},
                "context": {"type": "string", "description": "Optional extra detail the assistant should have when the reminder fires"},
                "recurrence": {"type": "string", "description": "Optional repeat rule: 'daily' | 'weekly:mon,wed' | 'monthly:15' | 'every N hours' | 'cron:<expr>'"},
            },
            "required": ["message", "due_at"],
        },
        "kind": "reminder",
    },
    {
        "name": "list_reminders",
        "writes": False,
        "description": "List reminders. status: 'pending' (default), 'fired', 'cancelled', or 'all'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "pending | fired | cancelled | all"},
            },
            "required": [],
        },
        "kind": "reminder",
    },
    {
        "name": "cancel_reminder",
        "writes": True,
        "description": "Cancel a pending reminder by id (stops the whole series if it recurs).",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "The reminder's id"},
            },
            "required": ["reminder_id"],
        },
        "kind": "reminder",
    },
]


def _create_reminder(message: str = "", due_at: str = "", context: str = "",
                    recurrence: str = "") -> dict:
    rule = None
    if recurrence and recurrence.strip():
        rule = recurrence_rules.parse_recurrence(recurrence)
        if rule is None:
            return {"error": f"Could not understand the recurrence '{recurrence}'. "
                             "Try 'daily', 'weekly:mon,wed', 'monthly:15', 'every 4 hours', or 'cron:<expr>'."}
    return service.create_reminder(message, due_at, context or None, rule)


def _list_reminders(status: str = "pending") -> dict:
    return {"reminders": service.list_reminders(status=status or "pending")}


def _cancel_reminder(reminder_id: str = "") -> dict:
    if not reminder_id:
        return {"error": "reminder_id is required"}
    return service.cancel_reminder(reminder_id)


def get_reminder_tools() -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """Return (tool_defs, executors) for the reminder tools."""
    executors: dict[str, Callable[..., dict]] = {
        "create_reminder": _create_reminder,
        "list_reminders": _list_reminders,
        "cancel_reminder": _cancel_reminder,
    }
    return REMINDER_TOOL_DEFS, executors
