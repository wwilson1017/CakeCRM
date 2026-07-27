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
from crm import touch_count_service

logger = logging.getLogger(__name__)

DEAL_STAGES = ["lead", "qualified", "proposal", "negotiation", "won", "lost"]
CONTACT_STATUSES = ["active", "inactive", "archived"]
TASK_PRIORITIES = ["low", "medium", "high"]
COMPANY_STATUSES = ["active", "archived"]

# The six ASCII whitespace bytes (space, tab, LF, CR, FF, VT). Company names are
# trimmed with THIS set (not Python's Unicode-aware str.strip()) so the value the
# service stores normalizes identically to the companies migration's backfill +
# unique index, which use btrim(name, E' \t\n\r\f\x0b') — a fixed byte set, so both
# sides agree regardless of the database's libc/locale.
_WS = " \t\n\r\f\v"

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
    tags: str = "", notes: str = "", company_id: int | None = None,
) -> dict:
    if status not in CONTACT_STATUSES:
        status = "active"  # unknown status would hide the contact from every status tab
    # company_id is appended last so the existing INSERT-param assertions (which
    # check the leading columns) stay valid; a bad FK raises ForeignKeyViolation
    # which the router maps to 400.
    row = pg_fetchone(
        """INSERT INTO contacts (name, email, phone, company, title, source, status, tags, notes, company_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name, email, phone, company, title, source, status, _normalize_tags(tags), notes, company_id),
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
    allowed = {"name", "email", "phone", "company", "title", "source", "status", "tags", "notes", "company_id"}
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
        # FOR UPDATE serializes against chatter_service.add_note and
        # field_service.set_field_values (both lock this row before inserting), so a
        # note or custom-field value can't be written to a contact that this
        # transaction is deleting — no orphaned crm_chatter / crm_field_values rows.
        cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (contact_id,))
        if cur.fetchone() is None:
            return False
        cur.execute("DELETE FROM activity_log WHERE contact_id = %s", (contact_id,))
        cur.execute("DELETE FROM tasks WHERE contact_id = %s", (contact_id,))
        # crm_chatter, crm_field_values and crm_field_provenance are polymorphic (no
        # FK), so their rows are dropped explicitly — otherwise a reused contact SERIAL
        # id would inherit this contact's notes / custom-field values / AI badges.
        # NOTE: deals have no delete path today; if a delete_deal is ever added it MUST
        # do the same FOR UPDATE lock + these DELETEs for entity_type='deal'.
        cur.execute(
            "DELETE FROM crm_chatter WHERE entity_type = 'contact' AND entity_id = %s",
            (contact_id,),
        )
        cur.execute(
            "DELETE FROM crm_field_values WHERE entity_type = 'contact' AND entity_id = %s",
            (contact_id,),
        )
        cur.execute(
            "DELETE FROM crm_field_provenance WHERE entity_type = 'contact' AND entity_id = %s",
            (contact_id,),
        )
        cur.execute("DELETE FROM contacts WHERE id = %s", (contact_id,))
    return True


def get_contact_detail(contact_id: int) -> dict | None:
    """Full contact profile with associated deals, tasks, and recent activity.

    The company_name join is inlined here (not in get_contact) so the linked
    company can be shown/linked on the detail page without changing get_contact's
    exact SQL.
    """
    contact = pg_fetchone(
        """SELECT ct.*, co.name AS company_name
           FROM contacts ct LEFT JOIN companies co ON ct.company_id = co.id
           WHERE ct.id = %s""",
        (contact_id,),
    )
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


# ── Companies ─────────────────────────────────────────────────────────────────

def create_company(
    name: str, domain: str = "", industry: str = "", phone: str = "",
    address: str = "", notes: str = "", source: str = "", status: str = "active",
) -> dict:
    if status not in COMPANY_STATUSES:
        status = "active"
    row = pg_fetchone(
        """INSERT INTO companies (name, domain, industry, phone, address, notes, source, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name.strip(_WS), domain, industry, phone, address, notes, source, status),
    )
    return get_company(row["id"])


def get_company(company_id: int) -> dict | None:
    return pg_fetchone("SELECT * FROM companies WHERE id = %s", (company_id,))


def _company_search_where(query: str, status: str | None) -> tuple[str, list]:
    """Build the shared WHERE clause + params for company free-text search."""
    like = f"%{query}%"
    conditions = ["(name ILIKE %s OR domain ILIKE %s OR industry ILIKE %s OR notes ILIKE %s)"]
    params: list = [like, like, like, like]
    if status:
        conditions.append("status = %s")
        params.append(status)
    return " AND ".join(conditions), params


