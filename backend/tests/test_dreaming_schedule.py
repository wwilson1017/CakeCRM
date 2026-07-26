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


async def test_stop_shields_inflight_cycle_under_cancellation(monkeypatch):
    # Forced shutdown (stop() cancelled) must NOT abandon an in-flight cycle — its worker
    # thread still holds a DB connection and main.py closes the pool right after stop()
    # returns. stop() must let the cycle finish AND propagate the cancellation.
    cycle_running = asyncio.Event()
    let_finish = asyncio.Event()
    done = {"finished": False}
    sched = schedule.DreamingScheduler()

    async def instant_sleep(self, seconds):
        return

    async def controlled_run_due(self):
        cycle_running.set()
        await let_finish.wait()
        done["finished"] = True
        self._stop.set()   # end the loop after this one cycle

    monkeypatch.setattr(schedule.DreamingScheduler, "_sleep", instant_sleep)
    monkeypatch.setattr(schedule.DreamingScheduler, "_run_due", controlled_run_due)

    sched.start()
    await cycle_running.wait()          # loop is in an in-flight cycle
    stop_task = asyncio.ensure_future(sched.stop())
    await asyncio.sleep(0.01)           # let stop() reach the shielded await
    stop_task.cancel()                  # forced shutdown mid-cycle
    await asyncio.sleep(0.01)
    assert done["finished"] is False    # cycle NOT abandoned — still shielded
    let_finish.set()                    # allow the cycle to complete
    with pytest.raises(asyncio.CancelledError):
        await stop_task                 # cancellation propagates...
    assert done["finished"] is True     # ...only AFTER the cycle finished


def test_seconds_until_next_slot_is_dst_correct():
    # US spring-forward is 2026-03-08 (02:00→03:00). From 2026-03-07 04:00 to the next
    # 03:00 slot is 22 REAL hours, not 23 wall-clock hours — the .timestamp() delta must
    # reflect the offset shift (naive same-tzinfo subtraction would return 23h).
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    secs = schedule.seconds_until_next_slot(datetime(2026, 3, 7, 4, 0, tzinfo=ny))
    assert secs == pytest.approx(22 * 3600, abs=5)
