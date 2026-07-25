"""Memory type classification — 10 types with durability tiers.

Every temporal fact can optionally carry a *memory type* that indicates what kind
of knowledge it represents. The type's durability tier gates the dreaming job's
archival: tier-1 facts (``decision`` / ``preference``) are never archived, the
single-assistant analog of chatty's ``PROTECTED_FILES``.

Ported near-verbatim from Chatty's ``core/agents/memory/types.py``; the inline
markdown tag helpers (``type_tag`` / ``extract_type_tag``) are dropped — they served
chatty's daily-note document layer, which does not exist here.
"""

MEMORY_TYPES: set[str] = {
    "decision",
    "preference",
    "problem",
    "milestone",
    "insight",
    "person",
    "task",
    "idea",
    "reference",
    "someday-maybe",
}

# ---------------------------------------------------------------------------
# Durability tiers  (lower number = more durable)
# ---------------------------------------------------------------------------

TIER_1_ALWAYS_KEEP: set[str] = {"decision", "preference"}
TIER_2_KEEP_IF_RECENT: set[str] = {"person", "insight", "reference"}
TIER_3_KEEP_IF_RELEVANT: set[str] = {"milestone", "idea", "problem"}
TIER_4_DROP_WHEN_RESOLVED: set[str] = {"task", "someday-maybe"}

DURABILITY_TIERS: dict[str, int] = {}
for _t in TIER_1_ALWAYS_KEEP:
    DURABILITY_TIERS[_t] = 1
for _t in TIER_2_KEEP_IF_RECENT:
    DURABILITY_TIERS[_t] = 2
for _t in TIER_3_KEEP_IF_RELEVANT:
    DURABILITY_TIERS[_t] = 3
for _t in TIER_4_DROP_WHEN_RESOLVED:
    DURABILITY_TIERS[_t] = 4


def validate_memory_type(memory_type: str | None) -> str | None:
    """Normalize and validate a memory type.  Returns *None* if invalid."""
    if not memory_type:
        return None
    normalized = memory_type.strip().lower()
    return normalized if normalized in MEMORY_TYPES else None
