"""Detached assistant turns (#282): the turn outlives the browser that started it.

`assistant/turns.py` owns the turn; these tests pin its contract from the outside,
DB-free:

  * every frame carries a monotonic `seq`, opening with `turn_start`;
  * closing the origin stream does not stop the turn, and a second reader replays it
    from the log alone;
  * the origin's terminal frame arrives only after the final status is committed;
  * Stop, shutdown and a judged-dead turn each end on exactly one terminal frame;
  * a turn is visible only to the seat that started it.

`MemoryTurnStore` is `TurnStore`'s in-memory twin (same methods, same atomicity). The
SQL itself is proved in `test_assistant_turns_pg.py`. Staleness is the store's own
injected `now`, never the real clock.
"""

import asyncio
import copy
import json
import logging
import threading
import uuid

import pytest

from assistant import turns
from providers.base import _sse

STARTED_AT = "2026-10-10T02:00:00+00:00"
TID = "11111111-1111-4111-8111-111111111111"


class MemoryTurnStore:
    """In-memory `TurnStore`. `now` is the store's clock, set by the test."""

    def __init__(self):
        self.turns: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}
        self.lock = threading.Lock()
        self.now = 0.0
        self.fail: set[str] = set()      # method names that raise, as a DB outage would
        self.finished_at: dict[str, float] = {}
        self.flush_gate: threading.Event | None = None  # a final flush blocks on it when set

    def _maybe_fail(self, name):
        if name in self.fail:
            raise RuntimeError(f"db down ({name})")

    def _stale(self, row):
        return self.now - row["heartbeat"] > turns.STALE_AFTER_S

    def insert(self, turn_id, user_id):
        self._maybe_fail("insert")
        with self.lock:
            if turn_id in self.turns:
                return None
            self.turns[turn_id] = {
                "id": turn_id, "user_id": user_id, "status": "running",
                "conversation_id": None, "started_at": STARTED_AT, "heartbeat": self.now,
                "last_seq": -1, "cancel_requested_at": None, "cancel_reason": None,
            }
            self.events[turn_id] = []
            return {"started_at": STARTED_AT}

    def get(self, turn_id):
        self._maybe_fail("get")
        with self.lock:
            row = self.turns.get(turn_id)
            return {**row, "stale": self._stale(row)} if row else None

    def flush(self, turn_id, frames, conversation_id, final_status):
        self._maybe_fail("flush")
        if final_status and self.flush_gate is not None:
            self.flush_gate.wait(5)
        with self.lock:
            row = self.turns.get(turn_id)
            if not row or row["status"] != "running":
                return None
            row["heartbeat"] = self.now
            if frames:
                row["last_seq"] = max(row["last_seq"], frames[-1]["seq"])
                seen = {e["seq"] for e in self.events[turn_id]}
                self.events[turn_id] += [turns.strip_nul(copy.deepcopy(f))
                                         for f in frames if f["seq"] not in seen]
            row["conversation_id"] = conversation_id or row["conversation_id"]
            if final_status:
                row["status"] = final_status
                self.finished_at[turn_id] = self.now
            return {"cancel_requested_at": row["cancel_requested_at"],
                    "cancel_reason": row["cancel_reason"]}

    def read_events(self, turn_id, after):
        self._maybe_fail("read_events")
        with self.lock:
            return [copy.deepcopy(e) for e in self.events.get(turn_id, []) if e["seq"] > after]

    def request_cancel(self, turn_id, reason):
        self._maybe_fail("request_cancel")
        with self.lock:
            row = self.turns.get(turn_id)
            if not row or row["status"] != "running":
                return False
            row["cancel_requested_at"] = row["cancel_requested_at"] or STARTED_AT
            row["cancel_reason"] = row["cancel_reason"] or reason
            return True

    def judge_dead(self, turn_id, stale_after_s):
        self._maybe_fail("judge_dead")
        with self.lock:  # the row lock: concurrent judges serialise, losers see `dead`
            row = self.turns.get(turn_id)
            if not row or row["status"] != "running" or not self._stale(row):
                return False
            row["status"] = "dead"
            row["last_seq"] += 1
            self.finished_at[turn_id] = self.now
            self.events[turn_id].append({**turns.DEAD_FRAME, "seq": row["last_seq"]})
            return True

    def stale_running(self, stale_after_s, limit=50):
        with self.lock:
            return [t for t, r in self.turns.items() if r["status"] == "running" and self._stale(r)]

    def running_for(self, conversation_id):
        self._maybe_fail("running_for")
        with self.lock:
            return [
                {"id": t, "started_at": r["started_at"], "last_seq": r["last_seq"],
                 "stale": self._stale(r)}
                for t, r in self.turns.items()
                if r["status"] == "running" and r["conversation_id"] == conversation_id
            ]

    def prune(self, retention_h):
        with self.lock:
            old = [t for t, at in self.finished_at.items() if self.now - at > retention_h * 3600]
            for t in old:
                del self.turns[t], self.events[t], self.finished_at[t]
            return len(old)


