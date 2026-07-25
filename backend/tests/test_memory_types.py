"""memory/types.py — the memory-type taxonomy + durability tiers (pure)."""

from memory.types import (
    DURABILITY_TIERS,
    MEMORY_TYPES,
    TIER_1_ALWAYS_KEEP,
    validate_memory_type,
)


def test_ten_memory_types_ported():
    assert MEMORY_TYPES == {
        "decision", "preference", "problem", "milestone", "insight",
        "person", "task", "idea", "reference", "someday-maybe",
    }


def test_tier1_is_decision_and_preference():
    # These are the "never archive" facts — the PROTECTED_FILES analog for dreaming.
    assert TIER_1_ALWAYS_KEEP == {"decision", "preference"}


def test_every_type_has_a_durability_tier():
    assert set(DURABILITY_TIERS) == MEMORY_TYPES
    assert all(1 <= t <= 4 for t in DURABILITY_TIERS.values())
    assert DURABILITY_TIERS["decision"] == 1
    assert DURABILITY_TIERS["preference"] == 1


def test_validate_normalizes_and_rejects():
    assert validate_memory_type("  Decision ") == "decision"
    assert validate_memory_type("PERSON") == "person"
    assert validate_memory_type("bogus") is None
    assert validate_memory_type("") is None
    assert validate_memory_type(None) is None


def test_validate_rejects_non_string():
    assert validate_memory_type(123) is None
    assert validate_memory_type(["decision"]) is None
    assert validate_memory_type({"x": 1}) is None
