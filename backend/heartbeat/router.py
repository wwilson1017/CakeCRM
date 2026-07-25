"""Heartbeat REST API (issue #6). All endpoints require JWT auth.

  POST /api/heartbeat/run-now  — run one tick synchronously and return the report
                                 (the C4 demo hook; works regardless of the env gate)
  GET  /api/heartbeat/status   — heartbeat_state + config (for Settings + evidence)
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from core.auth import get_current_user
from core.config import settings
from core.postgres import pg_fetchone
from heartbeat import service

logger = logging.getLogger(__name__)
router = APIRouter()


class RunNowRequest(BaseModel):
    # Default deterministic-only so the demo button returns fast; opt in to the AI
    # system heartbeat turn explicitly.
    run_ai_turn: bool = False


@router.post("/run-now")
async def run_now(req: RunNowRequest, _user: dict = Depends(get_current_user)):
    """Run one heartbeat tick now. Reminders always fire (deterministic baseline
    delivery); the system AI turn runs only when ``run_ai_turn`` is true."""
    report = await run_in_threadpool(
        service.tick, force_turn=req.run_ai_turn, run_ai_enhancement=req.run_ai_turn,
    )
    return report


@router.get("/status")
async def status(_user: dict = Depends(get_current_user)):
    state = await run_in_threadpool(
        pg_fetchone, "SELECT * FROM heartbeat_state WHERE id = 1")
    return {
        "state": state or {},
        "enabled": settings.heartbeat_enabled,
        "interval_minutes": settings.heartbeat_interval_minutes,
    }
