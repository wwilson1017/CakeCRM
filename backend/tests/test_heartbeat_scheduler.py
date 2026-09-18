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


def test_registers_all_jobs():
    try:
        scheduler.start_scheduler()
        sched = scheduler.get_scheduler()
        assert sched.get_job("maintenance_tick") is not None  # 60s dreaming + score refresh
        assert sched.get_job("heartbeat_turn") is not None    # throttled AI-turn job
        assert sched.get_job("gmail_scan") is not None        # #17 read-only inbox touch scan
        assert sched.get_job("proactive") is not None         # #22 P3 digest + nudges
    finally:
        scheduler.shutdown_scheduler()


def test_network_jobs_are_decoupled_from_the_maintenance_tick():
    """gmail_scan and proactive both do network I/O. They must stay on their OWN jobs,
    each max_instances=1, so a hung push endpoint or inbox request can never stall the
    maintenance passes — the rule heartbeat/service.py documents."""
    try:
        scheduler.start_scheduler()
        sched = scheduler.get_scheduler()
        for job_id in ("maintenance_tick", "gmail_scan", "proactive"):
            job = sched.get_job(job_id)
            assert job.max_instances == 1, job_id
        assert sched.get_job("proactive").func is not sched.get_job("maintenance_tick").func
    finally:
        scheduler.shutdown_scheduler()
