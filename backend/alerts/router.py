"""Alerts REST API (issue #6). All endpoints require JWT auth.

  GET  /api/alerts            — list (?status=active|acknowledged|resolved|all)
  GET  /api/alerts/counts     — active alert count (for the notification bell)
  POST /api/alerts/:id/acknowledge
  POST /api/alerts/:id/resolve
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from alerts import service
from core.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_alerts(status: str = "active", limit: int = 50,
                     _user: dict = Depends(get_current_user)):
    items = await run_in_threadpool(service.list_alerts, status, limit)
    return {"alerts": items, "count": len(items)}


@router.get("/counts")
async def alert_counts(_user: dict = Depends(get_current_user)):
    count = await run_in_threadpool(service.get_active_count)
    return {"count": count}


@router.post("/{alert_id}/acknowledge")
async def acknowledge(alert_id: str, _user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(service.acknowledge_alert, alert_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/{alert_id}/resolve")
async def resolve(alert_id: str, _user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(service.resolve_alert, alert_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result
