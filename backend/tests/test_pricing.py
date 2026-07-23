"""Pricing library — exact/prefix match, cost math, unknown-model handling."""


from providers import pricing


def test_exact_match():
    assert pricing.get_model_pricing("claude-sonnet-4-6") == (3.00, 15.00)


def test_longest_prefix_match_for_dated_snapshot():
    # A dated snapshot id resolves to its base model's price.
    assert pricing.get_model_pricing("claude-haiku-4-5-20251001") == \
        pricing.get_model_pricing("claude-haiku-4-5")


def test_unknown_model_is_unpriced_not_zero_dollars():
    assert pricing.get_model_pricing("totally-unknown-model") is None
    assert pricing.estimate_cost("totally-unknown-model", 1000, 1000) == 0.0
    assert pricing.is_priced("totally-unknown-model") is False


def test_priced_model_flagged_priced():
    assert pricing.is_priced("claude-sonnet-4-6") is True


def test_estimate_cost_arithmetic():
    # (1M * $3 + 1M * $15) / 1M = $18.00
    assert pricing.estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0


def test_every_priced_model_has_a_source():
    for model in pricing.MODEL_PRICING:
        assert model in pricing.PRICING_SOURCES, f"{model} missing a PRICING_SOURCES entry"


def test_source_entries_are_url_and_date():
    for model, (url, date) in pricing.PRICING_SOURCES.items():
        assert url.startswith("http"), f"{model} source is not a URL"
        assert date, f"{model} has no verified date"
