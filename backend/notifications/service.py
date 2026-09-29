"""Notification log — the always-on, in-app delivery channel (issue #6).

The ``notifications`` table is written for EVERY notification regardless of push
success, so the in-app bell works with zero push subscriptions and zero AI keys.
Per the plan's R12, delivery creates the row FIRST (so a crash mid-send never
loses the audit record) and updates ``channels_sent`` after the external send.

**Recipients (issue #192, multi-user Phase B).** ``user_id`` is the seat a
notification is FOR, and ``NULL`` means BROADCAST — the mirror of ``owner_id NULL``
= unassigned. Every read and every dismissal is therefore guarded by
``(user_id = %s OR user_id IS NULL)``: a seat sees its own notifications plus the
install's, and nobody else's.

Two deliberate shapes here:

* ``create_notification`` defaults ``user_id`` to ``None``, because broadcast is the
  correct default for the install-level senders (the daily digest, heartbeat-failure
  alerts, the unattributed background ``notify_user`` turn).
* The readers and dismissers take ``user_id`` as a REQUIRED keyword-only argument.
  There is no safe default for them — ``None`` would silently answer "broadcasts
  only" and any other guess would leak — so a caller that forgets gets a TypeError
  at the call site instead of a wrong answer at runtime.

**Accepted wart, stated plainly** (#98 Decision 6a): dismissal state lives on the row,
so dismissing a BROADCAST dismisses it for everyone. That is exactly today's behavior
for every notification, so it is not a regression, and targeted rows — the majority
once nudges route — are dismissible only by their recipient. The
``notification_dismissals`` join table is Decision 6b, additive later.
"""

import logging
import uuid

from psycopg2.extras import Json

from core.postgres import pg_execute, pg_fetchall, pg_fetchone

logger = logging.getLogger(__name__)

_TITLE_MAX = 200
_MESSAGE_MAX = 5000

# "Mine, or the whole install's." Bound once per statement, so every caller appends
# exactly one parameter for it.
_MINE_OR_BROADCAST = "(user_id = %s OR user_id IS NULL)"


def create_notification(title: str, message: str, channels_sent: list | None = None,
                       notification_id: str | None = None,
                       user_id: int | None = None, link: str | None = None) -> str:
    """Insert a notification row and return its id. ``user_id=None`` = broadcast.
    ``link`` is an in-app path already validated by ``delivery._clean_link`` (#235)."""
    nid = notification_id or str(uuid.uuid4())
    pg_execute(
        """INSERT INTO notifications (id, title, message, channels_sent, user_id, link)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (nid, (title or "")[:_TITLE_MAX], (message or "")[:_MESSAGE_MAX],
         Json(channels_sent or []), user_id, link),
    )
    return nid


def update_channels(notification_id: str, channels_sent: list) -> None:
    pg_execute(
        "UPDATE notifications SET channels_sent = %s WHERE id = %s",
        (Json(channels_sent or []), notification_id),
    )


def list_notifications(*, user_id: int, status: str = "active", limit: int = 10) -> list[dict]:
    """This seat's notifications plus the install's broadcasts, newest-first, capped.

    `id` closes the order so the capped window is stable when several notifications
    share a `created_at` — see `alerts.service`'s list_alerts for why `now()` makes that
    a routine tie rather than a rare one (#58)."""
    limit = max(1, min(int(limit or 10), 50))
    if status and status != "all":
        return pg_fetchall(
            f"SELECT * FROM notifications WHERE {_MINE_OR_BROADCAST} AND status = %s "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (user_id, status, limit),
        )
    return pg_fetchall(
        f"SELECT * FROM notifications WHERE {_MINE_OR_BROADCAST} "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (user_id, limit),
    )


def get_active_count(*, user_id: int) -> int:
    row = pg_fetchone(
        f"SELECT count(*) AS n FROM notifications WHERE {_MINE_OR_BROADCAST} "
        "AND status = 'active'",
        (user_id,),
    )
    return int((row or {}).get("n") or 0)


def dismiss_notification(notification_id: str, *, user_id: int) -> dict:
    """Dismiss one notification, if it is this seat's or the install's.

    Someone else's notification reports "not found" rather than "forbidden", so the
    endpoint is not an oracle for whether a given id exists on another seat.
    """
    dismissed = pg_execute(
        "UPDATE notifications SET status = 'dismissed', dismissed_at = now() "
        f"WHERE id = %s AND status = 'active' AND {_MINE_OR_BROADCAST}",
        (notification_id, user_id),
    )
    if dismissed == 0:
        return {"error": "notification not found or already dismissed"}
    return {"ok": True, "id": notification_id}


def dismiss_all(*, user_id: int) -> dict:
    """Clear the bell: this seat's active notifications and the install's broadcasts."""
    n = pg_execute(
        "UPDATE notifications SET status = 'dismissed', dismissed_at = now() "
        f"WHERE status = 'active' AND {_MINE_OR_BROADCAST}",
        (user_id,),
    )
    return {"ok": True, "dismissed": n}
