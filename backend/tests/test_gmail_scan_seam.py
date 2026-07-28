"""The heartbeat seam for the Gmail touch-scan job (issue #17).

Proves gmail_scan_tick delegates to run_scan_if_due and can NEVER abort its scheduler
job (both the lazy import and the call are guarded), and that the cadence knob exists.
Scheduler registration itself is asserted in test_heartbeat_scheduler.py.
"""

from gmail_scan import service as gs
from heartbeat import service


def test_gmail_scan_tick_delegates(monkeypatch):
    monkeypatch.setattr(gs, "run_scan_if_due", lambda: {"status": "ok", "seen": 1})
    assert service.gmail_scan_tick() == {"status": "ok", "seen": 1}


def test_gmail_scan_tick_returns_none_when_not_due(monkeypatch):
    monkeypatch.setattr(gs, "run_scan_if_due", lambda: None)
    assert service.gmail_scan_tick() is None


def test_gmail_scan_tick_swallows_exceptions(monkeypatch):
    def boom():
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(gs, "run_scan_if_due", boom)
    # A scan failure must never propagate out of the scheduler job.
    assert service.gmail_scan_tick() is None


def test_scan_interval_knob_exists_and_positive():
    from core.config import settings

    assert isinstance(settings.gmail_scan_interval_minutes, int)
    assert settings.gmail_scan_interval_minutes >= 1
