"""
CakeCRM — CRM CRUD + analytics (contacts, deals, tasks, activity, dashboard).

Ported from chatty's SQLite ``crm_lite/client.py`` and translated to Postgres:
``%s`` placeholders, ``INSERT ... RETURNING id`` then re-select to hydrate,
``ILIKE`` for the free-text search, ``COUNT(*) AS cnt`` scalars, a Python-side
``_now()`` on UPDATEs, and multi-statement writes wrapped in one
``get_connection()`` transaction. Query shapes follow the matching
``cake_os/backend/apps/crm`` services so later feature ports diff cleanly.
"""

import logging
from datetime import datetime, timezone

from core.postgres import get_connection, pg_execute, pg_fetchall, pg_fetchone

logger = logging.getLogger(__name__)

DEAL_STAGES = ["lead", "qualified", "proposal", "negotiation", "won", "lost"]
CONTACT_STATUSES = ["active", "inactive", "archived"]
TASK_PRIORITIES = ["low", "medium", "high"]

# SQL fragment for whitespace-tolerant boundary matching against the comma-separated
# tags column. Strips whitespace adjacent to commas so the filter survives free-form
# input like "PT, ET, MT". Valid Postgres (|| concat + REPLACE).
_TAGS_NORMALIZED_SQL = "(',' || REPLACE(REPLACE(tags, ', ', ','), ' ,', ',') || ',')"


def _now() -> str:
    """UTC ISO-8601 timestamp for updated_at bumps (matches cake_os _now())."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_tags(raw: str) -> str:
    """Clean a comma-separated tag string: strip whitespace around each label, drop empties."""
    if not raw:
        return ""
    return ",".join(label for label in (part.strip() for part in raw.split(",")) if label)


# ── Contacts ──────────────────────────────────────────────────────────────────

def create_contact(
    name: str, email: str = "", phone: str = "", company: str = "",
    title: str = "", source: str = "", status: str = "active",
    tags: str = "", notes: str = "",
) -> dict:
    if status not in CONTACT_STATUSES:
        status = "active"  # unknown status would hide the contact from every status tab
    row = pg_fetchone(
        """INSERT INTO contacts (name, email, phone, company, title, source, status, tags, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name, email, phone, company, title, source, status, _normalize_tags(tags), notes),
    )
    return get_contact(row["id"])


def get_contact(contact_id: int) -> dict | None:
    return pg_fetchone("SELECT * FROM contacts WHERE id = %s", (contact_id,))


def _contact_search_where(query: str, status: str | None, tags: str | None) -> tuple[str, list]:
    """Build the shared WHERE clause + params for contact free-text search."""
    like = f"%{query}%"
    conditions = ["(name ILIKE %s OR email ILIKE %s OR company ILIKE %s OR notes ILIKE %s)"]
    params: list = [like, like, like, like]
    if status:
        conditions.append("status = %s")
        params.append(status)
    if tags:
        labels = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if labels:
            tag_clauses = " OR ".join(f"{_TAGS_NORMALIZED_SQL} ILIKE %s" for _ in labels)
            conditions.append(f"({tag_clauses})")
            params.extend(f"%,{label},%" for label in labels)
    return " AND ".join(conditions), params


def search_contacts(
    query: str, status: str | None = None, tags: str | None = None,
    limit: int = 20, offset: int = 0,
) -> list[dict]:
    where, params = _contact_search_where(query, status, tags)
    return pg_fetchall(
        f"SELECT * FROM contacts WHERE {where} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        params + [limit, offset],
    )


def count_search_contacts(query: str, status: str | None = None, tags: str | None = None) -> int:
    """Total number of contacts matching a search (for accurate pagination totals)."""
    where, params = _contact_search_where(query, status, tags)
    row = pg_fetchone(f"SELECT COUNT(*) AS cnt FROM contacts WHERE {where}", params)
    return row["cnt"] if row else 0


def list_contacts(
    offset: int = 0, limit: int = 50, status: str | None = None,
    tags: str | None = None, sort: str = "updated_at",
) -> dict:
    allowed_sorts = {"updated_at", "created_at", "name", "company"}
    sort_col = sort if sort in allowed_sorts else "updated_at"

    conditions = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if tags:
        labels = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if labels:
            tag_clauses = " OR ".join(f"{_TAGS_NORMALIZED_SQL} ILIKE %s" for _ in labels)
            conditions.append(f"({tag_clauses})")
            params.extend(f"%,{label},%" for label in labels)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_row = pg_fetchone(f"SELECT COUNT(*) AS cnt FROM contacts {where}", params)
    total = total_row["cnt"] if total_row else 0

    params.extend([limit, offset])
    rows = pg_fetchall(
        f"SELECT * FROM contacts {where} ORDER BY {sort_col} DESC LIMIT %s OFFSET %s",
        params,
    )
    return {"contacts": rows, "total": total, "limit": limit, "offset": offset}


