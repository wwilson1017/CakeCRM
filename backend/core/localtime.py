"""The app's local-time convention: one timezone, read from ``TIMEZONE``.

Extracted from ``dreaming/schedule.py`` when #70 needed the same notion of "today"
for GTD due dates and recurrence. Two consumers of a 4-line helper is one too many
copies: a self-hoster sets ``TIMEZONE`` once and the nightly dreaming slot and the
GTD date math agree by construction.

Never use the implicit process-local time instead. Containers run UTC regardless of
intent (Railway, Docker), so ``date.today()`` there rolls over mid-evening for a
US-based user: a "due today" decision would fire hours early and disagree with the
date the same todo is rendered against.
"""

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def tz() -> ZoneInfo:
    """The configured timezone, falling back to UTC on an absent or bogus value.

    Fail-safe rather than fail-fast on purpose: a typo in an env var must not stop
    the app from booting.
    """
    try:
        return ZoneInfo(os.getenv("TIMEZONE") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def now_local() -> datetime:
    """The current aware datetime in the configured timezone."""
    return datetime.now(tz())


def today_local() -> date:
    """The current calendar date in the configured timezone.

    A due date carries the user's LOCAL calendar intent. The GTD frontend's
    ``todayStr()`` derives the same day from this zone, which ``get_filters`` hands it
    as ``tz`` (#259), so the browser's own clock never decides it.
    """
    return now_local().date()


def local_day_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    """``[start, next_start)`` for one local calendar day, as aware instants.

    For bounding one local calendar day as instants (issue #130's Today panel arms
    its reload on this day's end). Computed in Python rather than with
    SQL's ``AT TIME ZONE`` so ``zoneinfo`` stays the single timezone authority:
    Postgres ships its own tz database, and two copies of the same rule are exactly
    how a boundary drifts apart.

    Built from calendar fields rather than ``start + 24h``, so a DST day is honestly
    23 or 25 hours long. The zone is read ONCE — ``tz()`` re-reads the environment on
    every call, and a day whose two ends came from different zones is not a day.

    Pass ``day`` explicitly when the caller already decided which day it is: reading
    the clock a second time can straddle midnight and bound a different day than the
    one the rest of the request is about.
    """
    zone = tz()
    d = day if day is not None else datetime.now(zone).date()
    start = datetime.combine(d, time.min, tzinfo=zone)
    end = datetime.combine(d + timedelta(days=1), time.min, tzinfo=zone)
    return start, end
