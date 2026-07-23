"""
CakeCRM — Provider management API endpoints (key-based providers only).

Two routers:
  * router       (mounted at /api/providers) — connect/disconnect keys, list
    models live, manage tiers, switch the active provider/model.
  * setup_router (mounted at /api/setup)     — GET /status, the AI-readiness /
    graceful-degradation gate the CRM UI (issue #3) keys AI affordances off.

All endpoints require auth. Provider SDKs are only touched through the provider
classes (imported lazily), so a zero-key install never errors here.
"""

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.auth import get_current_user
from core.config import settings
from providers.credentials import CredentialStore

logger = logging.getLogger(__name__)

router = APIRouter()
setup_router = APIRouter()

KEY_PROVIDERS = ("anthropic", "openai", "google", "together")
ALL_PROVIDERS = ("anthropic", "openai", "google", "ollama", "together")
DISPLAY_NAMES = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "together": "Together AI",
    "ollama": "Ollama",
}
DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.4",
    "google": "gemini-2.5-flash",
    "together": "Qwen/Qwen3.5-7B",
}


def _make_key_provider(provider: str, api_key: str, model: str):
    """Lazily construct a key-based provider instance (SDKs imported on demand)."""
    if provider == "anthropic":
        from providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model)
    if provider == "openai":
        from providers.openai_provider import OpenAIProvider
        return OpenAIProvider(access_token=api_key, model=model)
    if provider == "google":
        from providers.google_provider import GoogleProvider
        return GoogleProvider(api_key=api_key, model=model)
    if provider == "together":
        from providers.together_provider import TogetherProvider
        return TogetherProvider(api_key=api_key, model=model)
    raise HTTPException(status_code=404, detail="Unknown provider")


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("")
async def get_providers(user=Depends(get_current_user)):
    """Return current provider configuration (no raw keys)."""
    store = CredentialStore()
    result = store.to_dict()
    result["is_railway"] = settings.is_railway
    return result


@router.get("/tiers")
async def get_tiers(user=Depends(get_current_user)):
    """Return resolved tier configuration for all providers.

    Cheap and store-only — does NOT list models live (the tier picker fetches
    options from GET /{provider}/models, which is cached). tier_models reflect
    override -> inferred -> hardcoded; tier_labels are always non-empty.
    """
    from providers.model_tiers import get_resolved
    from providers.tiers import TIER_MODELS, derive_tier_labels, supports_auto_triage
    store = CredentialStore()
    providers = list(TIER_MODELS.keys())
    tier_models = {p: get_resolved(p) for p in providers}
    return {
        "active_provider": store.data.get("active_provider", ""),
        "tier_models": tier_models,
        "tier_labels": {p: derive_tier_labels(p, tier_models[p]) for p in providers},
        "auto_triage_providers": [p for p in providers if supports_auto_triage(p)],
    }


class SetTiersRequest(BaseModel):
    provider: str
    models: dict[str, str]  # subset of {top,mid,light} -> model id ("" clears the override)


@router.put("/tiers")
async def set_tiers(body: SetTiersRequest, user=Depends(get_current_user)):
    """Persist user tier overrides for a provider, validating each requested model
    id against the provider's current (cached) model list before writing."""
    from providers import get_ai_provider, model_tiers
    from providers.tiers import TIER_MODELS

    if body.provider not in TIER_MODELS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider}")
    bad_keys = set(body.models) - {"top", "mid", "light"}
    if bad_keys:
        raise HTTPException(status_code=400, detail=f"Invalid tier keys: {sorted(bad_keys)}")

    nonempty = {t: m for t, m in body.models.items() if m}
    if nonempty:
        provider = get_ai_provider(agent_provider=body.provider)
        if provider is None:
            raise HTTPException(status_code=400, detail=f"Provider not configured: {body.provider}")
        available = await provider.list_models()  # cached; not under any store lock
        for tier, model in nonempty.items():
            if len(model) > 200:
                raise HTTPException(status_code=400, detail=f"Model id too long for tier '{tier}'")
            if model not in available:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{model}' is not an available {body.provider} model",
                )

    model_tiers.set_overrides(body.provider, body.models)
    return {
        "ok": True,
        "provider": body.provider,
        "tier_models": model_tiers.get_resolved(body.provider),
    }


# ── Inferred default model after a credential save ────────────────────────────

async def _materialize_inferred_tiers(provider_name: str, requested_model: str = "") -> str:
    """After credentials are saved: live-list models so the inferred tier defaults
    get materialized into the tier store (list_models persists them on a live fetch),
    giving the tier picker good defaults.

    Deliberately does NOT change the user's active model — that was set by the
    preceding store.set_*() to the requested/curated model. Returns the model to
    echo in the response body.
    """
    from providers import get_ai_provider
    try:
        provider = get_ai_provider(agent_provider=provider_name)
        if provider is not None:
            await provider.list_models()  # live fetch -> materialize inferred tiers
    except Exception as e:
        logger.warning("Tier materialization for %s failed: %s", provider_name, e)
    return requested_model or CredentialStore().data.get("active_model", "")


# ── Connect a key-based provider (normalized across all four) ──────────────────

class ConnectKeyRequest(BaseModel):
    api_key: str = Field(max_length=8192)   # bound the write to PG / Fernet / the SDK
    model: str = Field(default="", max_length=256)


