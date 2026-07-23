"""Fake-stream contract tests for the (dormant) streaming surface — the highest
faithful-port risk. Covers the shared openai_compat path (openai / ollama /
together) and the Anthropic-native path, feeding a mock stream and asserting the
yielded event dicts. No network, SDKs never actually called."""

import pytest

# ── OpenAI-compatible fakes (openai/ollama/together share this path) ───────────


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolDelta:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = _Fn(name, arguments) if (name or arguments) else None


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, delta):
        self.choices = [_Choice(delta)]


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()


class _Completions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kwargs):
        return _FakeStream(self._chunks)


class FakeOpenAIClient:
    def __init__(self, chunks):
        self.chat = type("_Chat", (), {"completions": _Completions(chunks)})()


@pytest.mark.asyncio
async def test_openai_compat_text_only():
    from providers.openai_compat import stream_openai_turn
    chunks = [_Chunk(_Delta(content="Hi")), _Chunk(_Delta(content=" there"))]
    events = [e async for e in stream_openai_turn(FakeOpenAIClient(chunks), "m", [], [], "sys")]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hi there"
    assert events[-1] == {"type": "_turn_complete", "tool_calls": [], "stop_reason": "stop"}


@pytest.mark.asyncio
async def test_openai_compat_tool_call_assembly():
    from providers.openai_compat import stream_openai_turn
    tool = {"name": "search", "description": "", "input_schema": {"type": "object", "properties": {}}}
    chunks = [
        _Chunk(_Delta(content="ok")),
        _Chunk(_Delta(tool_calls=[_ToolDelta(0, id="call_1", name="search")])),
        _Chunk(_Delta(tool_calls=[_ToolDelta(0, arguments='{"q": ')])),
        _Chunk(_Delta(tool_calls=[_ToolDelta(0, arguments='"hi"}')])),
    ]
    events = [e async for e in stream_openai_turn(FakeOpenAIClient(chunks), "m", [], [tool], "sys")]
    types = [e["type"] for e in events]
    assert "tool_start" in types and "tool_args" in types
    args_event = next(e for e in events if e["type"] == "tool_args")
    assert args_event["args"] == {"q": "hi"}
    assert events[-1]["stop_reason"] == "tool_use"


# ── Anthropic-native fakes ─────────────────────────────────────────────────────


class _CB:
    def __init__(self, type=None, id=None, name=None):
        if type is not None:
            self.type = type
        self.id = id
        self.name = name


class _TextDelta:
    def __init__(self, text):
        self.text = text


class _JsonDelta:
    def __init__(self, partial_json):
        self.partial_json = partial_json


class _E:
    def __init__(self, type, content_block=None, delta=None):
        self.type = type
        if content_block is not None:
            self.content_block = content_block
        if delta is not None:
            self.delta = delta


class _FinalMsg:
    stop_reason = "end_turn"
    usage = None

    def __init__(self, stop_reason="end_turn"):
        self.stop_reason = stop_reason


class _AnthStream:
    def __init__(self, events, final):
        self._events = events
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        return self._final


class FakeAnthClient:
    def __init__(self, events, final):
        self.messages = type("_M", (), {"stream": lambda _self, **kw: _AnthStream(events, final)})()


@pytest.mark.asyncio
async def test_anthropic_stream_text(monkeypatch):
    from providers import anthropic_provider as ap
    events = [
        _E("content_block_start", content_block=_CB(type="text")),
        _E("content_block_delta", delta=_TextDelta("Hello")),
        _E("content_block_stop"),
    ]
    monkeypatch.setattr(ap, "_get_client", lambda api_key="": FakeAnthClient(events, _FinalMsg("end_turn")))
    out = [e async for e in ap.AnthropicProvider(api_key="k").stream_turn(
        [{"role": "user", "content": "hi"}], [], "sys")]
    assert "".join(e["text"] for e in out if e["type"] == "text") == "Hello"
    assert out[-1]["type"] == "_turn_complete"
    assert out[-1]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_anthropic_stream_tool_call(monkeypatch):
    from providers import anthropic_provider as ap
    events = [
        _E("content_block_start", content_block=_CB(type="tool_use", id="tu1", name="search")),
        _E("content_block_delta", delta=_JsonDelta('{"q":')),
        _E("content_block_delta", delta=_JsonDelta('"hi"}')),
        _E("content_block_stop"),
    ]
    monkeypatch.setattr(ap, "_get_client", lambda api_key="": FakeAnthClient(events, _FinalMsg("tool_use")))
    tool = {"name": "search", "description": "", "input_schema": {"type": "object", "properties": {}}}
    out = [e async for e in ap.AnthropicProvider(api_key="k").stream_turn(
        [{"role": "user", "content": "hi"}], [tool], "sys")]
    types = [e["type"] for e in out]
    assert "tool_start" in types and "tool_args" in types
    assert next(e for e in out if e["type"] == "tool_args")["args"] == {"q": "hi"}
