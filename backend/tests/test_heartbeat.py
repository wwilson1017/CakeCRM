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


def test_enhancement_exception_after_delivery_no_alert(monkeypatch, mocks):
    # An exception in the enhancement PHASE (setup or the turn) after the baseline
    # was delivered is recorded as delivered_ai_error, NEVER a false failure alert.
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: True)

    def boom(*a, **k):
        raise RuntimeError("enhancement setup exploded")

    monkeypatch.setattr(service.background, "run_background_turn", boom)
    out = service.process_due_reminders(run_ai_enhancement=True)
    assert out[0]["status"] == "delivered_ai_error"
    assert mocks["delivered"]          # baseline delivered before the enhancement crash
    assert mocks["alerts"] == []       # R11: enhancement failure is never alerted


def test_baseline_delivery_failure_alerts(monkeypatch, mocks):
    # A genuine post-claim failure (baseline delivery itself) alerts, since the row
    # is already 'fired' and won't retry.
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])
    monkeypatch.setattr(service.reminders_service, "claim_reminder", lambda r: True)

    calls = {"n": 0}

    def deliver(title, message):
        calls["n"] += 1
        if calls["n"] == 1:   # the baseline call raises; the alert's own deliver is fine
            raise RuntimeError("push subsystem down")

    monkeypatch.setattr(service.delivery, "deliver_notification", deliver)
    out = service.process_due_reminders(run_ai_enhancement=False)
    assert out[0]["status"] == "error"
    assert mocks["alerts"] and mocks["alerts"][0]["source"] == "reminder"


def test_claim_failure_no_alert_stays_pending(monkeypatch, mocks):
    # A claim exception means the row is still pending (rolled back) → retries next
    # tick, no alert, no double handling.
    monkeypatch.setattr(service.reminders_service, "get_due_reminders", lambda n: [_reminder()])

    def boom(r):
        raise RuntimeError("db blip")

    monkeypatch.setattr(service.reminders_service, "claim_reminder", boom)
    out = service.process_due_reminders()
    assert out == []
    assert mocks["alerts"] == [] and mocks["delivered"] == []


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
    provider_called = {"n": 0}
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: provider_called.__setitem__("n", 1))
    # A skip must NOT write last_turn_status (would clobber the last real turn).
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("skip must not write heartbeat_state")))
    assert service.maybe_run_heartbeat_turn(force=False) == {"skipped": "disabled"}
    assert provider_called["n"] == 0


def test_turn_skipped_no_provider_no_alert(monkeypatch, mocks):
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: None)
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("skip must not write heartbeat_state")))
    ran = {"n": 0}
    monkeypatch.setattr(service.background, "run_background_turn", lambda *a, **k: ran.__setitem__("n", 1))
    assert service.maybe_run_heartbeat_turn() == {"skipped": "no_provider"}
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


def test_failure_alert_suppressed_within_cooldown(monkeypatch, mocks):
    # Over threshold, but the cooldown SELECT returns None → no new alert/push.
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: object())
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)

    def fetchone(sql, *a, **k):
        return {"consecutive_errors": 5} if "RETURNING consecutive_errors" in sql else None

    monkeypatch.setattr(service, "pg_fetchone", fetchone)
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: BackgroundResult(text="still down", error=True))
    service.maybe_run_heartbeat_turn(force=True)
    assert mocks["alerts"] == []       # cooldown gate held → no duplicate alert/push
    assert mocks["delivered"] == []


# ── tick / entrypoints ──────────────────────────────────────────────────────

def test_tick_runs_turn_only_when_forced(monkeypatch):
    calls = {"turn": 0, "reminders": 0}
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "process_due_reminders",
                        lambda run_ai_enhancement=True: calls.__setitem__("reminders", calls["reminders"] + 1) or [])
    monkeypatch.setattr(service, "_maybe_run_dreaming", lambda: None)
    monkeypatch.setattr(service, "maybe_run_heartbeat_turn",
                        lambda force=False: calls.__setitem__("turn", calls["turn"] + 1) or {"status": "ok"})

    out = service.tick(force_turn=False, run_ai_enhancement=False)
    assert out["heartbeat_turn"] == {"skipped": "not requested"}
    assert calls["turn"] == 0 and calls["reminders"] == 1   # run_ai_turn=false → no AI turn

    out = service.tick(force_turn=True, run_ai_enhancement=True)
    assert calls["turn"] == 1 and out["heartbeat_turn"] == {"status": "ok"}


def test_reminder_tick_gates_ai_on_env(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "_maybe_run_dreaming", lambda: None)
    monkeypatch.setattr(service, "process_due_reminders",
                        lambda run_ai_enhancement=True: captured.__setitem__("ai", run_ai_enhancement) or [])
    monkeypatch.setattr(service.settings, "heartbeat_enabled", False)
    service.reminder_tick()
    assert captured["ai"] is False       # local (disabled) → reminders deliver baseline-only
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    service.reminder_tick()
    assert captured["ai"] is True


def test_heartbeat_turn_tick_delegates(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "maybe_run_heartbeat_turn",
                        lambda force=False: seen.__setitem__("force", force) or {"skipped": "throttled"})
    assert service.heartbeat_turn_tick() == {"skipped": "throttled"}
    assert seen["force"] is False
