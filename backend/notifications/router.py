"""Notifications REST API (issue #6).

The in-app notification log + Web Push subscription management. All endpoints
require JWT auth. Keyless — no ``ai_ready`` gate (push works with zero AI keys).

  GET  /api/notifications                    — list (?status=active|dismissed|all, ?limit=)
  POST /api/notifications/:id/dismiss
  POST /api/notifications/dismiss-all
  GET  /api/notifications/push/vapid-public-key   — (lazily generates + persists keys)
  POST /api/notifications/push/subscribe
  POST /api/notifications/push/unsubscribe
  POST /api/notifications/test               — send a real test push (no AI involved)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from core.auth import get_current_user
from notifications import delivery, service, subscriptions, vapid

logger = logging.getLogger(__name__)
router = APIRouter()


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: str = ""


class UnsubscribeRequest(BaseModel):
    endpoint: str


class TestRequest(BaseModel):
    title: str = "Test notification"
    message: str = "Push delivery is working."


@router.get("")
async def list_notifications(status: str = "active", limit: int = 10,
                            _user: dict = Depends(get_current_user)):
    items = await run_in_threadpool(service.list_notifications, status, limit)
    return {"notifications": items, "count": len(items)}


@router.get("/counts")
async def notification_counts(_user: dict = Depends(get_current_user)):
    """The TRUE active-notification count (unbounded) for the bell badge — the list
    endpoint above is capped, so its length under-reports once there are many."""
    count = await run_in_threadpool(service.get_active_count)
    return {"count": count}


@router.post("/{notification_id}/dismiss")
async def dismiss(notification_id: str, _user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(service.dismiss_notification, notification_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/dismiss-all")
async def dismiss_all(_user: dict = Depends(get_current_user)):
    return await run_in_threadpool(service.dismiss_all)


@router.get("/push/vapid-public-key")
async def vapid_public_key(_user: dict = Depends(get_current_user)):
    try:
        public_key = await run_in_threadpool(vapid.get_vapid_public_key)
    except Exception as e:
        logger.warning("VAPID public key unavailable: %s", e)
        raise HTTPException(status_code=503, detail="Web Push is not configured on this server.")
    return {"public_key": public_key}


@router.post("/push/subscribe")
async def subscribe(req: SubscribeRequest, _user: dict = Depends(get_current_user)):
    if not req.endpoint or not req.keys.p256dh or not req.keys.auth:
        raise HTTPException(status_code=400, detail="endpoint and keys are required")
    # Reject an endpoint that isn't a safe public https push URL (defense-in-depth
    # against SSRF — the server later POSTs to this endpoint).
    if not delivery.is_safe_push_endpoint(req.endpoint):
        raise HTTPException(status_code=400, detail="invalid push endpoint")
    return await run_in_threadpool(
        subscriptions.save_subscription, req.endpoint, req.keys.p256dh, req.keys.auth, req.user_agent)


@router.post("/push/unsubscribe")
async def unsubscribe(req: UnsubscribeRequest, _user: dict = Depends(get_current_user)):
    removed = await run_in_threadpool(subscriptions.remove_subscription, req.endpoint)
    return {"ok": True, "removed": removed}


@router.post("/test")
async def test_notification(req: TestRequest, _user: dict = Depends(get_current_user)):
    """Send a real notification through the full pipeline — no AI anywhere.

    Reports whether Web Push actually reached a device, so the UI doesn't claim
    success when only the in-app log row was written (R12).
    """
    result = await run_in_threadpool(delivery.deliver_notification, req.title, req.message)
    return result
