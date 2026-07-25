"""Telegram long-poll task — a single main-loop asyncio task.

Started once from the app lifespan; runs for the process lifetime. Each iteration
re-reads the token + offset from the DB, so connecting/disconnecting a bot (which just
mutates ``telegram_settings``) is picked up automatically within a few seconds — no
task restart needed.

Why a main-loop task and not an OS thread: the blocking ``getUpdates`` is offloaded via
``asyncio.to_thread``, while ``service.handle_update`` (which drives ``engine.chat``)
runs directly on the main loop — the SAME loop as the SSE endpoint — so the provider's
loop-bound cached async client stays consistent. Updates are processed strictly
sequentially (one ``await handle_update`` at a time), which is exactly right for one
conversation with ``UNIQUE(conversation_id, seq)``.

Single-process invariant: production runs ``gunicorn --workers 1`` and local dev is a
single process, so there is normally one poller. As defense against a multi-replica
deploy, the task first takes a best-effort Postgres advisory lock (a second instance
idles instead of fighting over ``getUpdates``); if the lock mechanism itself errors we
fail OPEN and poll anyway (availability over the rare double-poll).
"""

import asyncio
import logging
import os

from . import client, service, store

logger = logging.getLogger(__name__)

_LONG_POLL = 25          # getUpdates server-side long-poll seconds
_IDLE_SLEEP = 5          # re-check interval while no token is connected
_BUSY_SLEEP = 30         # re-check interval when another instance holds the lock
_MIN_BACKOFF = 3
_MAX_BACKOFF = 60
_ADVISORY_LOCK_KEY = 720770  # distinct from the migration runner's pg_advisory_lock(1)

_task: asyncio.Task | None = None


def start() -> None:
    """Create the poll task on the running loop (idempotent). Call from the lifespan."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(), name="telegram-poller")
        logger.info("telegram poller started")


async def stop() -> None:
    """Cancel the poll task and wait for it to unwind. Call from lifespan shutdown."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):
        pass
    _task = None
    logger.info("telegram poller stopped")


def _acquire_lock():
    """Best-effort leader election. Returns a held connection, "BUSY", or None (error)."""
    import psycopg2

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return None
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        if cur.fetchone()[0]:
            return conn  # we are the leader; hold the connection to hold the lock
        conn.close()
        return "BUSY"
    except Exception:
        logger.warning("telegram poller: advisory-lock acquire errored — polling without leader lock")
        return None


async def _run() -> None:
    lock_conn = None
    leadership_resolved = False
    backoff = _MIN_BACKOFF
    try:
        while True:
            # ── Leader election (best effort) ──────────────────────────────
            if not leadership_resolved:
                res = await asyncio.to_thread(_acquire_lock)
                if res == "BUSY":
                    await asyncio.sleep(_BUSY_SLEEP)
                    continue
                lock_conn = res if res not in (None, "BUSY") else None
                leadership_resolved = True  # got the lock, or fail-open on error

            token = await asyncio.to_thread(store.get_bot_token)
            if not token:
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            offset = await asyncio.to_thread(store.get_offset)
            try:
                updates = await asyncio.to_thread(client.get_updates, token, offset, _LONG_POLL)
                backoff = _MIN_BACKOFF
            except client.TelegramError as e:
                if e.status == 409:
                    # A webhook is set (or another getUpdates is active): self-heal by
                    # removing any webhook, then back off and retry.
                    await asyncio.to_thread(client.delete_webhook, token, False)
                wait = e.retry_after or backoff
                logger.warning("telegram getUpdates error (status=%s) — backing off %ss", e.status, wait)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue

            for u in updates:
                try:
                    await service.handle_update(u)
                except Exception:
                    logger.exception("telegram poller: update handling failed")
                finally:
                    # Advance the cursor AFTER dispatch (at-least-once), monotonically.
                    uid = u.get("update_id")
                    if uid is not None:
                        await asyncio.to_thread(store.advance_offset, int(uid) + 1)
    except asyncio.CancelledError:
        raise
    finally:
        if lock_conn is not None:
            try:
                lock_conn.close()  # closing releases the session advisory lock
            except Exception:
                pass
