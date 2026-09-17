"""Web Push subscription storage (issue #6, re-keyed per seat by #192).

A row records a BROWSER (the endpoint is the natural key), and ``user_id`` records
whose browser it is. A person may have several devices, so a targeted push still fans
out to all of that seat's rows.

``user_id`` is nullable and its NULL has a narrower meaning than the one on
``notifications``: an unstamped endpoint belongs to NOBODY KNOWN, so it receives
BROADCASTS ONLY and never a targeted push. That is the whole reason the #192 migration
does not claim legacy rows for the admin — see the migration's comment.

**The self-heal.** The upsert re-stamps ``user_id = EXCLUDED.user_id`` on every
re-subscribe, so an endpoint migrates to whoever is actually logged in on that browser.
``frontend/src/crm/CrmLayout.tsx`` silently re-POSTs an already-granted subscription on
every authenticated load, which is what makes that happen with no user action.
"""

import logging
import uuid

from core.postgres import pg_execute, pg_fetchall

logger = logging.getLogger(__name__)


def save_subscription(endpoint: str, p256dh: str, auth: str, user_id: int | None,
                      user_agent: str = "") -> dict:
    """Upsert a push subscription by endpoint (idempotent re-subscribe).

    ``user_id`` is positional and required — the one caller is an authenticated route,
    and a default would quietly mint broadcast-only subscriptions that never self-heal.
    """
    pg_execute(
        """INSERT INTO push_subscriptions (id, endpoint, p256dh, auth, user_agent, user_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (endpoint) DO UPDATE
             SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                 user_agent = EXCLUDED.user_agent,
                 user_id = EXCLUDED.user_id""",
        (str(uuid.uuid4()), endpoint, p256dh, auth, user_agent or "", user_id),
    )
    return {"ok": True}


def remove_subscription(endpoint: str, user_id: int | None = None) -> bool:
    """Delete a subscription by endpoint. Returns True if a row was removed.

    ``user_id=None`` deletes by endpoint alone and is for the INTERNAL prune only:
    ``delivery._send_web_push`` gets a 404/410 from the push service and has no seat in
    hand, and a dead endpoint must go regardless of who owns it.

    The authenticated unsubscribe route passes a seat, which scopes the delete to
    "mine or unclaimed". Without that, one member who learned another's endpoint could
    silently unsubscribe their devices; with it, a legacy unstamped row is still
    removable by whoever is sitting at that browser (its self-heal may not have run).
    """
    if user_id is None:
        return pg_execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,)) > 0
    return pg_execute(
        "DELETE FROM push_subscriptions WHERE endpoint = %s "
        "AND (user_id = %s OR user_id IS NULL)",
        (endpoint, user_id),
    ) > 0


def list_subscriptions(user_id: int | None = None) -> list[dict]:
    """Subscriptions to push to. ``user_id=None`` means EVERY one (a broadcast).

    An int narrows to that seat's stamped rows ONLY — unstamped rows are excluded on
    purpose, because their owner is unknown and a targeted notification must not land
    on a browser that might belong to someone else.
    """
    # Uncapped, so no row can be skipped — but `created_at` is `now()` (transaction
    # start), so subscriptions registered together tie and the fan-out order flips
    # between reads. `id` pins it (issue #58).
    where = "" if user_id is None else "WHERE user_id = %s "
    params = () if user_id is None else (user_id,)
    return pg_fetchall(
        f"SELECT endpoint, p256dh, auth, user_agent FROM push_subscriptions {where}"
        "ORDER BY created_at ASC, id ASC", params)