def search_companies(
    query: str, status: str | None = None, limit: int = 20, offset: int = 0,
) -> list[dict]:
    where, params = _company_search_where(query, status)
    return pg_fetchall(
        f"SELECT * FROM companies WHERE {where} ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
        params + [limit, offset],
    )


def count_search_companies(query: str, status: str | None = None) -> int:
    """Total number of companies matching a search (for accurate pagination totals)."""
    where, params = _company_search_where(query, status)
    row = pg_fetchone(f"SELECT COUNT(*) AS cnt FROM companies WHERE {where}", params)
    return row["cnt"] if row else 0


def list_companies(
    offset: int = 0, limit: int = 50, status: str | None = None, sort: str = "name",
) -> dict:
    allowed_sorts = {"name", "industry", "created_at", "updated_at"}
    sort_col = sort if sort in allowed_sorts else "name"
    # Names/industry read best ascending; timestamps newest-first. Append an id
    # tie-breaker so offset/infinite-scroll pagination is stable.
    direction = "ASC" if sort_col in ("name", "industry") else "DESC"

    conditions = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_row = pg_fetchone(f"SELECT COUNT(*) AS cnt FROM companies {where}", params)
    total = total_row["cnt"] if total_row else 0

    params.extend([limit, offset])
    rows = pg_fetchall(
        f"SELECT * FROM companies {where} ORDER BY {sort_col} {direction}, id {direction} LIMIT %s OFFSET %s",
        params,
    )
    return {"companies": rows, "total": total, "limit": limit, "offset": offset}


def update_company(company_id: int, **fields) -> dict | None:
    allowed = {"name", "domain", "industry", "phone", "address", "notes", "source", "status"}
    # Drop None values: every company column is NOT NULL, and a tool call sending
    # an explicit null (the HTTP route already filters these out) would otherwise
    # raise a NotNullViolation. None means "field not provided" here.
    filtered = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "name" in filtered:
        # Reject a name that is blank once ALL whitespace is ignored (guards the
        # tool path, which bypasses the router's 400). Store it trimmed of the
        # ASCII whitespace set only (_WS), matching the migration's btrim.
        if filtered["name"].strip():
            filtered["name"] = filtered["name"].strip(_WS)
        else:
            del filtered["name"]
    if "status" in filtered and filtered["status"] not in COMPANY_STATUSES:
        filtered["status"] = "active"
    if not filtered:
        return get_company(company_id)
    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    values = list(filtered.values()) + [_now(), company_id]
    pg_execute(
        f"UPDATE companies SET {set_clause}, updated_at = %s WHERE id = %s", values
    )
    return get_company(company_id)


def delete_company(company_id: int) -> bool:
    """Delete a company. Its contacts/deals are kept — the FK is ON DELETE SET NULL,
    so they simply unlink. Its polymorphic crm_field_values rows are dropped
    explicitly (no FK), so a reused company SERIAL id can't inherit them.

    Runs in one transaction with a FOR UPDATE lock — the same discipline as
    delete_contact — serializing against field_service.set_field_values so a value
    can't be written to a company this transaction is deleting. (No crm_chatter
    cleanup: chatter attaches to deals/contacts only, never companies.)
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM companies WHERE id = %s FOR UPDATE", (company_id,))
        if cur.fetchone() is None:
            return False
        cur.execute(
            "DELETE FROM crm_field_values WHERE entity_type = 'company' AND entity_id = %s",
            (company_id,),
        )
        cur.execute("DELETE FROM companies WHERE id = %s", (company_id,))
    return True


def get_company_detail(company_id: int) -> dict | None:
    """Full company profile with rolled-up contacts, deals, and activity.

    Activity has no company_id column, so it's rolled up by joining through the
    company's own contacts and deals.
    """
    company = get_company(company_id)
    if not company:
        return None
    contacts = pg_fetchall(
        "SELECT * FROM contacts WHERE company_id = %s ORDER BY name ASC", (company_id,)
    )
    deals = pg_fetchall(
        """SELECT d.*, c.name AS contact_name
           FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
           WHERE d.company_id = %s ORDER BY d.updated_at DESC""",
        (company_id,),
    )
    activity = pg_fetchall(
        """SELECT a.*, c.name AS contact_name, d.title AS deal_title
           FROM activity_log a
           LEFT JOIN contacts c ON a.contact_id = c.id
           LEFT JOIN deals d ON a.deal_id = d.id
           WHERE a.contact_id IN (SELECT id FROM contacts WHERE company_id = %s)
              OR a.deal_id IN (SELECT id FROM deals WHERE company_id = %s)
           ORDER BY a.created_at DESC LIMIT 20""",
        (company_id, company_id),
    )
    # Single-currency (USD) sum, matching the rest of the app's hardcoded '$'.
    open_deal_value = sum(d["value"] for d in deals if d["stage"] not in ("won", "lost"))
    return {**company, "contacts": contacts, "deals": deals, "activity": activity,
            "open_deal_value": open_deal_value}


# ── Deals ─────────────────────────────────────────────────────────────────────

def create_deal(
    title: str, contact_id: int | None = None, stage: str = "lead",
    value: float = 0, notes: str = "", expected_close_date: str = "",
    probability: int = 0, currency: str = "USD", company_id: int | None = None,
) -> dict:
    # Coerce an unknown stage to 'lead' (mirrors update_deal's validation): a
    # deal with a stage outside DEAL_STAGES would be summed into the pipeline
    # value but never render in any Kanban column.
    if stage not in DEAL_STAGES:
        stage = "lead"
    probability = max(0, min(100, probability))  # keep the percentage in range
    # company_id appended last (see create_contact); a bad FK -> ForeignKeyViolation.
    row = pg_fetchone(
        """INSERT INTO deals (title, contact_id, stage, value, notes, expected_close_date, probability, currency, company_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, contact_id, stage, value, notes, expected_close_date, probability, currency, company_id),
    )
    return get_deal(row["id"])


