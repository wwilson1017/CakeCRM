"""Reminder recurrence: parse, validate, describe, and compute the next due.

Ported from Chatty's ``reminders/service.compute_next_due`` +
``reminders/tools.parse_recurrence``/``_describe_recurrence`` and consolidated
into ONE module so the service (``compute_next_due``) and the tool/router layers
(``parse_recurrence``/``describe_recurrence``) share it without an import cycle.

A rule is a dict (reminders.recurrence_rule is JSONB → psycopg2 returns it parsed):
    {"type": "daily"}
    {"type": "interval", "hours": h, "minutes": m}   # combined delta must be >= 60s
    {"type": "weekly", "days": [1..7]}               # ISO weekday, Mon=1..Sun=7
    {"type": "monthly", "day": 1..31}                # clamped to month length
    {"type": "cron", "expression": "<5-field cron>"}

All datetimes are timezone-aware UTC. Recurrence math is UTC-only for v1 — a
per-reminder IANA timezone (so "daily at 9am local" survives DST) is future work;
until then a recurring reminder fires at a fixed UTC instant.
"""

import re
from calendar import monthrange
from datetime import datetime, timedelta, timezone

_RECUR_TYPES = {"daily", "interval", "weekly", "monthly", "cron"}
_WEEKDAY_MAP = {
    "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7,
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
    "friday": 5, "saturday": 6, "sunday": 7,
}
_DAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}

# Bound catch-up loops so a pathological rule can never spin forever.
_MAX_CATCHUP = 4000


def _to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_recurrence(raw: str) -> dict | None:
    """Parse a natural-language recurrence string into a structured rule.

    Accepted: 'daily' | 'weekly:mon,wed,fri' (or ISO nums) | 'monthly:15' |
    'every 4 hours' / 'every 30 minutes' | 'cron:0 9 * * MON-FRI'.
    Returns None when ``raw`` is empty (→ one-shot) OR unparseable — the caller
    distinguishes the two (empty string = no recurrence; a non-empty unparseable
    string = user error).
    """
    if not raw or not raw.strip():
        return None
    raw = raw.strip().lower()

    if raw == "daily":
        return {"type": "daily"}

    if raw.startswith("weekly"):
        parts = raw.split(":", 1)
        if len(parts) < 2:
            return {"type": "weekly", "days": [1, 2, 3, 4, 5]}
        days = []
        for d in (s.strip() for s in parts[1].split(",")):
            if d in _WEEKDAY_MAP:
                days.append(_WEEKDAY_MAP[d])
            elif d.isdigit() and 1 <= int(d) <= 7:
                days.append(int(d))
        if not days:
            return None
        return {"type": "weekly", "days": sorted(set(days))}

    if raw.startswith("monthly"):
        parts = raw.split(":", 1)
        if len(parts) < 2:
            return {"type": "monthly", "day": 1}
        try:
            return {"type": "monthly", "day": max(1, min(31, int(parts[1].strip())))}
        except ValueError:
            return None

    m = re.match(r"every\s+(\d+)\s+(hours?|minutes?|mins?)$", raw)
    if m:
        n = int(m.group(1))
        if n < 1:
            return None
        if m.group(2).startswith("hour"):
            return {"type": "interval", "hours": n}
        return {"type": "interval", "minutes": n}

    if raw.startswith("cron:"):
        expression = raw[5:].strip()
        if expression:
            try:
                from croniter import croniter
                if croniter.is_valid(expression):
                    return {"type": "cron", "expression": expression}
            except Exception:
                pass
        return None

    return None


def validate_rule(rule: dict | None) -> str | None:
    """Return an error string if ``rule`` is structurally invalid, else None.

    ``None`` (one-shot) is valid. Called by the service on create/update so a
    malformed rule never reaches the DB.
    """
    if rule is None:
        return None
    if not isinstance(rule, dict):
        return "recurrence rule must be an object"
    rtype = rule.get("type")
    if rtype not in _RECUR_TYPES:
        return f"unknown recurrence type: {rtype!r}"
    if rtype == "interval":
        try:
            hours = int(rule.get("hours", 0) or 0)
            minutes = int(rule.get("minutes", 0) or 0)
        except (TypeError, ValueError):
            return "interval hours/minutes must be numbers"
        if timedelta(hours=hours, minutes=minutes).total_seconds() < 60:
            return "interval must be at least 60 seconds"
    elif rtype == "weekly":
        days = rule.get("days")
        if not isinstance(days, list) or not days or not all(
            isinstance(d, int) and 1 <= d <= 7 for d in days
        ):
            return "weekly rule needs a non-empty list of ISO weekdays (1-7)"
    elif rtype == "monthly":
        day = rule.get("day")
        if not isinstance(day, int) or not (1 <= day <= 31):
            return "monthly rule needs a day between 1 and 31"
    elif rtype == "cron":
        expr = rule.get("expression")
        if not expr:
            return "cron rule needs an expression"
        try:
            from croniter import croniter
            if not croniter.is_valid(expr):
                return f"invalid cron expression: {expr!r}"
        except ImportError:
            return "cron recurrence unavailable (croniter not installed)"
    return None


