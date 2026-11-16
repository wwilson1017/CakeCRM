"""Heartbeat REST API (issue #6). All endpoints require JWT auth.

  POST /api/heartbeat/run-now   — run one tick synchronously and return the report
                                  (the C4 demo hook; works regardless of the env gate)
  GET  /api/heartbeat/status    — heartbeat_state + config (for Settings + evidence)
  PUT  /api/heartbeat/settings  — change the background check-in schedule (#360)
  POST /api/heartbeat/proactive — toggle the daily digest + nudges (#22 Phase 3)

There is deliberately no GET for the proactive toggle: /status already returns the
whole heartbeat_state row, which is where proactive_enabled lives, so a second read
endpoint would be a second source of truth for the same column. /status also
is the single read source for the background check-in settings.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from core.auth import get_current_user, require_admin
from core.config import settings
from core.postgres import pg_fetchone
from heartbeat import service, settings as hb_settings

logger = logging.getLogger(__name__)
router = APIRouter()


class RunNowRequest(BaseModel):
    # Default deterministic-only so the demo button returns fast; opt in to the AI
    # system heartbeat turn explicitly.
    run_ai_turn: bool = False


@router.post("/run-now")
async def run_now(req: RunNowRequest, _user: dict = Depends(require_admin)):
    """Run one heartbeat tick now: the maintenance passes (dreaming, lead-score refresh)
    always run; the system AI turn runs only when ``run_ai_turn`` is true."""
    report = await run_in_threadpool(service.tick, force_turn=req.run_ai_turn)
    return report


@router.get("/status")
async def status(_user: dict = Depends(get_current_user)):
    def _read():
        from providers import get_ai_provider
        state = pg_fetchone("SELECT * FROM heartbeat_state WHERE id = 1")
        try:
            provider_ready = get_ai_provider() is not None
        except Exception:
            provider_ready = False
        return state, provider_ready, hb_settings.get_settings()

    state, provider_ready, heartbeat_settings = await run_in_threadpool(_read)
    return {
        # last_turn_status/result reflect the last ACTUAL turn; the current gate is
        # reported live below (enabled + provider_ready), never stored as a "skip".
        "state": state or {},
        "enabled": settings.heartbeat_enabled,
        "interval_minutes": settings.heartbeat_interval_minutes,
        "provider_ready": provider_ready,
        "heartbeat_settings": heartbeat_settings,
        # #22 Phase 3. Reported here so Settings can label the toggle with the hour the
        # digest actually fires rather than hard-coding 8am in the UI.
        "proactive_digest_hour": settings.proactive_digest_hour,
    }


class ProactiveRequest(BaseModel):
    enabled: bool


@router.post("/proactive")
async def set_proactive(req: ProactiveRequest, _user: dict = Depends(require_admin)):
    """Turn the daily digest + stale-record nudges on or off (#22 Phase 3).

    Keyless: both behaviors are deterministic and run with no AI provider configured,
    so this is a genuine user preference rather than an AI gate.
    """
    from proactive import service as proactive

    enabled = await run_in_threadpool(proactive.set_enabled, req.enabled)
    return {"ok": True, "enabled": enabled}


class HeartbeatSettingsRequest(BaseModel):
    # Strict and closed: preserve types and reject misspelled keys at the REST door.
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool | None = None
    interval_minutes: int | None = None
    quiet_hours: str | None = None
    min_gap_hours: int | None = None
    pause_days: int | None = None


@router.put("/settings")
async def put_settings(req: HeartbeatSettingsRequest, _user: dict = Depends(require_admin)):
    """Change the heartbeat check-in's install-wide settings (#360). Read them from /status."""
    try:
        view = await run_in_threadpool(hb_settings.update_settings, req.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "settings": view}
