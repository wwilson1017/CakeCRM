"""Dreaming scorer — ranks memory facts by usage signals (pure, no AI, no DB).

Ported from the *shape* of Chatty's ``dreaming/scorer.py`` (exponential-decay recency,
log-scaled frequency, linear age window, weighted sum, active/stale/dormant at
0.4/0.1), with chatty's five file signals renormalized to four fact signals.

Weights (sum 1.0):
    retrieval_recency   0.40   ← chatty write_recency .30 + load_rate .15
    retrieval_frequency 0.30   ← chatty write_freq .25 + mention_freq .20
    age                 0.25   ← chatty file_age .10, boosted (age is the only signal a
                                 never-retrieved fact has, and must keep a young fact
                                 safely above ARCHIVE_THRESHOLD)
    confidence          0.05   ← new (facts have it, files didn't)

Two properties this design guarantees, each pinned by a test:
- ``retrieval_frequency`` is **gated by recency** (multiplied by it) so a fact's
  lifetime ``retrieval_count`` cannot create a permanent score floor — a once-retrieved
  fact left untouched decays to dormant. (Chatty's un-gated frequency would floor a
  retrieved fact above ARCHIVE_THRESHOLD forever.)
- ``confidence``'s weight (0.05) is BELOW ARCHIVE_THRESHOLD (0.1), so a default-
  confidence (1.0) fact that is old and unused still scores dormant.
"""

import math

WEIGHT_RETRIEVAL_RECENCY = 0.40
WEIGHT_RETRIEVAL_FREQUENCY = 0.30
WEIGHT_AGE = 0.25
WEIGHT_CONFIDENCE = 0.05

ARCHIVE_THRESHOLD = 0.1     # below = dormant   (chatty verbatim)
STALE_THRESHOLD = 0.4       # below = stale     (chatty verbatim)
HALF_LIFE_DAYS = 14.0       # recency decay     (chatty verbatim)
AGE_WINDOW_DAYS = 90.0      # age signal window (chatty verbatim)

_LAMBDA = math.log(2) / HALF_LIFE_DAYS
_LOG1P_10 = math.log1p(10)


def _f(value, default=0.0) -> float:
    """Coerce to float, tolerating Decimal (psycopg2 numeric) and None."""
    if value is None:
        return default
    return float(value)


def score_fact(
    days_since_retrieved: float | None,
    retrieval_count: int,
    days_old: float,
    confidence: float,
) -> dict:
    """Score a single fact from its usage signals. Returns {score, classification, signals}.

    ``days_since_retrieved`` is None when the fact was never retrieved. Negative day
    deltas (clock skew / future timestamps) are clamped to 0 so no signal exceeds 1.
    """
    # Signal 1: retrieval recency (exponential decay); 0 if never retrieved.
    if days_since_retrieved is None:
        retrieval_recency = 0.0
    else:
        d = max(0.0, _f(days_since_retrieved))
        retrieval_recency = math.exp(-_LAMBDA * d)

    # Signal 2: retrieval frequency, GATED by recency (see module docstring).
    count = max(0, int(retrieval_count or 0))
    retrieval_frequency = min(math.log1p(count) / _LOG1P_10, 1.0) * retrieval_recency

    # Signal 3: age (newer = higher; linear over a 90-day window).
    age = 1.0 - min(max(0.0, _f(days_old)) / AGE_WINDOW_DAYS, 1.0)

    # Signal 4: confidence (clamped 0..1).
    conf = max(0.0, min(_f(confidence, 1.0), 1.0))

    score = (
        WEIGHT_RETRIEVAL_RECENCY * retrieval_recency
        + WEIGHT_RETRIEVAL_FREQUENCY * retrieval_frequency
        + WEIGHT_AGE * age
        + WEIGHT_CONFIDENCE * conf
    )

    if score >= STALE_THRESHOLD:
        classification = "active"
    elif score >= ARCHIVE_THRESHOLD:
        classification = "stale"
    else:
        classification = "dormant"

    return {
        "score": round(score, 3),
        "classification": classification,
        "signals": {
            "retrieval_recency": round(retrieval_recency, 3),
            "retrieval_frequency": round(retrieval_frequency, 3),
            "age": round(age, 3),
            "confidence": round(conf, 3),
        },
    }
