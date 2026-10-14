"""Reminder schedule maths (issue #283) — pure, so the zone and clock are pinned here."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from crm.gtd_common import ValidationError
from reminders import recurrence

CHI = ZoneInfo("America/Chicago")


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_monthly_clamps_to_month_end_and_never_drifts():
    anchor = date(2026, 1, 31)
    assert recurrence.occurrence("monthly", anchor, 1) == date(2026, 2, 28)
    assert recurrence.occurrence("monthly", anchor, 2) == date(2026, 3, 31)
    assert recurrence.occurrence("monthly", anchor, 13) == date(2027, 2, 28)


def test_yearly_feb_29_clamps_and_comes_back():
    anchor = date(2028, 2, 29)
    assert recurrence.occurrence("yearly", anchor, 1) == date(2029, 2, 28)
    assert recurrence.occurrence("yearly", anchor, 4) == date(2032, 2, 29)


def test_daily_and_weekly_step_from_the_anchor():
    anchor = date(2026, 10, 14)
    assert recurrence.occurrence("daily", anchor, 3) == date(2026, 10, 17)
    assert recurrence.occurrence("weekly", anchor, 2) == date(2026, 10, 28)


def test_next_due_is_strictly_after():
    anchor = date(2026, 10, 14)
    at = recurrence.due_instant(anchor, time(15, 0), CHI)
    d, nat = recurrence.next_due("daily", anchor, time(15, 0), after=at, zone=CHI)
    assert d == date(2026, 10, 15)
    assert nat == at + timedelta(days=1)


def test_a_late_repeating_reminder_resumes_at_the_next_future_time_not_the_backlog():
    anchor = date(2026, 10, 9)
    # 08:00 Chicago (13:00Z) on the 14th: today's 09:00 is still ahead.
    d, _ = recurrence.next_due("daily", anchor, None, after=_utc(2026, 10, 14, 13), zone=CHI)
    assert d == date(2026, 10, 14)
    # 09:30 Chicago: today's has passed, so tomorrow — one occurrence, not five.
    d, _ = recurrence.next_due("daily", anchor, None, after=_utc(2026, 10, 14, 14, 30), zone=CHI)
    assert d == date(2026, 10, 15)


def test_next_due_walks_far_forward_for_every_repeat():
    after = _utc(2030, 6, 1)
    for repeat in recurrence.REPEATS:
        d, at = recurrence.next_due(repeat, date(2026, 1, 31), None, after=after, zone=CHI)
        assert at > after
        prev = recurrence.next_due(repeat, date(2026, 1, 31), None,
                                   after=after - timedelta(days=400), zone=CHI)[1]
        assert prev <= at


def test_dst_keeps_the_wall_clock_time():
    # Chicago falls back on 2026-11-01.
    before = recurrence.due_instant(date(2026, 10, 31), None, CHI)
    after = recurrence.due_instant(date(2026, 11, 1), None, CHI)
    assert after - before == timedelta(hours=25)
    assert before.astimezone(CHI).hour == after.astimezone(CHI).hour == 9


def test_a_nonexistent_spring_forward_time_fires_at_the_shifted_instant():
    at = recurrence.due_instant(date(2026, 3, 8), time(2, 30), CHI)
    assert at == _utc(2026, 3, 8, 8, 30)


def test_date_only_uses_the_default_hour():
    at = recurrence.due_instant(date(2026, 10, 14), None, CHI)
    assert at.astimezone(CHI).time() == recurrence.DEFAULT_FIRE_TIME


@pytest.mark.parametrize("value", ["hourly", "Every day", 3])
def test_validate_repeat_rejects_junk(value):
    with pytest.raises(ValidationError):
        recurrence.validate_repeat(value)


@pytest.mark.parametrize("value", ["9:00", "25:00", "noon", "12:60"])
def test_parse_time_rejects_junk(value):
    with pytest.raises(ValidationError):
        recurrence.parse_time(value)


def test_validate_repeat_and_parse_time_accept_the_empty_forms():
    assert recurrence.validate_repeat("") is None
    assert recurrence.validate_repeat("Weekly") == "weekly"
    assert recurrence.parse_time("") is None
    assert recurrence.parse_time("07:05") == time(7, 5)