def list_distinct_tags() -> list[str]:
    """Return sorted list of unique tag labels currently in use across all contacts."""
    rows = pg_fetchall("SELECT tags FROM contacts WHERE tags != ''")
    seen: set[str] = set()
    for row in rows:
        for raw in row["tags"].split(","):
            label = raw.strip()
            if label:
                seen.add(label)
    return sorted(seen, key=str.lower)


def update_contact(contact_id: int, **fields) -> dict | None:
    allowed = {"name", "email", "phone", "company", "title", "source", "status", "tags", "notes"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if "tags" in filtered:
        filtered["tags"] = _normalize_tags(filtered["tags"] or "")
    if "status" in filtered and filtered["status"] not in CONTACT_STATUSES:
        filtered["status"] = "active"
    if not filtered:
        return get_contact(contact_id)
    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    values = list(filtered.values()) + [_now(), contact_id]
    pg_execute(
        f"UPDATE contacts SET {set_clause}, updated_at = %s WHERE id = %s", values
    )
    return get_contact(contact_id)


def delete_contact(contact_id: int) -> bool:
    """Delete a contact and its dependent activity/tasks in one transaction.

    FKs are ON DELETE SET NULL, so the explicit cascade (matching chatty)
    preserves the delete-the-rows behavior rather than orphaning them.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        # FOR UPDATE serializes against chatter_service.add_note (which locks the
        # same row before inserting), so a note can't be added to a contact that
        # this transaction is deleting — no orphaned crm_chatter rows.
        cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (contact_id,))
        if cur.fetchone() is None:
            return False
        cur.execute("DELETE FROM activity_log WHERE contact_id = %s", (contact_id,))
        cur.execute("DELETE FROM tasks WHERE contact_id = %s", (contact_id,))
        # crm_chatter is polymorphic (no FK), so its notes are dropped explicitly —
        # otherwise a reused contact SERIAL id would inherit this contact's notes.
        cur.execute(
            "DELETE FROM crm_chatter WHERE entity_type = 'contact' AND entity_id = %s",
            (contact_id,),
        )
        cur.execute("DELETE FROM contacts WHERE id = %s", (contact_id,))
    return True


def get_contact_detail(contact_id: int) -> dict | None:
    """Full contact profile with associated deals, tasks, and recent activity."""
    contact = get_contact(contact_id)
    if not contact:
        return None
    deals = pg_fetchall(
        "SELECT * FROM deals WHERE contact_id = %s ORDER BY updated_at DESC", (contact_id,)
    )
    tasks = pg_fetchall(
        "SELECT * FROM tasks WHERE contact_id = %s ORDER BY completed ASC, due_date ASC LIMIT 20",
        (contact_id,),
    )
    activity = pg_fetchall(
        "SELECT * FROM activity_log WHERE contact_id = %s ORDER BY created_at DESC LIMIT 20",
        (contact_id,),
    )
    return {**contact, "deals": deals, "tasks": tasks, "activity": activity}


# ── Deals ─────────────────────────────────────────────────────────────────────

def create_deal(
    title: str, contact_id: int | None = None, stage: str = "lead",
    value: float = 0, notes: str = "", expected_close_date: str = "",
    probability: int = 0, currency: str = "USD",
) -> dict:
    # Coerce an unknown stage to 'lead' (mirrors update_deal's validation): a
    # deal with a stage outside DEAL_STAGES would be summed into the pipeline
    # value but never render in any Kanban column.
    if stage not in DEAL_STAGES:
        stage = "lead"
    probability = max(0, min(100, probability))  # keep the percentage in range
    row = pg_fetchone(
        """INSERT INTO deals (title, contact_id, stage, value, notes, expected_close_date, probability, currency)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, contact_id, stage, value, notes, expected_close_date, probability, currency),
    )
    return get_deal(row["id"])


def get_deal(deal_id: int) -> dict | None:
    return pg_fetchone(
        """SELECT d.*, c.name AS contact_name
           FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
           WHERE d.id = %s""",
        (deal_id,),
    )


def get_deal_detail(deal_id: int) -> dict | None:
    """Full deal with contact info and activity."""
    deal = get_deal(deal_id)
    if not deal:
        return None
    activity = pg_fetchall(
        "SELECT * FROM activity_log WHERE deal_id = %s ORDER BY created_at DESC LIMIT 20", (deal_id,)
    )
    return {**deal, "activity": activity}