@router.post("/{provider}/connect-key")
async def connect_key(provider: str, body: ConnectKeyRequest, user=Depends(get_current_user)):
    """Validate and store an API key for a key-based provider, then materialize
    inferred tier defaults. Validation is inline (there is no separate test route)."""
    if provider not in KEY_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required")

    model = body.model or DEFAULT_MODELS[provider]
    prov = _make_key_provider(provider, api_key, model)
    if not await prov.validate():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not validate {DISPLAY_NAMES[provider]} API key — check the "
                f"key is correct and the service is reachable."
            ),
        )

    store = CredentialStore()
    store.set_api_key(provider, api_key, model=body.model or None)
    resolved = await _materialize_inferred_tiers(provider, body.model)
    return {"ok": True, "provider": provider, "model": resolved}


# ── Connect Ollama (local, keyless) ───────────────────────────────────────────

class OllamaConnectRequest(BaseModel):
    base_url: str = Field(default="http://localhost:11434", max_length=2048)
    model: str = Field(default="", max_length=256)


def _validated_ollama_url(raw: str) -> str:
    """Validate a user-supplied Ollama base URL before the server fetches it (SSRF
    guard). Local/LAN targets are intentionally ALLOWED — a self-hosted Ollama on
    localhost or the LAN is the legitimate use case — but the scheme must be
    http(s), the host must be present, and credentials/fragments are rejected."""
    url = (raw or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Ollama base URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Ollama base URL must be an http(s) URL")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="Ollama base URL is missing a host")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Ollama base URL must not contain credentials")
    if parsed.fragment:
        raise HTTPException(status_code=400, detail="Ollama base URL must not contain a fragment")
    return url


@router.post("/ollama/connect")
async def connect_ollama(body: OllamaConnectRequest, user=Depends(get_current_user)):
    """Validate Ollama is reachable and store the connection."""
    from providers.ollama_provider import OllamaProvider
    base_url = _validated_ollama_url(body.base_url)
    provider = OllamaProvider(base_url=base_url)
    if not await provider.validate():
        raise HTTPException(
            status_code=400,
            detail="Cannot connect to Ollama. Make sure it's running (ollama serve).",
        )
    models = await provider.list_models()
    if not models:
        raise HTTPException(
            status_code=400,
            detail="Ollama is running but no models are installed. Run: ollama pull qwen3.5:4b",
        )
    selected = body.model if body.model in models else models[0]
    store = CredentialStore()
    store.set_ollama(base_url=base_url, model=selected)
    return {"ok": True, "provider": "ollama", "models": models, "model": selected}


@router.get("/ollama/status")
async def ollama_status(user=Depends(get_current_user)):
    """Check if Ollama is running locally (auto-detect for the frontend). Uses the
    default localhost base URL — no user-supplied URL, so no SSRF surface."""
    from providers.ollama_provider import OllamaProvider
    provider = OllamaProvider()
    try:
        reachable = await provider.validate()
        models = await provider.list_models() if reachable else []
    except Exception:
        reachable, models = False, []
    return {"reachable": reachable, "models": models}


# ── Disconnect ────────────────────────────────────────────────────────────────

@router.post("/{provider}/disconnect")
async def disconnect_provider(provider: str, user=Depends(get_current_user)):
    """Remove credentials for a provider."""
    if provider not in ALL_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    store = CredentialStore()
    store.remove_provider(provider)
    return {"ok": True, "provider": provider}


# ── Set active provider / model ───────────────────────────────────────────────

class SetActiveRequest(BaseModel):
    provider: str
    model: str = Field(max_length=256)


@router.put("/active")
async def set_active(body: SetActiveRequest, user=Depends(get_current_user)):
    """Atomically switch the active provider AND model. If the requested model
    isn't in the provider's catalog, derive a provider-local default (top tier)
    so switching providers never carries over another provider's model. Returns
    the PERSISTED model."""
    if body.provider not in ALL_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    store = CredentialStore()
    if not store.is_provider_usable(body.provider):
        raise HTTPException(status_code=400, detail=f"No usable credentials for provider: {body.provider}")

    model = body.model
    try:
        from providers import get_ai_provider
        p = get_ai_provider(agent_provider=body.provider)
        available = await p.list_models() if p else []
    except Exception:
        available = []

    if body.provider == "ollama":
        # Ollama has no tier defaults — its "default" is whatever is locally
        # installed. Pick an installed model rather than blanking active_model when
        # the request omits one (or names one that isn't installed).
        if available and model not in available:
            model = available[0]
    elif model and available and model not in available:
        model = ""  # not in this key provider's catalog → set_active resolves the top-tier default

    persisted = store.set_active(body.provider, model)
    return {"ok": True, "provider": body.provider, "model": persisted}


# ── List models ───────────────────────────────────────────────────────────────

@router.get("/{provider}/models")
async def list_models(provider: str, user=Depends(get_current_user)):
    """Return available models for the given provider (live -> cache -> fallback)."""
    if provider not in ALL_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    from providers import get_ai_provider
    p = get_ai_provider(agent_provider=provider)
    if not p:
        raise HTTPException(status_code=400, detail=f"Provider not configured: {provider}")
    models = await p.list_models()
    return {"provider": provider, "models": models}


# ── Setup status (AI-readiness / degradation gate) ────────────────────────────

@setup_router.get("/status")
async def setup_status(user=Depends(get_current_user)):
    """AI-readiness gate for the frontend. ``ai_ready`` (the active provider
    resolves to a usable provider) is the flag the CRM UI keys AI affordances off;
    ``credentials_present`` is the softer "any provider configured" signal."""
    from providers import get_ai_provider
    store = CredentialStore()
    try:
        ai_ready = get_ai_provider() is not None
    except Exception:
        ai_ready = False
    return {
        "ai_ready": ai_ready,
        "credentials_present": store.is_configured(),
        "active_provider": store.data.get("active_provider", ""),
        "active_model": store.data.get("active_model", ""),
        "configured_providers": store.configured_providers(),
    }
