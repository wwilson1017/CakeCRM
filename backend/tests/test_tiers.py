"""Tier inference + resolution (pure logic; resolution reads mocked via _load_row)."""

from providers import tiers


def test_infer_anthropic_by_family_and_version():
    got = tiers.infer_tier_models(
        "anthropic", ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]
    )
    assert got["top"] == "claude-opus-4-8"
    assert got["mid"] == "claude-sonnet-4-6"
    assert got["light"] == "claude-haiku-4-5"


def test_infer_google_lite_excluded_from_mid():
    got = tiers.infer_tier_models(
        "google", ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    )
    assert got["top"] == "gemini-2.5-pro"
    assert got["mid"] == "gemini-2.5-flash"
    assert got["light"] == "gemini-2.5-flash-lite"


def test_infer_openai_prefix_rules():
    got = tiers.infer_tier_models(
        "openai", ["gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano"]
    )
    assert got["light"] == "gpt-5.4-nano"
    assert got["mid"] == "gpt-5.4-mini"
    assert got["top"] == "gpt-5.5"


def test_resolve_tier_model_order(monkeypatch):
    from providers import model_tiers
    monkeypatch.setattr(
        model_tiers, "_load_row",
        lambda p: {"overrides": {"top": "override-top"},
                   "inferred": {"top": "inferred-top", "mid": "inferred-mid"}},
    )
    assert tiers.resolve_tier_model("anthropic", "top") == "override-top"      # override wins
    assert tiers.resolve_tier_model("anthropic", "mid") == "inferred-mid"      # inferred next
    # falls through to the hardcoded TIER_MODELS constant
    assert tiers.resolve_tier_model("anthropic", "light") == \
        tiers.TIER_MODELS["anthropic"]["light"]


def test_resolve_unknown_provider_is_falsy(monkeypatch):
    from providers import model_tiers
    monkeypatch.setattr(model_tiers, "_load_row", lambda p: {})
    assert not tiers.resolve_tier_model("nonexistent-provider", "top")


def test_supports_auto_triage():
    assert tiers.supports_auto_triage("anthropic") is True
    assert tiers.supports_auto_triage("openai") is True
    assert tiers.supports_auto_triage("google") is True
    assert tiers.supports_auto_triage("together") is False
    assert tiers.supports_auto_triage("ollama") is False


def test_derive_tier_labels_short_label():
    labels = tiers.derive_tier_labels("together", {"top": "Qwen/Qwen3.5-32B", "mid": "", "light": ""})
    # slugs are shortened to the last path segment
    assert labels["top"] == "Qwen3.5-32B"


def test_infer_together_size_ranking():
    got = tiers.infer_tier_models(
        "together", ["Qwen/Qwen3.5-32B", "Qwen/Qwen3.5-14B", "Qwen/Qwen3.5-7B"]
    )
    assert got["top"] == "Qwen/Qwen3.5-32B"    # largest by param count
    assert got["light"] == "Qwen/Qwen3.5-7B"   # smallest


def test_infer_never_returns_a_model_absent_from_the_catalog():
    # anthropic catalog with NO opus: the 'top' heuristic/hardcoded fallback would
    # otherwise point at claude-opus-4-8 (absent) — the substitution block must
    # keep every returned tier inside the live catalog.
    catalog = ["claude-sonnet-4-6", "claude-haiku-4-5"]
    got = tiers.infer_tier_models("anthropic", catalog)
    for tier, model in got.items():
        if model:
            assert model in catalog, f"tier {tier}={model!r} is not in the live catalog"
