"""System alerts — persistent, dedup'd operational warnings (issue #6).

Adapted from Chatty's ``core/agents/alerts/service.py``, single-tenant. An alert
is raised for a system-level condition the user should see in-app — chiefly
repeated heartbeat failures. Dedup is enforced at the DB level: the partial
unique index ``uq_alerts_active_source (source, source_id) WHERE status='active'``
means ``create_alert`` is an idempotent upsert — a recurring condition updates the
existing active row instead of stacking duplicates (R10).
"""

import logging
import uuid

from core.postgres import pg_execute, pg_fetchall, pg_fetchone

logger = logging.getLogger(__name__)


def create_alert(title: str, message: str, source: str = "heartbeat",
                source_id: str | None = None) -> dict:
    """Create (or refresh) an active alert. Dedup'd on (source, source_id)."""
    aid = str(uuid.uuid4())
    if source_id is not None:
        # Idempotent upsert against the partial unique index.
        pg_execute(
            """INSERT INTO alerts (id, source, source_id, title, message)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (source, source_id) WHERE status = 'active'
               DO UPDATE SET title = EXCLUDED.title, message = EXCLUDED.message""",
            (aid, source, source_id, title, message),
        )
    else:
        pg_execute(
            "INSERT INTO alerts (id, source, source_id, title, message) VALUES (%s, %s, %s, %s, %s)",
            (aid, source, source_id, title, message),
        )
    return {"ok": True, "source": source, "source_id": source_id}


def list_alerts(status: str = "active", limit: int = 50) -> list[dict]:
    """Alerts newest-first, capped.

    `created_at` defaults to `now()`, which is TRANSACTION-start time — so every alert
    raised inside one transaction carries a byte-identical timestamp and the sort ties.
    Under a LIMIT, a tie Postgres is free to break differently on each execution makes
    rows repeat or vanish between reads, so the id (a uuid4 TEXT PK — arbitrary but
    unique, and there is no insertion-sequence column) closes the order (issue #58).
    """
    limit = max(1, min(int(limit or 50), 200))
    if status and status != "all":
        return pg_fetchall(
            "SELECT * FROM alerts WHERE status = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (status, limit),
        )
    return pg_fetchall(
        "SELECT * FROM alerts ORDER BY created_at DESC, id DESC LIMIT %s", (limit,))


def get_active_count() -> int:
    row = pg_fetchone("SELECT count(*) AS n FROM alerts WHERE status = 'active'")
    return int((row or {}).get("n") or 0)


def acknowledge_alert(alert_id: str) -> dict:
    n = pg_execute(
        "UPDATE alerts SET status = 'acknowledged', acknowledged_at = now() "
        "WHERE id = %s AND status = 'active'",
        (alert_id,),
    )
    if n == 0:
        return {"error": "alert not found or not active"}
    return {"ok": True, "id": alert_id}


def resolve_alert(alert_id: str) -> dict:
    n = pg_execute(
        "UPDATE alerts SET status = 'resolved', resolved_at = now() "
        "WHERE id = %s AND status IN ('active', 'acknowledged')",
        (alert_id,),
    )
    if n == 0:
        return {"error": "alert not found or already resolved"}
    return {"ok": True, "id": alert_id}


def resolve_by_source(source: str, source_id: str) -> int:
    """Auto-clear active alerts for a condition that has recovered.

    Returns the number of alerts resolved.
    """
    return pg_execute(
        "UPDATE alerts SET status = 'resolved', resolved_at = now() "
        "WHERE source = %s AND source_id = %s AND status IN ('active', 'acknowledged')",
        (source, source_id),
    )
