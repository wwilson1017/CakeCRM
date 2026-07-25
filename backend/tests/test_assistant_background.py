"""The non-SSE background turn runner (assistant/background.py).

Hermetic: a scripted FakeProvider + FakeRegistry, get_ai_provider monkeypatched.
Covers the C1 safety contract — writes auto-execute (no confirmation), the tool
allowlist is the authorization boundary, the write budget rejects/terminates,
no-provider degrades, a crashing/incomplete stream is caught, and the runner
refuses to run inside an event loop.
"""


from assistant import background
from assistant.background import BackgroundResult, run_background_turn


class FakeProvider:
    def __init__(self, scripts, raise_on_stream=False):
        self.model = "fake-model"
        self._scripts = scripts
        self._i = 0
        self.raise_on_stream = raise_on_stream
        self.build_tool_turn_calls = []

    async def stream_turn(self, messages, tools, system_prompt):
        if self.raise_on_stream:
            raise RuntimeError("provider boom")
        script = self._scripts[self._i] if self._i < len(self._scripts) else self._scripts[-1]
        self._i += 1
        for event in script:
            yield event

    def build_tool_turn(self, text, tool_calls, results):
        self.build_tool_turn_calls.append(text)
        return [{"role": "assistant", "content": text}, {"role": "tool", "results": results}]


class FakeRegistry:
    def __init__(self, writes=frozenset()):
        self._writes = set(writes)
        self.writes_map = {"crm_dashboard": False, "crm_create_task": True, "crm_delete_contact": True,
                           "notify_user": True}
        self.calls = []

    def is_write(self, name):
        return name in self._writes

    def provider_tools(self, tool_mode, allow=None):
        tools = [{"name": n} for n in self.writes_map]
        if allow is not None:
            tools = [t for t in tools if t["name"] in allow]
        return tools

    async def execute_tool(self, name, args):
        self.calls.append((name, args))
        return {"ok": True, "name": name}


def _tc(name, tid="t1", args=None):
    return {"id": tid, "name": name, "args": args or {}}


def _complete(tool_calls=None, stop="stop", usage=None):
    ev = {"type": "_turn_complete", "tool_calls": tool_calls or [], "stop_reason": stop}
    if usage is not None:
        ev["usage"] = usage
    return ev


def _use(provider, monkeypatch):
    monkeypatch.setattr(background, "get_ai_provider", lambda **k: provider)


def test_text_only_response(monkeypatch):
    prov = FakeProvider([[{"type": "text", "text": "all clear"}, _complete(stop="stop")]])
    _use(prov, monkeypatch)
    r = run_background_turn(("sys", "vol"), "check", allowed_tools={"crm_dashboard"},
                            registry=FakeRegistry())
    assert not r.error
    assert r.text == "all clear"


def test_write_executes_without_confirmation(monkeypatch):
    prov = FakeProvider([
        [_complete([_tc("crm_create_task")], stop="tool_use")],
        [{"type": "text", "text": "logged"}, _complete(stop="stop")],
    ])
    _use(prov, monkeypatch)
    reg = FakeRegistry(writes={"crm_create_task"})
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_create_task", "crm_dashboard"}, registry=reg)
    assert not r.error
    assert ("crm_create_task", {}) in reg.calls   # executed, no pending/confirm anywhere
    assert prov.build_tool_turn_calls == [""]       # build_tool_turn used (R2), not add_tool_results


def test_allowlist_blocks_offlist_tool(monkeypatch):
    prov = FakeProvider([
        [_complete([_tc("crm_delete_contact")], stop="tool_use")],
        [{"type": "text", "text": "done"}, _complete(stop="stop")],
    ])
    _use(prov, monkeypatch)
    reg = FakeRegistry(writes={"crm_delete_contact"})
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"}, registry=reg)
    # delete is not in the allowlist → never executed; a fail-closed error is returned to the model.
    assert reg.calls == []
    assert any("not permitted" in (log["result"]) for log in r.tool_log)


def test_write_budget_rejects_then_terminates(monkeypatch):
    prov = FakeProvider([
        [_complete([_tc("crm_create_task", "a"), _tc("crm_create_task", "b"), _tc("crm_create_task", "c")], stop="tool_use")],
    ])
    _use(prov, monkeypatch)
    reg = FakeRegistry(writes={"crm_create_task"})
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_create_task"},
                            registry=reg, write_budget_limit=1)
    assert r.error                    # TERMINATE on the 3rd write
    assert len(reg.calls) == 1        # only the 1st write executed (budget=1)


def test_no_provider_degrades(monkeypatch):
    monkeypatch.setattr(background, "get_ai_provider", lambda **k: None)
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"}, registry=FakeRegistry())
    assert r.error and r.no_provider


def test_provider_crash_is_caught(monkeypatch):
    prov = FakeProvider([[]], raise_on_stream=True)
    _use(prov, monkeypatch)
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"}, registry=FakeRegistry())
    assert r.error
    assert "boom" in r.text


def test_stream_without_turn_complete_errors(monkeypatch):
    prov = FakeProvider([[{"type": "text", "text": "partial"}]])  # no _turn_complete
    _use(prov, monkeypatch)
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"}, registry=FakeRegistry())
    assert r.error
    assert "unexpectedly" in r.text


def test_provider_error_event(monkeypatch):
    prov = FakeProvider([[{"type": "error", "error": "rate limited"}]])
    _use(prov, monkeypatch)
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"}, registry=FakeRegistry())
    assert r.error and "rate limited" in r.text


def test_allowlist_builders_use_writes_map():
    reg = FakeRegistry()
    hb = background.heartbeat_allowlist(reg)
    assert "crm_dashboard" in hb and "notify_user" in hb
    assert "crm_delete_contact" not in hb and "crm_create_task" not in hb
    rem = background.reminder_allowlist(reg)
    assert "crm_create_task" in rem and "notify_user" in rem
    assert "crm_delete_contact" not in rem


async def test_refuses_inside_running_loop(monkeypatch):
    # This test runs inside the asyncio event loop (asyncio_mode=auto). The sync
    # runner must detect the loop and refuse rather than raise/deadlock.
    prov = FakeProvider([[{"type": "text", "text": "x"}, _complete(stop="stop")]])
    _use(prov, monkeypatch)
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"}, registry=FakeRegistry())
    assert r.error
    assert "event loop" in r.text


def test_result_dataclass_defaults():
    r = BackgroundResult(text="hi")
    assert r.tool_log == [] and r.input_tokens == 0 and not r.error
