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
from datetime import datetime, timedelta, timezone

from core.postgres import get_connection, pg_execute, pg_fetchall, pg_fetchone
from crm import chatter_service, field_service, touch_count_service

logger = logging.getLogger(__name__)

DEAL_STAGES = ["lead", "qualified", "proposal", "negotiation", "won", "lost"]
CONTACT_STATUSES = ["active", "inactive", "archived"]
TASK_PRIORITIES = ["low", "medium", "high"]
COMPANY_STATUSES = ["active", "archived"]

# Soft-archive predicate for deals (issue #22). `deals.archived_at IS NULL` means the
# deal is live; an archived deal (archived_at set by archive_deal, or by merge_deals on
# the merged-away source) must disappear from EVERY list, board, rollup, count and
# aggregate together — a deal that vanishes from the Kanban but still inflates the
# dashboard's pipeline value is worse than no archive at all. Named so the sweep is
# greppable: every deal-reading query below carries one of these two forms, and the
# only deliberate exceptions are get_deal (fetch-by-id must still resolve an archived
# deal, so it can be shown/restored/merged) and the is-the-CRM-empty counts (an
# archived deal is still data).
# Public so crm/analytics_service.py imports them rather than re-typing the literal —
# a second copy is exactly how a sweep site gets missed when the definition changes.
LIVE_PREDICATE = "archived_at IS NULL"
LIVE_PREDICATE_D = "d.archived_at IS NULL"

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
        f"SELECT * FROM deals WHERE contact_id = %s AND {LIVE_PREDICATE} "
        "ORDER BY updated_at DESC",
        (contact_id,),
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
    so they simply unlink. Its polymorphic crm_chatter / crm_field_values rows are
    dropped explicitly (no FK), so a reused company SERIAL id can't inherit them.

    Runs in one transaction with a FOR UPDATE lock — the same discipline as
    delete_contact — serializing against chatter_service.add_note and
    field_service.set_field_values (both lock this row before inserting), so a note
    or custom-field value can't be written to a company this transaction is deleting.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM companies WHERE id = %s FOR UPDATE", (company_id,))
        if cur.fetchone() is None:
            return False
        # Company chatter arrived with issue #22; without this a deleted company's
        # notes would resurface on whatever company later reuses its SERIAL id.
        cur.execute(
            "DELETE FROM crm_chatter WHERE entity_type = 'company' AND entity_id = %s",
            (company_id,),
        )
        cur.execute(
            "DELETE FROM crm_field_values WHERE entity_type = 'company' AND entity_id = %s",
            (company_id,),
        )
        # NO crm_field_provenance cleanup here, unlike delete_contact — and that is
        # correct, not an oversight: provenance_service.VALID_ENTITY_TYPES is
        # {'deal', 'contact'} and its single INSERT is guarded by _check_entity_type,
        # so a company provenance row cannot be written in the first place. Pinned by
        # test_company_provenance_is_unwritable. If companies ever become a valid
        # provenance entity, this delete has to be added with it.
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
        f"""SELECT d.*, c.name AS contact_name
            FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
            WHERE d.company_id = %s AND {LIVE_PREDICATE_D} ORDER BY d.updated_at DESC""",
        (company_id,),
    )
    activity = pg_fetchall(
        f"""SELECT a.*, c.name AS contact_name, d.title AS deal_title
            FROM activity_log a
            LEFT JOIN contacts c ON a.contact_id = c.id
            LEFT JOIN deals d ON a.deal_id = d.id
            -- Only the DEAL side filters archived: an archived deal stops being one
            -- of the company's deals. The CONTACT side deliberately does not — that
            -- row is the history of talking to a person who still belongs to this
            -- company, and it already shows on the contact's own page. Filtering it
            -- here would make the two views disagree about the same interaction.
            WHERE a.contact_id IN (SELECT id FROM contacts WHERE company_id = %s)
               OR a.deal_id IN (SELECT id FROM deals WHERE company_id = %s
                                 AND {LIVE_PREDICATE})
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
    # custom_fields is embedded (issue #22 Q12a) so one read answers "tell me about
    # this deal" — previously the assistant needed a second crm_get_deal_fields call.
    return _embed_custom_fields([{**deal, "activity": activity}])[0]


def get_pipeline(stage: str | None = None) -> dict:
    # Single query (optional stage WHERE) so the two branches can't drift. Beyond the
    # contact-name join, the board payload carries `company_name` (mirrors get_deal) for
    # keyword search, and a derived `last_activity_at` (issue #21) = the most recent of the
    # deal's genuine activity signals: explicit activity_log rows (calls/emails logged via
    # POST /api/crm/activity — never written by edits/stage-moves) blended with un-archived
    # deal chatter notes. The UNION-ALL/GROUP BY yields one row per deal; the LEFT JOIN
    # leaves `last_at` NULL when a deal has neither → the client's "no activity" bucket.
    # Scale note: the subquery aggregates the whole activity_log + chatter before the join
    # (the stage WHERE can't push into it) — accepted at single-user v1 scale, where the
    # unpaginated all-deals board is the binding constraint, not this once-per-load aggregate.
    # If deal/activity volume ever grows, switch to a per-deal LATERAL MAX (indexes exist:
    # idx_activity_deal, idx_crm_chatter_entity) or a maintained last-activity column.
    where = f"WHERE {LIVE_PREDICATE_D}" + (" AND d.stage = %s" if stage else "")
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
        f"""SELECT stage, COUNT(*) AS count, COALESCE(SUM(value), 0) AS total_value
            FROM deals WHERE stage NOT IN ('won', 'lost') AND {LIVE_PREDICATE}
            GROUP BY stage"""
    )
    total_pipeline = sum(s["total_value"] for s in stage_summary)

    return {"deals": deals, "stage_summary": stage_summary, "total_pipeline_value": total_pipeline}


def list_deals(stage: str | None = None, contact_id: int | None = None, limit: int = 50) -> list[dict]:
    conditions = [LIVE_PREDICATE_D]
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


def _write_deal_update(deal_id: int, filtered: dict) -> bool:
    """Apply a validated column map to one deal in a single transaction.

    Every deal write funnels through here so the three things that must happen
    together with a stage change actually do (issue #22):

    1. **Stage history** — a ``deal_stage_events`` row is appended in the SAME
       transaction as the ``UPDATE``, so the log can never disagree with
       ``deals.stage``.
    2. **Stale lost_reason** — a deal leaving the 'lost' stage has its reason
       cleared. cake_os shipped this bug and fixed it later; porting the fixed
       behavior means a reopened deal can't carry "budget cut" into the timeline
       or a win/loss read.
    3. **Serialization** — the old stage is read under ``SELECT ... FOR UPDATE``,
       so two concurrent moves can't both log a transition from the same old
       stage (CLAUDE.md: a check-then-write spanning reads and updates is one
       transaction).

    Returns False when the deal does not exist. ``filtered`` must already be
    validated/clamped by the caller — this function writes what it is given, and must
    be non-empty (an empty map would build ``SET , updated_at = …``). No caller can
    reach that today; the guard is here so a future one can't either.
    """
    if not filtered:
        raise ValueError("_write_deal_update requires at least one column to set")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT stage FROM deals WHERE id = %s FOR UPDATE", (deal_id,))
        row = cur.fetchone()
        if row is None:
            return False
        old_stage = row[0]
        new_stage = filtered.get("stage", old_stage)
        if old_stage == "lost" and new_stage != "lost" and "lost_reason" not in filtered:
            filtered = {**filtered, "lost_reason": ""}
        # Closing a deal settles its win probability, whichever path closed it — the
        # Kanban drag, crm_update_deal_stage and the edit form all come through here.
        # This OVERRIDES a supplied probability on purpose: "probability" means chance
        # of winning, so it has exactly one correct value once the deal is decided, and
        # the edit form happily posts the old 30% alongside stage='won'. Only on the
        # TRANSITION though — editing probability on an already-closed deal stays the
        # caller's call.
        if new_stage != old_stage and new_stage in ("won", "lost"):
            filtered = {**filtered, "probability": 100 if new_stage == "won" else 0}
        set_clause = ", ".join(f"{k} = %s" for k in filtered)
        cur.execute(
            f"UPDATE deals SET {set_clause}, updated_at = %s WHERE id = %s",
            list(filtered.values()) + [_now(), deal_id],
        )
        if new_stage != old_stage:
            cur.execute(
                "INSERT INTO deal_stage_events (deal_id, old_stage, new_stage) "
                "VALUES (%s, %s, %s)",
                (deal_id, old_stage, new_stage),
            )
    return True


# Sortable deal columns for search_deals. An allowlist, not a passthrough: the column
# is interpolated into the ORDER BY, so anything outside this set is an injection
# vector. Sorting by a CUSTOM field key is deliberately out of scope for v1 (issue #22
# Q11) — it needs a join whose shape overlaps #21's filtering work.
_DEAL_SORTS = frozenset(
    {"updated_at", "created_at", "value", "expected_close_date", "title", "stage", "probability"}
)
MAX_DEAL_SEARCH_LIMIT = 100
MAX_CUSTOM_FIELD_FILTERS = 10


def _embed_custom_fields(rows: list[dict]) -> list[dict]:
    """Attach each deal's non-empty custom-field values as a ``custom_fields`` dict.

    One batched query for the whole result set (issue #22 Q12a) — the assistant used
    to need a second crm_get_deal_fields round-trip per deal to see them.
    """
    if not rows:
        return rows
    values = field_service.get_field_values_batch("deal", [r["id"] for r in rows])
    for row in rows:
        row["custom_fields"] = values.get(row["id"], {})
    return rows


def search_deals(
    search: str = "", stage: str | None = None, sort_by: str = "updated_at",
    sort_dir: str = "desc", custom_field_filters: dict | None = None, limit: int = 25,
) -> list[dict]:
    """Keyword + facet search over live deals, with custom-field values embedded.

    ``list_deals`` only ever filtered by stage/contact_id, so the assistant had no way
    to answer "which deals mention X". Keyword matches the deal's own title/notes plus
    the linked contact and company names — the three things a user names a deal by.

    ``custom_field_filters`` is a ``{field_key: value}`` map ANDed together, each an
    EXISTS on the EAV tables. Matching is case-insensitive EXACT, not substring: these
    fields are mostly dropdowns, where a substring match would silently match sibling
    options. The KEY is matched case-insensitively too — stored keys are slugified
    lowercase, and a model that echoes the display name ("Region") should still find
    the field rather than silently get zero rows.
    """
    try:
        limit = max(1, min(int(limit), MAX_DEAL_SEARCH_LIMIT))
    except (TypeError, ValueError):
        limit = 25
    sort_col = sort_by if sort_by in _DEAL_SORTS else "updated_at"
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

    conditions = [LIVE_PREDICATE_D]
    params: list = []
    if search:
        like = f"%{search}%"
        conditions.append(
            "(d.title ILIKE %s OR d.notes ILIKE %s OR c.name ILIKE %s OR co.name ILIKE %s)"
        )
        params.extend([like] * 4)
    if stage:
        conditions.append("d.stage = %s")
        params.append(stage)
    # Every other model-supplied bound in this function is clamped; the filter COUNT
    # is one too — an LLM could otherwise emit hundreds of keys and build a query with
    # hundreds of EXISTS subqueries. Extra keys are dropped, not an error: a truncated
    # filter still returns a superset, never wrong rows.
    for key, value in list((custom_field_filters or {}).items())[:MAX_CUSTOM_FIELD_FILTERS]:
        conditions.append(
            """EXISTS (SELECT 1 FROM crm_field_values v
                         JOIN crm_field_definitions fd ON fd.id = v.field_id
                        WHERE v.entity_type = 'deal' AND v.entity_id = d.id
                          AND fd.entity_type = 'deal'
                          AND lower(fd.field_key) = lower(%s)
                          AND lower(v.value) = lower(%s))"""
        )
        # normalize_value, not str(): booleans are stored '1'/'0', so a raw
        # str(True) -> 'True' would silently match nothing (the tool schema
        # advertises boolean filter values).
        params.extend([str(key), field_service.normalize_value(value)])
    params.append(limit)

    rows = pg_fetchall(
        f"""SELECT d.*, c.name AS contact_name, co.name AS company_name
            FROM deals d
            LEFT JOIN contacts c ON d.contact_id = c.id
            LEFT JOIN companies co ON d.company_id = co.id
            WHERE {' AND '.join(conditions)}
            ORDER BY d.{sort_col} {direction}, d.id {direction}
            LIMIT %s""",
        params,
    )
    return _embed_custom_fields(rows)


def update_deal(deal_id: int, **fields) -> dict | None:
    # lost_reason is deliberately NOT in `allowed`: mark_deal_lost is its single
    # writer, so a reason always arrives with the close (and its timeline note) and
    # can never be set on a deal that isn't lost.
    allowed = {"title", "stage", "value", "notes", "expected_close_date", "probability", "currency", "contact_id", "company_id"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if "stage" in filtered and filtered["stage"] not in DEAL_STAGES:
        return None
    if "probability" in filtered and filtered["probability"] is not None:
        filtered["probability"] = max(0, min(100, filtered["probability"]))
    if not filtered:
        return get_deal(deal_id)
    if not _write_deal_update(deal_id, filtered):
        return None
    return get_deal(deal_id)


def update_deal_stage(deal_id: int, stage: str) -> dict | None:
    if stage not in DEAL_STAGES:
        return None
    if not _write_deal_update(deal_id, {"stage": stage}):
        return None
    return get_deal(deal_id)


# ── Deal lifecycle: won / lost / archive / merge (issue #22) ──────────────────

MAX_LOST_REASON = 500


def mark_deal_won(deal_id: int) -> dict | None:
    """Close a deal as won: stage='won', probability=100.

    Any lost_reason from an earlier close is cleared by _write_deal_update.
    """
    if not _write_deal_update(deal_id, {"stage": "won", "probability": 100}):
        return None
    return get_deal(deal_id)


def mark_deal_lost(deal_id: int, lost_reason: str = "") -> dict | None:
    """Close a deal as lost: stage='lost', probability=0, reason recorded.

    The reason is stored on the deal (queryable, shown on the deal sheet) AND
    appended to the notes thread (visible where the user reads the deal's story).
    The note is best-effort and lands after the close commits — a chatter failure
    must never leave the deal un-closed.
    """
    reason = (lost_reason or "").strip()[:MAX_LOST_REASON]
    if not _write_deal_update(
        deal_id, {"stage": "lost", "probability": 0, "lost_reason": reason}
    ):
        return None
    if reason:
        try:
            chatter_service.add_note("deal", deal_id, f"Deal lost — {reason}")
        except Exception:
            logger.warning("lost-reason note failed for deal %s", deal_id, exc_info=True)
    return get_deal(deal_id)


def archive_deal(deal_id: int, archived: bool = True) -> dict | None:
    """Soft-archive (or restore) a deal. Nothing is deleted — archived_at is set,
    and every list/board/rollup/aggregate stops counting the deal (LIVE_PREDICATE).

    Idempotent: archiving an already-archived deal keeps the original timestamp, so
    "when was this archived" survives a repeat call.
    """
    now = _now()
    # COALESCE keeps the FIRST archive timestamp on a repeat call; restore just NULLs it.
    archived_at_sql = "COALESCE(archived_at, %s)" if archived else "NULL"
    params = (now, now, deal_id) if archived else (now, deal_id)
    row = pg_fetchone(
        f"UPDATE deals SET archived_at = {archived_at_sql}, updated_at = %s "
        "WHERE id = %s RETURNING id",
        params,
    )
    if not row:
        return None
    return get_deal(deal_id)


def merge_deals(target_deal_id: int, source_deal_id: int) -> dict:
    """Fold ``source`` into ``target`` and archive the source. Raises ValueError on
    a self-merge or a missing deal.

    Restorable by construction — the source is soft-archived, never deleted, and its
    own notes/history stay on it, so a wrong merge can be undone by un-archiving
    (the copied rows on the target are then the only cleanup).

    What moves vs. what is copied:
      * ``activity_log`` + ``tasks`` are **repointed** — a dated interaction and an
        open follow-up belong to exactly one deal, and leaving them on an archived
        deal would hide them from the timeline and the task list.
      * notes are **copied** (annotated with the source id), because the source keeps
        its own thread for the restore case.
      * custom fields are **gap-filled** — the target's own values always win; the
        source only fills keys the target left blank. Merging must never overwrite
        data on the deal being kept.

    Deliberately NOT touched: the target's standard columns (title/value/stage/…).
    A merge is a consolidation of *history*, not a silent edit of the surviving deal.
    """
    if target_deal_id == source_deal_id:
        raise ValueError("Cannot merge a deal into itself")
    now = _now()
    with get_connection() as conn:
        cur = conn.cursor()
        # Both rows locked in ascending id order (ORDER BY ... FOR UPDATE locks in the
        # scan's output order), so two concurrent merges over the same pair queue up
        # instead of deadlocking.
        cur.execute(
            "SELECT id, title, archived_at FROM deals WHERE id IN (%s, %s) "
            "ORDER BY id FOR UPDATE",
            (target_deal_id, source_deal_id),
        )
        rows = cur.fetchall()
        titles = {r[0]: r[1] for r in rows}
        archived = {r[0] for r in rows if r[2] is not None}
        missing = sorted({target_deal_id, source_deal_id} - set(titles))
        if missing:
            raise ValueError(f"Deal not found: {', '.join(str(i) for i in missing)}")
        # Merging into an archived target would quietly move the source's whole
        # history onto a deal that every list, board and report already hides — the
        # user asks to consolidate two deals and watches both disappear. Refuse and
        # say so; restoring first is one call.
        if archived:
            raise ValueError(
                "Cannot merge: deal "
                + ", ".join(f"#{i}" for i in sorted(archived))
                + " is archived — restore it first"
            )

        cur.execute(
            "UPDATE activity_log SET deal_id = %s WHERE deal_id = %s",
            (target_deal_id, source_deal_id),
        )
        cur.execute(
            "UPDATE tasks SET deal_id = %s, updated_at = %s WHERE deal_id = %s",
            (target_deal_id, now, source_deal_id),
        )
        # left(...) keeps a copied note inside chatter_service.MAX_MESSAGE_LEN so the
        # annotated copy stays editable in the UI (the validator rejects longer text).
        cur.execute(
            """INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, archived)
               SELECT 'deal', %s, left(%s || message, %s), created_at, archived
                 FROM crm_chatter WHERE entity_type = 'deal' AND entity_id = %s""",
            (target_deal_id, f"[Merged from deal #{source_deal_id}] ",
             chatter_service.MAX_MESSAGE_LEN, source_deal_id),
        )
        # DO UPDATE ... WHERE, not DO NOTHING: clearing a custom field UPSERTs
        # value='' rather than deleting the row (field_service.set_field_values), so
        # "the target left it blank" usually means an EXISTING row holding ''. DO
        # NOTHING would skip exactly the case this is meant to fill. The WHERE keeps
        # the promise intact in the other direction — a target value that is actually
        # set is never overwritten.
        cur.execute(
            """INSERT INTO crm_field_values
                   (entity_type, entity_id, field_id, value, updated_at, updated_by_email)
               SELECT 'deal', %s, s.field_id, s.value, s.updated_at, s.updated_by_email
                 FROM crm_field_values s
                WHERE s.entity_type = 'deal' AND s.entity_id = %s
                  AND s.value IS NOT NULL AND s.value <> ''
               ON CONFLICT (entity_type, entity_id, field_id)
               DO UPDATE SET value = EXCLUDED.value,
                             updated_at = EXCLUDED.updated_at,
                             updated_by_email = EXCLUDED.updated_by_email
                WHERE crm_field_values.value IS NULL OR crm_field_values.value = ''""",
            (target_deal_id, source_deal_id),
        )
        cur.execute(
            "INSERT INTO crm_chatter (entity_type, entity_id, message, created_at) "
            "VALUES ('deal', %s, %s, %s)",
            (target_deal_id,
             f'Merged deal #{source_deal_id} ("{titles[source_deal_id]}") into this deal.',
             now),
        )
        cur.execute(
            "UPDATE deals SET archived_at = COALESCE(archived_at, %s), updated_at = %s "
            "WHERE id = %s",
            (now, now, source_deal_id),
        )
    # The target just absorbed the source's evidence, so its touch count is stale —
    # force_write because the merged-in notes can move the watermark either way.
    touch_count_service.schedule_recompute(target_deal_id, force_write=True)
    return get_deal(target_deal_id)


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
        f"""SELECT stage, COUNT(*) AS count, COALESCE(SUM(value), 0) AS total_value
            FROM deals WHERE {LIVE_PREDICATE} GROUP BY stage"""
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
        f"""SELECT d.*, c.name AS contact_name
            FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
            WHERE d.stage NOT IN ('won', 'lost') AND {LIVE_PREDICATE_D}
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


# Deal-age buckets for the aging distribution (#20, D1: read-time only, from
# created_at). max_days=None means open-ended. All four buckets are always
# returned (zero-filled) so the UI renders a stable frame. Boundaries and labels
# are aligned exactly — day 90 is "31-90", day 91 is "91+" (no off-by-one).
AGE_BUCKETS = ((0, 7, "0-7"), (8, 30, "8-30"), (31, 90, "31-90"), (91, None, "91+"))

# Open-deal predicate (DEAL_STAGES sentinels; no status column / CHECK exists).
# Public for the same single-source-of-truth reason as LIVE_PREDICATE above.
OPEN_PREDICATE = "stage NOT IN ('won', 'lost')"
OPEN_PREDICATE_D = "d.stage NOT IN ('won', 'lost')"

# "When was this deal last touched" — the newest of: any edit (updated_at), any logged
# activity, any un-archived note. Requires the deals table aliased as `d`.
#
# Shared by get_analytics (below) and analytics_service.get_stale_deals (issue #22), so
# the dashboard's stale count and the assistant's stale-deal list can never disagree
# about what "touched" means. Change it here and both move together.
LAST_TOUCH_SQL = """GREATEST(
    d.updated_at,
    COALESCE((SELECT MAX(a.created_at) FROM activity_log a
               WHERE a.deal_id = d.id), d.updated_at),
    COALESCE((SELECT MAX(ch.created_at) FROM crm_chatter ch
               WHERE ch.entity_type = 'deal' AND ch.entity_id = d.id
                 AND ch.archived = 0), d.updated_at)
)"""

# The assistant tool trims activity_by_type to the top-N by count: the activity
# vocabulary is free text (no CHECK), so an unbounded tail of one-off kinds would
# defeat summarize_analytics's "lean payload" goal (D5). The page shows the full list.
_TOOL_MAX_ACTIVITY_TYPES = 10


def _as_float(x, default: float = 0.0) -> float:
    """Coerce a SQL numeric to float. psycopg2 returns Decimal for EXTRACT/AVG
    results and the pg adapter leaves those untouched (it only ISO-stringifies
    date/datetime), so Python arithmetic must never mix Decimal with float."""
    return float(x) if x is not None else default


# ── Analytics: pure shapers (no DB access — the hermetic-test surface) ─────────

def _shape_win_loss(row: dict | None) -> dict:
    """Win/loss + deal-size scalars from the single aggregate row. Guards: a None
    row (empty deals table / mock), NULL AVGs, and won+lost == 0 → win_rate_pct is
    None ("no closed deals yet", distinct from a genuine 0% record; UI shows "—")."""
    row = row or {}
    won = int(row.get("won") or 0)
    lost = int(row.get("lost") or 0)
    open_count = int(row.get("open_count") or 0)
    total_closed = won + lost
    awd = row.get("avg_won_deal_size")
    aod = row.get("avg_open_deal_size")
    adtc = row.get("avg_days_to_close")
    return {
        "deals_won": won,
        "deals_lost": lost,
        "open_deals": open_count,
        "win_rate_pct": round(won / total_closed * 100, 1) if total_closed else None,
        # None (not 0.0) when there is no qualifying deal — consistent with
        # win_rate_pct / avg_days_to_close so the UI shows "—" ("no data yet")
        # rather than a misleading "$0".
        "avg_won_deal_size": round(_as_float(awd), 2) if awd is not None else None,
        "avg_open_deal_size": round(_as_float(aod), 2) if aod is not None else None,
        # updated_at - created_at is an approximation of close time (updated_at moves
        # on any edit; there is no closed_at column) — same as the cake_os blueprint.
        "avg_days_to_close": round(_as_float(adtc), 1) if adtc is not None else None,
        "total_pipeline_value": round(_as_float(row.get("total_pipeline_value")), 2),
    }


def _bucket_deal_ages(open_rows: list[dict]) -> list[dict]:
    """Bucket open deals by whole-day age. Negative ages (clock skew) clamp to 0.
    Always returns all four AGE_BUCKETS in order, zero-filled."""
    counts = {label: 0 for _, _, label in AGE_BUCKETS}
    for row in open_rows:
        days = max(0, int(_as_float(row.get("age_days"))))  # int() floors: 7.9d → "0-7"
        for lo, hi, label in AGE_BUCKETS:
            if days >= lo and (hi is None or days <= hi):
                counts[label] += 1
                break
    return [
        {"label": label, "min_days": lo, "max_days": hi, "count": counts[label]}
        for lo, hi, label in AGE_BUCKETS
    ]


def _stale_open_deals(
    open_rows: list[dict], stale_days: int, limit: int
) -> tuple[list[dict], int]:
    """(top-N stalest open deals, total stale count). Stale = days_since_touch >=
    stale_days. Sorted stalest-first (days_since_touch desc, id asc tiebreak). The
    count is computed BEFORE the limit so truncation never undercounts."""
    stale = []
    for row in open_rows:
        days_since = int(_as_float(row.get("days_since_touch")))
        if days_since >= stale_days:
            stale.append({
                "id": row.get("id"),
                "title": row.get("title"),
                "value": _as_float(row.get("value")),
                "stage": row.get("stage"),
                "contact_name": row.get("contact_name"),
                "company_name": row.get("company_name"),
                "days_since_touch": days_since,
                "age_days": max(0, int(_as_float(row.get("age_days")))),
            })
    stale.sort(key=lambda d: (-d["days_since_touch"], d["id"]))
    return stale[:limit], len(stale)


def _fill_activity_daily(rows: list[dict], days: int, today=None) -> list[dict]:
    """Zero-fill the daily activity series over the trailing `days` UTC days
    (ending today, inclusive, ascending). Row 'day' may be a date (real SQL, ISO-
    stringified by the pg adapter) or a string (mocks): both normalize via
    str(day)[:10]. `today` (a date) is injectable for deterministic tests."""
    if today is None:
        today = datetime.now(timezone.utc).date()
    counts = {str(r["day"])[:10]: int(r.get("count") or 0) for r in rows}
    series = []
    for i in range(days):
        iso = (today - timedelta(days=days - 1 - i)).isoformat()
        series.append({"day": iso, "count": counts.get(iso, 0)})
    return series


def _shape_activity_types(rows: list[dict]) -> list[dict]:
    """Normalize the free-text activity vocabulary (no CHECK constraint): strip +
    lowercase, blank → 'other', merge post-normalization duplicates, order by count
    desc then label asc."""
    merged: dict[str, int] = {}
    for r in rows:
        kind = (r.get("activity") or "").strip().lower() or "other"
        merged[kind] = merged.get(kind, 0) + int(r.get("count") or 0)
    return [
        {"activity": k, "count": v}
        for k, v in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


# ── Analytics: query + assemble ───────────────────────────────────────────────

def get_analytics(days: int = 30, stale_days: int = 14, stale_limit: int = 8) -> dict:
    """Keyless SQL analytics for the enriched dashboard (issue #20): win/loss,
    activity volume, and read-time deal aging. Pure aggregation — no AI, no gate;
    renders a sensible zero state on an empty CRM.

    Split deliberately: this runs four queries and delegates every ratio, guard,
    bucket, sort, and Decimal→float coercion to the pure module-level shapers
    above, so the hermetic test suite (which mocks the pg helpers) exercises all
    the logic without a database.

    NOTE: avg_days_to_close uses updated_at - created_at on won deals — an
    approximation (updated_at moves on any edit; there is no closed_at column),
    same as the cake_os blueprint. Documented, not solved.
    """
    days = max(7, min(365, int(days)))
    stale_days = max(1, min(365, int(stale_days)))
    stale_limit = max(1, min(50, int(stale_limit)))

    # One shared UTC calendar-day window [start_dt, end_dt) for BOTH activity
    # queries, aligned EXACTLY with the frame _fill_activity_daily renders. Both
    # bounds are shared and end_dt is start-of-tomorrow-UTC (exclusive), so a row
    # landing on today+1 (a request straddling UTC midnight, or DB-clock skew) is
    # excluded from BOTH queries rather than counted in by_type but dropped from
    # the daily frame — making activity.total == sum(by_type) true by construction.
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)
    end_date = today + timedelta(days=1)
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc)

    stats_row = pg_fetchone(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE stage = 'won')                       AS won,
            COUNT(*) FILTER (WHERE stage = 'lost')                      AS lost,
            COUNT(*) FILTER (WHERE {OPEN_PREDICATE})                   AS open_count,
            COALESCE(SUM(value) FILTER (WHERE {OPEN_PREDICATE}), 0)    AS total_pipeline_value,
            AVG(value) FILTER (WHERE stage = 'won' AND value > 0)       AS avg_won_deal_size,
            AVG(value) FILTER (WHERE {OPEN_PREDICATE} AND value > 0)   AS avg_open_deal_size,
            AVG(EXTRACT(EPOCH FROM (updated_at - created_at)) / 86400.0)
                FILTER (WHERE stage = 'won')                            AS avg_days_to_close
        FROM deals
        WHERE {LIVE_PREDICATE}
        """
    )

    open_rows = pg_fetchall(
        f"""
        SELECT d.id, d.title, d.value, d.stage,
               c.name  AS contact_name,
               co.name AS company_name,
               EXTRACT(EPOCH FROM (now() - d.created_at)) / 86400.0 AS age_days,
               EXTRACT(EPOCH FROM (now() - {LAST_TOUCH_SQL})) / 86400.0 AS days_since_touch
        FROM deals d
        LEFT JOIN contacts  c  ON d.contact_id = c.id
        LEFT JOIN companies co ON d.company_id = co.id
        WHERE {OPEN_PREDICATE_D} AND {LIVE_PREDICATE_D}
        """
    )

    daily_rows = pg_fetchall(
        """
        SELECT (created_at AT TIME ZONE 'UTC')::date AS day, COUNT(*) AS count
        FROM activity_log
        WHERE created_at >= %s AND created_at < %s
        GROUP BY 1
        ORDER BY 1
        """,
        (start_dt, end_dt),
    )

    type_rows = pg_fetchall(
        """
        SELECT activity, COUNT(*) AS count
        FROM activity_log
        WHERE created_at >= %s AND created_at < %s
        GROUP BY activity
        ORDER BY count DESC, activity ASC
        """,
        (start_dt, end_dt),
    )

    daily = _fill_activity_daily(daily_rows, days, today=today)
    stale_deals, stale_count = _stale_open_deals(open_rows, stale_days, stale_limit)
    return {
        "window_days": days,
        "stale_days": stale_days,
        "win_loss": _shape_win_loss(stats_row),
        "activity": {
            "daily": daily,
            "by_type": _shape_activity_types(type_rows),
            "total": sum(d["count"] for d in daily),
        },
        "aging": {
            "buckets": _bucket_deal_ages(open_rows),
            "stale_count": stale_count,
            "stale_deals": stale_deals,
        },
    }


def summarize_analytics(analytics: dict) -> dict:
    """Lean assistant-tool shape (#20, D5): summarized scalars + compact lists only,
    never the raw daily series (window_days rows of noise to the model)."""
    wl = analytics["win_loss"]
    act = analytics["activity"]
    aging = analytics["aging"]
    return {
        "window_days": analytics["window_days"],
        "stale_days": analytics["stale_days"],
        "win_rate_pct": wl["win_rate_pct"],
        "deals_won": wl["deals_won"],
        "deals_lost": wl["deals_lost"],
        "open_deals": wl["open_deals"],
        "avg_won_deal_size": wl["avg_won_deal_size"],
        "avg_days_to_close": wl["avg_days_to_close"],
        "total_pipeline_value": wl["total_pipeline_value"],
        "activity_total": act["total"],
        "activity_by_type": act["by_type"][:_TOOL_MAX_ACTIVITY_TYPES],
        "aging_buckets": {b["label"]: b["count"] for b in aging["buckets"]},
        "stale_count": aging["stale_count"],
        "stale_deals": [
            {"id": d["id"], "title": d["title"], "days_since_touch": d["days_since_touch"]}
            for d in aging["stale_deals"][:5]
        ],
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
    # deal_stage_events trails everything: it is the one CRM table with a real FK to
    # deals, so Postgres REQUIRES it in the same TRUNCATE statement (truncating a
    # referenced table alone errors out). Its only writer, _write_deal_update, locks
    # the deals row first, so a later position can't invert against it.
    if include_definitions:
        cur.execute(
            "TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
            "crm_field_definitions, crm_field_values, crm_field_provenance, "
            "deal_stage_events RESTART IDENTITY"
        )
    else:
        cur.execute(
            "TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
            "crm_field_values, crm_field_provenance, deal_stage_events RESTART IDENTITY"
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