def describe_recurrence(rule: dict | None) -> str | None:
    """Human-readable description of a recurrence rule (dict), or None."""
    if not rule or not isinstance(rule, dict):
        return None
    rtype = rule.get("type", "")
    if rtype == "daily":
        return "Daily"
    if rtype == "weekly":
        names = [_DAY_NAMES.get(d, str(d)) for d in sorted(rule.get("days", []))]
        return f"Weekly: {', '.join(names)}" if names else "Weekly"
    if rtype == "monthly":
        return f"Monthly: day {rule.get('day', '?')}"
    if rtype == "interval":
        hours = rule.get("hours", 0) or 0
        minutes = rule.get("minutes", 0) or 0
        if hours and not minutes:
            return f"Every {hours}h"
        if minutes and not hours:
            return f"Every {minutes}m"
        return f"Every {hours}h {minutes}m"
    if rtype == "cron":
        return f"Cron: {rule.get('expression', '?')}"
    return None


def compute_next_due(current_due: datetime, rule: dict,
                     *, now: datetime | None = None) -> datetime | None:
    """Next due instant strictly after ``now`` given the fired reminder's due + rule.

    Returns a timezone-aware UTC datetime, or None (invalid/unsupported rule).
    Catches up past missed occurrences after downtime; intervals use arithmetic
    (not one-tick-at-a-time), other types use bounded loops.
    """
    if not isinstance(rule, dict):
        return None
    dt = _to_utc(current_due)
    now = _to_utc(now) if now else datetime.now(timezone.utc)
    rtype = rule.get("type")

    if rtype == "daily":
        dt += timedelta(days=1)
        if dt <= now:
            # Jump forward in bulk to just after now (O(1)) instead of one day at a
            # time — a reminder stale by years must NOT iterate itself into None.
            dt += timedelta(days=(now - dt).days + 1)
        while dt <= now:  # boundary guard (fractional day)
            dt += timedelta(days=1)
        return dt

    if rtype == "interval":
        try:
            delta = timedelta(hours=int(rule.get("hours", 0) or 0),
                              minutes=int(rule.get("minutes", 0) or 0))
        except (TypeError, ValueError):
            return None
        secs = delta.total_seconds()
        if secs < 60:
            return None
        # Arithmetic catch-up: jump straight to the first multiple after now.
        elapsed = (now - dt).total_seconds()
        n = int(elapsed // secs) + 1 if elapsed >= 0 else 1
        dt = dt + timedelta(seconds=secs * n)
        if dt <= now:
            dt += delta
        return dt

    if rtype == "weekly":
        days = set(rule.get("days", []))
        if not days:
            return None
        dt += timedelta(days=1)
        if dt <= now:
            # Jump to now's date (preserving time-of-day) so catch-up is O(1) — a
            # weekly reminder stale by weeks/years must not scan itself into None.
            dt = dt.replace(year=now.year, month=now.month, day=now.day)
        for _ in range(8):   # ≤7 days to reach any matching weekday, +1 boundary
            if dt > now and dt.isoweekday() in days:
                return dt
            dt += timedelta(days=1)
        return None

    if rtype == "monthly":
        target_day = rule.get("day", dt.day)
        for _ in range(_MAX_CATCHUP):
            month, year = dt.month + 1, dt.year
            if month > 12:
                month, year = 1, year + 1
            clamped = min(target_day, monthrange(year, month)[1])
            dt = dt.replace(year=year, month=month, day=clamped)
            if dt > now:
                return dt
        return None

    if rtype == "cron":
        expression = rule.get("expression")
        if not expression:
            return None
        try:
            from croniter import croniter
            # Seed at the later of (current due, now) so get_next() lands on the
            # next scheduled time strictly after now in ONE step — no per-occurrence
            # iteration (a per-minute cron would otherwise exhaust the loop after a
            # few days of downtime and silently return None).
            base = dt if dt > now else now
            nxt = croniter(expression, base).get_next(datetime)
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=timezone.utc)
            return nxt
        except Exception:
            return None

    return None
