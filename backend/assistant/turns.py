"""Detached assistant turns (#282): a Baker turn outlives the HTTP response that started it.

Before this the tool loop WAS the response — ``StreamingResponse(engine.chat(...))`` — so
a browser that dropped (tab closed, phone locked, network blip) cancelled the turn mid
flight. Now ``POST /api/assistant/chat`` hands ``engine.chat(...)`` to this module, which
drives it as a task the server owns and gives every frame a ``seq``. Two delivery paths,
never merged:

* **Origin stream** — the POST that started the turn tails an in-memory queue: every
  frame, at once. If that client leaves, the queue is dropped and the turn carries on.
* **Log attach** — ``GET /api/assistant/turns/{id}/events?after=N`` polls ONLY the
  committed log (``chat_turns`` + ``chat_turn_events``). That is all a returning tab, or
  a thread opened from the history list, needs.

One writer, one transaction per flush: buffered frames + the lease (``heartbeat_at``) +
the conversation id, and on the last flush the final status too — so a non-running row
always has its terminal frame committed beside it, and the origin gets its terminal frame
only after that commit (a client that re-reads the conversation on ``done`` never finds
its own turn still running). If that final write cannot reach Postgres the origin is
released after one attempt anyway, and the row is judged dead later.

One authorization rule: the turn row stores the seat that started it, and every turn
route answers 404 — never 403 — to anyone else. Conversations are owner-only with no
admin override (#191), so "started the turn" and "may read its conversation" are the
same seat.

Finalization, one path per cause:

* Stop (``POST /turns/{id}/cancel``) and lifespan shutdown cancel the task with a reason;
  closing the engine generator stops the provider stream / tool loop at its current
  ``await`` and the runner publishes ``{"type": "done", "stopped": true, "reason": …}``.
  A tool already running in a worker thread cannot be interrupted and may still finish.
* A process killed mid-turn (a redeploy) stops heartbeating. Any reader — an attach, the
  conversation GET, the 60 s ``maintenance_tick`` sweep — judges a lease older than
  ``STALE_AFTER_S`` dead in ONE transaction (status flip + a terminal ``error`` frame with
  ``dead_turn: true``). A turn this process still owns is never judged: with one worker
  (``gunicorn --workers 1``) a local task is alive by definition, however long Postgres
  was unreachable.
* Neither leaves a new transcript row. The engine already saved every completed
  iteration; the in-progress one is lost, exactly as before #282.

If the ``chat_turns`` insert fails, chat stays up: that one turn runs the pre-#282 way, as
the response itself (no ``turn_start``, no ``seq``, not reattachable).

Deliberately NOT ported from upstream: multi-instance lease handoff and the pause fence
(one worker, no CPU throttle), log redaction (no private-tool concept here, and the log's
only reader is the turn's owner), the saved stop / dead-turn note.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from core import postgres
from providers.base import _sse

logger = logging.getLogger(__name__)

HEARTBEAT_S = 5.0       # lease stamp cadence while a turn is silent
STALE_AFTER_S = 60.0    # 12 missed beats; wide so a Postgres hiccup is not a death
DB_FLUSH_S = 0.15       # log write cadence — the lag a log reader sees behind the origin
REMOTE_POLL_S = 0.25    # log attach poll
ATTACH_PING_S = 15.0    # keepalive on a silent stream (proxies cut idle connections)
ATTACH_MAX_S = 540.0    # close a long stream with a `reattach` frame; the client reopens
RETENTION_H = 24        # a finished turn's row and frames are transport, not the record
MAX_BUFFERED = 2000     # unflushed frames kept while Postgres is unreachable
FINAL_FLUSH_TRIES = 20
DETACHED_LOG_S = 30.0

TERMINAL = ("done", "error")
STOP_REASON = "you pressed Stop"
RESTART_REASON = "the server was restarting"
DEAD_FRAME = {
    "type": "error", "dead_turn": True,
    "error": "The server running this turn stopped before it finished.",
}

_mono = time.monotonic


async def _db(fn, *args):
    return await asyncio.to_thread(fn, *args)


def _utc_iso(value) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(timezone.utc).isoformat()


def valid_turn_id(value) -> str | None:
    """The client-minted id, canonicalised, or None when it is not a UUID."""
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


class TurnNotFound(Exception):
    """The turn does not exist for this caller. Routes answer 404, never 403."""


def strip_nul(value):
    """``value`` without NUL characters in any string, key or value (jsonb cannot hold them).

    Done on the Python values, before ``json.dumps``: replacing the escaped text afterwards
    would also eat a literal backslash followed by ``u0000``.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    return value


