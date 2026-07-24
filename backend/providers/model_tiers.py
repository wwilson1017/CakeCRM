"""CakeCRM — per-provider tier assignments (inferred defaults + user overrides).

The tier system (top/mid/light) must resolve *synchronously* because
resolve_tier_model() is called from synchronous code paths (get_ai_provider,
the assistant engine). The provider model APIs are async, so we can't list models
at resolution time. Instead:

  * the async listing path materializes name-based inference into this store
    (set_inferred), only on a genuine live fetch; and
  * the user's explicit choices are stored as overrides (set_overrides).

resolve_tier_model() then reads this store synchronously:
    per-tier override -> inferred -> hardcoded TIER_MODELS fallback.

Storage adaptation vs. chatty: chatty kept this in data/model-tiers.json guarded
by a threading lock. CakeCRM is Postgres-mandatory, so it lives in the
``ai_model_tiers`` table (one row per tiered provider; JSONB ``overrides`` /
``inferred`` columns). A DB transaction replaces the file lock.

Row-shape note: ``pg_fetchone`` returns a DICT (column name -> value; psycopg2
decodes JSONB columns to Python dicts). Inside ``get_connection()`` a raw
``conn.cursor()`` returns TUPLES, so ``cur.fetchone()[0]`` is the first column.
Read paths swallow DB errors into empty dicts so tier resolution degrades to the
hardcoded TIER_MODELS instead of raising; write paths fail loudly (their one
async caller, materialize_inference, wraps the call in try/except).
"""

from __future__ import annotations

import logging

from psycopg2.extras import Json

from core.postgres import get_connection, pg_fetchone

logger = logging.getLogger(__name__)

TIERS = ("top", "mid", "light")

# Sanity cap on persisted model ids — guards against a misbehaving/compromised
# provider API returning absurd strings.
_MAX_MODEL_ID_LEN = 200


def _load_row(provider: str) -> dict:
    """Return {'overrides': {...}, 'inferred': {...}} for a provider, or {} on a
    missing row or ANY DB error (logged) — mirrors chatty's corrupt-file fallback
    so resolution degrades to the hardcoded TIER_MODELS instead of raising."""
    try:
        row = pg_fetchone(
            "SELECT overrides, inferred FROM ai_model_tiers WHERE provider = %s",
            (provider,),
        )
    except Exception as e:
        logger.error("Failed to read ai_model_tiers for %s: %s", provider, e)
        return {}
    return row or {}


def get_resolved(provider: str) -> dict[str, str]:
    """Return {top, mid, light} using override -> inferred -> hardcoded fallback."""
    from providers.tiers import TIER_MODELS

    entry = _load_row(provider)
    overrides = entry.get("overrides", {}) or {}
    inferred = entry.get("inferred", {}) or {}
    fallback = TIER_MODELS.get(provider, {})
    return {
        t: overrides.get(t) or inferred.get(t) or fallback.get(t, "")
        for t in TIERS
    }


def get_overrides(provider: str) -> dict[str, str]:
    return (_load_row(provider).get("overrides", {}) or {})


def has_explicit_tier(provider: str, tier: str) -> bool:
    """True if a user override or live-inferred value exists for this tier — i.e.
    resolution would NOT fall through to the hardcoded TIER_MODELS constant.

    Inspects the raw store directly (not get_resolved, which always returns a
    hardcoded fallback). Used so background runs prefer the user's configured
    active_model over a baked-in constant on a fresh deploy with no tiers row.
    """
    entry = _load_row(provider)
    return bool(
        (entry.get("overrides", {}) or {}).get(tier)
        or (entry.get("inferred", {}) or {}).get(tier)
    )


def _sanitized(mapping: dict) -> dict[str, str]:
    """Keep only tier keys with a non-empty, sane-length string model id."""
    return {
        t: mapping[t]
        for t in TIERS
        if mapping.get(t)
        and isinstance(mapping[t], str)
        and len(mapping[t]) <= _MAX_MODEL_ID_LEN
    }


def set_inferred(provider: str, mapping: dict[str, str]) -> None:
    """Persist the name-inferred tier defaults for a provider (live-fetch only).

    Atomic UPSERT that replaces the ``inferred`` column wholesale. Called at most
    once per 12h per credential from the async materialize_inference path.
    """
    inferred = _sanitized(mapping)
    with get_connection() as conn:
        conn.cursor().execute(
            """
            INSERT INTO ai_model_tiers (provider, inferred) VALUES (%s, %s)
            ON CONFLICT (provider) DO UPDATE
            SET inferred = EXCLUDED.inferred, updated_at = now()
            """,
            (provider, Json(inferred)),
        )


def set_overrides(provider: str, mapping: dict[str, str | None]) -> None:
    """Persist user tier overrides. A falsy value for a tier clears that override.

    Only the tier keys present in ``mapping`` are touched; others are left as-is.
    One transaction with SELECT ... FOR UPDATE (repo check-then-write rule).
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_model_tiers (provider) VALUES (%s) "
            "ON CONFLICT (provider) DO NOTHING",
            (provider,),
        )
        cur.execute(
            "SELECT overrides FROM ai_model_tiers WHERE provider = %s FOR UPDATE",
            (provider,),
        )
        row = cur.fetchone()
        overrides = (row[0] if row else {}) or {}
        for t in TIERS:
            if t in mapping:
                value = mapping[t]
                if value and isinstance(value, str) and len(value) <= _MAX_MODEL_ID_LEN:
                    overrides[t] = value
                else:
                    overrides.pop(t, None)
        cur.execute(
            "UPDATE ai_model_tiers SET overrides = %s, updated_at = now() "
            "WHERE provider = %s",
            (Json(overrides), provider),
        )
