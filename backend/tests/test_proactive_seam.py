"""The heartbeat seam for the proactive digest/nudge job (issue #22, Phase 3).

Proves proactive_tick delegates to run_proactive_if_due and can NEVER abort its
scheduler job (both the lazy import and the call are guarded), and that the cadence
knobs exist. Scheduler registration itself is asserted in test_heartbeat_scheduler.py.

Mirrors test_gmail_scan_seam.py deliberately: #17 set this convention, and the point
of following it is that a reader who knows one job knows all of them.
"""

from heartbeat import service
from proactive import service as ps


def test_proactive_tick_delegates(monkeypatch):
    monkeypatch.setattr(ps, "run_proactive_if_due", lambda: {"digest": {"sent": True}})
    assert service.proactive_tick() == {"digest": {"sent": True}}


def test_proactive_tick_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(ps, "run_proactive_if_due", lambda: None)
    assert service.proactive_tick() is None


def test_proactive_tick_swallows_exceptions(monkeypatch):
    def boom():
        raise RuntimeError("digest blew up")

    monkeypatch.setattr(ps, "run_proactive_if_due", boom)
    # A proactive failure must never propagate out of the scheduler job — it shares a
    # thread pool with the maintenance tick.
    assert service.proactive_tick() is None


def test_cadence_knobs_exist_and_are_sane():
    from core.config import settings

    assert isinstance(settings.proactive_digest_hour, int)
    assert 0 <= settings.proactive_digest_hour <= 23
    assert settings.proactive_nudge_interval_minutes >= 1
    assert settings.proactive_nudge_cooldown_days >= 1
    assert settings.proactive_max_nudges_per_run >= 1


def test_digest_hour_env_accepts_midnight_and_rejects_nonsense(monkeypatch):
    """0 is a legitimate hour but not a legitimate interval, which is why the hour
    knob does not reuse _positive_int_env — a midnight digest must not be silently
    rewritten to the default."""
    from core.config import _hour_env

    monkeypatch.setenv("X_TEST_HOUR", "0")
    assert _hour_env("X_TEST_HOUR", 8) == 0
    monkeypatch.setenv("X_TEST_HOUR", "23")
    assert _hour_env("X_TEST_HOUR", 8) == 23
    for bad in ("24", "-1", "noon", ""):
        monkeypatch.setenv("X_TEST_HOUR", bad)
        assert _hour_env("X_TEST_HOUR", 8) == 8
    monkeypatch.delenv("X_TEST_HOUR")
    assert _hour_env("X_TEST_HOUR", 8) == 8
