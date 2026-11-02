import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import providers
from providers.anthropic_provider import AnthropicProvider
from providers.base import AIProvider
from providers.google_provider import GoogleProvider
from providers.ollama_provider import OllamaProvider
from providers.openai_provider import TRANSCRIBE_MODEL, OpenAIProvider
from providers.together_provider import TogetherProvider


class MinimalProvider(AIProvider):
    provider_name = "minimal"

    async def stream_turn(self, *args):
        yield {}

    def add_tool_results(self, *args):
        return []

    async def list_models(self):
        return []

    async def validate(self):
        return True


async def test_the_abc_default_cannot_transcribe():
    provider = MinimalProvider("test-model")
    assert provider.can_transcribe is False
    with pytest.raises(NotImplementedError):
        await provider.transcribe(b"x", "audio/webm", "clip.webm")


async def test_openai_transcribes_through_the_sdk(monkeypatch):
    create = AsyncMock(return_value=SimpleNamespace(text="  hello  "))
    close = AsyncMock()
    keys = []

    def client(**kw):
        keys.append(kw)
        return SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)), close=close)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=client))
    assert await OpenAIProvider("test-key").transcribe(b"bytes", "audio/webm", "clip.webm") == "hello"
    assert keys == [{"api_key": "test-key"}]
    assert create.call_args.kwargs == {
        "model": TRANSCRIBE_MODEL, "file": ("clip.webm", b"bytes", "audio/webm"), "response_format": "json",
    }
    assert TRANSCRIBE_MODEL == "gpt-4o-mini-transcribe"
    close.assert_awaited_once()


def test_openai_can_transcribe():
    assert OpenAIProvider("k").can_transcribe is True
    for provider in [AnthropicProvider("k"), GoogleProvider("k"), OllamaProvider(), TogetherProvider("k")]:
        assert provider.can_transcribe is False


@pytest.mark.parametrize("active_capable, fallback_capable", [(True, True), (False, True), (False, False), (None, False)])
def test_get_transcriber_prefers_the_active_provider_then_openai(monkeypatch, active_capable, fallback_capable):
    store = object()
    active = SimpleNamespace(can_transcribe=active_capable) if active_capable is not None else None
    fallback = SimpleNamespace(can_transcribe=True) if fallback_capable else None
    calls = []

    def get(agent_provider=None, **kw):
        assert kw["store"] is store
        calls.append(agent_provider)
        return active if agent_provider is None else fallback

    monkeypatch.setattr(providers, "get_ai_provider", get)
    assert providers.get_transcriber(store=store) is (active if active_capable else fallback)
    assert calls == ([None] if active_capable else [None, "openai"])
