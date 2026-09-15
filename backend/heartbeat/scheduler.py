"""APScheduler wiring for the heartbeat (issue #6).

A single ``BackgroundScheduler`` with FIVE jobs, deliberately decoupled so a slow
system AI turn (or a slow inbox scan) never stalls the bounded maintenance passes:
  * ``maintenance_tick`` — every 60s: stamp the clock, drive dreaming + the lead-score
    refresh (fast, bounded, local SQL).
  * ``heartbeat_turn`` — every 5 min: run the throttled system AI turn if due.
  * ``gmail_scan`` — every 60s: run the read-only Gmail touch scan if due (#17;
    network-bound, so it runs as its OWN job rather than inside maintenance_tick, and
    bounds its Gmail call with a wall-clock deadline).
  * ``proactive`` — every 60s: run the daily digest / stale nudges if due (#22 Phase 3;
    delivers over the network and may run one AI turn, so same reasoning as gmail_scan).
  * ``observer`` — every 60s: run the #72 Phase 4 observer if due (one light-tier call
    per settled conversation, so AI-bound: same reasoning again).
Each job is ``max_instances=1, coalesce=True`` so a slow run never stacks. The five
jobs share APScheduler's default thread pool, whose default width (10) far exceeds the
five low-frequency jobs here, so a slow scan can't starve maintenance_tick of a worker. No persistent job store — jobs are re-registered on every boot
(the ticks are idempotent). ``get_scheduler()`` exposes the scheduler so other
features (e.g. #5 dreaming, if it ever wants its own job) can register without
touching this module.

The scheduler ALWAYS starts (locally too): the maintenance passes are keyless,
cost-free work — only the heartbeat AI *turn* is env-gated (see heartbeat.service).
apscheduler is imported lazily per repo convention (note: it IS required at
startup because lifespan calls start_scheduler()).
"""

import logging

logger = logging.getLogger(__name__)

_scheduler = None


def start_scheduler() -> None:
    """Start the background scheduler. Idempotent — a second call is a no-op."""
    global _scheduler
    if _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    from heartbeat import service

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        service.maintenance_tick, "interval", seconds=60, id="maintenance_tick",
        max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        service.heartbeat_turn_tick, "interval", seconds=300, id="heartbeat_turn",
        max_instances=1, coalesce=True,
    )
    # #17: the Gmail touch scan is slow / network-bound, so it gets its OWN decoupled
    # job (not a maintenance_tick sibling) — a hung inbox request must never stall the
    # maintenance passes. run_scan_if_due self-throttles to GMAIL_SCAN_INTERVAL_MINUTES and no-ops
    # when Gmail isn't connected, so a 60s trigger is just the due-check cadence.
    _scheduler.add_job(
        service.gmail_scan_tick, "interval", seconds=60, id="gmail_scan",
        max_instances=1, coalesce=True,
    )
    # #22 Phase 3: the proactive digest + nudges DELIVER (web push / Telegram) and may
    # run one AI turn, so by the same rule as gmail_scan they get their own decoupled
    # job. run_proactive_if_due is settings-gated and due-guarded (once per day for the
    # digest, PROACTIVE_NUDGE_INTERVAL_MINUTES for nudges), so a 60s trigger is only the
    # due-check cadence, not the send cadence.
    _scheduler.add_job(
        service.proactive_tick, "interval", seconds=60, id="proactive",
        max_instances=1, coalesce=True,
    )
    # #72 Phase 4: the observer makes a light-tier provider call per settled
    # conversation, so by the same rule as gmail_scan and proactive it gets its own
    # decoupled job. run_observer_if_due is settings-gated, returns before any claim or
    # query when no provider is configured, and self-throttles to its own interval, so a
    # 60s trigger is only the due-check cadence. (#72's OTHER half, file-dreaming, is
    # pure local SQL and rides maintenance_tick's existing dreaming seam instead.)
    _scheduler.add_job(
        service.observer_tick, "interval", seconds=60, id="observer",
        max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info("Heartbeat scheduler started (maintenance_tick 60s + heartbeat_turn 300s "
                "+ gmail_scan 60s + proactive 60s + observer 60s)")


def shutdown_scheduler() -> None:
    """Stop the scheduler WITHOUT waiting for in-flight jobs.

    ``wait=False`` is deliberate: background AI turns run their coroutine on the MAIN
    event loop (via run_coroutine_threadsafe), and this shutdown is called from the
    lifespan handler ON that same loop — so ``shutdown(wait=True)`` would block the
    main loop while a scheduler-thread tick blocks on a turn that can only progress
    on the (now-blocked) main loop → deadlock until the turn's timeout. Not waiting
    avoids that; an abandoned in-flight tick is safe to abandon (every pass is
    advisory-locked, due-guarded and idempotent, so it just re-runs next tick) and the
    pool close below is guarded."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        logger.warning("scheduler shutdown errored", exc_info=True)
    _scheduler = None
    logger.info("Heartbeat scheduler stopped")


def get_scheduler():
    """The live BackgroundScheduler (or None if not started) — extension seam."""
    return _scheduler
