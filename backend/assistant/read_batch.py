"""One tool batch's reads: run first, the audited ones side by side, each with a timeout (#372).

``engine._chat_impl`` calls ``run_reads`` once per tool batch, BEFORE it gates or runs any
write, then walks the model's order recording results. Two lanes:

* ``registry.parallel_reads`` (CRM and todo Postgres reads) run concurrently on
  ``_executor``. The worker thread only runs the handler (``registry.run_read_sync``); the
  deal bookkeeping waits for ``accept``, which the engine calls as it records the result.
  So no two threads touch the registry's guard state, and a read that answers after its
  timeout is neither shown nor counted as a find (#332).
* every other read (Gmail, memory, context files, help, reminders) runs one at a time, in
  the model's order, through ``registry.execute_tool_sync`` inside an ordered worker lane.

Writes get no timeout and never come through here. Background turns do not use this module.
"""

import asyncio
import contextvars
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

READ_TIMEOUT_S = 120.0
MAX_PARALLEL_READS = 4

# simplification: ONE process-wide pool, shared by every turn. It bounds a batch's
# concurrency and holds these reads to 4 of the Postgres pool's 10 connections, and it
# keeps a hung read off the default executor that history saves use. Threads cannot be
# killed: a read that outlives its timeout keeps its worker (and connection) until the
# handler returns, so 4 hung reads make later parallel reads time out unstarted.
# Upgrade path: per-turn lanes under a process-wide thread cap.
_executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL_READS, thread_name_prefix="baker-read")


class ReadOutcome(NamedTuple):
    result: dict
    elapsed_ms: int  # the call's own run time; the timeout for a read that gave up
    epoch: object = None
    unrecorded: bool = False  # an in-time parallel read whose deals `accept` still has to record


def _timeout_result(name: str) -> dict:
    return {
        "error": f"{name} did not answer within {READ_TIMEOUT_S:g} s, so the wait was stopped "
        "and nothing was returned. Try it once more, or tell the user it timed out.",
        "status": "timeout",
    }


def _timed(registry, name: str, args: dict) -> tuple[dict, object, int]:
    t0 = time.monotonic()
    result, epoch = registry.run_read_sync(name, args)
    return result, epoch, int((time.monotonic() - t0) * 1000)


# One ordered lane per registry (= per turn). Awaiting a timed-out to_thread call does
# not stop its handler, so the lane is a lock the HANDLER holds for its whole run: the
# next ordered read waits for it (inside its own deadline) instead of starting beside a
# hung one, and gives up unstarted if the deadline passes first. Weak keys: the lane
# dies with the turn's registry.
_ordered_lanes: "weakref.WeakKeyDictionary[object, threading.Lock]" = weakref.WeakKeyDictionary()
_ordered_lanes_lock = threading.Lock()


def _ordered_lane(registry) -> threading.Lock:
    with _ordered_lanes_lock:
        return _ordered_lanes.setdefault(registry, threading.Lock())


def _ordered(registry, name: str, args: dict, deadline: float):
    """Run an unaudited read in the turn's ordered lane; ``None`` if it never started."""
    lane = _ordered_lane(registry)
    if not lane.acquire(timeout=max(0.0, deadline - time.monotonic())):
        return None
    try:
        if time.monotonic() >= deadline:
            return None
        return registry.execute_tool_sync(name, args)
    finally:
        lane.release()


async def run_reads(registry, tool_calls: list[dict]) -> dict[int, ReadOutcome]:
    """Run every read in ``tool_calls``; return ``{index in tool_calls: outcome}``.

    Writes are skipped (no entry). On return nothing is left running that can still be
    accepted: a read that missed its timeout is abandoned, and one that had not started
    never will.
    """
    # A registry that declares no audited set (the test fakes) runs every read in order.
    parallel = getattr(registry, "parallel_reads", frozenset())
    reads = [(i, tc.get("name"), tc.get("args") or {}) for i, tc in enumerate(tool_calls)
             if not registry.is_write(tc.get("name"))]
    loop = asyncio.get_running_loop()
    # run_in_executor, not asyncio.to_thread, for the dedicated pool; the context copy is
    # the one thing to_thread adds.
    futures = {
        i: loop.run_in_executor(_executor, contextvars.copy_context().run, _timed, registry, name, args)
        for i, name, args in reads if name in parallel
    }
    try:
        if futures:
            await asyncio.wait(futures.values(), timeout=READ_TIMEOUT_S)
    finally:
        # The deadline, Stop and a dropped client all end here, with no await since the
        # wait: a read that has not started is cancelled outright; a running one finishes
        # in its thread and its answer is dropped. A no-op on a read that answered in time.
        for future in futures.values():
            future.cancel()
    outcomes: dict[int, ReadOutcome] = {}
    for i, name, args in reads:
        if i in futures:
            if futures[i].cancelled():
                outcomes[i] = ReadOutcome(_timeout_result(name), int(READ_TIMEOUT_S * 1000))
            else:
                result, epoch, elapsed_ms = futures[i].result()
                outcomes[i] = ReadOutcome(result, elapsed_ms, epoch, True)
            continue
        t0 = time.monotonic()
        deadline = t0 + READ_TIMEOUT_S
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_ordered, registry, name, args, deadline), READ_TIMEOUT_S)
        except TimeoutError:
            result = _timeout_result(name)
        if result is None:  # never started: the turn's ordered lane stayed busy past the deadline
            result = _timeout_result(name)
        outcomes[i] = ReadOutcome(result, int((time.monotonic() - t0) * 1000))
    return outcomes


async def accept(registry, outcome: ReadOutcome) -> None:
    """Record an accepted read's deals. Call once, when its result is recorded for the model."""
    if outcome.unrecorded:
        await asyncio.to_thread(registry.record_deals, outcome.result, outcome.epoch)
