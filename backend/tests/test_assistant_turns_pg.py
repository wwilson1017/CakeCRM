"""Real-Postgres integration for detached turns (#282): the log tables and their SQL.

Proves what the memory twin in ``test_assistant_turns`` cannot: the migration applies,
a flush commits frames + lease + status in one transaction, a NUL never wedges the log,
judging is once-only and lands the terminal frame with the status, pruning cascades to
the frames, and a dropped turn finishes and replays through the real store.

Marked ``integration`` and excluded from the default no-DB run.
"""

import asyncio
import json
import os

import psycopg2
import pytest

from assistant import turns
from providers.base import _sse

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")
A, B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_turns_{os.getpid()}"
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()

    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{dbname}"
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = dsn
    postgres.close_pool()
    postgres.init_pool()
    postgres.run_migrations()
    yield dsn

    postgres.close_pool()
    if prev is not None:
        os.environ["DATABASE_URL"] = prev
    else:
        os.environ.pop("DATABASE_URL", None)
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.fixture
def store(pg_db):
    from core.postgres import pg_execute
    pg_execute("TRUNCATE chat_turn_events, chat_turns")
    return turns.TurnStore()


def _age(turn_id, column, interval):
    from core.postgres import pg_execute
    pg_execute(f"UPDATE chat_turns SET {column} = now() - interval '{interval}' WHERE id = %s", (turn_id,))


def _frames(*types, start=0):
    return [{"type": t, "seq": start + i} for i, t in enumerate(types)]


def test_migration_creates_the_log_tables_and_indexes(store):
    from core.postgres import pg_fetchall
    names = {r["indexname"] for r in pg_fetchall(
        "SELECT indexname FROM pg_indexes WHERE tablename IN ('chat_turns', 'chat_turn_events')"
        " ORDER BY indexname")}
    assert {"idx_chat_turns_running", "idx_chat_turns_finished_at"} <= names


def test_insert_flush_and_read_round_trip(store):
    assert store.insert(A, 7)["started_at"]
    assert store.insert(A, 7) is None  # a re-sent id
    assert store.flush(A, _frames("turn_start", "conversation_id", "text"), "c1", None) == {
        "cancel_requested_at": None, "cancel_reason": None}
    assert [e["seq"] for e in store.read_events(A, 0)] == [1, 2]
    store.flush(A, [], None, None)  # a bare lease write keeps the conversation id
    row = store.get(A)
    assert (row["last_seq"], row["conversation_id"], row["user_id"], row["stale"]) == (2, "c1", 7, False)


def test_a_nul_is_stripped_and_a_literal_escape_survives(store):
    store.insert(A, 1)
    store.flush(A, [{"type": "text", "seq": 0, "text": "a\x00b \\u0000"}], None, None)
    store.flush(A, _frames("done", start=1), None, "finished")  # the log is not wedged
    events = store.read_events(A, -1)
    assert events[0]["text"] == "ab \\u0000" and events[1]["type"] == "done"


def test_final_flush_commits_the_status_with_its_terminal_frame(store):
    store.insert(A, 1)
    store.flush(A, _frames("turn_start", "done"), None, "finished")
    row = store.get(A)
    assert row["status"] == "finished" and row["finished_at"]
    assert store.read_events(A, -1)[-1]["type"] == "done"
    assert store.flush(A, _frames("text", start=2), None, None) is None  # fenced: nothing written
    assert len(store.read_events(A, -1)) == 2


def test_the_lease_write_carries_the_cancel_mailbox(store):
    store.insert(A, 1)
    assert store.request_cancel(A, "you pressed Stop") is True
    assert store.request_cancel(A, "second reason") is True  # first reason wins
    mailbox = store.flush(A, [], None, None)
    assert mailbox["cancel_requested_at"] and mailbox["cancel_reason"] == "you pressed Stop"
    store.flush(A, [], None, "stopped")
    assert store.request_cancel(A, "late") is False


def test_only_a_stale_running_turn_is_judged_and_only_once(store):
    store.insert(A, 1)
    store.flush(A, _frames("turn_start", "text"), None, None)
    assert store.judge_dead(A, turns.STALE_AFTER_S) is False
    _age(A, "heartbeat_at", "2 minutes")
    assert store.judge_dead(A, turns.STALE_AFTER_S) is True
    assert store.judge_dead(A, turns.STALE_AFTER_S) is False
    row = store.get(A)
    assert row["status"] == "dead" and row["finished_at"] and row["last_seq"] == 2
    assert store.read_events(A, 1) == [{**turns.DEAD_FRAME, "seq": 2}]


def test_running_for_and_stale_running(store):
    store.insert(A, 1)
    store.insert(B, 1)
    for t in (A, B):
        store.flush(t, [], "c1", None)
    _age(A, "started_at", "1 minute")
    _age(A, "heartbeat_at", "2 minutes")
    rows = store.running_for("c1")
    assert [(r["id"], r["stale"]) for r in rows] == [(A, True), (B, False)]
    assert store.stale_running(turns.STALE_AFTER_S) == [A]


def test_prune_deletes_turns_that_ended_over_a_day_ago_and_their_frames(store):
    from core.postgres import pg_fetchone
    for t in (A, B):
        store.insert(t, 1)
        store.flush(t, _frames("turn_start", "done"), None, "finished")
    _age(A, "finished_at", "25 hours")
    _age(B, "finished_at", "23 hours")
    assert store.prune(turns.RETENTION_H) == 1
    assert store.get(A) is None and store.get(B)
    assert pg_fetchone("SELECT count(*) AS n FROM chat_turn_events WHERE turn_id = %s", (A,))["n"] == 0


async def test_a_dropped_turn_finishes_and_a_second_reader_replays_it(store, monkeypatch):
    monkeypatch.setattr(turns, "DB_FLUSH_S", 0.01)
    monkeypatch.setattr(turns, "REMOTE_POLL_S", 0.01)
    gate = asyncio.Event()

    def chat():
        async def gen():
            yield _sse({"type": "conversation_id", "id": "c1"})
            await gate.wait()
            yield _sse({"type": "text", "text": "Hello"})
            yield _sse({"type": "done", "model": "m"})
        return gen()

    runner = turns.TurnRunner(store)
    origin = await runner.start(turn_id=A, user_id=1, chat=chat)
    await origin.__anext__()
    await origin.aclose()  # the browser left
    gate.set()
    replay = [json.loads(line[6:]) async for line in turns.TurnRunner(store).attach(A)]
    assert [f["type"] for f in replay] == ["turn_start", "conversation_id", "text", "done"]
    assert store.get(A)["status"] == "finished"
