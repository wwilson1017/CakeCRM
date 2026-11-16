"""Settings tools and the strict admin REST door share one validator."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import require_admin
from heartbeat import router, tools


def test_the_read_is_a_read_and_the_write_is_not_routine():
    read, write = tools.HEARTBEAT_TOOL_DEFS
    assert read["writes"] is False
    assert write["writes"] is True
    assert all("confirm_tier" not in d for d in (read, write))


@pytest.mark.parametrize("user", [{"role": "member"}, None, {"id": 1}, {"role": "unknown"}])
def test_update_is_refused_for_a_member_and_for_an_unattended_registry(monkeypatch, user):
    def forbidden(*a):
        raise AssertionError("must never reach the settings write")

    monkeypatch.setattr(tools.hb_settings, "update_settings", forbidden)
    defs, executors = tools.get_heartbeat_tools(user)
    assert defs == tools.HEARTBEAT_TOOL_DEFS
    assert "error" in executors["heartbeat_update_settings"](interval_minutes=1440)


def test_an_admin_update_passes_through_and_a_bad_value_is_an_error_dict(monkeypatch):
    calls = []

    def update(changes):
        calls.append(changes)
        if changes == {"interval_minutes": 5}:
            raise ValueError("invalid interval")
        return changes

    monkeypatch.setattr(tools.hb_settings, "update_settings", update)
    monkeypatch.setattr(tools.hb_settings, "get_settings", lambda: {"enabled": True})
    _, executors = tools.get_heartbeat_tools({"role": "admin"})
    assert executors["heartbeat_get_settings"]() == {"enabled": True}
    assert executors["heartbeat_update_settings"](interval_minutes=1440) == {
        "ok": True, "settings": {"interval_minutes": 1440}}
    assert executors["heartbeat_update_settings"](interval_minutes=5) == {"error": "invalid interval"}
    assert calls == [{"interval_minutes": 1440}, {"interval_minutes": 5}]


def test_put_settings_validates_through_fastapi(monkeypatch):
    app = FastAPI()
    app.include_router(router.router, prefix="/api/heartbeat")
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "role": "admin"}
    calls = []

    def update(changes):
        calls.append(changes)
        if changes == {"interval_minutes": 5}:
            raise ValueError("x")
        if changes.get("enabled", True) is None:
            raise ValueError("enabled must be true or false.")
        return changes

    monkeypatch.setattr(router.hb_settings, "update_settings", update)
    with TestClient(app) as client:
        response = client.put("/api/heartbeat/settings", json={"interval_minutes": 5})
        assert response.status_code == 400 and response.json()["detail"] == "x"
        calls.clear()
        for invalid in ({"interval_minutes": "60"}, {"intervall": 60}, {"interval_minutes": True}):
            assert client.put("/api/heartbeat/settings", json=invalid).status_code == 422
        assert calls == []
        response = client.put("/api/heartbeat/settings", json={"interval_minutes": 1440})
        assert response.status_code == 200
        assert calls == [{"interval_minutes": 1440}]
        response = client.put("/api/heartbeat/settings", json={"enabled": None, "interval_minutes": 60})
        assert response.status_code == 400
        assert calls[-1] == {"enabled": None, "interval_minutes": 60}


def test_status_keeps_existing_fields_and_returns_the_shared_settings(monkeypatch):
    import providers
    from core.auth import get_current_user

    app = FastAPI()
    app.include_router(router.router, prefix="/api/heartbeat")
    app.dependency_overrides[get_current_user] = lambda: {"id": 2, "role": "member"}
    view = {"enabled": True, "interval_minutes": 1440, "quiet_hours": "off",
            "min_gap_hours": 4, "paused_until": None, "timezone": "UTC", "operator_enabled": True}
    monkeypatch.setattr(router, "pg_fetchone", lambda *a: {"proactive_enabled": False})
    monkeypatch.setattr(router.hb_settings, "get_settings", lambda: view)
    monkeypatch.setattr(providers, "get_ai_provider", lambda: None)
    with TestClient(app) as client:
        response = client.get("/api/heartbeat/status")
    assert response.status_code == 200
    assert response.json() == {
        "state": {"proactive_enabled": False}, "enabled": router.settings.heartbeat_enabled,
        "interval_minutes": router.settings.heartbeat_interval_minutes,
        "provider_ready": False, "heartbeat_settings": view,
        "proactive_digest_hour": router.settings.proactive_digest_hour,
    }
