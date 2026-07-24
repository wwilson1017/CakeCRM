"""
CakeCRM — AI provider factory.

Returns the active AIProvider based on credentials stored in Postgres (the
ai_providers / ai_settings tables). Returns None when no provider has USABLE
credentials — the backbone of the zero-keys graceful-degradation rule. Provider
modules are imported lazily inside the factory so a missing SDK never breaks
import or startup.
"""

from providers.base import AIProvider
from providers.credentials import CredentialStore


def get_ai_provider(
    agent_provider: str | None = None,
    agent_model: str | None = None,
    agent_model_tier: str | None = None,
) -> AIProvider | None:
    """
    Return an initialized AIProvider for the active (or specified) provider.

    Args:
        agent_provider: Optional per-agent provider override ("anthropic", "openai",
            "google", "ollama", "together").
        agent_model: Optional per-agent model override (takes precedence over tier).
        agent_model_tier: Optional tier ("auto", "top", "mid", "light").
            For "auto", resolves to "top" here (triage runs separately later).

    Returns None if no provider is configured OR the resolved provider's stored
    credentials are not usable (empty key / empty Ollama URL / mismatched type).
    """
    store = CredentialStore()
    profile_name, profile = store.get_active_profile(provider_override=agent_provider)

    if not profile:
        return None

    provider_key = profile_name.split(":")[0]
    # Graceful degradation: only hand back a provider when its credentials are
    # actually usable — an empty-key row or empty Ollama URL must resolve to None.
    if not store.is_provider_usable(provider_key):
        return None

    # Resolve model: agent_model > tier > global active_model
    if agent_model:
        raw_model = agent_model
    elif agent_model_tier:
        from providers.model_tiers import has_explicit_tier
        from providers.tiers import resolve_tier_model
        tier = "top" if agent_model_tier == "auto" else agent_model_tier
        active_model = store.data.get("active_model", "")
        active_provider = store.data.get("active_provider", "")
        if has_explicit_tier(provider_key, tier):
            raw_model = resolve_tier_model(provider_key, tier) or ""
        elif tier == "top" and active_model and provider_key == active_provider:
            # Fresh deploy / no tiers materialized yet: respect the user's
            # configured active_model instead of the hardcoded TIER_MODELS
            # constant, so a PR that bumps the constant can't silently swap the
            # model used by background runs. mid/light keep the hardcoded
            # fallback (they were never the user's explicit pick).
            raw_model = active_model
        else:
            raw_model = resolve_tier_model(provider_key, tier) or ""
    else:
        # Inherit the global active_model ONLY when the resolved provider is the
        # active one. An override to a different provider must fall through to that
        # provider's own default (below), never carry the active provider's model id.
        active_provider = store.data.get("active_provider", "")
        raw_model = store.data.get("active_model", "") if provider_key == active_provider else ""
    model = raw_model if raw_model and raw_model != "default" else ""

    if profile_name.startswith("anthropic:"):
        from providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=profile.get("key", ""), model=model or "claude-opus-4-8")

    elif profile_name.startswith("openai:"):
        from providers.openai_provider import OpenAIProvider
        return OpenAIProvider(access_token=profile.get("key", ""), model=model or "gpt-5.4")

    elif profile_name.startswith("google:"):
        from providers.google_provider import GoogleProvider
        return GoogleProvider(api_key=profile.get("key", ""), model=model or "gemini-2.5-flash")

    elif profile_name.startswith("ollama:"):
        from providers.ollama_provider import OllamaProvider
        base_url = profile.get("base_url", "http://localhost:11434")
        return OllamaProvider(base_url=base_url, model=model or "")

    elif profile_name.startswith("together:"):
        from providers.together_provider import TogetherProvider
        return TogetherProvider(api_key=profile.get("key", ""), model=model or "Qwen/Qwen3.5-7B")

    return None
