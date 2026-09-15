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


def test_dormant_stale_boundary_direction():
    # Bracket the ARCHIVE_THRESHOLD (0.1): just above → stale, just below → dormant.
    # Verifies the threshold direction without relying on float-exact equality at 0.1.
    # never-retrieved: score = 0.25*age + 0.05  (age = 1 - days_old/90)
    above = scorer.score_fact(None, 0, 60, 1.0)   # age .333 → .0833+.05 = .133 (> 0.1)
    below = scorer.score_fact(None, 0, 85, 1.0)   # age .056 → .0139+.05 = .064 (< 0.1)
    assert above["classification"] == "stale"
    assert below["classification"] == "dormant"


def test_stale_active_boundary_direction():
    # Bracket the STALE_THRESHOLD (0.4): a recently+frequently retrieved fact is active.
    active = scorer.score_fact(0, 8, 0, 1.0)      # recency 1, freq high, age 1 → > 0.4
    assert active["classification"] == "active"
    assert active["score"] > scorer.STALE_THRESHOLD


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


# ── score_file (#72 Phase 4) ────────────────────────────────────────────────────

def test_file_weights_sum_to_one():
    total = (scorer.WEIGHT_FILE_READ_RECENCY + scorer.WEIGHT_FILE_READ_FREQUENCY
             + scorer.WEIGHT_FILE_WRITE_RECENCY + scorer.WEIGHT_FILE_AGE)
    assert round(total, 10) == 1.0


def test_file_written_yesterday_is_active_even_though_never_read():
    # A brand-new topic file the assistant has not re-read yet must be nowhere near
    # archival — otherwise writing a file and moving on would put it away.
    r = scorer.score_file(None, 0, 1.0, 1.0)
    assert r["classification"] == "active"
    assert r["signals"]["read_recency"] == 0.0


def test_file_untouched_for_45_days_is_stale_but_kept():
    r = scorer.score_file(None, 0, 45.0, 45.0)
    assert r["classification"] == "stale"
    assert r["score"] >= scorer.ARCHIVE_THRESHOLD


def test_file_untouched_for_90_days_is_dormant():
    r = scorer.score_file(None, 0, 90.0, 90.0)
    assert r["classification"] == "dormant"
    assert r["score"] < scorer.ARCHIVE_THRESHOLD


def test_file_read_frequency_is_recency_gated():
    # THE property that makes file-dreaming work: an old file read 50 times but not for
    # 60 days must still go dormant. An un-gated read_count would floor it forever —
    # the exact defect the module docstring names in chatty's scorer.
    hot_but_stale = scorer.score_file(60.0, 50, 200.0, 200.0)
    assert hot_but_stale["classification"] == "dormant"
    # ... while the same file read 5 days ago is kept.
    recently_read = scorer.score_file(5.0, 3, 200.0, 200.0)
    assert recently_read["classification"] != "dormant"


def test_file_read_recency_alone_cannot_be_outvoted_by_nothing_else():
    # A 200-day-old, never-rewritten file read TODAY scores on read signals only; it
    # must stay above ARCHIVE_THRESHOLD or on-demand reads would count for nothing.
    r = scorer.score_file(0.0, 1, 200.0, 200.0)
    assert r["signals"]["write_recency"] < 0.01 and r["signals"]["age"] == 0.0
    assert r["classification"] != "dormant"


def test_file_score_is_rounded_before_classification():
    r = scorer.score_file(None, 0, 90.0, 90.0)
    assert r["score"] == round(r["score"], 3)
    assert (r["classification"] == "dormant") == (r["score"] < scorer.ARCHIVE_THRESHOLD)


def test_file_negative_day_deltas_are_clamped():
    r = scorer.score_file(-5.0, 0, -10.0, -10.0)
    assert r["signals"]["read_recency"] == 1.0
    assert r["signals"]["write_recency"] == 1.0
    assert r["signals"]["age"] == 1.0


def test_file_accepts_decimal_inputs():
    r = scorer.score_file(Decimal("1.5"), Decimal("3"), Decimal("30.2"), Decimal("30.2"))
    assert isinstance(r["score"], float)
    assert 0.0 <= r["score"] <= 1.0


def test_file_never_read_is_not_treated_as_read_today():
    never = scorer.score_file(None, 0, 200.0, 200.0)
    today = scorer.score_file(0.0, 0, 200.0, 200.0)
    assert never["score"] < today["score"]
    assert never["signals"]["read_recency"] == 0.0 and today["signals"]["read_recency"] == 1.0
