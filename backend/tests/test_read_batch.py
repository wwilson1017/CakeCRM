"""The read lanes of one tool batch (#372): ``assistant.read_batch`` and the registry's
audited set. Hermetic. The engine-level behaviour (overlap, writes wait, a hung read times
out, the #332 guard) is pinned in ``test_assistant_engine.py``.
"""

import asyncio
import threading
import time

import pytest

from assistant import delimiters, read_batch
from assistant.registry import ToolRegistry
from crm import deal_write_guard


def idle_pool():
    """Return once every read worker is idle — i.e. no earlier read is still in a thread."""
    gate = threading.Barrier(read_batch.MAX_PARALLEL_READS + 1)
    for _ in range(read_batch.MAX_PARALLEL_READS):
        read_batch._executor.submit(gate.wait, 5)
    gate.wait(5)


class Lanes:
    """A registry stand-in: ``par_*`` names are audited reads, ``w_*`` are writes."""

    def __init__(self, handler=lambda name: {"ok": name}):
        self.handler = handler
        self.parallel_reads = frozenset(f"par_{n}" for n in range(8))
        self.ordered: list[str] = []
        self.ordered_active = self.ordered_peak = 0
        self.ordered_lock = threading.Lock()
        self.recorded: list = []

    def is_write(self, name):
        return name.startswith("w_")

    def run_read_sync(self, name, args):
        return self.handler(name), "epoch"

    def record_deals(self, result, epoch):
        self.recorded.append((result, epoch))

    def execute_tool_sync(self, name, args):
        with self.ordered_lock:
            self.ordered_active += 1
            self.ordered_peak = max(self.ordered_peak, self.ordered_active)
            self.ordered.append(name)
        time.sleep(0.01)
        with self.ordered_lock:
            self.ordered_active -= 1
        return {"ok": name}

    async def execute_tool(self, name, args):
        return await asyncio.to_thread(self.execute_tool_sync, name, args)


def _calls(*names):
    return [{"id": f"t{i}", "name": name, "args": {}} for i, name in enumerate(names)]


async def test_audited_reads_fill_the_pool_and_never_exceed_it():
    lock, state = threading.Lock(), {"active": 0, "peak": 0}
    full = threading.Barrier(read_batch.MAX_PARALLEL_READS)

    def handler(name):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        full.wait(5)  # passes only when MAX_PARALLEL_READS handlers are inside at once
        time.sleep(0.05)  # ...and stay inside long enough for a fifth to show up
        with lock:
            state["active"] -= 1
        return {"ok": name}

    reg = Lanes(handler)
    names = sorted(reg.parallel_reads)  # 8 reads over 4 workers
    outcomes = await read_batch.run_reads(reg, _calls(*names))
    assert [outcomes[i].result for i in range(8)] == [{"ok": n} for n in names]
    assert state["peak"] == read_batch.MAX_PARALLEL_READS


async def test_other_reads_run_one_at_a_time_in_the_models_order_and_writes_are_skipped():
    reg = Lanes()
    outcomes = await read_batch.run_reads(
        reg, _calls("gmail_search", "w_draft", "par_0", "read_context_file", "gmail_read_thread"))
    assert reg.ordered == ["gmail_search", "read_context_file", "gmail_read_thread"]
    assert reg.ordered_peak == 1
    assert sorted(outcomes) == [0, 2, 3, 4]  # the write has no outcome
    assert outcomes[2].unrecorded and not outcomes[0].unrecorded


async def test_accept_records_only_an_audited_in_time_read():
    reg = Lanes()
    outcomes = await read_batch.run_reads(reg, _calls("par_0", "gmail_search"))
    await read_batch.accept(reg, outcomes[1])
    assert reg.recorded == []
    await read_batch.accept(reg, outcomes[0])
    assert reg.recorded == [({"ok": "par_0"}, "epoch")]


async def test_an_ordered_read_times_out_too(monkeypatch):
    monkeypatch.setattr(read_batch, "READ_TIMEOUT_S", 0.05)
    reg = Lanes()
    release, started = threading.Event(), threading.Event()

    def hung(name, args):
        started.set()
        release.wait(5)
        return {"ok": name}

    reg.execute_tool_sync = hung
    try:
        outcome = (await read_batch.run_reads(reg, _calls("gmail_search")))[0]
        assert started.is_set()
    finally:
        release.set()
        await asyncio.to_thread(_wait_for_ordered_lane, reg)
    assert outcome.result["status"] == "timeout" and "gmail_search" in outcome.result["error"]
    assert not outcome.unrecorded


def _wait_for_ordered_lane(reg):
    lane = read_batch._ordered_lane(reg)
    assert lane.acquire(timeout=5)
    lane.release()


async def test_a_hung_ordered_read_keeps_the_next_one_from_starting_beside_it(monkeypatch):
    monkeypatch.setattr(read_batch, "READ_TIMEOUT_S", 0.1)
    release, ran = threading.Event(), []
    reg = Lanes()

    def ordered(name, args):
        ran.append(name)
        if name == "gmail_search":
            release.wait(5)
        return {"ok": name}

    reg.execute_tool_sync = ordered
    try:
        outcomes = await read_batch.run_reads(reg, _calls("gmail_search", "read_context_file"))
        assert [outcomes[i].result["status"] for i in range(2)] == ["timeout", "timeout"]
        assert ran == ["gmail_search"]
    finally:
        release.set()
        await asyncio.to_thread(_wait_for_ordered_lane, reg)
    outcome = (await read_batch.run_reads(reg, _calls("read_context_file")))[0]
    assert outcome.result == {"ok": "read_context_file"}
    assert ran == ["gmail_search", "read_context_file"]


