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
fail OPEN and poll anyway (availability over the rare double-poll). The bot-swap
``stop → mutate → start`` guarantee (router.connect/disconnect) is likewise
single-process: under multiple replicas, connect on one replica can't stop another's
in-flight poll, so a swap has a rare offset-skew window that self-heals (once the new
bot's update_ids pass the stale cursor) or is fixed by regenerating — a durable
config-generation counter is future work if multi-replica ever becomes supported.
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
    """Cancel the poll task and wait for it to unwind. Call from lifespan shutdown.

    Swap-and-null the global FIRST so a concurrent ``start()`` (e.g. a second connect
    request while this one awaits the old task) can't have its freshly-created task
    clobbered to None and orphaned — we only ever cancel/await our OWN local reference.
    """
    global _task
    task, _task = _task, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("telegram poller: task ended with an error during shutdown")
    logger.info("telegram poller stopped")


def _acquire_lock():
    """Best-effort leader election. Returns a held connection, "BUSY", or None (error).

    Uses a DEDICATED psycopg2 connection rather than a ``core/postgres`` pooled one on
    purpose: a session-level ``pg_try_advisory_lock`` is held until its connection
    closes, so the leader must keep ONE connection open for the poller's lifetime.
    ``get_connection`` is a context manager that returns the connection to the shared
    pool on exit — a pooled lock-holder would leak process leadership to whoever next
    checks out that connection. This is the one sanctioned raw-connect in the module.
    """
    import psycopg2

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return None
    conn = None
    try:
        conn = psycopg2.connect(dsn, connect_timeout=10)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        if cur.fetchone()[0]:
            return conn  # we are the leader; hold the connection to hold the lock
        conn.close()
        return "BUSY"
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        logger.warning("telegram poller: advisory-lock acquire errored — polling without leader lock")
        return None


async def _acquire_lock_safe():
    """Acquire the leader lock without leaking a lock-holding connection on cancellation.

    If the task is cancelled mid-acquire, the executor thread still runs to completion
    and would otherwise return a connection (holding ``pg_try_advisory_lock``) into a
    dropped future — leaving the lock held until GC and locking every future poller out
    (BUSY). ``shield`` keeps that future alive so a done-callback can close the
    connection once it finishes.
    """
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, _acquire_lock)
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        def _close(f):
            try:
                r = f.result()
                if r not in (None, "BUSY") and hasattr(r, "close"):
                    r.close()
            except Exception:
                pass
        fut.add_done_callback(_close)
        raise


async def _run() -> None:
    lock_conn = None
    leadership_resolved = False
    backoff = _MIN_BACKOFF
    try:
        while True:
            try:
                # ── Leader election (best effort) ──────────────────────────
                if not leadership_resolved:
                    res = await _acquire_lock_safe()
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
                        # A webhook is set (or another getUpdates is active): self-heal
                        # by removing any webhook, then back off and retry.
                        await asyncio.to_thread(client.delete_webhook, token, False)
                    wait = e.retry_after or backoff
                    logger.warning("telegram getUpdates error (status=%s) — backing off %ss", e.status, wait)
                    await asyncio.sleep(wait)
                    backoff = min(backoff * 2, _MAX_BACKOFF)
                    continue

                for u in updates:
                    try:
                        await service.handle_update(u)
                    except asyncio.CancelledError:
                        # Shutdown (e.g. a Railway redeploy) mid-update: do NOT advance
                        # the offset — let Telegram redeliver this update next boot.
                        # resolve_confirmation is idempotent and a redelivered text just
                        # re-answers; both beat silently dropping the message.
                        raise
                    except Exception:
                        logger.exception("telegram poller: update handling failed")
                    # Advance the cursor AFTER a COMPLETED dispatch (success or handled
                    # error), monotonically. A handler error still consumes the update
                    # (at-MOST-once on a handler failure, so a bad update can't become a
                    # poison-pill retry loop; the user can resend). Only a crash or a
                    # graceful-shutdown cancel BEFORE this advance yields redelivery.
                    uid = u.get("update_id")
                    if uid is not None:
                        await asyncio.to_thread(store.advance_offset, int(uid) + 1)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Any OTHER error (a transient DB blip in a store.* call, pool
                # exhaustion, a decrypt failure) must NOT kill the only poll task —
                # log and back off, matching the getUpdates error path. Without this a
                # single Postgres hiccup would permanently disable Telegram.
                logger.exception("telegram poller: iteration error — backing off %ss", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
    except asyncio.CancelledError:
        raise
    finally:
        if lock_conn is not None:
            try:
                lock_conn.close()  # closing releases the session advisory lock
            except Exception:
                pass
