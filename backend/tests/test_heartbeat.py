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
        "ran_turn": 0, "notified": [], "saved": [],
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
    monkeypatch.setattr(service.dedupe, "current_items", lambda: {})
    monkeypatch.setattr(service.dedupe, "announced", lambda: set())
    monkeypatch.setattr(service.dedupe, "save", lambda items: state["saved"].append(set(items)))
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
    monkeypatch.setattr(service, "_maybe_sweep_turns", lambda: {"judged": 0, "pruned": 0})
    monkeypatch.setattr(service, "maybe_run_heartbeat_turn",
                        lambda force=False: calls.__setitem__("turn", calls["turn"] + 1))
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: calls.__setitem__("turn", calls["turn"] + 1))
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)   # even with AI enabled…

    out = service.maintenance_tick()

    assert out == {"dreaming": {"ran": True}, "score_refresh": {"refreshed": 0},
                   "turn_sweep": {"judged": 0, "pruned": 0}}
    assert calls["dreaming"] == 1 and calls["scores"] == 1
    assert calls["turn"] == 0                                          # …the tick runs no AI
    assert any("last_tick_at = now()" in q for q in calls["sql"])      # the clock stamp survived


def test_heartbeat_turn_tick_delegates(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "maybe_run_heartbeat_turn",
                        lambda force=False: seen.__setitem__("force", force) or {"skipped": "throttled"})
    assert service.heartbeat_turn_tick() == {"skipped": "throttled"}
    assert seen["force"] is False


# ── #72 Phase 4 observer seam ───────────────────────────────────────────────────

