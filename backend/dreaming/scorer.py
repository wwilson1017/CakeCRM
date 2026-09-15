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

``score_file`` (issue #72 Phase 4) is the second unit, and it is where chatty's file
scorer actually lands. Chatty scores a context file on five signals — write recency,
write frequency, mention frequency, load rate and age — of which only the load/truncate
events had a live producer even there. In THIS tree ``soul.md``/``MEMORY.md`` and both
manifests load unconditionally every turn, so a raw load count is a CONSTANT that would
keep every file alive forever. So the same call #5 made for facts is made again: only an
on-demand read the assistant CHOSE to make is recorded (``context_files.service
.track_read_for``, wired to the three read tool executors), and mention frequency — which
has no producer at all here — is dropped. Five signals renormalize to four:

    read_recency   0.35   <- chatty load_rate, promoted (it is the honest use signal)
    read_frequency 0.20   <- chatty mention_freq, recency-GATED like the fact scorer
    write_recency  0.25   <- chatty write_recency; the one signal a file has and a fact
                             does not, and chatty's top-weighted one
    age            0.20   <- chatty file_age, boosted so a brand-new, never-read file is
                             comfortably above ARCHIVE_THRESHOLD

Thresholds, half-life, age window and the round-then-classify rule are shared with
``score_fact`` deliberately — one set of constants, one tuning surface.
"""

import math

WEIGHT_RETRIEVAL_RECENCY = 0.40
WEIGHT_RETRIEVAL_FREQUENCY = 0.30
WEIGHT_AGE = 0.25
WEIGHT_CONFIDENCE = 0.05

# File weights (sum 1.0) — see the module docstring for the renormalization.
WEIGHT_FILE_READ_RECENCY = 0.35
WEIGHT_FILE_READ_FREQUENCY = 0.20
WEIGHT_FILE_WRITE_RECENCY = 0.25
WEIGHT_FILE_AGE = 0.20

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


def _recency(days_since: float | None) -> float:
    """Exponential-decay recency over HALF_LIFE_DAYS; 0.0 when the event never happened.

    ``None`` means "never" and must stay 0.0 rather than collapsing to "today" — that
    distinction is the whole reason the SELECTs preserve NULL instead of GREATEST-ing it.
    Negative deltas (clock skew / a future timestamp) clamp to 0 so no signal exceeds 1.
    """
    if days_since is None:
        return 0.0
    return math.exp(-_LAMBDA * max(0.0, _f(days_since)))


def _gated_frequency(count, recency: float) -> float:
    """Log-scaled count, MULTIPLIED by its own recency.

    The gate is the correction to chatty: an un-gated lifetime count creates a permanent
    score floor, so anything used once could never be archived. Gated, a thing used a lot
    and then abandoned decays like anything else.
    """
    n = max(0, int(count or 0))
    return min(math.log1p(n) / _LOG1P_10, 1.0) * recency


def _classify(score: float) -> str:
    """active / stale / dormant from a score that has ALREADY been rounded.

    Classifying the rounded value is what stops an audited score sitting on the other
    side of a threshold from its own classification.
    """
    if score >= STALE_THRESHOLD:
        return "active"
    if score >= ARCHIVE_THRESHOLD:
        return "stale"
    return "dormant"


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
    retrieval_recency = _recency(days_since_retrieved)

    # Signal 2: retrieval frequency, GATED by recency (see module docstring).
    retrieval_frequency = _gated_frequency(retrieval_count, retrieval_recency)

    # Signal 3: age (newer = higher; linear over a 90-day window).
    age = 1.0 - min(max(0.0, _f(days_old)) / AGE_WINDOW_DAYS, 1.0)

    # Signal 4: confidence (clamped 0..1).
    conf = max(0.0, min(_f(confidence, 1.0), 1.0))

    # Round first, then classify on the rounded value, so the audited score can never
    # sit on the other side of a threshold from its own classification.
    score = round(
        WEIGHT_RETRIEVAL_RECENCY * retrieval_recency
        + WEIGHT_RETRIEVAL_FREQUENCY * retrieval_frequency
        + WEIGHT_AGE * age
        + WEIGHT_CONFIDENCE * conf,
        3,
    )

    return {
        "score": score,
        "classification": _classify(score),
        "signals": {
            "retrieval_recency": round(retrieval_recency, 3),
            "retrieval_frequency": round(retrieval_frequency, 3),
            "age": round(age, 3),
            "confidence": round(conf, 3),
        },
    }


def score_file(
    days_since_read: float | None,
    read_count: int,
    days_since_written: float | None,
    days_old: float,
) -> dict:
    """Score a single context file from its usage signals. Same shape as ``score_fact``.

    ``days_since_read`` is None when the file has never been read by a read TOOL (the
    only reads that count — see the module docstring). ``days_since_written`` comes from
    ``updated_at``; it is None only if that column were somehow NULL, which the schema
    forbids, so in practice a file always has a write-recency signal.

    Properties pinned by tests rather than by exact numbers:
      written yesterday, never read              ~ 0.436  active
      45 days untouched, never read              ~ 0.127  stale   (kept)
      90 days untouched, never read              ~ 0.003  dormant
      200 days old, read 3x, last read 5 days    ~ 0.364  stale   (kept)
      200 days old, read 3x, last read 40 days   ~ 0.064  dormant
    The last pair is what the recency GATE buys: an un-gated read_count would floor that
    file above the threshold forever.
    """
    read_recency = _recency(days_since_read)
    read_frequency = _gated_frequency(read_count, read_recency)
    write_recency = _recency(days_since_written)
    age = 1.0 - min(max(0.0, _f(days_old)) / AGE_WINDOW_DAYS, 1.0)

    score = round(
        WEIGHT_FILE_READ_RECENCY * read_recency
        + WEIGHT_FILE_READ_FREQUENCY * read_frequency
        + WEIGHT_FILE_WRITE_RECENCY * write_recency
        + WEIGHT_FILE_AGE * age,
        3,
    )

    return {
        "score": score,
        "classification": _classify(score),
        "signals": {
            "read_recency": round(read_recency, 3),
            "read_frequency": round(read_frequency, 3),
            "write_recency": round(write_recency, 3),
            "age": round(age, 3),
        },
    }
