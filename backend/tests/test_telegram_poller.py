"""Telegram poll task — the background driver. Hermetic: client/store/service and
psycopg2/asyncio.sleep are mocked, so no DB, no network, no real waiting.

Covers the behaviors flagged as high-risk-but-untested: advisory-lock leader/BUSY/error
outcomes, start()/stop() idempotency, offset-advance-after-dispatch, 409 self-heal via
delete_webhook, retry_after honoring, and — critically — that a transient error in a
store.* call backs off and keeps the loop alive instead of permanently killing it.
"""

import asyncio
import types

import pytest

from telegram import poller

# ── Advisory-lock leader election ───────────────────────────────────────────

class _FakeCursor:
    def __init__(self, got):
        self._got = got

    def execute(self, *a, **k):
        if isinstance(self._got, Exception):
            raise self._got

    def fetchone(self):
        return (self._got,)


class _FakeConn:
    def __init__(self, got):
        self._got = got
        self.closed = False
        self.autocommit = False

    def cursor(self):
        return _FakeCursor(self._got)

    def close(self):
        self.closed = True


def _patch_connect(monkeypatch, conn):
    import psycopg2
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setattr(psycopg2, "connect", lambda dsn, **kw: conn)  # accepts connect_timeout


def test_acquire_lock_leader_holds_connection(monkeypatch):
    conn = _FakeConn(True)
    _patch_connect(monkeypatch, conn)
    assert poller._acquire_lock() is conn
    assert conn.closed is False  # held open to hold the session lock


def test_acquire_lock_busy_closes_connection(monkeypatch):
    conn = _FakeConn(False)
    _patch_connect(monkeypatch, conn)
    assert poller._acquire_lock() == "BUSY"
    assert conn.closed is True


def test_acquire_lock_error_closes_connection_and_fails_open(monkeypatch):
    conn = _FakeConn(RuntimeError("db down"))
    _patch_connect(monkeypatch, conn)
    assert poller._acquire_lock() is None   # fail-open (poll without the lock)
    assert conn.closed is True              # but don't leak the connection


# ── start()/stop() ──────────────────────────────────────────────────────────

async def test_start_is_idempotent_and_stop_cancels(monkeypatch):
    # A poll task that just idles forever, so we can prove start/stop task lifecycle.
    monkeypatch.setattr(poller, "_acquire_lock", lambda: None)
    monkeypatch.setattr(poller.store, "get_bot_token", lambda: "")  # no token → idle sleep
    monkeypatch.setattr(poller.asyncio, "sleep", _never)            # park forever
    poller.start()
    t1 = poller._task
    poller.start()                # second call must NOT create a second task
    assert poller._task is t1
    await poller.stop()
    assert poller._task is None
    assert t1.cancelled() or t1.done()


async def _never(_secs):
    await asyncio.Event().wait()  # never returns


# ── The loop ────────────────────────────────────────────────────────────────

def _leader(monkeypatch):
    monkeypatch.setattr(poller, "_acquire_lock", lambda: types.SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(poller.store, "get_bot_token", lambda: "TESTTOKEN")
    monkeypatch.setattr(poller.store, "get_offset", lambda: 0)


async def test_processes_updates_and_advances_offset(monkeypatch):
    _leader(monkeypatch)
    advanced, handled = [], []
    monkeypatch.setattr(poller.store, "advance_offset", lambda n: advanced.append(n))

    batches = iter([[{"update_id": 5}, {"update_id": 6}]])

    def get_updates(token, offset, timeout):
        try:
            return next(batches)
        except StopIteration:
            raise asyncio.CancelledError  # exit the loop after the one batch

    monkeypatch.setattr(poller.client, "get_updates", get_updates)

    async def handle(u):
        handled.append(u["update_id"])

    monkeypatch.setattr(poller.service, "handle_update", handle)

    with pytest.raises(asyncio.CancelledError):
        await poller._run()
    assert handled == [5, 6]
    assert advanced == [6, 7]  # update_id + 1, after dispatch


async def test_cancelled_update_is_not_acknowledged(monkeypatch):
    # A shutdown (CancelledError) mid-update must NOT advance the offset, so Telegram
    # redelivers it next boot rather than silently dropping it.
    _leader(monkeypatch)
    advanced = []
    monkeypatch.setattr(poller.store, "advance_offset", lambda n: advanced.append(n))
    monkeypatch.setattr(poller.client, "get_updates", lambda t, o, to: [{"update_id": 9}])

    async def handle(u):
        raise asyncio.CancelledError

    monkeypatch.setattr(poller.service, "handle_update", handle)
    with pytest.raises(asyncio.CancelledError):
        await poller._run()
    assert advanced == []  # the interrupted update was left for redelivery


async def test_409_triggers_delete_webhook(monkeypatch):
    _leader(monkeypatch)
    deleted = []
    monkeypatch.setattr(poller.client, "delete_webhook", lambda token, drop: deleted.append(token))

    def get_updates(token, offset, timeout):
        raise poller.client.TelegramError("conflict", status=409)

    monkeypatch.setattr(poller.client, "get_updates", get_updates)

    async def sleep_stop(_secs):
        raise asyncio.CancelledError  # exit after the first backoff

    monkeypatch.setattr(poller.asyncio, "sleep", sleep_stop)
    with pytest.raises(asyncio.CancelledError):
        await poller._run()
    assert deleted == ["TESTTOKEN"]  # self-heal removed any webhook


async def test_retry_after_used_as_sleep(monkeypatch):
    _leader(monkeypatch)

    def get_updates(token, offset, timeout):
        raise poller.client.TelegramError("rate", status=429, retry_after=7)

    monkeypatch.setattr(poller.client, "get_updates", get_updates)
    slept = []

    async def sleep_stop(secs):
        slept.append(secs)
        raise asyncio.CancelledError

    monkeypatch.setattr(poller.asyncio, "sleep", sleep_stop)
    with pytest.raises(asyncio.CancelledError):
        await poller._run()
    assert slept == [7]  # honored Telegram's retry_after over the computed backoff


async def test_transient_db_error_does_not_kill_the_loop(monkeypatch):
    # A store.* error must be caught and backed off, NOT propagate out of _run (which
    # would permanently disable Telegram). We prove it by making get_offset raise, then
    # exit via the backoff sleep — the loop reached the broad handler rather than dying.
    monkeypatch.setattr(poller, "_acquire_lock", lambda: None)
    monkeypatch.setattr(poller.store, "get_bot_token", lambda: "TESTTOKEN")

    def boom():
        raise RuntimeError("transient db blip")

    monkeypatch.setattr(poller.store, "get_offset", boom)
    slept = []

    async def sleep_stop(secs):
        slept.append(secs)
        raise asyncio.CancelledError

    monkeypatch.setattr(poller.asyncio, "sleep", sleep_stop)
    with pytest.raises(asyncio.CancelledError):
        await poller._run()
    # We exited via the backoff sleep (broad except), not via the RuntimeError escaping.
    assert slept and slept[0] == poller._MIN_BACKOFF
