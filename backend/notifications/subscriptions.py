"""Web Push subscription storage (issue #6).

Single-tenant: every stored subscription belongs to the one user, so a push
fans out to all of them (a person may have several devices/browsers). Ported
from Chatty's ``notifications/subscriptions.py``, upsert-by-endpoint.
"""

import logging
import uuid

from core.postgres import pg_execute, pg_fetchall

logger = logging.getLogger(__name__)


def save_subscription(endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> dict:
    """Upsert a push subscription by endpoint (idempotent re-subscribe)."""
    pg_execute(
        """INSERT INTO push_subscriptions (id, endpoint, p256dh, auth, user_agent)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (endpoint) DO UPDATE
             SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                 user_agent = EXCLUDED.user_agent""",
        (str(uuid.uuid4()), endpoint, p256dh, auth, user_agent or ""),
    )
    return {"ok": True}


def remove_subscription(endpoint: str) -> bool:
    """Delete a subscription by endpoint. Returns True if a row was removed."""
    return pg_execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,)) > 0


def list_subscriptions() -> list[dict]:
    # Uncapped, so no row can be skipped — but `created_at` is `now()` (transaction
    # start), so subscriptions registered together tie and the fan-out order flips
    # between reads. `id` pins it (issue #58).
    return pg_fetchall(
        "SELECT endpoint, p256dh, auth, user_agent FROM push_subscriptions "
        "ORDER BY created_at ASC, id ASC")
