"""Notification delivery — a flat fan-out over the available channels (issue #6).

``deliver_notification(title, message)`` is the single entry point every caller
uses (the ``notify_user`` tool, the proactive digest, heartbeat-failure alerts). It:
  1. writes the in-app ``notifications`` row FIRST (guaranteed audit record, R12);
  2. best-effort sends to each channel — Web Push (VAPID) now, Telegram via the
     frozen #7 seam — each swallowing its own errors so one broken channel never
     blocks the others or the row;
  3. records which channels actually delivered.

**Recipients (issue #192).** The optional keyword-only ``user_id`` is a DELIVERY
ADDRESS, not an acting identity: it changes where a notification lands and nothing
about what the caller may do. ``None`` means broadcast, which is the right default for
the install-level senders. Targeted delivery narrows Web Push to that seat's own
subscriptions; a broadcast still fans out to every stored subscription, so an
unclaimed legacy endpoint receives broadcasts and only broadcasts.

Telegram is routed too since #193 — targeted to that seat's link, broadcast to all.

There is no channel base class — adding/deferring a channel is adding/omitting one
``_send_*`` branch (Telegram is lazy-imported so its module stays optional). WhatsApp is
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


def deliver_notification(title: str, message: str, *, user_id: int | None = None) -> dict:
    """Deliver a notification to all channels and log it. NEVER raises.

    Callers (notify_user, the proactive digest, heartbeat-failure alerts) rely on this
    contract — a DB hiccup or one broken channel must never propagate out and
    abort a digest send or a scheduler tick.

    ``user_id`` is the recipient seat; ``None`` (the default) broadcasts to the whole
    install.
    """
    title = (title or "").strip() or "Notification"
    message = (message or "").strip()

    # Persist the in-app row FIRST (the guaranteed audit record). If even that
    # fails, fall through with a local id so push can still be attempted.
    notification_id = None
    logged = False
    try:
        notification_id = service.create_notification(title, message, [], user_id=user_id)
        logged = True
    except Exception:
        logger.warning("failed to create notification row", exc_info=True)
    if notification_id is None:
        notification_id = str(uuid.uuid4())

    channels_sent: list[str] = []
    web_push_ok = False
    try:
        web_push_ok = _send_web_push(title, message, notification_id, user_id)
    except Exception:
        logger.warning("web push channel errored", exc_info=True)
    if web_push_ok:
        channels_sent.append("web_push")
    try:
        if _send_telegram(title, message, user_id):
            channels_sent.append("telegram")
    except Exception:
        logger.debug("telegram channel errored", exc_info=True)

    try:
        service.update_channels(notification_id, channels_sent)
    except Exception:
        logger.warning("failed to record channels_sent for %s", notification_id, exc_info=True)

    return {"ok": True, "notification_id": notification_id,
            "channels_sent": channels_sent, "web_push": web_push_ok, "logged": logged}


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


def _send_web_push(title: str, message: str, notification_id: str,
                   user_id: int | None = None) -> bool:
    """Send to the recipient's subscriptions. Returns True if ≥1 device accepted it.

    ``user_id=None`` is a broadcast and reaches every stored subscription, unclaimed
    ones included. A targeted send reaches that seat's stamped rows ONLY — an endpoint
    whose owner is unknown must never receive somebody's personal notification.
    """
    subs = subscriptions.list_subscriptions(user_id=user_id)
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
        # operator sees it in the bell. Delivery over the other channels still continues.
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


def _send_telegram(title: str, message: str, user_id: int | None = None) -> bool:
    """Telegram delivery — targeted to one seat's link, or broadcast to every link.

    Calls ``telegram.service.notify_user_telegram(user_id, text)`` or
    ``broadcast_telegram(text)`` (both sync, no loop/engine dependency, returning False
    when Telegram isn't connected or the recipient has no link) via a lazy
    ImportError-guarded import, so this no-ops rather than raising if the module is absent.

    **Routed since #193 (Phase B / B4).** #192 gave a notification a recipient but there
    was exactly ONE install-wide Telegram binding to deliver it to, so a targeted
    notification's text still reached whoever linked that chat — a stated gap, and this
    is where it closes. Each seat now has its own link, so ``user_id`` picks a chat the
    way it already picks push subscriptions: a seat with no link (or a deactivated one)
    simply receives nothing here, and ``None`` still means broadcast.
    """
    try:
        from telegram.service import broadcast_telegram, notify_user_telegram
    except ImportError:
        return False
    text = f"{title}\n{message}"
    try:
        if user_id is None:
            return bool(broadcast_telegram(text))
        return bool(notify_user_telegram(user_id, text))
    except Exception:
        logger.debug("telegram delivery failed", exc_info=True)
        return False
