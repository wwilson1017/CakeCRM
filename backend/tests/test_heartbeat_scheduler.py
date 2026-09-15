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


# ── #72 Phase 4: the observer's own job ─────────────────────────────────────────

def test_the_observer_runs_on_its_own_job():
    try:
        scheduler.start_scheduler()
        sched = scheduler.get_scheduler()
        job = sched.get_job("observer")
        assert job is not None
        assert job.max_instances == 1
        assert job.trigger.interval.total_seconds() == 60
        from heartbeat import service
        assert job.func is service.observer_tick
    finally:
        scheduler.shutdown_scheduler()


def test_no_other_job_shares_the_observers_callable():
    """It must be a job of its OWN, not a second trigger on someone else's callable —
    otherwise a hung provider call would delay whatever that callable also does."""
    try:
        scheduler.start_scheduler()
        sched = scheduler.get_scheduler()
        observer_job = sched.get_job("observer")
        others = [j for j in sched.get_jobs() if j.id != "observer"]
        assert others, "expected sibling jobs"
        assert all(j.func is not observer_job.func for j in others)
    finally:
        scheduler.shutdown_scheduler()


def test_every_registered_job_has_a_distinct_callable():
    try:
        scheduler.start_scheduler()
        sched = scheduler.get_scheduler()
        funcs = [j.func for j in sched.get_jobs()]
        assert len(funcs) == len({id(f) for f in funcs})
    finally:
        scheduler.shutdown_scheduler()


def test_only_the_observers_own_tick_can_reach_the_observer():
    """The AI-bound rule, asserted at the source rather than by a return value.

    An absent key in some other tick's return dict would not prove that no AI work
    happened inside it. This reads heartbeat/service.py's AST and shows that exactly ONE
    top-level function reaches the observer seam — so no other tick can be delaying its
    own deliveries behind a light-tier provider call. Naming no sibling tick keeps this
    true across renames.
    """
    import ast
    import pathlib

    from heartbeat import service

    tree = ast.parse(pathlib.Path(service.__file__).read_text(encoding="utf-8"))
    reaching = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        names |= {a.name for n in ast.walk(node) if isinstance(n, ast.ImportFrom)
                  for a in n.names}
        if {"_maybe_run_observer", "run_observer_if_due"} & names:
            reaching.append(node.name)
    assert sorted(reaching) == ["_maybe_run_observer", "observer_tick"]