def get_deal(deal_id: int) -> dict | None:
    return pg_fetchone(
        """SELECT d.*, c.name AS contact_name, co.name AS company_name
           FROM deals d
           LEFT JOIN contacts c ON d.contact_id = c.id
           LEFT JOIN companies co ON d.company_id = co.id
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
    # Single query (optional stage WHERE) so the two branches can't drift. Beyond the
    # contact-name join, the board payload carries `company_name` (mirrors get_deal) for
    # keyword search, and a derived `last_activity_at` (issue #21) = the most recent of the
    # deal's genuine activity signals: explicit activity_log rows (calls/emails logged via
    # POST /api/crm/activity — never written by edits/stage-moves) blended with un-archived
    # deal chatter notes. The UNION-ALL/GROUP BY yields one row per deal; the LEFT JOIN
    # leaves `last_at` NULL when a deal has neither → the client's "no activity" bucket.
    where = "WHERE d.stage = %s" if stage else ""
    deals = pg_fetchall(
        f"""SELECT d.*, c.name AS contact_name, co.name AS company_name,
                   la.last_at AS last_activity_at
            FROM deals d
            LEFT JOIN contacts c ON d.contact_id = c.id
            LEFT JOIN companies co ON d.company_id = co.id
            LEFT JOIN (
                SELECT deal_id, MAX(created_at) AS last_at FROM (
                    SELECT deal_id, created_at FROM activity_log WHERE deal_id IS NOT NULL
                    UNION ALL
                    SELECT entity_id AS deal_id, created_at FROM crm_chatter
                    WHERE entity_type = 'deal' AND archived = 0
                ) events GROUP BY deal_id
            ) la ON la.deal_id = d.id
            {where}
            ORDER BY d.updated_at DESC""",
        (stage,) if stage else (),
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
    allowed = {"title", "stage", "value", "notes", "expected_close_date", "probability", "currency", "contact_id", "company_id"}
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
    result = pg_fetchone("SELECT * FROM activity_log WHERE id = %s", (row["id"],)) or {}
    # An activity on a deal is fresh touch-count evidence — queue a recompute (O(1), never
    # raises; the insert has already committed via the pg_fetchone helpers).
    if deal_id:
        touch_count_service.schedule_recompute(deal_id)
    return result


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

# Entity-data tables cleaned on every demo/reset path. crm_field_values and
# crm_field_provenance are polymorphic (no FK to the entity tables), so they belong
# here alongside crm_chatter. The GLOBAL schema table crm_field_definitions is user
# *configuration* — it is NOT entity data, survives demo-clear, and is truncated only
# by clear_all (see _truncate_all).
_CRM_TABLES = (
    "companies", "contacts", "deals", "tasks", "activity_log", "crm_chatter",
    "crm_field_values", "crm_field_provenance",
)


def get_crm_meta() -> dict:
    """Return the crm_meta singleton row (sample_data_loaded / onboarding_dismissed /
    ai_key_prompt_dismissed)."""
    return pg_fetchone("SELECT * FROM crm_meta WHERE id = 1") or {
        "id": 1, "sample_data_loaded": False, "onboarding_dismissed": False,
        "ai_key_prompt_dismissed": False,
    }


def is_crm_empty() -> bool:
    """True only when ALL entity-data CRM tables are empty (companies, contacts,
    deals, tasks, activity_log, crm_chatter, crm_field_values).

    Checking every table matters: deals/tasks/activity/chatter/field-values can exist
    without contacts, and the fixed-id demo seed must never be inserted into a
    partially-populated CRM. crm_field_definitions is deliberately EXCLUDED — it is
    user configuration (like branding), not entity data; counting it would make a
    just-defined custom field suppress the first-run sample-data prompt.
    """
    row = pg_fetchone(
        """SELECT (SELECT COUNT(*) FROM companies)
                + (SELECT COUNT(*) FROM contacts)
                + (SELECT COUNT(*) FROM deals)
                + (SELECT COUNT(*) FROM tasks)
                + (SELECT COUNT(*) FROM activity_log)
                + (SELECT COUNT(*) FROM crm_chatter)
                + (SELECT COUNT(*) FROM crm_field_values)
                + (SELECT COUNT(*) FROM crm_field_provenance) AS total"""
    )
    return bool(row) and row["total"] == 0


def _crm_empty_in_txn(cur) -> bool:
    """All-tables-empty check on a caller-supplied cursor (inside a lock/transaction).

    Excludes crm_field_definitions for the same reason as is_crm_empty().
    """
    cur.execute(
        """SELECT (SELECT COUNT(*) FROM companies)
                + (SELECT COUNT(*) FROM contacts)
                + (SELECT COUNT(*) FROM deals)
                + (SELECT COUNT(*) FROM tasks)
                + (SELECT COUNT(*) FROM activity_log)
                + (SELECT COUNT(*) FROM crm_chatter)
                + (SELECT COUNT(*) FROM crm_field_values)
                + (SELECT COUNT(*) FROM crm_field_provenance) AS total"""
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
        "ai_key_prompt_dismissed": bool(meta.get("ai_key_prompt_dismissed")),
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


def dismiss_ai_prompt() -> dict:
    """Durably dismiss the first-run 'add an AI key' nudge (cross-device).

    Single-statement blind write like dismiss_onboarding — no check-then-write,
    so no FOR UPDATE lock is needed.
    """
    pg_execute(
        "UPDATE crm_meta SET ai_key_prompt_dismissed = TRUE, updated_at = %s WHERE id = 1",
        (_now(),),
    )
    return {"ok": True}


def _truncate_all(cur, include_definitions: bool = False) -> None:
    # Order chosen for the multi-statement writers: delete_contact (SELECT ... FOR
    # UPDATE on contacts, then deletes) and add_note (locks its target, then writes
    # crm_chatter) both take contacts/deals FIRST and crm_chatter LAST, so TRUNCATE
    # acquires its ACCESS EXCLUSIVE locks in the same order and can't invert against
    # them. companies leads because delete_company's ON DELETE SET NULL locks
    # companies then contact/deal rows (parent-then-child) inside one statement.
    # crm_field_values trails crm_chatter — set_field_values locks its ENTITY row
    # first (contacts/companies/deals) then writes crm_field_values, same ordering.
    # (A residual microsecond-window inversion with FK-checking INSERTs
    # — child-then-parent lock order — is unavoidable by any single table order and
    # is left to Postgres's deadlock detector.) The exact string is pinned by a test.
    #
    # include_definitions=True (clear_all only) additionally wipes the GLOBAL custom-
    # field schema. crm_field_definitions is placed AFTER the entity tables but BEFORE
    # crm_field_values so the lock order is consistent with BOTH concurrent writers:
    # set_field_values locks entity→definitions, and delete_field_definition locks
    # definitions→values — entity, then definitions, then values satisfies both and
    # can't invert against either. demo-clear leaves definitions intact (user config).
    # crm_field_provenance trails both variants, matching the entity-tables-first order
    # its own writers take (record_fields/confirm lock the entity row FOR UPDATE before
    # touching provenance), so it inverts against no writer either.
    if include_definitions:
        cur.execute(
            "TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
            "crm_field_definitions, crm_field_values, crm_field_provenance RESTART IDENTITY"
        )
    else:
        cur.execute(
            "TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
            "crm_field_values, crm_field_provenance RESTART IDENTITY"
        )


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
        # Lock the crm_meta singleton FIRST, before truncating — same order as
        # clear_demo_data / load_sample_data, so all three demo-state writers
        # serialize on this row instead of deadlocking (truncate-then-update here
        # vs lock-then-count there would otherwise invert).
        cur.execute("SELECT id FROM crm_meta WHERE id = 1 FOR UPDATE")
        # include_definitions=True: clear_all is the deliberate real-data reset, so it
        # also wipes user-defined custom-field definitions. demo-clear does NOT.
        _truncate_all(cur, include_definitions=True)
        cur.execute(
            "UPDATE crm_meta SET sample_data_loaded = FALSE, updated_at = %s WHERE id = 1",
            (_now(),),
        )
    return {"ok": True}
