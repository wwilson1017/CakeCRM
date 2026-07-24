"""Assistant engine — the SSE tool loop, confirmation gate, and confirm resolver.

Hermetic: a scripted FakeProvider stands in for stream_turn (à la test_streaming),
and an in-memory store replaces the Postgres history/assembly/identity functions.
No DB, no SDKs, no network. These tests pin the three acceptance criteria:
streaming works, normal-mode writes confirm (and don't execute), uploads/tools
run — plus the security-relevant read-only and idempotent-confirm behaviors.
"""

import json

import pytest

from assistant import assembly, engine, history, identity

# ── Scripted fake provider ────────────────────────────────────────────────────

class FakeProvider:
    def __init__(self, scripts, context_window=None):
        self.model = "fake-model"
        self.context_window = context_window
        self._scripts = scripts
        self._i = 0
        self.captured_tools: list[list] = []
        self.captured_messages: list[list] = []

    async def stream_turn(self, messages, tools, system_prompt):
        self.captured_tools.append(tools)
        self.captured_messages.append(messages)
        script = self._scripts[self._i] if self._i < len(self._scripts) else self._scripts[-1]
        self._i += 1
        for event in script:
            yield event

    def build_tool_turn(self, text, tool_calls, results):
        return [{"role": "assistant", "content": text, "tool_calls": tool_calls},
                {"role": "tool", "results": results}]


def _tc(name, tid="t1", args=None):
    return {"id": tid, "name": name, "args": args or {}}


def _complete(tool_calls=None, stop="stop", usage=None):
    ev = {"type": "_turn_complete", "tool_calls": tool_calls or [], "stop_reason": stop}
    if usage is not None:
        ev["usage"] = usage
    return ev


# ── In-memory history/assembly/identity ───────────────────────────────────────

class Store:
    def __init__(self):
        self.convs: dict[str, dict] = {}
        self.saved: list[dict] = []
        self.merges: list[dict] = []
        self._n = 0

    def create_conversation(self):
        self._n += 1
        cid = f"conv{self._n}"
        self.convs[cid] = {"id": cid, "messages": []}
        return {"id": cid}

    def conversation_exists(self, cid):
        return cid in self.convs

    def auto_title(self, cid, text):
        return (text or "")[:60]

    def save_message(self, cid, mid, role, content, tool_calls=None, model=""):
        self.saved.append({"cid": cid, "mid": mid, "role": role, "content": content, "tool_calls": tool_calls})
        self.convs.setdefault(cid, {"id": cid, "messages": []})["messages"].append(
            {"id": mid, "role": role, "content": content, "tool_calls": tool_calls, "tool_results": None})

    def merge_tool_result(self, mid, tuid, tname, content):
        self.merges.append({"mid": mid, "tuid": tuid, "content": content})
        for c in self.convs.values():
            for m in c["messages"]:
                if m["id"] == mid:
                    tr = [r for r in (m.get("tool_results") or []) if r["tool_use_id"] != tuid]
                    tr.append({"tool_use_id": tuid, "tool_name": tname, "content": content})
                    m["tool_results"] = tr


class Registry:
    def __init__(self, writes=frozenset(), descriptions=None):
        self._writes = set(writes)
        self.descriptions = descriptions or {}
        self.calls: list[tuple[str, dict]] = []

    def is_write(self, name):
        return name in self._writes

    def provider_tools(self, tool_mode):
        tools = [{"name": "crm_dashboard"}, {"name": "crm_create_contact"}]
        if tool_mode == "read-only":
            tools = [t for t in tools if not self.is_write(t["name"])]
        return tools

    def execute_tool_sync(self, name, args):
        self.calls.append((name, args))
        return {"ok": True, "name": name}

    async def execute_tool(self, name, args):
        return self.execute_tool_sync(name, args)


@pytest.fixture
def store(monkeypatch):
    s = Store()
    for fn in ("create_conversation", "conversation_exists", "auto_title",
               "save_message", "merge_tool_result"):
        monkeypatch.setattr(history, fn, getattr(s, fn))
    monkeypatch.setattr(identity, "get_identity",
                        lambda: {"name": "Baker", "personality": "p", "using_default": True})
    # assemble just needs to return a non-empty provider message list
    monkeypatch.setattr(assembly, "assemble_messages",
                        lambda provider, cid: [{"role": "user", "content": "hi"}])
    return s


