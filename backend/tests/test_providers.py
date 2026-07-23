"""Provider implementations + factory — no network, SDKs mocked."""

import httpx
import pytest

from providers import credentials as cred_mod, get_ai_provider
from providers.anthropic_provider import ANTHROPIC_MODELS, AnthropicProvider
from providers.google_provider import GoogleProvider
from providers.ollama_provider import OllamaProvider
from providers.openai_provider import OpenAIProvider
from providers.together_provider import TogetherProvider


def test_provider_names():
    assert AnthropicProvider(api_key="k").provider_name == "anthropic"
    assert OpenAIProvider(access_token="k").provider_name == "openai"
    assert GoogleProvider(api_key="k").provider_name == "google"
    assert OllamaProvider().provider_name == "ollama"
    assert TogetherProvider(api_key="k").provider_name == "together"


def test_anthropic_context_window():
    assert AnthropicProvider(api_key="k", model="claude-opus-4-8").context_window is not None
    # unknown but recognizably-Claude id -> 200K floor
    assert AnthropicProvider(api_key="k", model="claude-brand-new-9").context_window == 200_000
    # non-Claude id -> unknown
    assert AnthropicProvider(api_key="k", model="gpt-4").context_window is None


def test_add_tool_results_anthropic_shape():
    p = AnthropicProvider(api_key="k")
    msgs = p.add_tool_results(
        [], [{"id": "t1", "name": "foo", "args": {"a": 1}}],
        [{"tool_use_id": "t1", "content": "result"}],
    )
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"][0]["type"] == "tool_use"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"][0]["type"] == "tool_result"


def test_add_tool_results_openai_shape():
    p = OpenAIProvider(access_token="k")
    msgs = p.add_tool_results(
        [], [{"id": "t1", "name": "foo", "args": {"a": 1}}],
        [{"tool_use_id": "t1", "content": "r"}],
    )
    assert msgs[0]["tool_calls"][0]["function"]["name"] == "foo"
    assert msgs[1]["role"] == "tool"
    assert msgs[1]["tool_call_id"] == "t1"


def test_add_tool_results_google_shape():
    p = GoogleProvider(api_key="k")
    msgs = p.add_tool_results(
        [], [{"id": "c0", "name": "foo", "args": {"a": 1}}],
        [{"tool_use_id": "c0", "tool_name": "foo", "content": "r"}],
    )
    assert msgs[0]["content"][0]["_type"] == "function_call"
    assert msgs[1]["content"][0]["_type"] == "function_response"


def test_build_tool_turn_stubs_missing_result_and_reinjects_text():
    p = AnthropicProvider(api_key="k")
    msgs = p.build_tool_turn("hello world", [{"tool_use_id": "t1", "tool": "foo", "args": {}}], [])
    # missing result is stubbed, never orphaned
    assert "result not recorded" in msgs[1]["content"][0]["content"]
    # assistant text re-injected ahead of the tool_use block
    assert msgs[0]["content"][0] == {"type": "text", "text": "hello world"}


@pytest.mark.asyncio
async def test_list_models_falls_back_on_fetch_error(monkeypatch):
    from providers import model_listing
    monkeypatch.setattr(model_listing, "materialize_inference", lambda *a, **k: None)
    p = AnthropicProvider(api_key="k")

    async def boom():
        raise RuntimeError("no network")

    monkeypatch.setattr(p, "_fetch_models", boom)
    assert await p.list_models() == ANTHROPIC_MODELS


@pytest.mark.asyncio
async def test_list_models_live_fetch_materializes(monkeypatch):
    from providers import model_listing
    calls = {}
    monkeypatch.setattr(model_listing, "materialize_inference",
                        lambda provider, models, is_live: calls.update(provider=provider, is_live=is_live))
    p = OpenAIProvider(access_token="k")

    async def fake_fetch():
        return ["gpt-5.5", "gpt-5.4"]

    monkeypatch.setattr(p, "_fetch_models", fake_fetch)
    models = await p.list_models()
    assert models == ["gpt-5.5", "gpt-5.4"]
    assert calls == {"provider": "openai", "is_live": True}


@pytest.mark.asyncio
async def test_ollama_list_models_empty_on_connection_error(monkeypatch):
    class BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", BoomClient)
    assert await OllamaProvider(base_url="http://localhost:11434").list_models() == []


@pytest.mark.asyncio
async def test_ollama_validate_false_when_unreachable(monkeypatch):
    class BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", BoomClient)
    assert await OllamaProvider().validate() is False


# ── Factory (graceful degradation) ────────────────────────────────────────────

def _patch_store(monkeypatch, data):
    monkeypatch.setattr(cred_mod.CredentialStore, "_load", lambda self: data)


def test_factory_none_when_no_profile(monkeypatch):
    _patch_store(monkeypatch, {"active_provider": "", "active_model": "", "profiles": {}})
    assert get_ai_provider() is None


def test_factory_returns_provider_when_configured(monkeypatch):
    _patch_store(monkeypatch, {
        "active_provider": "anthropic", "active_model": "claude-opus-4-8",
        "profiles": {"anthropic:default": {"type": "api_key", "key": "test-x"}},
    })
    p = get_ai_provider()
    assert isinstance(p, AnthropicProvider)
    assert p.api_key == "test-x"


def test_factory_none_when_key_empty(monkeypatch):
    _patch_store(monkeypatch, {
        "active_provider": "anthropic", "active_model": "m",
        "profiles": {"anthropic:default": {"type": "api_key", "key": ""}},
    })
    assert get_ai_provider() is None  # empty key -> not usable -> None


def test_factory_none_when_ollama_url_empty(monkeypatch):
    _patch_store(monkeypatch, {
        "active_provider": "ollama", "active_model": "",
        "profiles": {"ollama:default": {"type": "ollama_local", "base_url": ""}},
    })
    assert get_ai_provider() is None


def test_factory_override_provider(monkeypatch):
    _patch_store(monkeypatch, {
        "active_provider": "anthropic", "active_model": "claude-opus-4-8",
        "profiles": {
            "anthropic:default": {"type": "api_key", "key": "test-a"},
            "together:default": {"type": "api_key", "key": "test-t"},
        },
    })
    p = get_ai_provider(agent_provider="together")
    assert isinstance(p, TogetherProvider)