def get_pipeline(stage: str | None = None) -> dict:
    if stage:
        deals = pg_fetchall(
            """SELECT d.*, c.name AS contact_name
               FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
               WHERE d.stage = %s ORDER BY d.updated_at DESC""",
            (stage,),
        )
    else:
        deals = pg_fetchall(
            """SELECT d.*, c.name AS contact_name
               FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
               ORDER BY d.updated_at DESC""",
        )

    # Value summaries per stage (open stages only).
    stage_summary = pg_fetchall(
        """SELECT stage, COUNT(*) AS count, COALESCE(SUM(value), 0) AS total_value
           FROM deals WHERE stage NOT IN ('won', 'lost')
           GROUP BY stage"""
    )
    total_pipeline = sum(s["total_value"] for s in stage_summary)

    return {"deals": deals, "stage_summary": stage_summary, "total_pipeline_value": total_pipeline}


def list_deals(stage: str | None = None, contact_id: int | None = None, limit: int = 50) -> list[dict]:
    conditions = []
    params: list = []
    if stage:
        conditions.append("d.stage = %s")
        params.append(stage)
    if contact_id:
        conditions.append("d.contact_id = %s")
        params.append(contact_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    return pg_fetchall(
        f"""SELECT d.*, c.name AS contact_name
            FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
            {where} ORDER BY d.updated_at DESC LIMIT %s""",
        params,
    )


def update_deal(deal_id: int, **fields) -> dict | None:
    allowed = {"title", "stage", "value", "notes", "expected_close_date", "probability", "currency", "contact_id"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if "stage" in filtered and filtered["stage"] not in DEAL_STAGES:
        return None
    if "probability" in filtered and filtered["probability"] is not None:
        filtered["probability"] = max(0, min(100, filtered["probability"]))
    if not filtered:
        return get_deal(deal_id)
    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    values = list(filtered.values()) + [_now(), deal_id]
    pg_execute(
        f"UPDATE deals SET {set_clause}, updated_at = %s WHERE id = %s", values
    )
    return get_deal(deal_id)


def update_deal_stage(deal_id: int, stage: str) -> dict | None:
    if stage not in DEAL_STAGES:
        return None
    pg_execute(
        "UPDATE deals SET stage = %s, updated_at = %s WHERE id = %s", (stage, _now(), deal_id)
    )
    return get_deal(deal_id)


# ── Tasks ─────────────────────────────────────────────────────────────────────

def create_task(
    title: str, description: str = "", due_date: str = "",
    contact_id: int | None = None, deal_id: int | None = None,
    priority: str = "medium",
) -> dict:
    if priority not in TASK_PRIORITIES:
        priority = "medium"
    row = pg_fetchone(
        """INSERT INTO tasks (title, description, due_date, contact_id, deal_id, priority)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, description, due_date, contact_id, deal_id, priority),
    )
    return get_task(row["id"])


def get_task(task_id: int) -> dict | None:
    return pg_fetchone("SELECT * FROM tasks WHERE id = %s", (task_id,))


def list_tasks(
    contact_id: int | None = None, deal_id: int | None = None,
    completed: bool | None = None, due_before: str | None = None,
    priority: str | None = None, limit: int = 50,
) -> list[dict]:
    conditions = []
    params: list = []
    if contact_id is not None:
        conditions.append("t.contact_id = %s")
        params.append(contact_id)
    if deal_id is not None:
        conditions.append("t.deal_id = %s")
        params.append(deal_id)
    if completed is not None:
        conditions.append("t.completed = %s")
        params.append(1 if completed else 0)
    if due_before:
        conditions.append("t.due_date != '' AND t.due_date <= %s")
        params.append(due_before)
    if priority:
        conditions.append("t.priority = %s")
        params.append(priority)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    return pg_fetchall(
        f"""SELECT t.*, c.name AS contact_name, d.title AS deal_title
            FROM tasks t
            LEFT JOIN contacts c ON t.contact_id = c.id
            LEFT JOIN deals d ON t.deal_id = d.id
            {where} ORDER BY t.completed ASC, t.due_date ASC LIMIT %s""",
        params,
    )


def complete_task(task_id: int) -> dict | None:
    row = pg_fetchone(
        "UPDATE tasks SET completed = 1, updated_at = %s WHERE id = %s RETURNING id",
        (_now(), task_id),
    )
    if row is None:
        return None
    return get_task(task_id)


def update_task(task_id: int, **fields) -> dict | None:
    allowed = {"title", "description", "due_date", "contact_id", "deal_id", "priority", "completed"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    # Normalize the flag to the 0/1 invariant (a stray value like 2 is truthy in
    # the UI but matches neither `completed = 0` nor `= 1` filters).
    if "completed" in filtered:
        filtered["completed"] = 1 if filtered["completed"] else 0
    if "priority" in filtered and filtered["priority"] not in TASK_PRIORITIES:
        filtered["priority"] = "medium"
    if not filtered:
        return get_task(task_id)
    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    values = list(filtered.values()) + [_now(), task_id]
    pg_execute(
        f"UPDATE tasks SET {set_clause}, updated_at = %s WHERE id = %s", values
    )
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    return pg_execute("DELETE FROM tasks WHERE id = %s", (task_id,)) > 0


# ── Activity log ──────────────────────────────────────────────────────────────

def log_activity(activity: str, note: str = "", contact_id: int | None = None,
                 deal_id: int | None = None) -> dict:
    row = pg_fetchone(
        """INSERT INTO activity_log (activity, note, contact_id, deal_id)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (activity, note, contact_id, deal_id),
    )
    return pg_fetchone("SELECT * FROM activity_log WHERE id = %s", (row["id"],)) or {}


def get_activity_log(contact_id: int | None = None, deal_id: int | None = None, limit: int = 20) -> list[dict]:
    conditions = []
    params: list = []
    if contact_id is not None:
        conditions.append("a.contact_id = %s")
        params.append(contact_id)
    if deal_id is not None:
        conditions.append("a.deal_id = %s")
        params.append(deal_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    return pg_fetchall(
        f"""SELECT a.*, c.name AS contact_name, d.title AS deal_title
            FROM activity_log a
            LEFT JOIN contacts c ON a.contact_id = c.id
            LEFT JOIN deals d ON a.deal_id = d.id
            {where} ORDER BY a.created_at DESC LIMIT %s""",
        params,
    )


def update_activity(activity_id: int, activity: str | None = None, note: str | None = None) -> dict:
    fields = []
    params: list = []
    if activity is not None:
        fields.append("activity = %s")
        params.append(activity)
    if note is not None:
        fields.append("note = %s")
        params.append(note)
    if not fields:
        return pg_fetchone("SELECT * FROM activity_log WHERE id = %s", (activity_id,)) or {}
    params.append(activity_id)
    pg_execute(f"UPDATE activity_log SET {', '.join(fields)} WHERE id = %s", params)
    return pg_fetchone("SELECT * FROM activity_log WHERE id = %s", (activity_id,)) or {}


def delete_activity(activity_id: int) -> bool:
    return pg_execute("DELETE FROM activity_log WHERE id = %s", (activity_id,)) > 0


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_dashboard_stats() -> dict:
    total_row = pg_fetchone("SELECT COUNT(*) AS cnt FROM contacts")
    total_contacts = total_row["cnt"] if total_row else 0

    contacts_by_status = {}
    for row in pg_fetchall("SELECT status, COUNT(*) AS count FROM contacts GROUP BY status"):
        contacts_by_status[row["status"]] = row["count"]

    pipeline_by_stage = pg_fetchall(
        """SELECT stage, COUNT(*) AS count, COALESCE(SUM(value), 0) AS total_value
           FROM deals GROUP BY stage"""
    )
    total_pipeline_value = sum(
        r["total_value"] for r in pipeline_by_stage if r["stage"] not in ("won", "lost")
    )

    # Overdue = incomplete tasks whose due date is strictly before TODAY. Date-only
    # TEXT comparison: matches the Tasks page's client-side rule (a task due today is
    # NOT overdue) and can never cast-error on a malformed row (unlike ::date).
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    overdue_row = pg_fetchone(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE completed = 0 AND due_date != '' AND due_date < %s",
        (today,),
    )
    overdue_tasks = overdue_row["cnt"] if overdue_row else 0

    pending_row = pg_fetchone("SELECT COUNT(*) AS cnt FROM tasks WHERE completed = 0")
    pending_tasks = pending_row["cnt"] if pending_row else 0

    recent_activity = get_activity_log(limit=10)

    # Top open deals by value.
    top_deals = pg_fetchall(
        """SELECT d.*, c.name AS contact_name
           FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
           WHERE d.stage NOT IN ('won', 'lost')
           ORDER BY d.value DESC LIMIT 5"""
    )

    return {
        "total_contacts": total_contacts,
        "contacts_by_status": contacts_by_status,
        "pipeline_by_stage": pipeline_by_stage,
        "total_pipeline_value": total_pipeline_value,
        "overdue_tasks": overdue_tasks,
        "pending_tasks": pending_tasks,
        "recent_activity": recent_activity,
        "top_deals": top_deals,
    }


# ── First-run / sample-data state (crm_meta singleton) ────────────────────────

_CRM_TABLES = ("contacts", "deals", "tasks", "activity_log", "crm_chatter")


def get_crm_meta() -> dict:
    """Return the crm_meta singleton row (sample_data_loaded / onboarding_dismissed)."""
    return pg_fetchone("SELECT * FROM crm_meta WHERE id = 1") or {
        "id": 1, "sample_data_loaded": False, "onboarding_dismissed": False,
    }


def is_crm_empty() -> bool:
    """True only when ALL CRM tables are empty (contacts, deals, tasks, activity_log, crm_chatter).

    Checking every table matters: deals/tasks/activity/chatter can exist without
    contacts, and the fixed-id demo seed must never be inserted into a
    partially-populated CRM.
    """
    row = pg_fetchone(
        """SELECT (SELECT COUNT(*) FROM contacts)
                + (SELECT COUNT(*) FROM deals)
                + (SELECT COUNT(*) FROM tasks)
                + (SELECT COUNT(*) FROM activity_log)
                + (SELECT COUNT(*) FROM crm_chatter) AS total"""
    )
    return bool(row) and row["total"] == 0


def _crm_empty_in_txn(cur) -> bool:
    """All-tables-empty check on a caller-supplied cursor (inside a lock/transaction)."""
    cur.execute(
        """SELECT (SELECT COUNT(*) FROM contacts)
                + (SELECT COUNT(*) FROM deals)
                + (SELECT COUNT(*) FROM tasks)
                + (SELECT COUNT(*) FROM activity_log)
                + (SELECT COUNT(*) FROM crm_chatter) AS total"""
    )
    return cur.fetchone()[0] == 0


def get_demo_status() -> dict:
    """Drive the first-run onboarding prompt and the example-data banner."""
    meta = get_crm_meta()
    empty = is_crm_empty()
    sample_loaded = bool(meta.get("sample_data_loaded"))
    dismissed = bool(meta.get("onboarding_dismissed"))
    return {
        "empty": empty,
        "sample_data_loaded": sample_loaded,
        "show_onboarding": empty and not sample_loaded and not dismissed,
    }


def load_sample_data() -> dict:
    """Seed fictional demo data on first run (idempotent, transactional).

    Locks the crm_meta singleton (FOR UPDATE) to serialize double-clicks, re-checks
    that every CRM table is empty, seeds, and flips sample_data_loaded +
    onboarding_dismissed. A non-empty CRM is a clean no-op — never an error.
    """
    from crm.seed_data import seed_demo_data

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM crm_meta WHERE id = 1 FOR UPDATE")
        if not _crm_empty_in_txn(cur):
            return {"ok": True, "seeded": False}
        seed_demo_data(conn)
        cur.execute(
            "UPDATE crm_meta SET sample_data_loaded = TRUE, onboarding_dismissed = TRUE, "
            "updated_at = %s WHERE id = 1",
            (_now(),),
        )
    return {"ok": True, "seeded": True}


def dismiss_onboarding() -> dict:
    pg_execute(
        "UPDATE crm_meta SET onboarding_dismissed = TRUE, updated_at = %s WHERE id = 1",
        (_now(),),
    )
    return {"ok": True}


def _truncate_all(cur) -> None:
    cur.execute("TRUNCATE crm_chatter, activity_log, tasks, deals, contacts RESTART IDENTITY")


def clear_demo_data() -> dict:
    """Clear example data — only when sample data was actually loaded (guarded).

    Prevents an authenticated call from wiping a real CRM: a no-op unless
    crm_meta.sample_data_loaded is TRUE. onboarding_dismissed stays TRUE so the
    load prompt does not reappear. The deliberate real-data wipe is clear_all().
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT sample_data_loaded FROM crm_meta WHERE id = 1 FOR UPDATE")
        row = cur.fetchone()
        if not row or not row[0]:
            return {"ok": True, "cleared": False}
        _truncate_all(cur)
        cur.execute(
            "UPDATE crm_meta SET sample_data_loaded = FALSE, updated_at = %s WHERE id = 1",
            (_now(),),
        )
    return {"ok": True, "cleared": True}


def clear_all() -> dict:
    """Wipe ALL CRM data (the confirmation-gated real-data reset)."""
    with get_connection() as conn:
        cur = conn.cursor()
        _truncate_all(cur)
        cur.execute(
            "UPDATE crm_meta SET sample_data_loaded = FALSE, updated_at = %s WHERE id = 1",
            (_now(),),
        )
    return {"ok": True}
