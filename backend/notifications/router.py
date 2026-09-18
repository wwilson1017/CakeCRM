"""Notifications REST API (issue #6).

The in-app notification log + Web Push subscription management. All endpoints
require JWT auth. Keyless — no ``ai_ready`` gate (push works with zero AI keys).

Every endpoint here is scoped to the CALLING SEAT (issue #192): reads and dismissals
see "mine OR broadcast", a subscribe stamps this browser as this seat's (which is also
the self-heal for a legacy unstamped endpoint), an unsubscribe can only remove a row
that is mine or unclaimed, and the test push goes to me rather than to everyone's
phone. There is no admin bypass and no "all seats" view — the notification log is not
an install-administration surface.

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
                            user: dict = Depends(get_current_user)):
    items = await run_in_threadpool(service.list_notifications, user_id=user["id"],
                                    status=status, limit=limit)
    return {"notifications": items, "count": len(items)}


@router.get("/counts")
async def notification_counts(user: dict = Depends(get_current_user)):
    """The TRUE active-notification count (unbounded) for the bell badge — the list
    endpoint above is capped, so its length under-reports once there are many."""
    count = await run_in_threadpool(service.get_active_count, user_id=user["id"])
    return {"count": count}


@router.post("/{notification_id}/dismiss")
async def dismiss(notification_id: str, user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(service.dismiss_notification, notification_id,
                                     user_id=user["id"])
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/dismiss-all")
async def dismiss_all(user: dict = Depends(get_current_user)):
    return await run_in_threadpool(service.dismiss_all, user_id=user["id"])


@router.get("/push/vapid-public-key")
async def vapid_public_key(_user: dict = Depends(get_current_user)):
    try:
        public_key = await run_in_threadpool(vapid.get_vapid_public_key)
    except Exception as e:
        logger.warning("VAPID public key unavailable: %s", e)
        raise HTTPException(status_code=503, detail="Web Push is not configured on this server.")
    return {"public_key": public_key}


@router.post("/push/subscribe")
async def subscribe(req: SubscribeRequest, user: dict = Depends(get_current_user)):
    if not req.endpoint or not req.keys.p256dh or not req.keys.auth:
        raise HTTPException(status_code=400, detail="endpoint and keys are required")
    # Reject an endpoint that isn't a safe public https push URL (defense-in-depth
    # against SSRF — the server later POSTs to this endpoint).
    if not delivery.is_safe_push_endpoint(req.endpoint):
        raise HTTPException(status_code=400, detail="invalid push endpoint")
    # Stamps this browser as this seat's on every call, not only the first — an
    # idempotent re-subscribe is how a legacy or handed-down endpoint migrates to
    # whoever is actually logged in on it (the CrmLayout self-heal re-POSTs on load).
    return await run_in_threadpool(
        subscriptions.save_subscription, req.endpoint, req.keys.p256dh, req.keys.auth,
        user["id"], req.user_agent)


@router.post("/push/unsubscribe")
async def unsubscribe(req: UnsubscribeRequest, user: dict = Depends(get_current_user)):
    # Scoped to "mine or unclaimed": the endpoint arrives in the request body, so
    # without the scope one member who learned another's endpoint could unsubscribe
    # their devices. `removed` is reported honestly either way — an endpoint that was
    # not ours simply reports False rather than 403, which keeps this from being an
    # oracle for whether some endpoint is registered to somebody else.
    removed = await run_in_threadpool(subscriptions.remove_subscription, req.endpoint,
                                      user["id"])
    return {"ok": True, "removed": removed}


@router.post("/test")
async def test_notification(req: TestRequest, user: dict = Depends(get_current_user)):
    """Send a real notification through the full pipeline — no AI anywhere.

    Reports whether Web Push actually reached a device, so the UI doesn't claim
    success when only the in-app log row was written (R12).

    Targeted at the caller (#192), not broadcast: "does push work for ME" must not fire
    on every colleague's phone. If this browser's subscription has not self-healed yet
    the honest answer is ``web_push: false``, which the card already renders as
    "logged to your notifications (no push device subscribed)".
    """
    result = await run_in_threadpool(delivery.deliver_notification, req.title, req.message,
                                     user_id=user["id"])
    return result
