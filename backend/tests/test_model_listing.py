"""Model-list cache (12h TTL + single-flight) and inferred-tier materialization.
These branches back cost (don't re-hit the vendor on every dropdown open) and tier
correctness (never materialize inferred tiers off stale/fallback data)."""

import time

import pytest

from providers import model_listing, model_tiers


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_skips_fetch():
    key = "prov:abc"
    model_listing._cache[key] = (time.monotonic(), ["cached-1", "cached-2"])
    called = {"n": 0}

    async def fetch():
        called["n"] += 1
        return ["fresh"]

    models, is_live = await model_listing.cached_models(key, fetch, ["fb"])
    assert models == ["cached-1", "cached-2"]
    assert is_live is False          # a cache hit is never "live"
    assert called["n"] == 0          # fetch_fn not invoked


@pytest.mark.asyncio
async def test_expired_cache_serves_last_good_on_fetch_error():
    key = "prov:xyz"
    model_listing._cache[key] = (0.0, ["stale-good"])  # monotonic 0 => far past => expired

    async def fetch():
        raise RuntimeError("api down")

    models, is_live = await model_listing.cached_models(key, fetch, ["fallback"], ttl=1)
    assert models == ["stale-good"]  # last-good, NOT the hardcoded fallback
    assert is_live is False


@pytest.mark.asyncio
async def test_cold_cache_fetch_error_serves_fallback():
    async def fetch():
        raise RuntimeError("down")

    models, is_live = await model_listing.cached_models("cold:key", fetch, ["fb1", "fb2"])
    assert models == ["fb1", "fb2"]
    assert is_live is False


@pytest.mark.asyncio
async def test_live_fetch_caches_then_serves_cached():
    async def fetch():
        return ["m1", "m2"]

    models, is_live = await model_listing.cached_models("live:key", fetch, ["fb"])
    assert models == ["m1", "m2"]
    assert is_live is True

    async def fetch_again():
        raise AssertionError("must not refetch within TTL")

    models2, is_live2 = await model_listing.cached_models("live:key", fetch_again, ["fb"])
    assert models2 == ["m1", "m2"]
    assert is_live2 is False


@pytest.mark.asyncio
async def test_empty_fetch_result_is_not_live():
    async def fetch():
        return []  # empty is treated as a non-fetch → fallback, not live

    models, is_live = await model_listing.cached_models("empty:key", fetch, ["fb"])
    assert models == ["fb"]
    assert is_live is False


def test_cache_key_hides_secret_and_segments_per_credential():
    k1 = model_listing.cache_key("anthropic", "secret-A")
    k2 = model_listing.cache_key("anthropic", "secret-B")
    assert k1 != k2                       # rotating the key busts the cache
    assert "secret-A" not in k1           # the secret never appears in the key
    assert k1.startswith("anthropic:")


def test_materialize_inference_only_on_live_fetch(monkeypatch):
    calls = []
    monkeypatch.setattr(model_tiers, "set_inferred", lambda p, mapping: calls.append((p, mapping)))
    model_listing.materialize_inference("anthropic", ["claude-opus-4-8"], is_live=False)
    assert calls == []  # stale/fallback data must NOT overwrite inferred tiers
    model_listing.materialize_inference(
        "anthropic", ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"], is_live=True
    )
    assert len(calls) == 1 and calls[0][0] == "anthropic"


def test_materialize_inference_swallows_persist_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(model_tiers, "set_inferred", boom)
    # A failed persist must never break the model dropdown.
    model_listing.materialize_inference("anthropic", ["claude-opus-4-8"], is_live=True)