def seed(store, turn_id=TID, *, user_id=1, conversation_id=None,
         events=("turn_start", "text"), status="running"):
    """A turn as the log has it — for readers that never saw it start."""
    store.insert(turn_id, user_id)
    frames = [{"type": t, "seq": i, **({"text": "hi"} if t == "text" else {})}
              for i, t in enumerate(events)]
    store.flush(turn_id, frames, conversation_id, None if status == "running" else status)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """Tight cadences so a test waits milliseconds, not the production intervals."""
    monkeypatch.setattr(turns, "DB_FLUSH_S", 0.005)
    monkeypatch.setattr(turns, "REMOTE_POLL_S", 0.005)


class Script:
    """A scripted stand-in for `engine.chat`: frames, with gates to park on."""

    def __init__(self, *steps):
        self.steps = steps
        self.calls = 0
        self.closed = False

    def __call__(self):
        self.calls += 1

        async def gen():
            try:
                for step in self.steps:
                    if isinstance(step, asyncio.Event):
                        await step.wait()
                    else:
                        yield _sse(step)
            finally:
                self.closed = True
        return gen()


async def frames(gen) -> list[dict]:
    return [json.loads(line[6:]) async for line in gen]


async def _settle(runner, turn_id=TID):
    """Wait until the runner has let go of the turn (final flush done)."""
    for _ in range(400):
        if turn_id not in runner._runs:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("turn never finished")


HELLO = (
    {"type": "conversation_id", "id": "c1"},
    {"type": "text", "text": "Hello"},
    {"type": "done", "model": "m"},
)


# ── seq and the two delivery paths ─────────────────────────────────────────

async def test_every_frame_carries_a_monotonic_seq_and_turn_start_is_first():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    got = await frames(await runner.start(turn_id=TID, user_id=1, chat=Script(*HELLO)))
    assert [f["type"] for f in got] == ["turn_start", "conversation_id", "text", "done"]
    assert [f["seq"] for f in got] == [0, 1, 2, 3]
    assert got[0] == {"type": "turn_start", "turn_id": TID, "started_at": STARTED_AT, "seq": 0}
    await _settle(runner)
    assert store.events[TID] == got
    assert store.turns[TID]["status"] == "finished"
    assert store.turns[TID]["conversation_id"] == "c1"


async def test_disconnecting_the_origin_stream_does_not_cancel_the_turn():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    script = Script(HELLO[0], gate, *HELLO[1:])
    origin = await runner.start(turn_id=TID, user_id=1, chat=script)
    await origin.__anext__()  # turn_start
    await origin.aclose()     # the browser went away
    gate.set()
    await _settle(runner)
    assert store.turns[TID]["status"] == "finished"
    assert [e["type"] for e in store.events[TID]] == ["turn_start", "conversation_id", "text", "done"]


async def test_origin_gets_its_terminal_frame_only_after_the_status_is_committed():
    store = MemoryTurnStore()
    store.flush_gate = threading.Event()
    runner = turns.TurnRunner(store)
    origin = await runner.start(turn_id=TID, user_id=1, chat=Script(*HELLO))
    seen = [json.loads((await origin.__anext__())[6:])["type"] for _ in range(3)]
    assert seen == ["turn_start", "conversation_id", "text"]
    done = asyncio.ensure_future(origin.__anext__())
    await asyncio.sleep(0.05)
    assert not done.done() and store.turns[TID]["status"] == "running"
    store.flush_gate.set()
    assert json.loads((await done)[6:])["type"] == "done"
    assert store.turns[TID]["status"] == "finished"


