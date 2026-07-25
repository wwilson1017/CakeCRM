"""dreaming/schedule.py — pure slot/due logic + the interim async loop lifecycle."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dreaming import schedule

UTC = timezone.utc


def _at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


# ── pure slot / due functions ───────────────────────────────────────────────────

def test_most_recent_slot_before_and_after_run_hour():
    assert schedule.most_recent_slot(_at(2026, 7, 24, 10)) == _at(2026, 7, 24, schedule.RUN_HOUR)
    # before RUN_HOUR → yesterday's slot
    assert schedule.most_recent_slot(_at(2026, 7, 24, 2)) == _at(2026, 7, 23, schedule.RUN_HOUR)


def test_is_due_true_when_never_run():
    assert schedule.is_due(None, _at(2026, 7, 24, 10)) is True


def test_is_due_false_when_ran_after_the_slot():
    # ran today at 04:00, now 10:00 → already ran since today's 03:00 slot
    assert schedule.is_due(_at(2026, 7, 24, 4), _at(2026, 7, 24, 10)) is False


def test_is_due_true_when_last_run_precedes_the_slot():
    # ran yesterday 10:00, now today 10:00 → slot is today 03:00 → due
    assert schedule.is_due(_at(2026, 7, 23, 10), _at(2026, 7, 24, 10)) is True


def test_catchup_run_does_not_skip_next_slot():
    # A 10:00 catch-up run must NOT suppress the next 03:00 run (the 41h-skip bug).
    catchup = _at(2026, 7, 24, 10)
    next_slot = _at(2026, 7, 25, schedule.RUN_HOUR)
    assert schedule.is_due(catchup, next_slot) is True


def test_seconds_until_next_slot():
    # 02:00 → next slot today 03:00 (~3600s)
    assert schedule.seconds_until_next_slot(_at(2026, 7, 24, 2)) == pytest.approx(3600, abs=1)
    # 04:00 → next slot tomorrow 03:00 (~23h)
    assert schedule.seconds_until_next_slot(_at(2026, 7, 24, 4)) == pytest.approx(23 * 3600, abs=1)


def test_is_due_handles_cross_timezone_last_run():
    # last_ok in UTC, now in a +02:00 zone — aware comparison must stay correct.
    tz2 = timezone(timedelta(hours=2))
    now = datetime(2026, 7, 24, 10, tzinfo=tz2)
    last = datetime(2026, 7, 23, 23, tzinfo=UTC)  # = 2026-07-24 01:00 +02:00, before today's 03:00
    assert schedule.is_due(last, now) is True


# ── async loop lifecycle ────────────────────────────────────────────────────────

async def test_loop_runs_due_on_startup_then_stops(monkeypatch):
    calls = {"n": 0}
    sched = schedule.DreamingScheduler()

    async def fast_sleep(self, seconds):
        await asyncio.sleep(0)

    def fake_if_due():
        calls["n"] += 1

    monkeypatch.setattr(schedule.DreamingScheduler, "_sleep", fast_sleep)
    monkeypatch.setattr("dreaming.processor.run_dreaming_if_due", fake_if_due)

    sched.start()
    await asyncio.sleep(0.05)   # let the startup catch-up + a loop iteration run
    await sched.stop()

    assert calls["n"] >= 1
    assert sched._task is None   # cleaned up


async def test_stop_awaits_in_flight_cycle(monkeypatch):
    done = {"finished": False}
    sched = schedule.DreamingScheduler()

    async def sleep_until_startup(self, seconds):
        # first (startup) sleep returns immediately; later sleeps block until stop
        if seconds == schedule._STARTUP_DELAY_SECONDS:
            return
        await asyncio.wait_for(self._stop.wait(), timeout=5)

    def slow_cycle():
        import time
        time.sleep(0.1)
        done["finished"] = True

    monkeypatch.setattr(schedule.DreamingScheduler, "_sleep", sleep_until_startup)
    monkeypatch.setattr("dreaming.processor.run_dreaming_if_due", slow_cycle)

    sched.start()
    await asyncio.sleep(0.02)   # let the cycle start (in a worker thread)
    await sched.stop()          # must wait for the in-flight cycle to finish

    assert done["finished"] is True


def test_module_level_start_stop(monkeypatch):
    # start_scheduler / stop_scheduler manage a singleton for main.py's lifespan.
    async def run():
        async def noop_sleep(self, seconds):
            await asyncio.wait_for(self._stop.wait(), timeout=5)
        monkeypatch.setattr(schedule.DreamingScheduler, "_sleep", noop_sleep)
        s = schedule.start_scheduler()
        assert s is schedule._scheduler
        await schedule.stop_scheduler()
        assert schedule._scheduler is None

    asyncio.run(run())


async def test_stop_propagates_own_cancellation(monkeypatch):
    import contextlib
    sched = schedule.DreamingScheduler()

    async def block_forever(self, seconds):
        await asyncio.Event().wait()   # loop task never returns on its own

    monkeypatch.setattr(schedule.DreamingScheduler, "_sleep", block_forever)
    sched.start()
    task = sched._task
    await asyncio.sleep(0.01)

    stop_task = asyncio.ensure_future(sched.stop())
    await asyncio.sleep(0.01)
    stop_task.cancel()                 # cancel stop() ITSELF (forced shutdown)
    with pytest.raises(asyncio.CancelledError):
        await stop_task                # must propagate, not be swallowed

    task.cancel()                      # cleanup the still-blocked loop task
    with contextlib.suppress(asyncio.CancelledError):
        await task