async def _run(provider, registry, messages, **kw):
    out = []
    async for line in engine.chat(provider, registry, messages, **kw):
        assert isinstance(line, str) and line.startswith("data: ")
        out.append(json.loads(line[len("data: "):]))
    return out


def _types(events):
    return [e["type"] for e in events]


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_only_turn(store):
    prov = FakeProvider([[{"type": "text", "text": "Hi"}, _complete()]])
    events = await _run(prov, Registry(), [{"role": "user", "content": "hello"}])
    assert _types(events) == ["conversation_id", "text", "done"]
    roles = [s["role"] for s in store.saved]
    assert roles == ["user", "assistant"]


@pytest.mark.asyncio
async def test_power_mode_executes_write(store):
    reg = Registry(writes={"crm_create_contact"})
    prov = FakeProvider([
        [_complete([_tc("crm_create_contact", args={"name": "X"})], stop="tool_use")],
        [{"type": "text", "text": "done"}, _complete()],
    ])
    events = await _run(prov, reg, [{"role": "user", "content": "add X"}], tool_mode="power")
    assert reg.calls == [("crm_create_contact", {"name": "X"})]
    assert "tool_end" in _types(events)
    assert _types(events)[-1] == "done"
    # the real result was persisted (not a placeholder)
    assert any('"ok": true' in m["content"] for m in store.merges)


@pytest.mark.asyncio
async def test_normal_mode_write_confirms_without_executing(store):
    """Acceptance: a write tool prompts for confirmation in normal mode."""
    reg = Registry(writes={"crm_create_contact"}, descriptions={"crm_create_contact": "Create a contact"})
    prov = FakeProvider([
        [_complete([_tc("crm_create_contact", args={"name": "X"})], stop="tool_use")],
        [{"type": "text", "text": "Shall I create X?"}, _complete()],
    ])
    events = await _run(prov, reg, [{"role": "user", "content": "add X"}], tool_mode="normal")
    confirm = next(e for e in events if e["type"] == "confirm")
    assert confirm["tool"] == "crm_create_contact"
    assert confirm["args"] == {"name": "X"} and confirm["tool_use_id"] == "t1" and confirm["msg_id"]
    assert confirm["description"] == "Create a contact"
    assert reg.calls == []  # NOT executed
    assert any(m["content"] == history.PENDING_RESULT_JSON for m in store.merges)  # placeholder persisted
    assert _types(events)[-1] == "done"


@pytest.mark.asyncio
async def test_normal_mode_read_executes(store):
    reg = Registry(writes={"crm_create_contact"})  # dashboard is a read
    prov = FakeProvider([
        [_complete([_tc("crm_dashboard")], stop="tool_use")],
        [{"type": "text", "text": "here"}, _complete()],
    ])
    events = await _run(prov, reg, [{"role": "user", "content": "status"}], tool_mode="normal")
    assert reg.calls == [("crm_dashboard", {})]
    assert "confirm" not in _types(events)


@pytest.mark.asyncio
async def test_read_only_hides_and_refuses_writes(store):
    reg = Registry(writes={"crm_create_contact"})
    prov = FakeProvider([
        [_complete([_tc("crm_create_contact", args={"name": "X"})], stop="tool_use")],
        [{"type": "text", "text": "ok"}, _complete()],
    ])
    events = await _run(prov, reg, [{"role": "user", "content": "add"}], tool_mode="read-only")
    # write tool never offered to the provider
    assert all("crm_create_contact" not in [t["name"] for t in tools] for tools in prov.captured_tools)
    # and even when named, it is NOT executed — an error result is returned instead
    assert reg.calls == []
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert "read-only" in tool_end["result"]["error"]


@pytest.mark.asyncio
async def test_anthropic_usage_emits_event_others_do_not(store):
    prov = FakeProvider([[{"type": "text", "text": "hi"}, _complete(usage={"input_tokens": 100})]],
                        context_window=200_000)
    events = await _run(prov, Registry(), [{"role": "user", "content": "x"}])
    assert "usage" in _types(events)
    # no usage key + no window → no usage event, no crash
    prov2 = FakeProvider([[{"type": "text", "text": "hi"}, _complete()]])
    events2 = await _run(prov2, Registry(), [{"role": "user", "content": "x"}])
    assert "usage" not in _types(events2)


@pytest.mark.asyncio
async def test_provider_error_is_forwarded_and_ends(store):
    prov = FakeProvider([[{"type": "error", "error": "boom"}, _complete(stop="error")]])
    events = await _run(prov, Registry(), [{"role": "user", "content": "x"}])
    assert events[-1] == {"type": "error", "error": "boom"}


