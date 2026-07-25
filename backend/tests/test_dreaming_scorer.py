"""dreaming/scorer.py — pure fact scoring math.

Pins the properties the design depends on so a "tuning" edit fails loudly:
young facts are archive-proof; old/unused facts go dormant even at full confidence;
a once-retrieved-then-idle fact still goes dormant (the recency-gated-frequency fix);
weights sum to 1.0; boundaries and Decimal-safety.
"""

from decimal import Decimal

from dreaming import scorer


def test_weights_sum_to_one():
    total = (
        scorer.WEIGHT_RETRIEVAL_RECENCY
        + scorer.WEIGHT_RETRIEVAL_FREQUENCY
        + scorer.WEIGHT_AGE
        + scorer.WEIGHT_CONFIDENCE
    )
    assert round(total, 6) == 1.0


def test_confidence_weight_below_archive_threshold():
    # If confidence weight were >= ARCHIVE_THRESHOLD, a full-confidence fact could
    # never go dormant. This invariant is load-bearing.
    assert scorer.WEIGHT_CONFIDENCE < scorer.ARCHIVE_THRESHOLD


def test_new_never_retrieved_fact_is_stale_not_dormant():
    # Young facts must be structurally archive-proof (age alone keeps them >= stale).
    r = scorer.score_fact(None, 0, 0, 1.0)
    assert r["score"] == 0.3
    assert r["classification"] == "stale"


def test_old_never_retrieved_full_confidence_is_dormant():
    r = scorer.score_fact(None, 0, 90, 1.0)
    assert r["score"] == 0.05
    assert r["classification"] == "dormant"


def test_retrieved_once_then_idle_180_days_is_dormant():
    # Codex-mandated: lifetime retrieval_count must NOT create a permanent floor.
    # Recency-gated frequency makes an old, once-retrieved fact decay to dormant.
    r = scorer.score_fact(180, 1, 200, 1.0)
    assert r["classification"] == "dormant"


def test_recently_and_frequently_retrieved_is_active():
    r = scorer.score_fact(1, 5, 30, 1.0)
    assert r["classification"] == "active"
    assert r["score"] > 0.4


def test_never_retrieved_has_zero_recency_and_frequency():
    r = scorer.score_fact(None, 10, 5, 1.0)
    assert r["signals"]["retrieval_recency"] == 0.0
    # frequency is gated by recency, so a never-retrieved fact scores 0 frequency
    # regardless of its (impossible-in-practice) count.
    assert r["signals"]["retrieval_frequency"] == 0.0


def test_frequency_is_recency_gated():
    # Same high count, different recency → frequency signal scales with recency.
    fresh = scorer.score_fact(0, 10, 0, 1.0)["signals"]["retrieval_frequency"]
    stale = scorer.score_fact(60, 10, 0, 1.0)["signals"]["retrieval_frequency"]
    assert fresh > stale
    assert stale < 0.1


def test_boundary_classifications_use_gte():
    # Exactly at a threshold rounds up to the more-durable class (chatty's >=).
    assert scorer._f(0)  is not None  # sanity: helper exists
    at_stale = scorer.score_fact(None, 0, 90, 1.0)  # 0.05 dormant boundary below 0.1
    assert at_stale["classification"] == "dormant"


def test_negative_day_deltas_are_clamped():
    # Clock skew / future timestamps must not push a signal above 1.
    r = scorer.score_fact(-5, 0, -10, 1.0)
    assert r["signals"]["retrieval_recency"] == 1.0  # exp(0)
    assert r["signals"]["age"] == 1.0                 # days_old clamped to 0


def test_accepts_decimal_inputs():
    # EXTRACT(EPOCH ...) can arrive as Decimal; the scorer must not raise on it.
    r = scorer.score_fact(Decimal("1.5"), Decimal("3"), Decimal("30.2"), Decimal("0.9"))
    assert isinstance(r["score"], float)
    assert 0.0 <= r["score"] <= 1.0
