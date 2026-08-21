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
from datetime import date, datetime
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

    A due date carries the user's LOCAL calendar intent — the frontend's
    ``todayStr()`` derives it from local date parts for the same reason.
    """
    return now_local().date()