@pytest.mark.asyncio
async def test_iteration_cap_errors(store):
    reg = Registry(writes=set())  # dashboard read loops forever
    prov = FakeProvider([[_complete([_tc("crm_dashboard")], stop="tool_use")]])  # always tool_use
    events = await _run(prov, reg, [{"role": "user", "content": "x"}], tool_mode="power")
    assert events[-1]["type"] == "error" and "maximum iterations" in events[-1]["error"]


@pytest.mark.asyncio
async def test_continuation_requires_existing_conversation(store):
    prov = FakeProvider([[{"type": "text", "text": "hi"}, _complete()]])
    events = await _run(prov, Registry(), [], conversation_id="does-not-exist")
    assert events[-1]["type"] == "error"


@pytest.mark.asyncio
async def test_continuation_saves_no_user_row(store):
    s = store
    s.convs["conv-x"] = {"id": "conv-x", "messages": []}
    prov = FakeProvider([[{"type": "text", "text": "continuing"}, _complete()]])
    await _run(prov, Registry(), [], conversation_id="conv-x")
    assert all(row["role"] != "user" for row in s.saved)  # no user row on continuation


@pytest.mark.asyncio
async def test_continuation_appends_user_ack_when_history_ends_on_assistant(store, monkeypatch):
    """The persisted wrap-up narration makes a resumed sequence end on an assistant
    turn; the engine must append a user ack so the provider never sees a trailing
    assistant turn (Anthropic mis-prefills, Gemini rejects)."""
    store.convs["conv-x"] = {"id": "conv-x", "messages": []}
    # assembled history ends on an assistant row (the pending-confirmation narration)
    monkeypatch.setattr(assembly, "assemble_messages", lambda provider, cid: [
        {"role": "user", "content": "add X"},
        {"role": "assistant", "content": "tool"},
        {"role": "user", "content": "tool result"},
        {"role": "assistant", "content": "Shall I create X?"},
    ])
    prov = FakeProvider([[{"type": "text", "text": "Done."}, _complete()]])
    await _run(prov, Registry(), [], conversation_id="conv-x")
    # the messages the provider actually received must NOT end on an assistant turn
    assert prov.captured_messages[0][-1]["role"] == "user"


@pytest.mark.asyncio
async def test_write_budget_rejects_then_terminates(store, monkeypatch):
    """Over-budget writes in one turn get REJECTed then TERMINATE the turn — the
    runaway-mutation backstop, exercised end-to-end through the engine."""
    monkeypatch.setattr(engine, "WRITE_BUDGET_PER_TURN", 1)
    reg = Registry(writes={"crm_create_contact"})
    prov = FakeProvider([[_complete([
        _tc("crm_create_contact", tid="a", args={"n": 1}),
        _tc("crm_create_contact", tid="b", args={"n": 2}),
        _tc("crm_create_contact", tid="c", args={"n": 3}),
    ], stop="tool_use")]])
    events = await _run(prov, reg, [{"role": "user", "content": "add three"}], tool_mode="power")
    assert len(reg.calls) == 1  # only the first write executed; budget stopped the rest
    ends = [e for e in events if e["type"] == "tool_end"]
    assert any("budget" in (e["result"].get("error", "").lower()) for e in ends)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_setup_persist_failure_fails_closed(store, monkeypatch):
    monkeypatch.setattr(history, "save_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    prov = FakeProvider([[{"type": "text", "text": "hi"}, _complete()]])
    events = await _run(prov, Registry(), [{"role": "user", "content": "hi"}])
    assert events[-1]["type"] == "error"  # never half-runs


@pytest.mark.asyncio
async def test_tool_iteration_persist_failure_fails_closed(store, monkeypatch):
    """If persisting a tool-using iteration fails, the engine must NOT execute the
    tool (the confirm flow keys off that row)."""
    reg = Registry(writes={"crm_create_contact"})
    prov = FakeProvider([[_complete([_tc("crm_create_contact")], stop="tool_use")]])

    def _save(cid, mid, role, content, tool_calls=None, model=""):
        if tool_calls is not None:
            raise RuntimeError("db down")
        store.save_message(cid, mid, role, content, tool_calls, model)

    monkeypatch.setattr(history, "save_message", _save)
    events = await _run(prov, reg, [{"role": "user", "content": "add"}], tool_mode="power")
    assert events[-1]["type"] == "error"
    assert reg.calls == []  # fail-closed: no execution


@pytest.mark.asyncio
async def test_pending_persist_failure_emits_no_confirm(store, monkeypatch):
    reg = Registry(writes={"crm_create_contact"})
    prov = FakeProvider([[_complete([_tc("crm_create_contact")], stop="tool_use")]])
    monkeypatch.setattr(history, "merge_tool_result",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("db down")))
    events = await _run(prov, reg, [{"role": "user", "content": "add"}], tool_mode="normal")
    assert events[-1]["type"] == "error"
    assert "confirm" not in _types(events)  # no confirm for an unrecorded pending action


