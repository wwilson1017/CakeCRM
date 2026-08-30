"""The non-SSE background turn runner (assistant/background.py).

Hermetic: a scripted FakeProvider + FakeRegistry, get_ai_provider monkeypatched.
Covers the C1 safety contract — writes auto-execute (no confirmation), the tool
allowlist is the authorization boundary, the write budget rejects/terminates,
no-provider degrades, a crashing/incomplete stream is caught, and the runner
refuses to run inside an event loop.
"""


from assistant import background, delimiters
from assistant.background import BackgroundResult, run_background_turn


class FakeProvider:
    def __init__(self, scripts, raise_on_stream=False):
        self.model = "fake-model"
        self._scripts = scripts
        self._i = 0
        self.raise_on_stream = raise_on_stream
        self.build_tool_turn_calls = []
        self.advertised_tools = []          # tool names offered per stream_turn call
        self.tool_results = []              # results handed back via build_tool_turn

    async def stream_turn(self, messages, tools, system_prompt):
        self.advertised_tools.append([t["name"] for t in tools])
        if self.raise_on_stream:
            raise RuntimeError("provider boom")
        script = self._scripts[self._i] if self._i < len(self._scripts) else self._scripts[-1]
        self._i += 1
        for event in script:
            yield event

    def build_tool_turn(self, text, tool_calls, results):
        self.build_tool_turn_calls.append(text)
        self.tool_results.append(results)
        return [{"role": "assistant", "content": text}, {"role": "tool", "results": results}]


class FakeRegistry:
    def __init__(self, writes=frozenset()):
        self._writes = set(writes)
        self.writes_map = {"crm_dashboard": False, "crm_create_task": True, "crm_delete_contact": True,
                           "notify_user": True}
        # Advertise every untrusted-source read (writes:False, as they really are), or the
        # #114 exclusion tests would pass vacuously against a registry lacking them.
        # Derived from the real set, not re-listed: a second hand-maintained copy here
        # would break CI the day a third tool joins UNTRUSTED_SOURCE_TOOLS, which is
        # exactly the drift this feature is written to avoid.
        self.writes_map.update({name: False for name in delimiters.UNTRUSTED_SOURCE_TOOLS})
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


def test_allowlist_is_reads_plus_notify_only():
    reg = FakeRegistry()
    allowed = background.background_allowlist(reg)
    assert "crm_dashboard" in allowed and "notify_user" in allowed   # read + notify
    # NO CRM writes at all — not deletes, not creates, not logging.
    assert "crm_delete_contact" not in allowed
    assert "crm_create_task" not in allowed
    # heartbeat_allowlist / reminder_allowlist are aliases of the same boundary.
    assert background.heartbeat_allowlist(reg) == allowed
    assert background.reminder_allowlist(reg) == allowed


# ── #114: live external-source reads never reach an unattended turn ──────────────

def test_untrusted_source_reads_are_excluded_from_the_allowlist():
    """The Gmail reads are writes:False, so the pre-#114 derivation admitted them and a
    prompt injection could exfiltrate mail through the one permitted notify_user.

    Written against the whole BACKGROUND_EXCLUDED_TOOLS set rather than today's two
    names, so a tool added to it later is covered here with no edit to this file."""
    excluded = set(background.BACKGROUND_EXCLUDED_TOOLS)
    assert excluded, "empty exclusion set would make every assertion below vacuous"

    reg = FakeRegistry()
    assert excluded <= reg.writes_map.keys()                # the registry advertises them
    assert not (excluded & set(reg._writes))                # as READS, not writes

    allowed = background.background_allowlist(reg)
    assert excluded.isdisjoint(allowed), f"untrusted-source reads leaked: {excluded & allowed}"
    assert "crm_dashboard" in allowed        # ordinary reads are untouched


def test_background_exclusion_tracks_the_shared_untrusted_source_set():
    """The exclusion must stay coupled to the set the interactive engine taints off, so
    the two loops can never disagree about which reads carry third-party content."""
    assert background.BACKGROUND_EXCLUDED_TOOLS is delimiters.UNTRUSTED_SOURCE_TOOLS


def test_untrusted_source_read_is_refused_even_when_a_caller_allows_it(monkeypatch):
    """The exclusion is a property of the background MODE, not just of the builder:
    a caller that hands in its own allowlist still cannot run a live Gmail read."""
    prov = FakeProvider([
        [_complete([_tc("gmail_search")], stop="tool_use")],
        [{"type": "text", "text": "done"}, _complete(stop="stop")],
    ])
    _use(prov, monkeypatch)
    reg = FakeRegistry()
    r = run_background_turn(("sys", "vol"), "go",
                            allowed_tools={"gmail_search", "crm_dashboard"}, registry=reg)

    # (a) never advertised to the provider …
    assert prov.advertised_tools, "provider was never called"
    assert all("gmail_search" not in names for names in prov.advertised_tools)
    assert "crm_dashboard" in prov.advertised_tools[0]     # the rest of the set survived
    # (b) … never executed …
    assert reg.calls == []
    # (c) … and the fail-closed refusal reached the model in the tool results.
    assert any("not permitted" in res["content"]
               for results in prov.tool_results for res in results)
    assert any("not permitted" in log["result"] for log in r.tool_log)


def test_timeout_returns_error(monkeypatch):
    class HangingProvider:
        model = "fake-model"

        async def stream_turn(self, messages, tools, system_prompt):
            import asyncio as _a
            await _a.sleep(5)   # never completes within the short timeout
            yield _complete(stop="stop")

        def build_tool_turn(self, text, tool_calls, results):
            return []

    _use(HangingProvider(), monkeypatch)
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"},
                            registry=FakeRegistry(), timeout=1)
    assert r.error
    assert "timed out" in r.text


async def test_refuses_inside_running_loop(monkeypatch):
    # This test runs inside the asyncio event loop (asyncio_mode=auto). The sync
    # runner must detect the loop and refuse rather than raise/deadlock.
    prov = FakeProvider([[{"type": "text", "text": "x"}, _complete(stop="stop")]])
    _use(prov, monkeypatch)
    r = run_background_turn(("sys", "vol"), "go", allowed_tools={"crm_dashboard"}, registry=FakeRegistry())
    assert r.error
    assert "event loop" in r.text


def test_runs_on_captured_main_loop(monkeypatch):
    # When a main loop is captured, the turn runs ON it (via run_coroutine_threadsafe)
    # from the calling/scheduler thread — the path that keeps the loop-bound provider
    # client alive across many background turns.
    import asyncio
    import threading

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    ready.wait()
    monkeypatch.setattr(background, "_main_loop", loop)
    try:
        prov = FakeProvider([[{"type": "text", "text": "on main loop"}, _complete(stop="stop")]])
        _use(prov, monkeypatch)
        r = run_background_turn(("s", "v"), "go", allowed_tools={"crm_dashboard"}, registry=FakeRegistry())
        assert not r.error and r.text == "on main loop"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()


def test_result_dataclass_defaults():
    r = BackgroundResult(text="hi")
    assert r.tool_log == [] and r.input_tokens == 0 and not r.error