async def test_attach_replays_then_tails_with_monotonic_seq():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    origin = await runner.start(turn_id=TID, user_id=1, chat=Script(HELLO[0], gate, *HELLO[1:]))
    await origin.aclose()
    await asyncio.sleep(0.03)  # turn_start + conversation_id are in the log
    reader = asyncio.ensure_future(frames(runner.attach(TID, 0)))
    await asyncio.sleep(0.02)
    gate.set()
    got = await reader
    assert [f["seq"] for f in got] == [1, 2, 3]
    assert got[-1]["type"] == "done"


async def test_a_second_reader_needs_only_the_log():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    await frames(await runner.start(turn_id=TID, user_id=1, chat=Script(*HELLO)))
    await _settle(runner)
    other = turns.TurnRunner(store)  # no in-memory run: the log is all it has
    got = await frames(other.attach(TID))
    assert [f["type"] for f in got] == ["turn_start", "conversation_id", "text", "done"]


async def test_attach_to_an_ended_turn_with_no_terminal_frame_still_ends_with_one():
    store = MemoryTurnStore()
    seed(store, status="finished")
    got = await frames(turns.TurnRunner(store).attach(TID))
    assert got[-1] == {"type": "error", "seq": 2, "error": "This turn ended without a final message."}


async def test_idle_streams_ping_and_pings_are_never_stored(monkeypatch):
    monkeypatch.setattr(turns, "ATTACH_PING_S", 0.02)
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    origin = await runner.start(turn_id=TID, user_id=1, chat=Script(gate, *HELLO))
    await origin.__anext__()
    assert json.loads((await origin.__anext__())[6:]) == {"type": "ping"}
    attached = runner.attach(TID)
    await attached.__anext__()  # turn_start
    assert json.loads((await attached.__anext__())[6:]) == {"type": "ping"}
    await attached.aclose()
    gate.set()
    await _settle(runner)
    assert all(e["type"] != "ping" for e in store.events[TID])


async def test_streams_close_with_a_reattach_frame_at_the_cap(monkeypatch):
    monkeypatch.setattr(turns, "ATTACH_MAX_S", 0.03)
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    got = await frames(await runner.start(turn_id=TID, user_id=1, chat=Script(gate, *HELLO)))
    assert got[-1] == {"type": "reattach", "after": 0}
    tail = await frames(runner.attach(TID))
    assert tail[-1] == {"type": "reattach", "after": 0}
    gate.set()
    await _settle(runner)
    assert store.turns[TID]["status"] == "finished"  # the cap closed streams, not the turn


# ── the turn id ────────────────────────────────────────────────────────────

async def test_a_resent_turn_id_attaches_for_the_same_seat_and_is_not_found_for_another():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    script = Script(*HELLO)
    first = await frames(await runner.start(turn_id=TID, user_id=1, chat=script))
    await _settle(runner)
    again = await frames(await runner.start(turn_id=TID, user_id=1, chat=script))
    assert again == first and script.calls == 1
    with pytest.raises(turns.TurnNotFound):
        await runner.start(turn_id=TID, user_id=2, chat=script)


@pytest.mark.parametrize("bad", [None, "nope", 123])
async def test_an_invalid_turn_id_is_replaced_by_a_server_minted_one(bad):
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    got = await frames(await runner.start(turn_id=bad, user_id=1, chat=Script(*HELLO)))
    minted = got[0]["turn_id"]
    assert str(uuid.UUID(minted)) == minted and minted in store.turns


async def test_turn_row_insert_failure_falls_back_to_the_attached_stream(caplog):
    store = MemoryTurnStore()
    store.fail = {"insert"}
    with caplog.at_level(logging.WARNING):
        got = await frames(await turns.TurnRunner(store).start(turn_id=TID, user_id=1, chat=Script(*HELLO)))
    assert got == [dict(f) for f in HELLO]  # no turn_start, no seq
    assert store.turns == {}
    assert "running it attached to the request" in caplog.text