class TurnStore:
    """The Postgres seam. Sync; called through ``_db``. Tests swap in a memory twin."""

    def insert(self, turn_id: str, user_id: int) -> dict | None:
        """The new row, or None when the id already exists (a re-sent POST)."""
        return postgres.pg_fetchone(
            "INSERT INTO chat_turns (id, user_id) VALUES (%s, %s)"
            " ON CONFLICT (id) DO NOTHING RETURNING started_at",
            (turn_id, user_id),
        )

    def get(self, turn_id: str) -> dict | None:
        return postgres.pg_fetchone(
            "SELECT *, heartbeat_at < now() - make_interval(secs => %s) AS stale"
            " FROM chat_turns WHERE id = %s",
            (STALE_AFTER_S, turn_id),
        )

    def flush(self, turn_id: str, frames: list[dict], conversation_id: str | None,
              final_status: str | None) -> dict | None:
        """Lease stamp + frame batch (+ final status) in one transaction.

        Returns the cancel mailbox, or None when the turn is no longer ``running`` (it was
        judged dead) — nothing is written then.
        """
        with postgres.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chat_turns SET heartbeat_at = now(),"
                " last_seq = GREATEST(last_seq, %s),"
                " conversation_id = COALESCE(%s, conversation_id),"
                " status = COALESCE(%s, status),"
                " finished_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END"
                " WHERE id = %s AND status = 'running'"
                " RETURNING cancel_requested_at, cancel_reason",
                (frames[-1]["seq"] if frames else -1, conversation_id,
                 final_status, final_status, turn_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            if frames:
                # ON CONFLICT: a retry after a commit whose ack was lost must not wedge.
                cur.execute(
                    "INSERT INTO chat_turn_events (turn_id, seq, event)"
                    " SELECT %s, (e->>'seq')::int, e FROM jsonb_array_elements(%s::jsonb) AS e"
                    " ON CONFLICT (turn_id, seq) DO NOTHING",
                    (turn_id, json.dumps(strip_nul(frames))),
                )
            return {"cancel_requested_at": row[0], "cancel_reason": row[1]}

    def read_events(self, turn_id: str, after: int) -> list[dict]:
        rows = postgres.pg_fetchall(
            "SELECT event FROM chat_turn_events WHERE turn_id = %s AND seq > %s ORDER BY seq",
            (turn_id, after),
        )
        return [r["event"] for r in rows]

    def request_cancel(self, turn_id: str, reason: str) -> bool:
        return postgres.pg_execute(
            "UPDATE chat_turns SET cancel_requested_at = COALESCE(cancel_requested_at, now()),"
            " cancel_reason = COALESCE(cancel_reason, %s) WHERE id = %s AND status = 'running'",
            (reason, turn_id),
        ) > 0

    def judge_dead(self, turn_id: str, stale_after_s: float) -> bool:
        """Judge a stale turn dead: status flip and terminal frame in ONE transaction.

        False when it is not stale or is already judged (the row lock serialises judges).
        """
        with postgres.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chat_turns SET status = 'dead', finished_at = now(), last_seq = last_seq + 1"
                " WHERE id = %s AND status = 'running'"
                " AND heartbeat_at < now() - make_interval(secs => %s)"
                " RETURNING last_seq",
                (turn_id, stale_after_s),
            )
            row = cur.fetchone()
            if row is None:
                return False
            cur.execute(
                "INSERT INTO chat_turn_events (turn_id, seq, event) VALUES (%s, %s, %s)",
                (turn_id, row[0], json.dumps({**DEAD_FRAME, "seq": row[0]})),
            )
            return True

    def stale_running(self, stale_after_s: float, limit: int = 50) -> list[str]:
        rows = postgres.pg_fetchall(
            "SELECT id FROM chat_turns WHERE status = 'running'"
            " AND heartbeat_at < now() - make_interval(secs => %s)"
            " ORDER BY started_at, id LIMIT %s",
            (stale_after_s, limit),
        )
        return [r["id"] for r in rows]

    def running_for(self, conversation_id: str) -> list[dict]:
        return postgres.pg_fetchall(
            "SELECT id, started_at, last_seq,"
            " heartbeat_at < now() - make_interval(secs => %s) AS stale"
            " FROM chat_turns WHERE conversation_id = %s AND status = 'running'"
            " ORDER BY started_at, id",
            (STALE_AFTER_S, conversation_id),
        )

    def prune(self, retention_h: int) -> int:
        """Delete turns that ended more than ``retention_h`` ago (frames cascade)."""
        return postgres.pg_execute(
            "DELETE FROM chat_turns WHERE finished_at < now() - make_interval(hours => %s)",
            (retention_h,),
        )


