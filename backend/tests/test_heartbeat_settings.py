"""Strict shared settings validation; invalid input must never partially write."""

import pytest

from heartbeat import settings as hb_settings


@pytest.fixture
def writes(monkeypatch):
    calls = []
    monkeypatch.setattr(hb_settings, "pg_execute", lambda sql, params: calls.append((sql, params)))
    monkeypatch.setattr(hb_settings, "pg_fetchone", lambda *a: None)
    return calls


@pytest.mark.parametrize("changes", [
    {}, {"nope": 1},
    *({"interval_minutes": v} for v in [29, 1441, "60", True, None]),
    *({"quiet_hours": v} for v in ["25:00-07:00", "22:00", "22:00-22:00", 5, None]),
    *({"min_gap_hours": v} for v in [-1, 25, True, "4", None]),
    *({"pause_days": v} for v in [-1, 366, True, "7", None]),
    *({"enabled": v} for v in ["yes", 1, None]),
    {"enabled": False, "interval_minutes": 5},
])
def test_bad_input_is_refused_before_any_write(writes, changes):
    with pytest.raises(ValueError):
        hb_settings.update_settings(changes)
    assert writes == []


@pytest.mark.parametrize("changes,fragment,params", [
    ({"interval_minutes": 1440}, "interval_minutes = %s", [1440]),
    ({"quiet_hours": "22:00-07:00"}, "quiet_start = %s, quiet_end = %s", ["22:00", "07:00"]),
    ({"quiet_hours": "off"}, "quiet_start = %s, quiet_end = %s", [None, None]),
    ({"quiet_hours": " OFF "}, "quiet_start = %s, quiet_end = %s", [None, None]),
    ({"pause_days": 7}, "make_interval(days => %s)", [7, 7]),
    ({"pause_days": 0}, "CASE WHEN %s > 0", [0, 0]),
    ({"enabled": False}, "turn_enabled = %s", [False]),
    ({"min_gap_hours": 0}, "notify_min_gap_hours = %s", [0]),
    ({"enabled": True, "min_gap_hours": 24}, "turn_enabled = %s, notify_min_gap_hours = %s", [True, 24]),
])
def test_each_setting_writes_its_own_column(writes, changes, fragment, params):
    hb_settings.update_settings(changes)
    assert len(writes) == 1
    assert fragment in writes[0][0]
    assert writes[0][1] == params


@pytest.mark.parametrize("quiet,expected", [(None, "off"), (("22:00", "07:00"), "22:00-07:00")])
def test_get_settings_shapes_the_row(monkeypatch, quiet, expected):
    monkeypatch.setattr(hb_settings.settings, "heartbeat_enabled", False)
    monkeypatch.setattr(hb_settings, "pg_fetchone", lambda *a: {
        "turn_enabled": False, "interval_minutes": 90, "notify_min_gap_hours": 3,
        "quiet_start": quiet[0] if quiet else None, "quiet_end": quiet[1] if quiet else None,
        "paused_until": "2026-12-01T00:00:00+00:00",
    })
    result = hb_settings.get_settings()
    assert result == {"enabled": False, "interval_minutes": 90, "quiet_hours": expected,
                      "min_gap_hours": 3, "paused_until": "2026-12-01T00:00:00+00:00",
                      "operator_enabled": False, "timezone": hb_settings.localtime.tz().key}
    assert isinstance(result["timezone"], str)


def test_missing_row_reads_defaults(writes):
    result = hb_settings.get_settings()
    assert result["enabled"] is True
    assert result["min_gap_hours"] == 4
    assert result["interval_minutes"] == hb_settings.settings.heartbeat_interval_minutes
    assert result["quiet_hours"] == "off"
    assert result["paused_until"] is None
