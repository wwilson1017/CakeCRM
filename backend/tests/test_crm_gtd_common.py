"""Todo-GTD leaf-module tests: validation and recurrence date math (#70).

Pure functions, no database. The recurrence cases are the ones worth pinning — a
month-end or DST-adjacent off-by-one silently reschedules someone's real life.
"""

import datetime

import pytest

from crm import gtd_common as c

# ── Validation ────────────────────────────────────────────────────────────────

def test_validate_due_accepts_iso_and_empty():
    assert c.validate_due("2026-08-21") == "2026-08-21"
    assert c.validate_due("") == ""
    assert c.validate_due(None) == ""
    assert c.validate_due(datetime.date(2026, 8, 21)) == "2026-08-21"


def test_validate_due_rejects_a_well_shaped_but_impossible_date():
    """The regex alone would pass 2026-13-40 through to Postgres, where it surfaces
    as a driver error (500) instead of a clean 400."""
    with pytest.raises(c.ValidationError, match="not a real calendar date"):
        c.validate_due("2026-13-40")


def test_validate_due_rejects_a_wrong_shape():
    with pytest.raises(c.ValidationError, match="YYYY-MM-DD"):
        c.validate_due("21/08/2026")


@pytest.mark.parametrize("value", ["", "daily", "weekdays", "weekly", "monthly", "yearly",
                                   "every:1", "every:9999", "NONE", "none"])
def test_validate_repeat_accepts_the_vocabulary(value):
    assert c.validate_repeat(value) in c.REPEAT_OPTIONS or c.validate_repeat(value).startswith("every:")


@pytest.mark.parametrize("value", ["every:0", "every:10000", "every:-1", "fortnightly", "every:"])
def test_validate_repeat_rejects_out_of_range_and_unknown(value):
    with pytest.raises(c.ValidationError):
        c.validate_repeat(value)


def test_validate_tags_trims_drops_blanks_and_caps_count():
    assert c.validate_tags([" a ", "", "  ", "b"]) == ["a", "b"]
    with pytest.raises(c.ValidationError, match="too many tags"):
        c.validate_tags([f"t{i}" for i in range(c.MAX_TAGS + 1)])


def test_validate_tags_rejects_non_strings():
    with pytest.raises(c.ValidationError, match="list of strings"):
        c.validate_tags([1, 2])


def test_validate_title_requires_content():
    with pytest.raises(c.ValidationError, match="title is required"):
        c.validate_title("   ")


# ── parse_capture ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("capture buy vanilla", "buy vanilla"),
    ("/capture buy vanilla", "buy vanilla"),
    ("/capture@MyBot buy vanilla", "buy vanilla"),
    ("CAPTURE: buy vanilla", "buy vanilla"),
    ("capture", ""),
])
def test_parse_capture_extracts_the_payload(text, expected):
    assert c.parse_capture(text) == expected


@pytest.mark.parametrize("text", ["captured the flag", "recapture it", "what did I capture?"])
def test_parse_capture_leaves_conversation_alone(text):
    """None means "not a capture command" — the normal assistant path continues."""
    assert c.parse_capture(text) is None


# ── Recurrence ────────────────────────────────────────────────────────────────

def _d(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


@pytest.mark.parametrize("repeat,base,expected", [
    ("daily", "2026-08-21", "2026-08-22"),
    ("weekly", "2026-08-21", "2026-08-28"),
    ("monthly", "2026-08-21", "2026-09-21"),
    ("yearly", "2026-08-21", "2027-08-21"),
    ("every:3", "2026-08-21", "2026-08-24"),
])
def test_advance_basic_intervals(repeat, base, expected):
    assert c._advance(repeat, _d(base)) == _d(expected)


def test_advance_monthly_clamps_to_a_short_month():
    """Jan 31 + 1 month has no 31st to land on — clamp rather than overflow."""
    assert c._advance("monthly", _d("2026-01-31")) == _d("2026-02-28")


def test_advance_yearly_clamps_leap_day():
    assert c._advance("yearly", _d("2028-02-29")) == _d("2029-02-28")


def test_advance_weekdays_skips_the_weekend():
    # Friday 2026-08-21 -> Monday 2026-08-24 (not Saturday).
    assert c._advance("weekdays", _d("2026-08-21")) == _d("2026-08-24")
    # Mid-week stays mid-week.
    assert c._advance("weekdays", _d("2026-08-19")) == _d("2026-08-20")


def test_next_due_advances_from_the_old_due_date():
    assert c.next_due("weekly", "2026-08-21", today=_d("2026-08-21")) == "2026-08-28"


def test_next_due_reanchors_when_completed_very_late():
    """Completing three weeks late must not spawn an already-overdue copy."""
    assert c.next_due("weekly", "2026-08-01", today=_d("2026-08-21")) == "2026-08-28"


def test_next_due_treats_a_due_today_result_as_not_overdue():
    """Exactly one interval late lands on today — that is the auto-star case, and it
    must NOT be re-anchored forward."""
    assert c.next_due("weekly", "2026-08-14", today=_d("2026-08-21")) == "2026-08-21"


def test_next_due_without_a_due_date_counts_from_today():
    assert c.next_due("daily", "", today=_d("2026-08-21")) == "2026-08-22"


def test_next_due_tolerates_a_garbage_stored_date():
    """A malformed stored value must degrade to "count from today", never raise into
    the completion transaction."""
    assert c.next_due("daily", "not-a-date", today=_d("2026-08-21")) == "2026-08-22"