async def test_a_failed_log_write_keeps_the_frames_and_retries(caplog):
    store = MemoryTurnStore()
    store.fail = {"flush"}
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    with caplog.at_level(logging.WARNING):
        origin = await runner.start(turn_id=TID, user_id=1, chat=Script(HELLO[0], gate, *HELLO[1:]))
        await origin.__anext__()
        await asyncio.sleep(0.03)
        assert store.events[TID] == []
        store.fail = set()
        gate.set()
        await frames(origin)
        await _settle(runner)
    assert [e["seq"] for e in store.events[TID]] == [0, 1, 2, 3]
    assert caplog.text.count("log write failed") == 1


# ── Stop, shutdown, death ──────────────────────────────────────────────────

async def test_cancel_publishes_done_stopped_and_commits_status_stopped():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    script = Script(HELLO[0], gate, *HELLO[1:])
    origin = await runner.start(turn_id=TID, user_id=1, chat=script)
    await origin.__anext__()
    await origin.__anext__()
    assert await runner.request_cancel(TID, turns.STOP_REASON) is True
    rest = await frames(origin)
    assert rest[-1] == {"type": "done", "stopped": True, "reason": turns.STOP_REASON, "seq": 2}
    await _settle(runner)
    assert store.turns[TID]["status"] == "stopped"
    assert script.closed  # the engine generator was closed, not abandoned


async def test_cancel_of_an_ended_turn_is_not_taken():
    store = MemoryTurnStore()
    seed(store, events=("turn_start", "done"), status="finished")
    assert await turns.TurnRunner(store).request_cancel(TID, turns.STOP_REASON) is False


async def test_a_cancel_that_could_not_be_noted_is_an_error_unless_the_owner_took_it():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    origin = await runner.start(turn_id=TID, user_id=1, chat=Script(gate, *HELLO))
    await origin.__anext__()
    store.fail = {"request_cancel"}
    assert await runner.request_cancel(TID, turns.STOP_REASON) is True
    await frames(origin)
    with pytest.raises(RuntimeError):
        await runner.request_cancel("22222222-2222-4222-8222-222222222222", turns.STOP_REASON)


async def test_a_cancel_noted_in_the_store_lands_on_the_next_lease_write():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    origin = await runner.start(turn_id=TID, user_id=1, chat=Script(gate, *HELLO))
    await origin.__anext__()
    store.request_cancel(TID, "noted before the owner saw it")  # bypassing the local run
    rest = await frames(origin)
    assert rest[-1]["stopped"] is True and rest[-1]["reason"] == "noted before the owner saw it"
    await _settle(runner)
    assert store.turns[TID]["status"] == "stopped"


async def test_shutdown_stops_local_turns_with_the_restart_reason():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    origin = await runner.start(turn_id=TID, user_id=1, chat=Script(asyncio.Event(), *HELLO))
    await origin.__anext__()
    await runner.shutdown()
    rest = await frames(origin)
    assert rest[-1]["reason"] == turns.RESTART_REASON
    assert runner._runs == {} and store.turns[TID]["status"] == "stopped"


async def test_a_stale_turn_is_judged_dead_exactly_once():
    store = MemoryTurnStore()
    seed(store)
    runner = turns.TurnRunner(store)
    assert runner.judge(TID) is False  # live lease
    store.now += turns.STALE_AFTER_S + 1
    assert runner.judge(TID) is True
    assert runner.judge(TID) is False
    assert store.turns[TID]["status"] == "dead"
    assert store.events[TID][-1] == {**turns.DEAD_FRAME, "seq": 2}


async def test_judge_failure_is_logged_and_retried(caplog):
    store = MemoryTurnStore()
    seed(store)
    store.now += turns.STALE_AFTER_S + 1
    store.fail = {"judge_dead"}
    with caplog.at_level(logging.WARNING):
        assert turns.TurnRunner(store).judge(TID) is False
    assert "will retry" in caplog.text and store.turns[TID]["status"] == "running"


