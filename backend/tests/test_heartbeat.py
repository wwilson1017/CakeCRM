"""Heartbeat orchestration (heartbeat/service.py).

Hermetic: delivery, the background runner, alerts, provider, and pg helpers are all
mocked. Covers the heartbeat turn's three gates + failure-alert threshold, and the two
tick entrypoints (the 60s maintenance tick and the run-now workhorse).
"""

import pytest

from assistant.background import BackgroundResult
from heartbeat import service


@pytest.fixture
def mocks(monkeypatch):
    state = {
        "delivered": [], "alerts": [], "resolved": [],
        "ran_turn": 0, "notified": [],
    }
    def _deliver(title, message):
        state["delivered"].append((title, message))
        return {"ok": True, "notification_id": "n1", "channels_sent": ["web_push"],
                "web_push": True, "logged": True}

    monkeypatch.setattr(service.delivery, "deliver_notification", _deliver)
    monkeypatch.setattr(service, "_maybe_run_dreaming", lambda: None)
    monkeypatch.setattr(service.alerts, "create_alert",
                        lambda **k: state["alerts"].append(k))
    monkeypatch.setattr(service.alerts, "resolve_by_source",
                        lambda s, sid: state["resolved"].append((s, sid)) or 0)
    return state


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


def test_heartbeat_turn_runs_under_the_background_allowlist(monkeypatch, mocks):
    """Both unattended heartbeat surfaces must route through the SHARED builder, which is
    where the #114 exclusion lives. test_proactive_service pins the third call site the
    same way; these two were unpinned, so an ad-hoc allowlist assembled here would have
    re-opened live Gmail reads with every existing heartbeat test still green."""
    import assistant.background as background

    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: object())
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)    # claimed
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"consecutive_errors": 0})
    captured = {}

    def fake_turn(prompt, user_message, *, allowed_tools, registry, model_tier, timeout):
        captured["allowed"] = allowed_tools
        return BackgroundResult(text="HEARTBEAT_OK", error=False)

    monkeypatch.setattr(background, "run_background_turn", fake_turn)
    monkeypatch.setattr(background, "background_allowlist", lambda reg: {"crm_dashboard", "notify_user"})
    assert service.maybe_run_heartbeat_turn(force=True)["status"] == "ok"
    assert captured["allowed"] == {"crm_dashboard", "notify_user"}


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
    calls = {"turn": 0, "dreaming": 0, "scores": 0}
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "_maybe_run_dreaming",
                        lambda: calls.__setitem__("dreaming", calls["dreaming"] + 1))
    monkeypatch.setattr(service, "_maybe_refresh_scores",
                        lambda: calls.__setitem__("scores", calls["scores"] + 1))
    monkeypatch.setattr(service, "maybe_run_heartbeat_turn",
                        lambda force=False: calls.__setitem__("turn", calls["turn"] + 1) or {"status": "ok"})

    out = service.tick(force_turn=False)
    assert out["heartbeat_turn"] == {"skipped": "not requested"}
    assert calls["turn"] == 0                      # run_ai_turn=false → no AI turn
    # The maintenance passes run either way — run-now is not an AI-only button.
    assert calls["dreaming"] == 1 and calls["scores"] == 1

    out = service.tick(force_turn=True)
    assert calls["turn"] == 1 and out["heartbeat_turn"] == {"status": "ok"}
    assert calls["dreaming"] == 2 and calls["scores"] == 2


def test_maintenance_tick_is_keyless_and_drives_both_passes(monkeypatch):
    """The 60s job stamps the clock, drives dreaming + the score refresh, and runs NO AI.

    The only AI this tick ever ran was an enhancement turn on a fired row, and that whole
    path is gone. This pins the job keyless so a later edit cannot quietly put a provider
    call back on a 60-second interval.
    """
    calls = {"dreaming": 0, "scores": 0, "turn": 0, "sql": []}
    monkeypatch.setattr(service, "pg_execute", lambda sql, *a, **k: calls["sql"].append(sql) or 1)
    monkeypatch.setattr(service, "_maybe_run_dreaming",
                        lambda: calls.__setitem__("dreaming", calls["dreaming"] + 1) or {"ran": True})
    monkeypatch.setattr(service, "_maybe_refresh_scores",
                        lambda: calls.__setitem__("scores", calls["scores"] + 1) or {"refreshed": 0})
    monkeypatch.setattr(service, "maybe_run_heartbeat_turn",
                        lambda force=False: calls.__setitem__("turn", calls["turn"] + 1))
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: calls.__setitem__("turn", calls["turn"] + 1))
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)   # even with AI enabled…

    out = service.maintenance_tick()

    assert out == {"dreaming": {"ran": True}, "score_refresh": {"refreshed": 0}}
    assert calls["dreaming"] == 1 and calls["scores"] == 1
    assert calls["turn"] == 0                                          # …the tick runs no AI
    assert any("last_tick_at = now()" in q for q in calls["sql"])      # the clock stamp survived


def test_heartbeat_turn_tick_delegates(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "maybe_run_heartbeat_turn",
                        lambda force=False: seen.__setitem__("force", force) or {"skipped": "throttled"})
    assert service.heartbeat_turn_tick() == {"skipped": "throttled"}
    assert seen["force"] is False