def test_observer_tick_survives_the_module_being_absent(monkeypatch):
    """The seam is lazily imported so merge order is irrelevant; an ImportError must be
    a quiet no-op, never a dead scheduler job."""
    import builtins

    real_import = builtins.__import__

    def no_observer(name, *args, **kwargs):
        if name == "memory.observer":
            raise ImportError("not here yet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_observer)
    assert service.observer_tick() is None


def test_observer_tick_survives_a_raising_callee(monkeypatch):
    from memory import observer

    monkeypatch.setattr(observer, "run_observer_if_due",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert service.observer_tick() is None


def test_observer_tick_returns_the_pass_summary(monkeypatch):
    from memory import observer

    monkeypatch.setattr(observer, "run_observer_if_due", lambda: {"conversations": 2})
    assert service.observer_tick() == {"conversations": 2}


# ── #283 reminders seam ─────────────────────────────────────────────────────────

def test_reminders_tick_survives_a_raising_callee(monkeypatch):
    """A broken pass must be a logged no-op, never a dead scheduler job."""
    from reminders import service as reminders_service

    monkeypatch.setattr(reminders_service, "fire_due",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert service.reminders_tick() is None


def test_reminders_tick_returns_the_fire_summary(monkeypatch):
    from reminders import service as reminders_service

    monkeypatch.setattr(reminders_service, "fire_due",
                        lambda: {"fired": 1, "spawned": 0, "attempted": 2})
    assert service.reminders_tick() == {"fired": 1, "spawned": 0, "attempted": 2}


@pytest.fixture
def ready_turn(monkeypatch, mocks):
    """A due turn with one newly overdue todo; no network or database."""
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda: object())
    monkeypatch.setattr(service, "pg_execute", lambda *a: 1)
    monkeypatch.setattr(service.dedupe, "current_items", lambda: {("todo", 5): "Call"})
    mocks["completions"] = []
    mocks["messages"] = []
    mocks["prompts"] = []

    def complete(sql, params):
        mocks["completions"].append((sql, params))
        return {"consecutive_errors": 0}

    def run(prompt, user_message, *, allowed_tools, registry, model_tier, timeout):
        mocks["prompts"].append(prompt)
        mocks["messages"].append(user_message)
        return BackgroundResult(text="HEARTBEAT_OK")

    monkeypatch.setattr(service, "pg_fetchone", complete)
    monkeypatch.setattr(service.background, "run_background_turn", run)
    return mocks


def _forbid_model(*a, **k):
    raise AssertionError("unchanged records must not wake the model")


def test_second_turn_with_the_same_overdue_todo_sends_nothing(monkeypatch, ready_turn):
    monkeypatch.setattr(service.dedupe, "announced", lambda: {("todo", 5)})
    monkeypatch.setattr(service.background, "run_background_turn", _forbid_model)
    result = service.maybe_run_heartbeat_turn()
    assert result["status"] == "ok"
    assert result["result"] == "Nothing new since the last check."
    assert ready_turn["saved"] == [{("todo", 5)}]


def test_closing_one_of_two_announced_todos_makes_no_model_call_and_prunes(monkeypatch, ready_turn):
    monkeypatch.setattr(service.dedupe, "announced", lambda: {("todo", 5), ("todo", 6)})
    monkeypatch.setattr(service.background, "run_background_turn", _forbid_model)
    assert service.maybe_run_heartbeat_turn()["status"] == "ok"
    assert ready_turn["saved"] == [{("todo", 5)}]


def test_only_the_new_records_reach_the_user_message(monkeypatch, ready_turn):
    monkeypatch.setattr(service.dedupe, "current_items", lambda: {("todo", 5): "Call", ("todo", 6): "Send quote"})
    monkeypatch.setattr(service.dedupe, "announced", lambda: {("todo", 5)})
    service.maybe_run_heartbeat_turn()
    message = ready_turn["messages"][0]
    assert "todo 6" in message and "Send quote" in message
    assert "todo 5" not in message
    assert "<untrusted_external_content" in message
    assert 'include that count' in message
    assert ready_turn["saved"] == [{("todo", 5), ("todo", 6)}]


def test_heartbeat_prompt_carries_no_per_turn_data(monkeypatch, ready_turn):
    monkeypatch.setattr(service.dedupe, "current_items", lambda: {("todo", 6): "Send quote"})
    service.maybe_run_heartbeat_turn()
    assert "Send quote" not in ready_turn["prompts"][0][0]
    assert "Send quote" in ready_turn["messages"][0]


def test_an_errored_turn_does_not_save_the_snapshot(monkeypatch, ready_turn):
    monkeypatch.setattr(service.background, "run_background_turn", lambda *a, **k: BackgroundResult(text="error", error=True))
    assert service.maybe_run_heartbeat_turn()["status"] == "error"
    assert ready_turn["saved"] == []


@pytest.mark.parametrize("delivered,error,remembered", [(True, True, True), (False, True, True), (False, False, False)])
def test_send_delivery_and_timeout_control_the_snapshot_and_gap(monkeypatch, ready_turn, delivered, error, remembered):
    def run(prompt, user_message, *, allowed_tools, registry, model_tier, timeout):
        registry._notify_user_called = True
        if delivered:
            registry._notify_user_delivered = True
        return BackgroundResult(text="timeout" if error else "ok", error=error)

    monkeypatch.setattr(service.background, "run_background_turn", run)
    service.maybe_run_heartbeat_turn()
    assert ready_turn["saved"] == ([{("todo", 5)}] if remembered else [])
    sql, params = ready_turn["completions"][-1]
    assert "last_turn_notify_at" in sql
    assert params[-1] is remembered


def test_a_clean_turn_that_did_not_notify_does_not_stamp_the_gap(ready_turn):
    service.maybe_run_heartbeat_turn()
    assert ready_turn["completions"][-1][1][-1] is False
    assert ready_turn["saved"] == [{("todo", 5)}]


def test_the_snapshot_is_saved_before_the_turn_is_released(monkeypatch, ready_turn):
    events = []
    monkeypatch.setattr(service.dedupe, "save", lambda items: events.append("save"))
    monkeypatch.setattr(service, "pg_fetchone", lambda *a: events.append("complete") or {"consecutive_errors": 0})
    service.maybe_run_heartbeat_turn()
    assert events == ["save", "complete"]


def test_a_failed_save_makes_the_turn_an_error(monkeypatch, ready_turn):
    def fail(items):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service.dedupe, "save", fail)
    assert service.maybe_run_heartbeat_turn()["status"] == "error"
    assert ready_turn["completions"][-1][1][0] == "error"


def test_run_now_bypasses_the_dedupe(monkeypatch, ready_turn):
    monkeypatch.setattr(service.dedupe, "announced", lambda: {("todo", 5)})
    assert service.maybe_run_heartbeat_turn(force=True)["status"] == "ok"
    assert ready_turn["messages"] == ["Run your background heartbeat check now."]
    assert ready_turn["saved"] == [{("todo", 5)}]


def test_a_failed_pre_check_is_an_error_turn_with_no_model_call(monkeypatch, ready_turn):
    def fail():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service.dedupe, "current_items", fail)
    monkeypatch.setattr(service.background, "run_background_turn", _forbid_model)
    assert service.maybe_run_heartbeat_turn()["status"] == "error"
    assert ready_turn["saved"] == []


def test_the_periodic_claim_reads_every_setting_in_sql_and_run_now_reads_none(monkeypatch, ready_turn):
    claims = []
    monkeypatch.setattr(service, "pg_execute", lambda sql, params: claims.append((sql, params)) or 1)
    service.maybe_run_heartbeat_turn()
    service.maybe_run_heartbeat_turn(force=True)
    periodic, forced = claims
    for column in ("turn_enabled", "paused_until", "COALESCE(interval_minutes", "last_turn_notify_at", "quiet_start"):
        assert column in periodic[0]
        assert column not in forced[0]
    assert "local" in periodic[1] and "inflight" in periodic[1]
    assert "last_turn_status <> 'running'" in periodic[0]
