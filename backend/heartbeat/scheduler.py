"""APScheduler wiring for the heartbeat (issue #6).

A single ``BackgroundScheduler`` with TWO jobs, deliberately decoupled so a slow
system AI turn never delays reminder delivery:
  * ``reminder_tick`` — every 60s: fire due reminders + drive dreaming (fast, bounded).
  * ``heartbeat_turn`` — every 5 min: run the throttled system AI turn if due.
Each job is ``max_instances=1, coalesce=True`` so a slow run never stacks and never
blocks the OTHER job. No persistent job store — jobs are re-registered on every boot
(the ticks are idempotent). ``get_scheduler()`` exposes the scheduler so other
features (e.g. #5 dreaming, if it ever wants its own job) can register without
touching this module.

The scheduler ALWAYS starts (locally too): processing due reminders is keyless,
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
        service.reminder_tick, "interval", seconds=60, id="reminder_tick",
        max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        service.heartbeat_turn_tick, "interval", seconds=300, id="heartbeat_turn",
        max_instances=1, coalesce=True,
    )
    # #17: the Gmail touch scan is slow / network-bound, so it gets its OWN decoupled
    # job (not a reminder_tick sibling) — a hung inbox request must never delay reminder
    # delivery. run_scan_if_due self-throttles to GMAIL_SCAN_INTERVAL_MINUTES and no-ops
    # when Gmail isn't connected, so a 60s trigger is just the due-check cadence.
    _scheduler.add_job(
        service.gmail_scan_tick, "interval", seconds=60, id="gmail_scan",
        max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info("Heartbeat scheduler started (reminder_tick 60s + heartbeat_turn 300s + gmail_scan 60s)")


def shutdown_scheduler() -> None:
    """Stop the scheduler WITHOUT waiting for in-flight jobs.

    ``wait=False`` is deliberate: background AI turns run their coroutine on the MAIN
    event loop (via run_coroutine_threadsafe), and this shutdown is called from the
    lifespan handler ON that same loop — so ``shutdown(wait=True)`` would block the
    main loop while a scheduler-thread tick blocks on a turn that can only progress
    on the (now-blocked) main loop → deadlock until the turn's timeout. Not waiting
    avoids that; an abandoned in-flight tick is at-most-once-safe (a reminder may be
    left ``processing``, never double-fired) and the pool close below is guarded."""
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
