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
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, delta, finish_reason=None):
        self.choices = [_Choice(delta, finish_reason)]


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
@pytest.mark.parametrize('provider_kind', ['openai', 'compat'])
async def test_openai_length_finish_is_max_tokens(monkeypatch, provider_kind):
    """A reply cut at the output cap reaches the engine as max_tokens (#340)."""
    import openai

    from providers import openai_provider as op
    from providers.openai_compat import stream_openai_turn
    client = FakeOpenAIClient([_Chunk(_Delta(content="Half")), _Chunk(_Delta(), finish_reason="length")])
    if provider_kind == 'compat':
        stream = stream_openai_turn(client, "m", [], [], "sys")
    else:
        monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **kw: client)
        stream = op.OpenAIProvider(access_token='k', model='m').stream_turn([], [], 'sys')
    events = [e async for e in stream]
    assert events[-1]["stop_reason"] == "max_tokens"


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


@pytest.mark.parametrize('header', ['7', None, 'invalid'])
def test_rate_limit_event(header):
    from types import SimpleNamespace

    from providers.base import rate_limit_event

    err = Exception('limited')
    err.response = SimpleNamespace(headers={'retry-after': header})
    assert rate_limit_event('Qwen/Qwen3.5-7B', err) == {
        'type': 'error', 'error': 'Qwen3.5-7B is rate-limited. Please try again in a minute.',
        'rate_limited': True, 'retry_after': 7.0 if header == '7' else None,
    }
    assert rate_limit_event('model', Exception())['retry_after'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_kind', ['anthropic', 'openai', 'compat'])
async def test_provider_rate_limit_names_model(monkeypatch, provider_kind):
    from types import SimpleNamespace

    import anthropic
    import httpx
    import openai

    from providers import anthropic_provider as ap, openai_provider as op
    from providers.openai_compat import stream_openai_turn

    sdk = anthropic if provider_kind == 'anthropic' else openai
    response = httpx.Response(429, headers={'retry-after': '7'}, request=httpx.Request('POST', 'https://example.com'))
    error = sdk.RateLimitError('limited', response=response, body=None)

    def fail(**kwargs):
        raise error

    async def async_fail(**kwargs):
        raise error

    if provider_kind == 'anthropic':
        monkeypatch.setattr(ap, '_get_client', lambda api_key="": SimpleNamespace(messages=SimpleNamespace(stream=fail)))
        stream = ap.AnthropicProvider(api_key='k', model='Qwen/Qwen3.5-7B').stream_turn([], [], 'sys')
    else:
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=async_fail)))
        if provider_kind == 'compat':
            stream = stream_openai_turn(client, 'Qwen/Qwen3.5-7B', [], [], 'sys')
        else:
            monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **kw: client)
            stream = op.OpenAIProvider(access_token='k', model='Qwen/Qwen3.5-7B').stream_turn([], [], 'sys')
    events = [event async for event in stream]
    assert events[0] == {
        'type': 'error', 'error': 'Qwen3.5-7B is rate-limited. Please try again in a minute.',
        'rate_limited': True, 'retry_after': 7.0,
    }
    assert events[1]['stop_reason'] == 'error'
