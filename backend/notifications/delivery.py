"""Notification delivery — a flat fan-out over the available channels (issue #6).

``deliver_notification(title, message)`` is the single entry point every caller
uses (the ``notify_user`` tool, reminder firing, heartbeat-failure alerts). It:
  1. writes the in-app ``notifications`` row FIRST (guaranteed audit record, R12);
  2. best-effort sends to each channel — Web Push (VAPID) now, Telegram via the
     frozen #7 seam — each swallowing its own errors so one broken channel never
     blocks the others or the row;
  3. records which channels actually delivered.

There is no channel base class — adding/deferring a channel is adding/omitting one
``_send_*`` branch (Telegram is a lazy-imported stub until #7 lands). WhatsApp is
NOT ported (per issue #6). pywebpush is imported lazily.
"""

import ipaddress
import json
import logging
import uuid
from urllib.parse import urlparse

from notifications import service, subscriptions

logger = logging.getLogger(__name__)

_PAYLOAD_MAX_BYTES = 3900  # keep under the 4KB Web Push limit
_PUSH_TIMEOUT_SECONDS = 10
_MAX_ENDPOINT_LEN = 2000


def deliver_notification(title: str, message: str) -> dict:
    """Deliver a notification to all channels and log it. NEVER raises.

    Callers (notify_user, reminder firing, heartbeat-failure alerts) rely on this
    contract — a DB hiccup or one broken channel must never propagate out and
    abort a reminder batch or a scheduler tick.
    """
    title = (title or "").strip() or "Notification"
    message = (message or "").strip()

    # Persist the in-app row FIRST (the guaranteed audit record). If even that
    # fails, fall through with a local id so push can still be attempted.
    notification_id = None
    try:
        notification_id = service.create_notification(title, message, [])
    except Exception:
        logger.warning("failed to create notification row", exc_info=True)
    if notification_id is None:
        notification_id = str(uuid.uuid4())

    channels_sent: list[str] = []
    web_push_ok = False
    try:
        web_push_ok = _send_web_push(title, message, notification_id)
    except Exception:
        logger.warning("web push channel errored", exc_info=True)
    if web_push_ok:
        channels_sent.append("web_push")
    try:
        if _send_telegram(title, message):
            channels_sent.append("telegram")
    except Exception:
        logger.debug("telegram channel errored", exc_info=True)

    try:
        service.update_channels(notification_id, channels_sent)
    except Exception:
        logger.warning("failed to record channels_sent for %s", notification_id, exc_info=True)

    return {"ok": True, "notification_id": notification_id,
            "channels_sent": channels_sent, "web_push": web_push_ok}


def is_safe_push_endpoint(endpoint: str) -> bool:
    """Best-effort guard against SSRF via a subscription endpoint.

    Requires an https URL of bounded length and rejects endpoints whose host is an
    obvious internal target (localhost, or a private/loopback/link-local/reserved IP
    LITERAL). This is defense-in-depth for a single-tenant, auth-gated endpoint — it
    does NOT resolve DNS, so it doesn't cover rebinding or redirects; real push
    endpoints are public push-service hosts (fcm.googleapis.com, *.push.apple.com …).
    """
    if not endpoint or len(endpoint) > _MAX_ENDPOINT_LEN:
        return False
    try:
        parsed = urlparse(endpoint)
    except Exception:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(parsed.hostname)   # only when host is an IP literal
    except ValueError:
        return True  # a hostname (not an IP) — allowed
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _build_payload(title: str, message: str, notification_id: str) -> str:
    body = message or title
    payload = {"title": title, "body": body, "url": "/crm", "notification_id": notification_id}
    data = json.dumps(payload)
    if len(data.encode()) > _PAYLOAD_MAX_BYTES:
        # Trim the body until the whole payload fits.
        payload["body"] = body[:400]
        data = json.dumps(payload)
    return data


def _send_web_push(title: str, message: str, notification_id: str) -> bool:
    """Send to every stored subscription. Returns True if ≥1 device accepted it."""
    subs = subscriptions.list_subscriptions()
    if not subs:
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush not installed — Web Push channel unavailable")
        return False

    from notifications import vapid
    try:
        _pub, private_key = vapid.get_vapid_keys()
        claims = vapid.get_vapid_claims()
    except Exception as e:
        # A persistent VAPID failure (e.g. ENCRYPTION_KEY rotated) breaks push for
        # good — surface it as a deduplicated alert, not just a log line, so the
        # operator sees it in the bell. Delivery of reminders still continues.
        logger.warning("VAPID keys unavailable — skipping Web Push", exc_info=True)
        try:
            from alerts import service as alerts_service
            alerts_service.create_alert(
                title="Web Push unavailable",
                message=f"VAPID keys could not be loaded: {str(e)[:200]}",
                source="vapid", source_id="vapid",
            )
        except Exception:
            logger.debug("failed to raise VAPID alert", exc_info=True)
        return False

    # Keys loaded → auto-clear any prior "Web Push unavailable" alert (symmetric with
    # the heartbeat-failure recovery path).
    try:
        from alerts import service as alerts_service
        alerts_service.resolve_by_source("vapid", "vapid")
    except Exception:
        logger.debug("failed to resolve VAPID alert", exc_info=True)

    data = _build_payload(title, message, notification_id)
    sent = 0
    for sub in subs:
        endpoint = sub.get("endpoint") or ""
        if not is_safe_push_endpoint(endpoint):
            logger.warning("skipping push to invalid or unsafe endpoint")
            continue
        try:
            webpush(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=data,
                vapid_private_key=private_key,
                vapid_claims=dict(claims),  # pywebpush mutates the claims dict (adds exp)
                timeout=_PUSH_TIMEOUT_SECONDS,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                # Subscription is gone — prune it so we stop trying.
                subscriptions.remove_subscription(endpoint)
                logger.info("removed expired push subscription (%s)", status)
            else:
                logger.warning("web push failed (status=%s)", status)
        except Exception:
            logger.warning("web push send errored", exc_info=True)
    return sent > 0


def _send_telegram(title: str, message: str) -> bool:
    """Telegram delivery — the frozen seam for issue #7.

    Calls ``telegram.service.notify_linked_user(text)`` (sync, no loop/engine
    dependency, returns False when not connected/linked) via a lazy
    ImportError-guarded import so merge order is irrelevant: until #7 lands the
    import fails and this no-ops (channels_sent stays ["web_push"]); when #7 lands
    the channel lights up with zero change here.
    """
    try:
        from telegram.service import notify_linked_user  # lands with issue #7
    except ImportError:
        return False
    try:
        return bool(notify_linked_user(f"{title}\n{message}"))
    except Exception:
        logger.debug("telegram delivery failed", exc_info=True)
        return False