async def test_the_parallel_bound_is_process_wide():
    lock, state = threading.Lock(), {"active": 0, "peak": 0}
    full = threading.Barrier(read_batch.MAX_PARALLEL_READS)

    def handler(name):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            full.wait(5)
            time.sleep(0.05)
            return {"ok": name}
        finally:
            with lock:
                state["active"] -= 1

    names = [f"par_{i}" for i in range(read_batch.MAX_PARALLEL_READS)]
    batches = await asyncio.gather(*(
        read_batch.run_reads(Lanes(handler), _calls(*names)) for _ in range(2)
    ))
    assert all([batch[i].result for i in range(len(names))] == [{"ok": n} for n in names]
               for batch in batches)
    assert state == {"active": 0, "peak": read_batch.MAX_PARALLEL_READS}


async def test_a_read_that_answers_after_its_deadline_stays_timed_out(monkeypatch):
    monkeypatch.setattr(read_batch, "READ_TIMEOUT_S", 0.1)
    release = threading.Event()
    reg = Lanes(lambda name: release.wait(5) and {"ok": name})

    def ordered(name, args):
        release.set()  # the hung read answers now — late, while this later read is running
        idle_pool()
        return {"ok": name}

    reg.execute_tool_sync = ordered
    try:
        outcomes = await read_batch.run_reads(reg, _calls("gmail_search", "par_0"))
    finally:
        release.set()
        await asyncio.to_thread(idle_pool)
        await asyncio.to_thread(_wait_for_ordered_lane, reg)
    assert outcomes[0].result == {"ok": "gmail_search"}
    assert outcomes[1].result["status"] == "timeout" and not outcomes[1].unrecorded


async def test_stop_mid_batch_abandons_running_reads_and_never_starts_queued_ones():
    release, started, ran = threading.Event(), threading.Semaphore(0), []

    def handler(name):
        ran.append(name)
        started.release()
        release.wait(5)
        return {"ok": name}

    reg = Lanes(handler)
    names = sorted(reg.parallel_reads)[:read_batch.MAX_PARALLEL_READS + 2]
    task = asyncio.create_task(read_batch.run_reads(reg, _calls(*names)))
    try:
        for _ in range(read_batch.MAX_PARALLEL_READS):  # every worker is inside a handler
            assert await asyncio.to_thread(started.acquire, True, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.to_thread(idle_pool)
    assert len(ran) == read_batch.MAX_PARALLEL_READS  # the two queued reads never ran


def test_the_audited_set_is_the_crm_and_todo_reads_and_nothing_else(monkeypatch):
    from assistant import registry as registry_module

    gmail = {"name": "gmail_search", "writes": False, "description": "test"}
    monkeypatch.setattr(registry_module, "get_gmail_tools", lambda **kw: ([gmail], {"gmail_search": dict}))
    reg = ToolRegistry()
    assert {"crm_search_deals", "crm_get_deal", "todo_list"} <= reg.parallel_reads
    assert all(name.startswith(("crm_", "todo_")) for name in reg.parallel_reads)
    assert not any(reg.is_write(name) for name in reg.parallel_reads)
    assert reg.parallel_reads.isdisjoint(delimiters.UNTRUSTED_SOURCE_TOOLS | delimiters.CONTEXT_READ_TOOLS)
    assert "gmail_search" in reg.writes_map  # reached the real composition, Gmail included
    # Every read that can record a find takes the deferred-bookkeeping lane — the property
    # "a late answer is never counted as a find" rests on.
    finders = {n for n, w in reg.writes_map.items() if not w and deal_write_guard._records_finds(n, w)}
    assert finders and finders <= reg.parallel_reads


def test_run_read_sync_refuses_anything_outside_the_audited_set():
    reg = ToolRegistry()
    ran = []
    reg.executors["crm_create_contact"] = lambda **kw: ran.append(kw)
    reg.executors["help_search"] = lambda **kw: ran.append(kw)
    for name in ("crm_create_contact", "help_search", "no_such_tool"):
        result, epoch = reg.run_read_sync(name, {})
        assert "error" in result and epoch is deal_write_guard.NO_EPOCH
    assert ran == []


async def test_a_background_turn_keeps_the_models_order(monkeypatch):
    from test_assistant_engine import FakeProvider, _complete, _tc

    from assistant.background import _run_turn

    monkeypatch.setattr(deal_write_guard, "pg_fetchone", lambda *a: None)
    reg = ToolRegistry()
    order = []
    reg.executors["crm_create_contact"] = lambda **kw: order.append("write") or {"ok": True}
    reg.executors["crm_dashboard"] = lambda **kw: order.append("read") or {"ok": True}
    provider = FakeProvider([
        [_complete([_tc("crm_create_contact", "w1"), _tc("crm_dashboard", "r1")], "tool_use")],
        [_complete()],
    ])
    await _run_turn(provider, reg, "sys", "go", {"crm_create_contact", "crm_dashboard"}, 5, 10)
    assert order == ["write", "read"]  # unattended turns do not reorder (#372 left them alone)