@pytest.mark.asyncio
async def test_top_level_catch_all_emits_error(store, monkeypatch):
    monkeypatch.setattr(identity, "get_identity",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    prov = FakeProvider([[{"type": "text", "text": "hi"}, _complete()]])
    events = await _run(prov, Registry(), [{"role": "user", "content": "hi"}])
    assert events[-1] == {"type": "error", "error": "The assistant hit an unexpected error and stopped."}


# ── Confirm resolver (server-authoritative, idempotent) ───────────────────────

def test_resolve_confirmation_approve_executes_and_merges(monkeypatch):
    reg = Registry(writes={"crm_create_contact"})
    monkeypatch.setattr(history, "claim_pending_tool",
                        lambda cid, tuid, msg_id=None: {"msg_id": "m1", "tool": "crm_create_contact", "args": {"name": "Y"}})
    merged = {}
    monkeypatch.setattr(history, "merge_tool_result",
                        lambda mid, tuid, tname, content: merged.update({"content": content}))
    out = engine.resolve_confirmation(reg, "c1", "t1", "approve")
    assert reg.calls == [("crm_create_contact", {"name": "Y"})]  # server-owned args, not client's
    assert out["result"] == {"ok": True, "name": "crm_create_contact"}
    assert '"ok": true' in merged["content"]


def test_resolve_confirmation_threads_msg_id_to_claim(monkeypatch):
    """msg_id must reach claim_pending_tool so a reused tool_use_id resolves the
    correct pending row (Gemini positional-id collision)."""
    reg = Registry(writes={"crm_create_contact"})
    seen = {}

    def _claim(cid, tuid, msg_id=None):
        seen["msg_id"] = msg_id
        return {"msg_id": msg_id or "m1", "tool": "crm_create_contact", "args": {}}

    monkeypatch.setattr(history, "claim_pending_tool", _claim)
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)
    engine.resolve_confirmation(reg, "c1", "call_0", "approve", msg_id="row-42")
    assert seen["msg_id"] == "row-42"


def test_resolve_confirmation_refuses_non_write(monkeypatch):
    """Defense-in-depth: if a non-write somehow got marked pending, approving it
    must NOT execute it."""
    reg = Registry(writes=set())  # crm_dashboard is a read
    monkeypatch.setattr(history, "claim_pending_tool",
                        lambda cid, tuid, msg_id=None: {"msg_id": "m1", "tool": "crm_dashboard", "args": {}})
    merged = {}
    monkeypatch.setattr(history, "merge_tool_result",
                        lambda mid, tuid, tname, content: merged.update({"content": content}))
    out = engine.resolve_confirmation(reg, "c1", "t1", "approve")
    assert reg.calls == []  # not executed
    assert "error" in out["result"]


def test_resolve_confirmation_deny_records_denied(monkeypatch):
    reg = Registry(writes={"crm_create_contact"})
    monkeypatch.setattr(history, "claim_pending_tool",
                        lambda cid, tuid, msg_id=None: {"msg_id": "m1", "tool": "crm_create_contact", "args": {}})
    merged = {}
    monkeypatch.setattr(history, "merge_tool_result",
                        lambda mid, tuid, tname, content: merged.update({"content": content}))
    out = engine.resolve_confirmation(reg, "c1", "t1", "deny")
    assert reg.calls == []  # deny never executes
    assert history.DENIED_STATUS in merged["content"]
    assert out["decision"] == "deny"


def test_resolve_confirmation_idempotent_noop(monkeypatch):
    reg = Registry(writes={"crm_create_contact"})
    monkeypatch.setattr(history, "claim_pending_tool", lambda cid, tuid, msg_id=None: None)  # already resolved
    out = engine.resolve_confirmation(reg, "c1", "t1", "approve")
    assert out == {"status": "already_resolved"}
    assert reg.calls == []  # never double-executes
