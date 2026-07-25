"""Heartbeat orchestration (heartbeat/service.py).

Hermetic: reminders service, delivery, the background runner, alerts, provider,
and pg helpers are all mocked. Covers baseline-delivery-always, claim-loss skip,
no-alert-on-AI-enhancement-failure (R11), and the heartbeat turn's three gates +
failure-alert threshold.
"""

import pytest

from assistant.background import BackgroundResult
from heartbeat import service


@pytest.fixture
def mocks(monkeypatch):
    state = {
        "delivered": [], "finished": [], "alerts": [], "resolved": [],
        "ran_turn": 0, "notified": [],
    }
    monkeypatch.setattr(service.delivery, "deliver_notification",
                        lambda title, message: state["delivered"].append((title, message)))
    monkeypatch.setattr(service, "_maybe_run_dreaming", lambda: None)
    monkeypatch.setattr(service.reminders_service, "finish_reminder",
                        lambda rid, result: state["finished"].append((rid, result)))
    monkeypatch.setattr(service.alerts, "create_alert",
                        lambda **k: state["alerts"].append(k))
    monkeypatch.setattr(service.alerts, "resolve_by_source",
                        lambda s, sid: state["resolved"].append((s, sid)) or 0)
    # Prompt builders read the identity singleton — mock it so no DB is touched.
    monkeypatch.setattr(service.identity, "get_identity", lambda: {"name": "Baker"})
    return state


def _reminder(rid="r1"):
    return {"id": rid, "message": "Call Dana", "context": "", "due_at": "2026-07-24T09:00:00+00:00",
            "recurrence_rule": None, "series_id": None}


# ── process_due_reminders ───────────────────────────────────────────────────

def test_baseline_delivery_always_then_ai(monkeypatch, mocks):
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: True)
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: BackgroundResult(text="did something", error=False))
    out = service.process_due_reminders(run_ai_enhancement=True)
    assert out[0]["status"] == "processed"
    assert mocks["delivered"] and mocks["delivered"][0][0].startswith("Reminder:")
    assert mocks["finished"][0][1].startswith("processed:")
    assert mocks["alerts"] == []


def test_claim_loss_skips(monkeypatch, mocks):
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: False)  # lost
    called = {"turn": False}
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: called.__setitem__("turn", True))
    out = service.process_due_reminders()
    assert out == []
    assert mocks["delivered"] == []       # never processed
    assert called["turn"] is False


def test_no_provider_reminder_delivers_no_alert(monkeypatch, mocks):
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: True)
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: BackgroundResult(text="No AI provider configured", error=True))
    out = service.process_due_reminders()
    assert out[0]["status"] == "delivered_no_ai"
    assert mocks["delivered"]              # baseline still delivered
    assert mocks["alerts"] == []           # keyless is never an alert


def test_ai_error_after_delivery_no_alert(monkeypatch, mocks):
    # R11: baseline delivered → an AI-enhancement failure is recorded, NOT alerted.
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: True)
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: BackgroundResult(text="rate limited", error=True))
    out = service.process_due_reminders()
    assert out[0]["status"] == "delivered_ai_error"
    assert mocks["alerts"] == []
    assert "AI enhancement error" in mocks["finished"][0][1]


def test_processing_exception_alerts(monkeypatch, mocks):
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: True)

    def boom(*a, **k):
        raise RuntimeError("enhancement exploded")

    # An unexpected crash inside processing (baseline already delivered) is caught,
    # the reminder marked errored, and a reminder alert raised.
    monkeypatch.setattr(service.background, "run_background_turn", boom)
    out = service.process_due_reminders(run_ai_enhancement=True)
    assert out[0]["status"] == "error"
    assert mocks["delivered"]      # baseline delivery still happened before the crash
    assert mocks["alerts"] and mocks["alerts"][0]["source"] == "reminder"


def test_run_ai_enhancement_false_skips_turn(monkeypatch, mocks):
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: True)
    ran = {"n": 0}
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: ran.__setitem__("n", ran["n"] + 1))
    out = service.process_due_reminders(run_ai_enhancement=False)
    assert out[0]["status"] == "delivered"
    assert ran["n"] == 0
    assert mocks["delivered"]              # baseline still fired


# ── maybe_run_heartbeat_turn (three gates + failure alert) ──────────────────

def test_turn_skipped_when_disabled(monkeypatch, mocks):
    monkeypatch.setattr(service.settings, "heartbeat_enabled", False)
    monkeypatch.setattr(service, "_set_turn_status", lambda s: None)
    provider_called = {"n": 0}
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: provider_called.__setitem__("n", 1))
    assert service.maybe_run_heartbeat_turn(force=False) == {"skipped": "disabled"}
    assert provider_called["n"] == 0


def test_turn_skipped_no_provider_no_alert(monkeypatch, mocks):
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: None)
    statuses = []
    monkeypatch.setattr(service, "_set_turn_status", lambda s: statuses.append(s))
    ran = {"n": 0}
    monkeypatch.setattr(service.background, "run_background_turn", lambda *a, **k: ran.__setitem__("n", 1))
    assert service.maybe_run_heartbeat_turn() == {"skipped": "no_provider"}
    assert statuses == ["skipped_no_provider"]
    assert ran["n"] == 0 and mocks["alerts"] == []


def test_turn_throttled(monkeypatch, mocks):
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: object())
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 0)   # claim lost → not due
    ran = {"n": 0}
    monkeypatch.setattr(service.background, "run_background_turn", lambda *a, **k: ran.__setitem__("n", 1))
    assert service.maybe_run_heartbeat_turn() == {"skipped": "throttled"}
    assert ran["n"] == 0


def test_turn_runs_ok_resolves_alert(monkeypatch, mocks):
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: object())
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)    # claimed
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"consecutive_errors": 0})
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: BackgroundResult(text="HEARTBEAT_OK", error=False))
    out = service.maybe_run_heartbeat_turn(force=True)
    assert out["status"] == "ok"
    assert mocks["resolved"] == [("heartbeat", "heartbeat")]


def test_failure_alert_fires_at_threshold(monkeypatch, mocks):
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: object())
    # claim UPDATE → 1, cooldown claim UPDATE → 1
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"consecutive_errors": 3})
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: BackgroundResult(text="model down", error=True))
    out = service.maybe_run_heartbeat_turn(force=True)
    assert out["status"] == "error"
    assert mocks["alerts"] and mocks["alerts"][0]["source"] == "heartbeat"
    assert mocks["delivered"]     # failure notification pushed


def test_failure_below_threshold_no_alert(monkeypatch, mocks):
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: object())
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"consecutive_errors": 1})
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: BackgroundResult(text="blip", error=True))
    service.maybe_run_heartbeat_turn(force=True)
    assert mocks["alerts"] == []