class TurnRun:
    """One turn this process owns."""

    def __init__(self, turn_id: str):
        self.turn_id = turn_id
        self.origin: asyncio.Queue | None = asyncio.Queue()
        self.held: list[dict] = []       # terminal frame(s), owed to the origin after the final flush
        self.buffer: list[dict] = []     # frames not yet committed to the log
        self.next_seq = 0
        self.conversation_id: str | None = None
        self.final_status: str | None = None
        self.finished = asyncio.Event()
        self.wake = asyncio.Event()
        self.flush_lock = asyncio.Lock()
        self.fenced = False
        self.cancelling = False
        self.cancel_reason: str | None = None
        self.db_warned = False
        self.pump: asyncio.Task | None = None
        self.task: asyncio.Task | None = None

    def publish(self, event: dict) -> None:
        event["seq"] = self.next_seq
        self.next_seq += 1
        if event.get("type") == "conversation_id":
            self.conversation_id = event.get("id")
        self.buffer.append(event)
        if len(self.buffer) > MAX_BUFFERED:
            del self.buffer[0]  # Postgres has been down a long time; the newest frame is kept
        if self.held or event.get("type") in TERMINAL:
            self.held.append(event)
        elif self.origin is not None:
            self.origin.put_nowait(event)

    def release_held(self) -> None:
        """Hand the origin its terminal frame and close it. Idempotent."""
        if self.origin is not None:
            for event in self.held:
                self.origin.put_nowait(event)
            self.origin.put_nowait(None)
        self.held = []
        self.origin = None


