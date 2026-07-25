"""APScheduler wiring for the heartbeat (issue #6).

A single ``BackgroundScheduler`` with one 60s ``heartbeat_tick`` job. No
persistent job store — the job is re-registered from code on every boot (matches
Chatty; the tick is idempotent). ``get_scheduler()`` exposes the scheduler so
other features (e.g. #5 dreaming, if it ever wants its own APScheduler job rather
than the tick seam) can register jobs without touching this module.

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
        service.tick, "interval", seconds=60, id="heartbeat_tick",
        max_instances=1, coalesce=True,   # a slow tick never stacks
    )
    _scheduler.start()
    logger.info("Heartbeat scheduler started (60s tick)")


def shutdown_scheduler() -> None:
    """Stop the scheduler, waiting for an in-flight tick to finish (so it never
    loses the Postgres pool out from under it)."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=True)
    except Exception:
        logger.warning("scheduler shutdown errored", exc_info=True)
    _scheduler = None
    logger.info("Heartbeat scheduler stopped")


def get_scheduler():
    """The live BackgroundScheduler (or None if not started) — extension seam."""
    return _scheduler
