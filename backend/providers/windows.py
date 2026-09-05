"""CakeCRM — Model context-window sizes and context-usage event building.

Static per-model context-window sizes (in tokens) for known models, used to
drive the chat composer's "context fullness" meter. Mirrors the resolution
pattern in pricing.py: exact match first, then longest-prefix match so dated
snapshots (e.g. claude-haiku-4-5-20251001) resolve to their base model.
Unknown models return None — the meter is hidden rather than guessed.
"""

from __future__ import annotations

# model id -> context window in tokens
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # `claude-opus-4-8` is providers.get_ai_provider's DEFAULT Anthropic model and
    # tiers.TIER_MODELS' "top" entry, and it was missing here — "claude-opus-4-6" is
    # not a prefix of it, so get_context_window returned None and the composer's
    # context meter was hidden on a default install. Found while porting conversation
    # compaction (#72 Phase 3), which reads this to size its trigger.
    "claude-opus-4-8":   200_000,
    "claude-opus-4-6":   200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5":  200_000,
}


def get_context_window(model: str) -> int | None:
    """Resolve the context-window size (tokens) for a model id.

    Exact match first, then longest-prefix match so dated snapshots resolve
    to their base model. Returns None for unknown models.
    """
    if not model:
        return None
    exact = MODEL_CONTEXT_WINDOWS.get(model)
    if exact is not None:
        return exact
    best_key = None
    for key in MODEL_CONTEXT_WINDOWS:
        if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return MODEL_CONTEXT_WINDOWS[best_key] if best_key else None


def cache_inclusive_input_tokens(usage: dict) -> int | None:
    """Total input tokens the model actually READ this turn, or None if unusable.

    With prompt caching active most of the input lives in cache_read /
    cache_creation rather than in the plain ``input_tokens`` field, so summing all
    three is the only honest measure of how full the context is.

    Split out of ``context_usage_event`` because the two callers ask different
    questions. The meter needs a window to render a percentage and hides itself
    without one; conversation compaction (#72 Phase 3) needs only the NUMBER, and
    persists it whether or not the window happens to be known — an accurate count
    against an assumed budget still beats a chars/4 estimate.

    Returns None for missing/empty usage or a non-positive total, so a caller can
    tell "no signal" apart from a genuine zero.
    """
    if not usage:
        return None
    # `or 0` guards a provider that supplies an explicit None for any field
    # (this helper is provider-generic; Anthropic returns ints today).
    total = (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )
    return total if total > 0 else None


def context_usage_event(usage: dict, context_window: int | None,
                        meter_only: bool = False) -> dict | None:
    """Build the 'usage' SSE payload, or None when the meter can't be shown.

    The composer meter shows how full the context window is, so it needs the
    cache-inclusive total input the model actually read this turn — with prompt
    caching active most of that lives in cache_read/cache_creation, not in the
    plain input_tokens field. That cache-inclusive total is emitted as
    `context_tokens`.

    `input_tokens` / `output_tokens` stay RAW: other consumers depend on them —
    the CLI accumulates them into its session total (backend/cli/output.py) and
    the activity/cost log sums them. Only `context_tokens` is cache-inclusive,
    and it is emit-only (never accumulated).

    `meter_only=True` is used for the plan-mode / pending-confirmation wrap-up
    turns. Those turns are deliberately excluded from token/cost accounting (the
    backend cost log never accumulates them), so we zero the raw
    input_tokens/output_tokens to keep the CLI session total — which sums every
    usage event it sees — from picking them up. The meter still updates from
    `context_tokens`.

    Returns None when there's no usage data, no known window, or no usable
    input-side count — so the caller emits nothing and the meter stays hidden
    rather than rendering a misleading 0% / negative.
    """
    if context_window is None:
        return None
    context_tokens = cache_inclusive_input_tokens(usage)
    if context_tokens is None:
        # No usable input-side count (e.g. output-only or malformed usage, or a
        # negative from a buggy provider) — hide the meter instead of showing a
        # bogus 0% / negative reading.
        return None
    return {
        "type": "usage",
        "input_tokens": 0 if meter_only else (usage.get("input_tokens") or 0),
        "output_tokens": 0 if meter_only else (usage.get("output_tokens") or 0),
        "context_tokens": context_tokens,                  # cache-inclusive — meter
        "context_window": context_window,
    }
