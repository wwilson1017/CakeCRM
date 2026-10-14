"""Reminder schedule maths (issue #283) — pure: no I/O, no clock.

Ported in spirit from cake_os's reminder recurrence (56e19637b): every "next" is computed
from an explicit ``after`` instant and an explicit zone, so tests pin both, and a 09:00
reminder stays at 09:00 wall-clock across a DST change.

The repeat set is the issue's: daily, weekly, monthly, yearly, anchored on the series'
FIRST date. The n-th occurrence is always computed from that anchor, never from the
previous fire, so a reminder on the 31st lands on Feb 28 and comes back to Mar 31
(and a Feb 29 anchor lands on Feb 28 in common years) — clamping cannot drift.

DST: a wall time that does not exist on the spring-forward day (02:30) is the shifted
instant (03:30 local) — zoneinfo's ``fold=0`` reading; one that happens twice on the
fall-back day fires once, on the first pass.
"""

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from crm.gtd_common import ValidationError

REPEATS = ("daily", "weekly", "monthly", "yearly")

# simplification: a date-only reminder fires at 09:00 install-local; a per-install
# setting is the upgrade path if a team wants another morning hour.
DEFAULT_FIRE_TIME = time(9, 0)


def _add_months(anchor: date, months: int) -> date:
    y, m = divmod(anchor.month - 1 + months, 12)
    year, month = anchor.year + y, m + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def occurrence(repeat: str, anchor: date, n: int) -> date:
    """The n-th date of a series (n=0 is the anchor), always from the anchor."""
    if repeat == "daily":
        return anchor + timedelta(days=n)
    if repeat == "weekly":
        return anchor + timedelta(weeks=n)
    if repeat == "monthly":
        return _add_months(anchor, n)
    if repeat == "yearly":
        return _add_months(anchor, 12 * n)
    raise ValueError(f"unknown repeat {repeat!r}")


def due_instant(due_date: date, due_time: time | None, zone: ZoneInfo) -> datetime:
    """The UTC instant a reminder on ``due_date`` (at ``due_time``, or the default
    morning hour) is due, reading wall-clock time in ``zone``."""
    local = datetime.combine(due_date, due_time or DEFAULT_FIRE_TIME, tzinfo=zone)
    return local.astimezone(timezone.utc)


def next_due(repeat: str, anchor: date, due_time: time | None, *, after: datetime,
             zone: ZoneInfo) -> tuple[date, datetime]:
    """The first occurrence of the series whose instant is STRICTLY after ``after``.

    A series found late (the server was down) therefore skips its backlog and resumes
    at the next future time instead of firing once per missed day."""
    gap_days = max((after.astimezone(zone).date() - anchor).days, 0)
    per = {"daily": 1, "weekly": 7, "monthly": 31, "yearly": 366}[repeat]
    # A floor estimate that can only undershoot, then walk forward: a few steps at most.
    n = max(gap_days // per - 1, 0)
    while True:
        d = occurrence(repeat, anchor, n)
        at = due_instant(d, due_time, zone)
        if at > after:
            return d, at
        n += 1


def validate_repeat(value) -> str | None:
    """'' / None → None (one-time); otherwise one of REPEATS."""
    if value in (None, ""):
        return None
    value = str(value).strip().lower()
    if value not in REPEATS:
        raise ValidationError(f"repeat must be one of {', '.join(REPEATS)}, or empty")
    return value


def parse_time(value) -> time | None:
    """None / '' → None (date-only); 'HH:MM' 24-hour → ``time``."""
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    try:
        hour, minute = str(value).strip().split(":")[:2]
        if len(hour) != 2 or len(minute) != 2:
            raise ValueError
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        raise ValidationError("due_time must be HH:MM in 24-hour time, e.g. 15:00") from None
