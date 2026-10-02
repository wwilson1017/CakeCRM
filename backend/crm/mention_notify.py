"""Chatter @-mention notifications (issue #235, port of cake_os #2934).

``chatter_service.post_note`` / ``edit_note`` store the mentions in the note's own
transaction and hand back the seats to tell; the router schedules ``notify_mentions``
as a FastAPI background task, i.e. strictly post-commit and off the event loop. So a
slow push endpoint or Telegram can never hold the record's row lock, delay the note
reaching the screen, or roll back a note that is already there.

One ``deliver_notification(..., user_id=R, link=<record path>)`` per recipient. That is
a TARGETED delivery (#192/#193): the bell row is that seat's, Web Push reaches only that
seat's own subscriptions, and Telegram reaches only that seat's own link — a seat with no
link simply gets nothing there. No AI is involved, so this works with zero keys.

Deliberately not reachable from the assistant: ``crm_add_note`` calls ``add_note``, which
takes no mentions, so no agent turn — attended or unattended — can make one fire.
"""

from __future__ import annotations

import logging

from core.postgres import pg_fetchone
from crm.links import record_path

logger = logging.getLogger(__name__)

#: How much of the note body rides along in the bell entry, push body and Telegram text.
EXCERPT_LEN = 200

#: How a notification names the record: a label, and the table + column holding its name.
#: Code literals interpolated as SQL identifiers — never user input.
_CONTEXT = {
    "deal": ("Deal", "deals", "title"),
    "contact": ("Contact", "contacts", "name"),
    "company": ("Company", "companies", "name"),
}


def _context(entity_type: str, entity_id: int) -> str:
    """``Deal — Acme renewal``, degrading to ``Deal #12`` when the name cannot be read —
    a notification naming the record by number beats one that fails to send."""
    label, table, column = _CONTEXT[entity_type]
    try:
        row = pg_fetchone(f"SELECT {column} AS name FROM {table} WHERE id = %s", (entity_id,))
    except Exception:
        logger.warning("mention context read failed for %s %s", entity_type, entity_id)
        row = None
    name = str((row or {}).get("name") or "").strip()
    return f"{label} — {name}" if name else f"{label} #{int(entity_id)}"


def _excerpt(message: str) -> str:
    text = " ".join((message or "").split())
    return text if len(text) <= EXCERPT_LEN else text[: EXCERPT_LEN - 1].rstrip() + "…"


def _actor_name(actor: dict | None) -> str:
    actor = actor or {}
    return (str(actor.get("name") or "").strip() or str(actor.get("email") or "").strip()
            or "A teammate")


def notify_mentions(note: dict, recipients: list[int], actor: dict | None) -> int:
    """Tell each recipient they were mentioned on ``note``. Returns how many deliveries
    were attempted. Never raises: one failed recipient must not stop the rest."""
    if not recipients:
        return 0
    from notifications.delivery import deliver_notification

    entity_type, entity_id = note["entity_type"], note["entity_id"]
    title = f"{_actor_name(actor)} mentioned you on {_context(entity_type, entity_id)}"
    message = _excerpt(note.get("message") or "")
    link = record_path(entity_type, entity_id)
    actor_id = (actor or {}).get("id")
    sent = 0
    for user_id in recipients:
        if user_id == actor_id:  # the service already excludes the actor; belt and braces
            continue
        try:
            deliver_notification(title, message, user_id=user_id, link=link)
            sent += 1
        except Exception:
            logger.warning("mention notification to user %s failed", user_id, exc_info=True)
    return sent
