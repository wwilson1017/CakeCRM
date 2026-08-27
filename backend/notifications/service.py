"""Notification log — the always-on, in-app delivery channel (issue #6).

The ``notifications`` table is written for EVERY notification regardless of push
success, so the in-app bell works with zero push subscriptions and zero AI keys.
Per the plan's R12, delivery creates the row FIRST (so a crash mid-send never
loses the audit record) and updates ``channels_sent`` after the external send.
"""

import logging
import uuid

from psycopg2.extras import Json

from core.postgres import pg_execute, pg_fetchall, pg_fetchone

logger = logging.getLogger(__name__)

_TITLE_MAX = 200
_MESSAGE_MAX = 5000


def create_notification(title: str, message: str, channels_sent: list | None = None,
                       notification_id: str | None = None) -> str:
    """Insert a notification row and return its id."""
    nid = notification_id or str(uuid.uuid4())
    pg_execute(
        """INSERT INTO notifications (id, title, message, channels_sent)
           VALUES (%s, %s, %s, %s)""",
        (nid, (title or "")[:_TITLE_MAX], (message or "")[:_MESSAGE_MAX],
         Json(channels_sent or [])),
    )
    return nid


def update_channels(notification_id: str, channels_sent: list) -> None:
    pg_execute(
        "UPDATE notifications SET channels_sent = %s WHERE id = %s",
        (Json(channels_sent or []), notification_id),
    )


def list_notifications(status: str = "active", limit: int = 10) -> list[dict]:
    """Notifications newest-first, capped. `id` closes the order so the capped window is
    stable when several notifications share a `created_at` — see `alerts.service`'s
    list_alerts for why `now()` makes that a routine tie rather than a rare one (#58)."""
    limit = max(1, min(int(limit or 10), 50))
    if status and status != "all":
        return pg_fetchall(
            "SELECT * FROM notifications WHERE status = %s "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (status, limit),
        )
    return pg_fetchall(
        "SELECT * FROM notifications ORDER BY created_at DESC, id DESC LIMIT %s", (limit,))


def get_active_count() -> int:
    row = pg_fetchone("SELECT count(*) AS n FROM notifications WHERE status = 'active'")
    return int((row or {}).get("n") or 0)


def dismiss_notification(notification_id: str) -> dict:
    dismissed = pg_execute(
        "UPDATE notifications SET status = 'dismissed', dismissed_at = now() "
        "WHERE id = %s AND status = 'active'",
        (notification_id,),
    )
    if dismissed == 0:
        return {"error": "notification not found or already dismissed"}
    return {"ok": True, "id": notification_id}


def dismiss_all() -> dict:
    n = pg_execute(
        "UPDATE notifications SET status = 'dismissed', dismissed_at = now() "
        "WHERE status = 'active'")
    return {"ok": True, "dismissed": n}