async def test_a_turn_this_process_owns_is_never_judged_however_stale():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    gate = asyncio.Event()
    origin = await runner.start(turn_id=TID, user_id=1, chat=Script(gate, *HELLO))
    await origin.__anext__()
    store.fail = {"flush"}  # lease writes failing...
    store.now += turns.STALE_AFTER_S + 1  # ...long enough to look dead
    assert runner.judge(TID) is False
    assert runner.sweep() == (0, 0)
    store.fail = set()
    gate.set()
    await frames(origin)
    await _settle(runner)
    assert store.turns[TID]["status"] == "finished"


async def test_an_attached_reader_judges_a_stale_turn_and_ends():
    store = MemoryTurnStore()
    seed(store)
    store.now += turns.STALE_AFTER_S + 1
    got = await frames(turns.TurnRunner(store).attach(TID, 1))
    assert got == [{**turns.DEAD_FRAME, "seq": 2}]


async def test_an_owner_whose_turn_was_judged_dead_stops_and_writes_nothing_more():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    script = Script(asyncio.Event(), *HELLO)
    origin = await runner.start(turn_id=TID, user_id=1, chat=script)
    await origin.__anext__()
    await asyncio.sleep(0.02)
    store.now += turns.STALE_AFTER_S + 1
    assert store.judge_dead(TID, turns.STALE_AFTER_S)  # e.g. a previous process's judge
    await frames(origin)
    await _settle(runner)
    assert script.closed
    assert store.turns[TID]["status"] == "dead"
    assert store.events[TID][-1]["dead_turn"] is True


async def test_sweep_judges_abandoned_turns_and_prunes_old_ones():
    store = MemoryTurnStore()
    seed(store, "a")
    seed(store, "b", events=("turn_start", "done"), status="finished")
    store.now += turns.RETENTION_H * 3600 + 1
    runner = turns.TurnRunner(store)
    assert runner.sweep() == (1, 1)
    assert "b" not in store.turns and store.turns["a"]["status"] == "dead"


# ── visibility and the conversation read ──────────────────────────────────

async def test_visible_turn_is_the_starting_seat_only():
    store = MemoryTurnStore()
    seed(store, user_id=1)
    runner = turns.TurnRunner(store)
    assert (await runner.visible_turn(TID, user_id=1))["id"] == TID
    assert await runner.visible_turn(TID, user_id=2) is None
    assert await runner.visible_turn("nope", user_id=1) is None
    store.fail = {"get"}
    with pytest.raises(RuntimeError):  # an outage is not "no such turn"
        await runner.visible_turn(TID, user_id=1)


async def test_running_turn_for_reports_the_oldest_live_turn_and_judges_stale_ones():
    store = MemoryTurnStore()
    seed(store, "old", conversation_id="c1")
    store.now += turns.STALE_AFTER_S + 1
    seed(store, "new", conversation_id="c1")
    runner = turns.TurnRunner(store)
    assert runner.running_turn_for("c1") == {"turn_id": "new", "started_at": STARTED_AT, "last_seq": 1}
    assert store.turns["old"]["status"] == "dead"
    assert runner.running_turn_for("c2") is None


async def test_running_turn_lookup_never_fails_a_conversation_read():
    store = MemoryTurnStore()
    store.fail = {"running_for"}
    assert turns.TurnRunner(store).running_turn_for("c1") is None


def test_strip_nul_is_recursive_and_leaves_literal_escapes_alone():
    value = {"a\x00": ["x\x00y", {"k": "\\u0000 literal"}], "n": 1}
    assert turns.strip_nul(value) == {"a": ["xy", {"k": "\\u0000 literal"}], "n": 1}


async def test_a_stop_that_lands_before_the_turn_first_runs_still_ends_it():
    store = MemoryTurnStore()
    runner = turns.TurnRunner(store)
    script = Script(*HELLO)
    origin = await runner.start(turn_id=TID, user_id=1, chat=script)
    await runner.shutdown()  # cancels synchronously, before the turn's task first ran
    got = await frames(origin)
    assert [f["type"] for f in got] == ["turn_start", "done"] and got[-1]["reason"] == turns.RESTART_REASON
    await _settle(runner)
    assert store.turns[TID]["status"] == "stopped" and script.calls == 0