class TurnRunner:
    def __init__(self, store: TurnStore):
        self.store = store
        self._runs: dict[str, TurnRun] = {}  # strong refs: a bare task can be collected

    # ── start ───────────────────────────────────────────────────────

    async def start(self, *, turn_id, user_id: int, chat):
        """Start a detached turn; return the SSE generator for the POST's response.

        ``chat()`` is the route's own ``engine.chat(...)`` call, deferred so a re-sent id
        never starts a second turn: the same seat attaches to the existing one, anyone
        else gets ``TurnNotFound``.
        """
        tid = valid_turn_id(turn_id) or str(uuid.uuid4())
        try:
            row = await _db(self.store.insert, tid, user_id)
        except Exception:
            logger.warning(
                "turns: could not record turn %s — running it attached to the request",
                tid, exc_info=True,
            )
            return chat()
        if row is None:
            existing = await _db(self.store.get, tid)
            if not existing or existing["user_id"] != user_id:
                raise TurnNotFound(tid)
            return self.attach(tid)
        run = TurnRun(tid)
        run.publish({"type": "turn_start", "turn_id": tid, "started_at": _utc_iso(row["started_at"])})
        self._runs[tid] = run
        run.task = asyncio.create_task(self._run(run, chat))
        return self._origin_stream(run, run.origin)

    async def _run(self, run: TurnRun, chat) -> None:
        run.pump = asyncio.create_task(self._pump(run, chat))
        writer = asyncio.create_task(self._writer(run))
        try:
            run.final_status = await run.pump
        except asyncio.CancelledError:
            # Cancelled before its first step, so `_pump`'s own handler never ran.
            if not run.fenced:
                run.publish({"type": "done", "stopped": True,
                             "reason": run.cancel_reason or STOP_REASON})
            run.final_status = "dead" if run.fenced else "stopped"
        finally:
            run.finished.set()
            run.wake.set()
            with contextlib.suppress(BaseException):
                await writer
            run.release_held()
            self._runs.pop(run.turn_id, None)

    async def _pump(self, run: TurnRun, chat) -> str:
        """Drive the turn; return its final status. The only task Stop cancels."""
        if run.cancelling:  # stopped before this task first ran: the engine never starts
            run.publish({"type": "done", "stopped": True, "reason": run.cancel_reason})
            return "stopped"
        agen = chat()
        try:
            async for raw in agen:
                run.publish(json.loads(raw[6:]))  # every engine frame is `_sse`-shaped
            return "finished"
        except asyncio.CancelledError:
            # Closing the generator delivers GeneratorExit at the engine's current await,
            # which is what actually stops the provider stream / tool loop.
            with contextlib.suppress(BaseException):
                await agen.aclose()
            if run.fenced:
                return "dead"  # never written: the fenced owner's flush matches zero rows
            run.publish({"type": "done", "stopped": True, "reason": run.cancel_reason or STOP_REASON})
            return "stopped"
        except Exception:
            # Unreachable in practice — engine.chat converts in-band failures into an
            # `error` frame — kept so a stream can never end without a terminal frame.
            logger.exception("turns: turn %s crashed outside the engine", run.turn_id)
            run.publish({"type": "error",
                         "error": "The assistant hit an internal error and this turn ended early."})
            return "dead"

    # ── the log writer ──────────────────────────────────────────────

    async def _writer(self, run: TurnRun) -> None:
        last_flush = last_note = _mono()
        noted_seq = 0
        tries = 0
        while True:
            ending = run.finished.is_set()
            if ending or run.buffer or _mono() - last_flush >= HEARTBEAT_S:
                ok = await self._flush(run, final=ending)
                last_flush = _mono()
                if ending:
                    # The origin's terminal frame waits for ONE final attempt, not for
                    # Postgres to come back.
                    run.release_held()
                    tries += 1
                    if ok or tries >= FINAL_FLUSH_TRIES:
                        if not ok:
                            logger.error(
                                "turns: turn %s ended but its log could not be closed — "
                                "it will be judged dead", run.turn_id)
                        return
            if run.origin is None and _mono() - last_note >= DETACHED_LOG_S:
                # The line that shows a detached turn is still making progress.
                logger.info("turns: %s detached — %d frames in last %.0fs",
                            run.turn_id, run.next_seq - noted_seq, _mono() - last_note)
                last_note, noted_seq = _mono(), run.next_seq
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(run.wake.wait(), DB_FLUSH_S)
            run.wake.clear()

    async def _flush(self, run: TurnRun, final: bool = False) -> bool:
        """Write the buffer and the lease. True when committed (or the owner is fenced)."""
        async with run.flush_lock:
            if run.fenced:
                return True
            batch = list(run.buffer)  # a copy: frames leave the buffer only once committed
            try:
                row = await _db(self.store.flush, run.turn_id, batch, run.conversation_id,
                                run.final_status if final else None)
            except Exception:
                if not run.db_warned:
                    run.db_warned = True
                    logger.warning("turns: log write failed for turn %s — buffering",
                                   run.turn_id, exc_info=True)
                return False
            if row is None:
                # Judged dead before this process could stop it (or already finalized).
                run.fenced = True
                logger.warning("turns: turn %s was finalized elsewhere — stopping", run.turn_id)
                if run.pump is not None and not run.pump.done():
                    run.pump.cancel()
                return True
            if batch:
                run.buffer = [f for f in run.buffer if f["seq"] > batch[-1]["seq"]]
            if row["cancel_requested_at"] and not final:
                # A cancel noted while this process had no run for it yet (Stop pressed
                # before the start committed) is read back on the lease write.
                self._cancel_local(run, row["cancel_reason"] or STOP_REASON)
            return True

    # ── delivery ────────────────────────────────────────────────────

    async def _origin_stream(self, run: TurnRun, queue: asyncio.Queue):
        started = _mono()
        last = -1
        try:
            while True:
                left = ATTACH_MAX_S - (_mono() - started)
                if left <= 0:
                    yield _sse({"type": "reattach", "after": last})
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), min(ATTACH_PING_S, left))
                except asyncio.TimeoutError:
                    if left > ATTACH_PING_S:
                        yield _sse({"type": "ping"})
                    continue
                if event is None:
                    return
                last = event["seq"]
                yield _sse(event)
        finally:
            # The client left (or the stream hit its cap): stop queueing for it. The turn
            # itself is untouched — that is the point of this module.
            if run.origin is queue:
                run.origin = None

    async def attach(self, turn_id: str, after: int = -1):
        """Replay committed frames with seq > ``after``, then tail the log to the end."""
        started = last_sent = _mono()
        last = after
        terminal = False
        while True:
            # Row BEFORE events: the final flush commits the status with the last frames,
            # so a non-running row means this read of the log is complete.
            row = await _db(self.store.get, turn_id)
            events = await _db(self.store.read_events, turn_id, last) if row else []
            for event in events:
                last = event["seq"]
                terminal = terminal or event.get("type") in TERMINAL
                yield _sse(event)
            if events:
                last_sent = _mono()
            if row is None or row["status"] != "running":
                break
            if row["stale"]:
                await _db(self.judge, turn_id)
            if _mono() - started >= ATTACH_MAX_S:
                yield _sse({"type": "reattach", "after": last})
                return
            if _mono() - last_sent >= ATTACH_PING_S:
                yield _sse({"type": "ping"})
                last_sent = _mono()
            await asyncio.sleep(REMOTE_POLL_S)
        if not terminal:
            # Defensive: every status writer commits a terminal frame with it.
            yield _sse({"type": "error", "seq": last + 1,
                        "error": "This turn ended without a final message."})

    # ── cancel / judge / housekeeping ───────────────────────────────

    def _cancel_local(self, run: TurnRun, reason: str) -> None:
        if run.cancelling or (run.pump is not None and run.pump.done()):
            return
        run.cancelling = True
        run.cancel_reason = reason
        if run.pump is not None:
            run.pump.cancel()  # else `_pump` sees `cancelling` before it starts the engine

    async def request_cancel(self, turn_id: str, reason: str) -> bool:
        """Stop a turn. True when it was running and the stop was taken."""
        failed = None
        try:
            ok = await _db(self.store.request_cancel, turn_id, reason)
        except Exception as exc:
            logger.warning("turns: could not record the cancel of %s", turn_id, exc_info=True)
            ok, failed = False, exc
        run = self._runs.get(turn_id)
        if run is not None and not (run.pump is not None and run.pump.done()):
            self._cancel_local(run, reason)  # the owner needs no mailbox
            return True
        if failed is not None:
            raise failed  # not ours and not noted: an error, so the client asks again
        return ok

    def judge(self, turn_id: str) -> bool:
        """Judge one turn dead if its lease is stale. Sync. Never raises.

        A turn this process still runs is never judged: with one worker, a local task is
        alive however long its lease writes have been failing.
        """
        if turn_id in self._runs:
            return False
        try:
            return self.store.judge_dead(turn_id, STALE_AFTER_S)
        except Exception:
            logger.warning("turns: judging turn %s failed — will retry", turn_id, exc_info=True)
            return False

    async def visible_turn(self, turn_id: str, *, user_id: int) -> dict | None:
        """The turn row when this seat started it, else None (routes answer 404).

        A store failure RAISES rather than answering None: a missing turn sends the client
        to its fallback for good, an outage must only make it retry.
        """
        row = await _db(self.store.get, turn_id)
        return row if row and row["user_id"] == user_id else None

    def running_turn_for(self, conversation_id: str) -> dict | None:
        """The OLDEST live turn on a conversation, for ``GET /conversations/{id}``.

        Stale ones are judged on the way. Sync. Never raises — a conversation read must
        not fail because the turn log is unreachable.
        """
        try:
            for row in self.store.running_for(conversation_id):
                if row["stale"] and self.judge(row["id"]):
                    continue
                return {"turn_id": row["id"], "started_at": _utc_iso(row["started_at"]),
                        "last_seq": row["last_seq"]}
        except Exception:
            logger.debug("turns: running-turn lookup failed", exc_info=True)
        return None

    def sweep(self) -> tuple[int, int]:
        """Judge abandoned turns and prune old ones. Returns (judged, pruned)."""
        judged = sum(self.judge(tid) for tid in self.store.stale_running(STALE_AFTER_S))
        return judged, self.store.prune(RETENTION_H)

    async def shutdown(self) -> None:
        """Lifespan shutdown: stop this process's turns with the true reason.

        Best effort — a turn that does not finish within the grace is judged dead by the
        next reader.
        """
        tasks = []
        for run in list(self._runs.values()):
            self._cancel_local(run, RESTART_REASON)
            tasks.append(run.task)
        if tasks:
            await asyncio.wait(tasks, timeout=5)


runner = TurnRunner(TurnStore())
