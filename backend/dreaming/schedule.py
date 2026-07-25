"""Interim dreaming scheduler — issue #6's background loop absorbs this.

Issue #5 must satisfy its own "the dreaming job runs on schedule" acceptance before
#6's heartbeat loop lands, so this ships a minimal guarded asyncio task: run once
shortly after startup (self-hosted machines are rarely awake at 03:00), then once per
day at RUN_HOUR local. It calls ``processor.run_dreaming_if_due()`` — never an
unconditional variant — so the advisory-lock + due-guard always apply; running it more
often than needed is a harmless no-op.

When #6's loop lands it calls ``run_dreaming_if_due()`` from its own scheduler and this
module's single ``start_scheduler()`` / ``stop_scheduler()`` call-site in ``main.py`` is
deleted. The pure due-logic here (``is_due`` / slot helpers) is unit-tested independently.

Timezone is explicit (``TIMEZONE`` env, default UTC) — never the process-local time,
which is UTC on Railway regardless of intent.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

RUN_HOUR = 3                    # 03:00 local — arbitrary "overnight" slot; #6 may retune
_STARTUP_DELAY_SECONDS = 120    # let migrations/pool settle; stay off the boot path


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("TIMEZONE") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def now_local() -> datetime:
    return datetime.now(_tz())


def most_recent_slot(now: datetime) -> datetime:
    """The most recent RUN_HOUR:00 boundary at or before *now* (same tz as *now*)."""
    slot = now.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    if now < slot:
        slot -= timedelta(days=1)
    return slot


def is_due(last_ok: datetime | None, now: datetime) -> bool:
    """True iff no successful run since the most recent scheduled slot.

    Slot-based (not a raw N-hour window), so a catch-up run at, say, 10:00 does NOT
    cause the next 03:00 slot to be skipped. ``last_ok`` may be in any timezone
    (psycopg2 returns aware datetimes); aware-vs-aware comparison is tz-correct.
    """
    if last_ok is None:
        return True
    return last_ok < most_recent_slot(now)


def seconds_until_next_slot(now: datetime) -> float:
    """Seconds from *now* until the next RUN_HOUR:00 boundary (strictly future)."""
    slot = now.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    if now >= slot:
        slot += timedelta(days=1)
    return max(1.0, (slot - now).total_seconds())


class DreamingScheduler:
    """A single asyncio task that runs the due-guarded dreaming cycle daily.

    Cooperative shutdown: ``stop()`` signals the loop and awaits it, so a cycle already
    running (in a worker thread via ``to_thread``) finishes before the task returns —
    the caller can then close the DB pool without pulling it out from under a cycle.
    """

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _sleep(self, seconds: float) -> None:
        """Sleep up to *seconds*, returning early if stop is signalled."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _run_due(self) -> None:
        # Lazy import avoids a processor<->schedule import cycle.
        from dreaming.processor import run_dreaming_if_due
        try:
            await asyncio.to_thread(run_dreaming_if_due)
        except Exception:
            logger.exception("dreaming scheduler: cycle raised")

    async def _loop(self) -> None:
        try:
            await self._sleep(_STARTUP_DELAY_SECONDS)      # startup catch-up
            if self._stop.is_set():
                return
            await self._run_due()
            while not self._stop.is_set():
                await self._sleep(seconds_until_next_slot(now_local()))
                if self._stop.is_set():
                    break
                await self._run_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dreaming scheduler loop crashed")


_scheduler: DreamingScheduler | None = None


def start_scheduler() -> DreamingScheduler:
    """Start the interim dreaming scheduler (called from main.py's lifespan)."""
    global _scheduler
    _scheduler = DreamingScheduler()
    _scheduler.start()
    logger.info("interim dreaming scheduler started")
    return _scheduler


async def stop_scheduler() -> None:
    """Stop the interim dreaming scheduler, awaiting any in-flight cycle."""
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
        logger.info("interim dreaming scheduler stopped")
