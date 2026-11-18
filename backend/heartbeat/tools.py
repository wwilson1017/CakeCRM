"""Keyless tools for reading and administering the background check-in schedule."""

from collections.abc import Callable

from heartbeat import settings as hb_settings

_ADMIN_ONLY = {"error": "Only an admin can change the background check-in settings."}

HEARTBEAT_TOOL_DEFS = [
    {
        "name": "heartbeat_get_settings", "writes": False, "kind": "integration",
        "description": (
            "Read how your background check-in is set up for this install: whether it is on, "
            "how often it runs, its quiet hours (install-local time, with the timezone), the "
            "minimum gap between its notifications, whether it is paused and until when, and "
            "whether the server operator has switched it off entirely. The check-in is the "
            "scheduled run where you look for newly overdue todos, deals going cold and "
            "contacts nobody has followed up with."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "heartbeat_update_settings", "writes": True, "kind": "integration",
        "description": (
            "Change how your background check-in runs for the whole install. Administrators only. "
            "Pass only the settings to change, and read the current values with heartbeat_get_settings "
            "first. Use it for requests like 'only check once a day', 'stop at night' or 'pause for a week'."
        ),
        "input_schema": {
            "type": "object", "required": [],
            "properties": {
                "enabled": {"type": "boolean", "description":
                    "false switches the check-in off until someone switches it on again; true "
                    "switches it on. A pause is separate: end one with pause_days 0."},
                "interval_minutes": {"type": "integer", "minimum": 30, "maximum": 1440,
                    "description": "Minutes between checks, 30 to 1440. 1440 is once a day."},
                "quiet_hours": {"type": "string", "description":
                    "A daily window with no checks, as HH:MM-HH:MM in 24-hour install-local time, "
                    'for example 22:00-07:00. It may cross midnight. "off" removes it.'},
                "min_gap_hours": {"type": "integer", "minimum": 0, "maximum": 24,
                    "description": "Least number of hours between two check-in notifications, 0 to 24. "
                    "0 means no minimum."},
                "pause_days": {"type": "integer", "minimum": 0, "maximum": 365,
                    "description": "Pause the check-in for this many days from now; it resumes by itself. "
                    "0 ends a pause now."},
            },
        },
    },
]


def _heartbeat_get_settings() -> dict:
    return hb_settings.get_settings()


def _heartbeat_update_settings(**changes) -> dict:
    try:
        return {"ok": True, "settings": hb_settings.update_settings(changes)}
    except ValueError as e:
        return {"error": str(e)}


def get_heartbeat_tools(user: dict | None = None) -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """Never raises or reads the DB at collection. Advertised to all seats, the write
    fails closed unless the seat is admin. Registries are rebuilt per turn, /confirm
    and Telegram approval, so approval after demotion is refused too."""
    is_admin = isinstance(user, dict) and user.get("role") == "admin"
    update = _heartbeat_update_settings if is_admin else (lambda **_changes: dict(_ADMIN_ONLY))
    return HEARTBEAT_TOOL_DEFS, {"heartbeat_get_settings": _heartbeat_get_settings,
                                 "heartbeat_update_settings": update}
