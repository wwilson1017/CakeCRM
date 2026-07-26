"""Reminder recurrence math — parse, validate, describe, compute_next_due.

Pure logic, no DB. Covers catch-up-past-now, arithmetic interval jumps, sub-60s
rejection, weekly ISO-day scanning, monthly day clamping across short months,
cron via real croniter, and rule validation.
"""

from datetime import datetime, timedelta, timezone

from reminders import recurrence


def _dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ── parse_recurrence ────────────────────────────────────────────────────────

def test_parse_empty_is_none():
    assert recurrence.parse_recurrence("") is None
    assert recurrence.parse_recurrence("   ") is None


def test_parse_daily():
    assert recurrence.parse_recurrence("daily") == {"type": "daily"}


def test_parse_weekly_names_and_nums():
    assert recurrence.parse_recurrence("weekly:mon,wed,fri") == {"type": "weekly", "days": [1, 3, 5]}
    assert recurrence.parse_recurrence("weekly:1,3,5") == {"type": "weekly", "days": [1, 3, 5]}
    assert recurrence.parse_recurrence("weekly") == {"type": "weekly", "days": [1, 2, 3, 4, 5]}


def test_parse_weekly_garbage_is_none():
    assert recurrence.parse_recurrence("weekly:zzz") is None


def test_parse_monthly_clamps():
    assert recurrence.parse_recurrence("monthly:15") == {"type": "monthly", "day": 15}
    assert recurrence.parse_recurrence("monthly:99") == {"type": "monthly", "day": 31}
    assert recurrence.parse_recurrence("monthly:abc") is None


def test_parse_interval():
    assert recurrence.parse_recurrence("every 4 hours") == {"type": "interval", "hours": 4}
    assert recurrence.parse_recurrence("every 30 minutes") == {"type": "interval", "minutes": 30}
    assert recurrence.parse_recurrence("every 0 hours") is None


def test_parse_cron_valid_and_invalid():
    # parse lowercases the whole string (keyword matching); croniter is case-insensitive.
    assert recurrence.parse_recurrence("cron:0 9 * * MON-FRI") == {"type": "cron", "expression": "0 9 * * mon-fri"}
    assert recurrence.parse_recurrence("cron:not a cron") is None


def test_parse_unknown_is_none():
    assert recurrence.parse_recurrence("whenever") is None


# ── validate_rule ───────────────────────────────────────────────────────────

def test_validate_none_ok():
    assert recurrence.validate_rule(None) is None


def test_validate_interval_min_60s():
    assert recurrence.validate_rule({"type": "interval", "minutes": 0}) is not None
    assert recurrence.validate_rule({"type": "interval", "minutes": 1}) is None


def test_validate_weekly_needs_days():
    assert recurrence.validate_rule({"type": "weekly", "days": []}) is not None
    assert recurrence.validate_rule({"type": "weekly", "days": [8]}) is not None
    assert recurrence.validate_rule({"type": "weekly", "days": [1, 5]}) is None


def test_validate_monthly_range():
    assert recurrence.validate_rule({"type": "monthly", "day": 0}) is not None
    assert recurrence.validate_rule({"type": "monthly", "day": 32}) is not None
    assert recurrence.validate_rule({"type": "monthly", "day": 15}) is None


def test_validate_unknown_type():
    assert recurrence.validate_rule({"type": "yearly"}) is not None


def test_validate_cron():
    assert recurrence.validate_rule({"type": "cron", "expression": "0 9 * * *"}) is None
    assert recurrence.validate_rule({"type": "cron", "expression": "bogus"}) is not None


# ── describe_recurrence ─────────────────────────────────────────────────────

def test_describe():
    assert recurrence.describe_recurrence(None) is None
    assert recurrence.describe_recurrence({"type": "daily"}) == "Daily"
    assert recurrence.describe_recurrence({"type": "weekly", "days": [1, 3]}) == "Weekly: Mon, Wed"
    assert recurrence.describe_recurrence({"type": "monthly", "day": 15}) == "Monthly: day 15"
    assert recurrence.describe_recurrence({"type": "interval", "hours": 4}) == "Every 4h"
    assert recurrence.describe_recurrence({"type": "cron", "expression": "0 9 * * *"}) == "Cron: 0 9 * * *"


# ── compute_next_due ────────────────────────────────────────────────────────

def test_next_daily_catches_up_past_now():
    # due was 3 days ago; next must be strictly after now.
    now = _dt(2026, 7, 24, 12, 0)
    due = _dt(2026, 7, 21, 9, 0)
    nxt = recurrence.compute_next_due(due, {"type": "daily"}, now=now)
    assert nxt > now
    assert nxt.hour == 9 and nxt.minute == 0


def test_next_interval_uses_arithmetic_jump():
    # due 10h ago, every 3h → next multiple strictly after now.
    now = _dt(2026, 7, 24, 12, 0)
    due = _dt(2026, 7, 24, 2, 0)  # 10h before now
    nxt = recurrence.compute_next_due(due, {"type": "interval", "hours": 3}, now=now)
    assert nxt > now
    # 2:00 + k*3h > 12:00 → first is 14:00
    assert nxt == _dt(2026, 7, 24, 14, 0)


def test_next_interval_sub_60s_none():
    now = _dt(2026, 7, 24, 12, 0)
    assert recurrence.compute_next_due(now, {"type": "interval", "minutes": 0}, now=now) is None


def test_next_weekly_finds_matching_day():
    # Fri 2026-07-24; weekly Mon/Wed → next is Mon 2026-07-27.
    now = _dt(2026, 7, 24, 12, 0)
    due = _dt(2026, 7, 24, 9, 0)
    nxt = recurrence.compute_next_due(due, {"type": "weekly", "days": [1, 3]}, now=now)
    assert nxt.isoweekday() in (1, 3)
    assert nxt > now


def test_next_monthly_clamps_short_month():
    # Jan 31 monthly day 31 → Feb clamps to 28 (2026 not a leap year).
    now = _dt(2026, 1, 31, 12, 0)
    due = _dt(2026, 1, 31, 9, 0)
    nxt = recurrence.compute_next_due(due, {"type": "monthly", "day": 31}, now=now)
    assert nxt.month == 2 and nxt.day == 28


def test_next_cron():
    now = _dt(2026, 7, 24, 12, 0)
    due = _dt(2026, 7, 24, 8, 0)
    nxt = recurrence.compute_next_due(due, {"type": "cron", "expression": "0 9 * * *"}, now=now)
    assert nxt > now
    assert nxt.hour == 9


def test_next_unknown_rule_none():
    now = _dt(2026, 7, 24, 12, 0)
    assert recurrence.compute_next_due(now, {"type": "nope"}, now=now) is None


def test_next_naive_datetime_treated_utc():
    naive = datetime(2026, 7, 21, 9, 0)  # no tzinfo
    now = _dt(2026, 7, 24, 12, 0)
    nxt = recurrence.compute_next_due(naive, {"type": "daily"}, now=now)
    assert nxt.tzinfo is not None and nxt > now


def test_next_future_due_advances_one_period():
    # due is in the future → next is due + one period.
    now = _dt(2026, 7, 24, 12, 0)
    due = _dt(2026, 7, 25, 9, 0)
    nxt = recurrence.compute_next_due(due, {"type": "daily"}, now=now)
    assert nxt == due + timedelta(days=1)
