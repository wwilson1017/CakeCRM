"""Install-wide background check-in preferences; shared by REST and Baker tools."""

import re

from core import localtime
from core.config import settings
from core.postgres import pg_execute, pg_fetchone

_QUIET_RE = re.compile(r"^((?:[01]\d|2[0-3]):[0-5]\d)-((?:[01]\d|2[0-3]):[0-5]\d)$")
FIELDS = ("enabled", "interval_minutes", "quiet_hours", "min_gap_hours", "pause_days")


def get_settings() -> dict:
    row = pg_fetchone(
        "SELECT turn_enabled, COALESCE(interval_minutes, %s) AS interval_minutes, quiet_start, "
        "quiet_end, notify_min_gap_hours, CASE WHEN paused_until > now() THEN paused_until END "
        "AS paused_until FROM heartbeat_state WHERE id = 1",
        (settings.heartbeat_interval_minutes,),
    ) or {}
    start, end = row.get("quiet_start"), row.get("quiet_end")
    return {
        "enabled": row.get("turn_enabled", True),
        "interval_minutes": row.get("interval_minutes", settings.heartbeat_interval_minutes),
        "quiet_hours": f"{start}-{end}" if start and end else "off",
        "min_gap_hours": row.get("notify_min_gap_hours", 4),
        "paused_until": row.get("paused_until"),
        "timezone": localtime.tz().key,
        "operator_enabled": settings.heartbeat_enabled,
    }


def update_settings(changes: dict) -> dict:
    if not changes:
        raise ValueError("Nothing to change.")
    fragments, params = [], []
    for key, value in changes.items():
        if key not in FIELDS:
            raise ValueError(f"Unknown setting: {key}")
        if key == "enabled":
            if not isinstance(value, bool):
                raise ValueError("enabled must be true or false.")
            fragments.append("turn_enabled = %s")
            params.append(value)
        elif key == "interval_minutes":
            if type(value) is not int or not 30 <= value <= 1440:
                raise ValueError("interval_minutes must be a whole number from 30 to 1440 (1440 is once a day).")
            fragments.append("interval_minutes = %s")
            params.append(value)
        elif key == "quiet_hours":
            match = _QUIET_RE.fullmatch(value) if isinstance(value, str) else None
            if isinstance(value, str) and value.strip().lower() == "off":
                start, end = None, None
            elif match and match[1] != match[2]:
                start, end = match.groups()
            else:
                raise ValueError('quiet_hours must look like 22:00-07:00 (24-hour, start and end different), or "off".')
            fragments.extend(("quiet_start = %s", "quiet_end = %s"))
            params.extend((start, end))
        elif key == "min_gap_hours":
            if type(value) is not int or not 0 <= value <= 24:
                raise ValueError("min_gap_hours must be a whole number from 0 to 24.")
            fragments.append("notify_min_gap_hours = %s")
            params.append(value)
        elif key == "pause_days":
            if type(value) is not int or not 0 <= value <= 365:
                raise ValueError("pause_days must be a whole number from 0 to 365.")
            fragments.append("paused_until = CASE WHEN %s > 0 THEN now() + make_interval(days => %s) END")
            params.extend((value, value))
    pg_execute("UPDATE heartbeat_state SET " + ", ".join(fragments) + " WHERE id = 1", params)
    return get_settings()
