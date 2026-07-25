"""Heartbeat scheduler lifecycle (heartbeat/scheduler.py).

The 60s job never fires during the test window (interval triggers first-fire is
now+interval), so no DB is touched — this stays hermetic.
"""

from heartbeat import scheduler


def test_start_is_idempotent_and_shutdown_clears():
    try:
        scheduler.start_scheduler()
        first = scheduler.get_scheduler()
        assert first is not None
        scheduler.start_scheduler()                 # second call is a no-op
        assert scheduler.get_scheduler() is first    # same instance, not restarted
    finally:
        scheduler.shutdown_scheduler()
    assert scheduler.get_scheduler() is None


def test_shutdown_without_start_is_safe():
    scheduler.shutdown_scheduler()                   # no-op, must not raise
    assert scheduler.get_scheduler() is None


def test_registers_both_jobs():
    try:
        scheduler.start_scheduler()
        sched = scheduler.get_scheduler()
        assert sched.get_job("reminder_tick") is not None    # 60s reminder job
        assert sched.get_job("heartbeat_turn") is not None    # throttled AI-turn job
    finally:
        scheduler.shutdown_scheduler()
