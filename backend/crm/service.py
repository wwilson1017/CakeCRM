"""
CakeCRM — CRM CRUD + analytics (contacts, deals, todos, activity, dashboard).

Ported from chatty's SQLite ``crm_lite/client.py`` and translated to Postgres:
``%s`` placeholders, ``INSERT ... RETURNING id`` then re-select to hydrate,
``ILIKE`` for the free-text search, ``COUNT(*) AS cnt`` scalars, a Python-side
``_now()`` on UPDATEs, and multi-statement writes wrapped in one
``get_connection()`` transaction. Query shapes follow the matching
``cake_os/backend/apps/crm`` services so later feature ports diff cleanly.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import psycopg2

from core.localtime import today_local
from core.postgres import (
    get_connection,
    pg_execute,
    pg_fetchall,
    pg_fetchone,
    row_to_dict,
)
from crm import (
    chatter_service,
    field_service,
    gtd_common,
    scoring_service,
    touch_count_service,
)

# One resolver for a person's label, so the Weekly Touches rep rows and the Team
# settings list can never spell the same user differently. `users.service` imports only
# `core.postgres`, so this adds no cycle — the router that would drag `core.auth` in
# lives in `users.router` and is not imported here.
from users import service as users_service

logger = logging.getLogger(__name__)

DEAL_STAGES = ["lead", "qualified", "proposal", "negotiation", "won", "lost"]
# The two terminal stages, as a Python tuple the tool layer can test membership against
# (#99) — OPEN_PREDICATE below is the SQL statement of the same fact, and a test pins the
# two in agreement. OPEN_STAGES is the complement on purpose: a stage added to
# DEAL_STAGES later is open unless it is declared terminal here. scoring_service keeps its
# own _TERMINAL_* copies deliberately (importing service there is a circular import).
CLOSED_STAGES = ("won", "lost")
OPEN_STAGES = tuple(s for s in DEAL_STAGES if s not in CLOSED_STAGES)
CONTACT_STATUSES = ["active", "inactive", "archived"]
TODO_PRIORITIES = ["low", "medium", "high"]
COMPANY_STATUSES = ["active", "archived"]

# Deal temperature (issue #125) — the rep's own read on a deal, and an input to #18's lead
# score. A tuple rather than a list because it is membership-tested, published verbatim as
# the agent tools' schema `enum`, and mirrored by the migration's CHECK constraint; a test
# pins those three in agreement.
#
# NULL is a fourth, unnamed state and is NOT in here: it means nobody has triaged the deal,
# the same call owner_id and lead_score make. It is deliberately not spelled 'cold' — Cold
# is a judgment someone made, and only a judgment moves the score.
DEAL_TEMPERATURES = ("hot", "warm", "cold")


def normalize_deal_temperature(value) -> str | None:
    """Coerce a caller-supplied temperature to a stored value, or raise.

    Returns None for "clear it" — None itself, or the empty string a `<select>`'s blank
    option submits. Otherwise a trimmed, lower-cased member of ``DEAL_TEMPERATURES``.

    RAISES ``ValueError`` on anything else rather than silently dropping or coercing it,
    for the reason ``_write_deal_update``'s undeclared-column guard exists: the callers are
    ``PUT /api/crm/deals/{id}`` (whose body reaches here as-is) and ``crm_update_deal``,
    which forwards the model's raw kwargs with nothing validating them. Both turn a
    ValueError into a message the caller can act on — a 400, and a tool error naming the
    problem — where letting a bad value through would hit the migration's CHECK as an
    opaque 500, and swallowing it would report success on a write that never happened.

    A non-string is rejected here too, deliberately. `deal_temperature=3` from a model
    would otherwise reach `.strip()` and raise AttributeError, which the assistant registry
    reports as the generic "the tool failed, please try again" — true but useless, where
    this says which values exist.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"deal_temperature must be one of {', '.join(DEAL_TEMPERATURES)} (or null to "
            f"clear it), not {type(value).__name__}"
        )
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if cleaned not in DEAL_TEMPERATURES:
        raise ValueError(
            f"Invalid deal_temperature '{value}' — expected one of "
            f"{', '.join(DEAL_TEMPERATURES)}, or null to clear it"
        )
    return cleaned

# Soft-archive predicate for deals (issue #22). `deals.archived_at IS NULL` means the
# deal is live; an archived deal (archived_at set by archive_deal, or by merge_deals on
# the merged-away source) must disappear from EVERY list, board, rollup, count and
# aggregate together — a deal that vanishes from the Kanban but still inflates the
# dashboard's pipeline value is worse than no archive at all. Named so the sweep is
# greppable: every deal-reading query below carries one of these two forms, and the
# deliberate exceptions are get_deal (fetch-by-id must still resolve an archived deal,
# so it can be shown/restored/merged), the is-the-CRM-empty counts (an archived deal is
# still data), and the two OPT-IN holes that make an archive recoverable —
# search_deals(include_archived=True) and, since issue #83, get_pipeline(
# include_archived=True). Both default to False, and get_pipeline's flag opens its deals
# query only: stage_summary keeps the sweep unconditionally, because an archived deal may
# be findable but must never be money.
# Public so crm/analytics_service.py imports them rather than re-typing the literal —
# a second copy is exactly how a sweep site gets missed when the definition changes.
LIVE_PREDICATE = "archived_at IS NULL"
LIVE_PREDICATE_D = "d.archived_at IS NULL"

# A todo belongs to a live deal, or to no deal at all. Archiving is the user's "stop
# nagging me about this" gesture and the heartbeat reads the todo surfaces, so EVERY
# todo reader applies this rule — the dashboard's overdue/pending counts and the
# contact detail page use this constant; list_todos uses the equivalent aliased form
# (`t.deal_id IS NULL OR d.archived_at IS NULL`) since it already joins deals. Any new
# todo reader must carry one of the two. Standalone todos (deal_id NULL) are unaffected.
LIVE_TODO_PREDICATE = (
    "(todos.deal_id IS NULL OR EXISTS (SELECT 1 FROM deals ld "
    "WHERE ld.id = todos.deal_id AND ld.archived_at IS NULL))"
)

# GTD's `dropped` status (#70) is a soft delete: the row is NOT completed, but it is
# not open work either. Normal mode renders two buckets from `completed`, so without
# this a dropped todo would reappear as a pending todo the moment the user switches
# back — and the heartbeat would nag about it. Swept exactly like LIVE_TODO_PREDICATE
# above: every "open todo" query carries one of these two forms. Queries that count
# ALL todos (the is-the-CRM-empty checks) deliberately do NOT — a dropped row is
# still data.
NOT_DROPPED_TODO = "todos.status != 'dropped'"
NOT_DROPPED_TODO_T = "t.status != 'dropped'"

# Contact list ORDER BY fragments (allowlisted — the param is NEVER interpolated). Every
# fragment ends with `ct.id DESC` so limit/offset pagination is deterministic (no dupes/
# skips on tied sort keys). The lead_score fragment is `DESC NULLS LAST` so unscored rows
# sink rather than float to the top (Postgres DESC defaults to NULLS FIRST). Added by #18.
# ct-qualified because both consumers join companies (issue #35: the LINK is
# authoritative for display), which shares column names with contacts; "company" sorts
# by the EFFECTIVE display name — co.name first, legacy free text for unlinked
# contacts — so the order matches what the list actually renders.
_CONTACT_SORTS = {
    "updated_at": "ct.updated_at DESC, ct.id DESC",
    "created_at": "ct.created_at DESC, ct.id DESC",
    # A name sort means A→Z — list_companies says so in its own comment, and it is what
    # the `?sort=name` REST parameter promises. These two were DESC and answered Z→A;
    # unreachable from the UI and from the assistant (crm_list_contacts exposes no sort),
    # but wrong for anyone reading the documented parameter. Fixed with #77.
    "name": "ct.name ASC, ct.id ASC",
    "company": "COALESCE(co.name, ct.company) ASC, ct.id ASC",
    "lead_score": "ct.lead_score DESC NULLS LAST, ct.updated_at DESC, ct.id DESC",
    # The frontend's full-corpus assembly key (#77). It is the only TOTAL, IMMUTABLE,
    # APPEND-ONLY order here, which is what a keyset sweep needs: a row inserted while the
    # sweep is walking sorts PAST the cursor instead of displacing rows behind it, and an
    # updated row cannot move at all. Presentation order is chosen client-side once the
    # whole set has landed.
    "id": "ct.id ASC",
}


def _contact_order_by(sort: str) -> str:
    return _CONTACT_SORTS.get(sort, _CONTACT_SORTS["updated_at"])


# Todo ORDER BY fragments — allow-listed, never interpolated from caller input.
# "due" is the historical order plus an id tie-breaker, so a LIMIT window is deterministic
# where before it was arbitrary among ties. "id" is the immutable assembly key (#77 — see
# _CONTACT_SORTS["id"]); it is its own tie-breaker.
_TODO_SORTS = {
    "due": "t.completed ASC, t.due_date ASC, t.id ASC",
    "id": "t.id ASC",
}


def _count_or_none(after_id: int | None, sql: str, params: list) -> int | None:
    """Total matching rows, or None on a CURSOR page (see list_contacts for why)."""
    if after_id is not None:
        return None
    row = pg_fetchone(sql, params)
    return row["cnt"] if row else 0


def _check_assembly_cursor(after_id: int | None, sort: str, offset: int = 0) -> None:
    """Refuse a keyset cursor against any order but the immutable id one (#77).

    ``after_id`` means "the rows after this one IN THE CURRENT ORDER". Under `updated_at`
    or `name` that sentence is not even well defined — the column is not unique and not
    stable — so the window would silently skip and repeat rows. Failing loudly beats
    paginating wrong, and beats silently ignoring the parameter (which looks identical to
    a client bug that re-reads page one forever).

    A cursor combined with a non-zero OFFSET is refused for the same reason: the two are
    competing ways to say where the window starts, and applying both skips exactly
    `offset` eligible rows with no error.
    """
    if after_id is None:
        return
    if sort != "id":
        raise ValueError("after_id is only valid with sort='id'")
    if offset:
        raise ValueError("after_id cannot be combined with a non-zero offset")

# The six ASCII whitespace bytes (space, tab, LF, CR, FF, VT). Company names are
# trimmed with THIS set (not Python's Unicode-aware str.strip()) so the value the
# service stores normalizes identically to the companies migration's backfill +
# unique index, which use btrim(name, E' \t\n\r\f\x0b') — a fixed byte set, so both
# sides agree regardless of the database's libc/locale.
_WS = " \t\n\r\f\v"

# ── Owner filters (issue #190) ────────────────────────────────────────────────
# Every owner-filterable read takes ONE ``owner_id`` argument with three legal
# values: an int ("this person's records"), ``None`` ("everyone" — the default that
# keeps an install which never assigns owners behaving exactly as before), or this
# sentinel ("nobody's — the unassigned pile").
#
# A sentinel rather than a second boolean parameter, deliberately: the shared WHERE
# builders exist so a filter can never reach a page query without also reaching its
# COUNT, and a second parameter is a second thing to forget at one of the two call
# sites. Riding the argument that is already threaded everywhere keeps that property.
# The REST routers type ``owner_id`` as ``int | None`` through FastAPI, so a client
# string is a 422 long before it gets here — only the assistant's tool layer, which
# resolves the model's ``owner`` word, can produce the sentinel.
UNASSIGNED = "unassigned"


def owner_condition(column: str, owner_id: int | str | None, params: list) -> str | None:
    """SQL for an owner filter on ``column``, or None when there is no filter.

    Appends the bind parameter to ``params`` when one is needed, so the caller adds
    the returned condition at the same point it would have appended the parameter and
    the two stay in lockstep. Compared with ``==`` rather than ``is``: the sentinel
    travels as a plain string and an equal value built elsewhere must behave the same.
    """
    if owner_id is None:
        return None
    if owner_id == UNASSIGNED:
        return f"{column} IS NULL"
    params.append(owner_id)
    return f"{column} = %s"

# SQL fragment for whitespace-tolerant boundary matching against the comma-separated
# tags column. Strips whitespace adjacent to commas so the filter survives free-form
# input like "PT, ET, MT". Valid Postgres (|| concat + REPLACE).
_TAGS_NORMALIZED_SQL = "(',' || REPLACE(REPLACE(tags, ', ', ','), ' ,', ',') || ',')"

# Per-contact last touch (#77), for the Contacts list's "Last contact" column and facet.
#
# Contacts have no last_contacted_at column, so recency is DERIVED from the same two signals
# analytics_service.get_contact_staleness reads — the contact's own activity_log rows and its
# un-archived chatter, minus provenance housekeeping notes — and exposed under the same alias.
# One definition, so the list and the assistant's staleness tool cannot disagree about what
# counts as talking to someone. GREATEST ignores NULLs and returns NULL only when every
# argument is NULL, so a contact with notes but no logged activity still gets a real date and
# one with neither correctly comes out NULL = never contacted.
#
# A LATERAL, deliberately, and NOT get_pipeline's grouped subquery: that one aggregates the
# whole activity/chatter tables once per query, which it can afford because it runs once for
# an unpaginated board. This runs per page of a corpus sweep, so it must touch only the rows
# the page returns — which it does, index-driven, via idx_activity_contact and
# idx_crm_chatter_entity.
#
# Carries no placeholder: the housekeeping exclusion is a rendered, `%`-free predicate (#239).
_CONTACT_LAST_TOUCH_JOIN = f"""
    LEFT JOIN LATERAL (
        SELECT GREATEST(
            (SELECT MAX(a.created_at) FROM activity_log a WHERE a.contact_id = ct.id),
            (SELECT MAX(ch.created_at) FROM crm_chatter ch
              WHERE ch.entity_type = 'contact' AND ch.entity_id = ct.id
                AND ch.archived = 0
                AND {scoring_service.not_housekeeping_sql('ch.message')})
        ) AS last_at
    ) lt ON TRUE
"""

# The SELECT list every contact read that carries the derived touch shares.
_CONTACT_LIST_SELECT = "ct.*, co.name AS company_name, lt.last_at AS last_contact_at"

_CONTACT_JOINS = f"LEFT JOIN companies co ON ct.company_id = co.id {_CONTACT_LAST_TOUCH_JOIN}"


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
    owner_id: int | None = None,
) -> dict:
    if status == "archived":
        # #239: archiving records who and why, and a record born archived has no one to
        # ask — create it, then archive it with a reason.
        raise ValueError(
            "A contact cannot be created already archived — create it, then archive it with a reason."
        )
    if status not in CONTACT_STATUSES:
        status = "active"  # unknown status would hide the contact from every status tab
    # Ongoing-ingestion coherence (issue #35): with no explicit link, resolve the
    # free-text company against the companies table, auto-creating it when new —
    # the same rule the one-shot backfill applied, now at write time. This is the
    # choke point every ingestion path funnels through (CSV import, smart import,
    # the REST route, crm_create_contact), so companies populate no matter how the
    # contact arrived. An explicit company_id always wins and skips the lookup:
    # bulk importers pass a pre-resolved id here, which is what keeps a 5000-row
    # import at 2 resolution queries instead of 5000.
    #
    # Note the deliberate asymmetry with update_contact: there, an explicit
    # company_id=None means "unlink" and suppresses resolution. On create there is
    # no link to remove, and the REST create route passes company_id=None whether
    # or not the client sent it (model_dump() without exclude_unset), so
    # absent-vs-null is not even expressible here — resolving on None is the only
    # rule that satisfies "ingestion always links". The eventual freetext↔link
    # combobox merge is where this asymmetry goes away.
    if company_id is None and company and company.strip(_WS):
        company_id = resolve_or_create_company_ids([company]).get(company)
    # company_id is appended last so the existing INSERT-param assertions (which
    # check the leading columns) stay valid; a bad FK raises ForeignKeyViolation
    # which the router maps to 400.
    row = pg_fetchone(
        """INSERT INTO contacts (name, email, phone, company, title, source, status, tags, notes, company_id, owner_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name, email, phone, company, title, source, status, _normalize_tags(tags), notes,
         company_id, owner_id),
    )
    scoring_service.score_on_event(contact_ids=(row["id"],))  # #18: seed lead_score (never raises)
    return get_contact(row["id"])


def get_contact(contact_id: int) -> dict | None:
    # Joins companies (mirroring get_deal) so company_name is present on EVERY
    # contact payload — including the dicts create_contact/update_contact return.
    # Without it, a contact linked by id with no legacy text would come back from
    # a write looking company-less to an API or agent-tool caller (issue #35).
    return pg_fetchone(
        """SELECT ct.*, co.name AS company_name
           FROM contacts ct LEFT JOIN companies co ON ct.company_id = co.id
           WHERE ct.id = %s""",
        (contact_id,),
    )


def _contact_search_where(
    query: str, status: str | None, tags: str | None, owner_id: int | str | None = None
) -> tuple[str, list]:
    """Build the shared WHERE clause + params for contact free-text search.

    Assumes the caller's FROM is ``contacts ct LEFT JOIN companies co ON
    ct.company_id = co.id`` — contact columns are ct-qualified because companies
    shares several of their names (id, name, status, notes, created_at,
    updated_at), which would otherwise be ambiguous. ``tags`` stays unqualified
    inside _TAGS_NORMALIZED_SQL: companies has no tags column.

    ``co.name`` is part of the match (issue #35) so a contact linked to a company
    is findable by that company's name even when its legacy free-text is empty or
    stale.

    ``ct.company`` is matched for EVERY contact, not just unlinked ones — this is
    deliberate, not an oversight (two reviewers read it as one). Link-authority is
    about identity and display, not about forgetting former names: after a company
    is renamed Acme→Beta, searching "Acme" still finds the contact and the result
    renders "Beta" via ``company_name``. Search is recall, and a superset with an
    authoritative label beats hiding a record because the user remembered the old
    name. Scoping it to ``ct.company_id IS NULL`` would silently drop those hits.
    Pinned by test_search_matches_old_company_spelling_but_displays_new_name.

    ``owner_id`` (issue #60) narrows to one person's records — "Mine" is simply this
    parameter set to the caller's own id. Absent means everyone, which is what keeps
    the endpoint's behavior identical for an install that never assigns owners, and
    the ``UNASSIGNED`` sentinel means the unowned pile (#190; see ``owner_condition``).
    It lives in this SHARED builder precisely so it can never reach the page query
    without also reaching the COUNT: a filter present in one and not the other does
    not fail, it just reports a total that disagrees with the rows.
    """
    like = f"%{query}%"
    conditions = [
        "(ct.name ILIKE %s OR ct.email ILIKE %s OR ct.company ILIKE %s"
        " OR co.name ILIKE %s OR ct.notes ILIKE %s)"
    ]
    params: list = [like, like, like, like, like]
    if status:
        conditions.append("ct.status = %s")
        params.append(status)
    owner_sql = owner_condition("ct.owner_id", owner_id, params)
    if owner_sql:
        conditions.append(owner_sql)
    if tags:
        labels = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if labels:
            tag_clauses = " OR ".join(f"{_TAGS_NORMALIZED_SQL} ILIKE %s" for _ in labels)
            conditions.append(f"({tag_clauses})")
            params.extend(f"%,{label},%" for label in labels)
    return " AND ".join(conditions), params


def search_contacts(
    query: str, status: str | None = None, tags: str | None = None,
    limit: int = 20, offset: int = 0, sort: str = "updated_at",
    owner_id: int | str | None = None,
) -> list[dict]:
    where, params = _contact_search_where(query, status, tags, owner_id)
    return pg_fetchall(
        f"""SELECT {_CONTACT_LIST_SELECT}
            FROM contacts ct {_CONTACT_JOINS}
            WHERE {where} ORDER BY {_contact_order_by(sort)} LIMIT %s OFFSET %s""",
        params + [limit, offset],
    )


def count_search_contacts(
    query: str, status: str | None = None, tags: str | None = None,
    owner_id: int | str | None = None,
) -> int:
    """Total number of contacts matching a search (for accurate pagination totals).

    Carries the same join as search_contacts because the shared WHERE references
    co.name. LEFT JOIN on the companies PRIMARY KEY yields at most one company row
    per contact, so COUNT(*) is still a count of contacts, not of pairs.
    """
    where, params = _contact_search_where(query, status, tags, owner_id)
    row = pg_fetchone(
        f"""SELECT COUNT(*) AS cnt
            FROM contacts ct LEFT JOIN companies co ON ct.company_id = co.id
            WHERE {where}""",
        params,
    )
    return row["cnt"] if row else 0


def list_contacts(
    offset: int = 0, limit: int = 50, status: str | None = None,
    tags: str | None = None, sort: str = "updated_at",
    owner_id: int | str | None = None, after_id: int | None = None,
) -> dict:
    _check_assembly_cursor(after_id, sort, offset)
    order_by = _contact_order_by(sort)

    conditions = []
    params: list = []
    if status:
        conditions.append("ct.status = %s")
        params.append(status)
    # One condition list feeds BOTH the COUNT and the page query below. Adding an
    # owner filter to only one of them would silently return a total that disagrees
    # with the rows on the page.
    owner_sql = owner_condition("ct.owner_id", owner_id, params)
    if owner_sql:
        conditions.append(owner_sql)
    if tags:
        labels = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if labels:
            tag_clauses = " OR ".join(f"{_TAGS_NORMALIZED_SQL} ILIKE %s" for _ in labels)
            conditions.append(f"({tag_clauses})")
            params.extend(f"%,{label},%" for label in labels)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # The count needs no join — its WHERE only touches ct columns (the alias is
    # here so the shared qualified conditions parse).
    # Keyed on the CURSOR, not on `sort=id`. A corpus sweep is up to MAX_PAGES requests and
    # never reads `total`, so counting on each would add a scan of the whole filtered set to
    # the heaviest read path in the app — but only its continuation pages can be identified
    # as a sweep. Its first page looks exactly like an ordinary `?sort=id&offset=N`, which
    # is legitimate offset pagination whose caller does need the total. So the sweep pays
    # exactly one COUNT and no envelope loses a field it used to carry.
    total = _count_or_none(after_id, f"SELECT COUNT(*) AS cnt FROM contacts ct {where}", params)

    # The cursor is deliberately NOT in the shared conditions above. That rule exists for
    # FILTERS — a status or owner narrowing the rows but not the COUNT reports a total that
    # disagrees with the page. `after_id` is not a filter, it is the window, exactly like
    # OFFSET (which the COUNT has always ignored): `total` stays the size of the whole
    # matching set, which is what a caller paging through it needs.
    row_conditions = conditions + (["ct.id > %s"] if after_id is not None else [])
    row_where = f"WHERE {' AND '.join(row_conditions)}" if row_conditions else ""
    rows = pg_fetchall(
        f"""SELECT {_CONTACT_LIST_SELECT}
            FROM contacts ct {_CONTACT_JOINS}
            {row_where} ORDER BY {order_by} LIMIT %s OFFSET %s""",
        # The cursor's bind comes last because its condition was appended last.
        params + ([after_id] if after_id is not None else []) + [limit, offset],
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


def _write_status_audited(
    table: str, entity_type: str, entity_id: int, filtered: dict,
    archive_reason: str | None, actor_id: int | None,
) -> bool:
    """Write a contact/company column update, auditing a status move across the
    ``archived`` boundary (#239). False when the row does not exist.

    One transaction under a row lock: a move INTO archived needs a non-blank reason and
    appends an "Archived — <reason>" note by ``actor_id``; a move OUT appends "Restored
    from archive."; any other write (including archived→archived) appends nothing. The
    lock also serializes against chatter_service.add_note and the deletes, which lock
    the same row. ``updated_at`` keeps its normal bump — unlike a deal's, no contact or
    company staleness read uses it, and the note itself is excluded from every one."""
    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    now = _now()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT status FROM {table} WHERE id = %s FOR UPDATE", (entity_id,))
        row = cur.fetchone()
        if row is None:
            return False
        old, new = row[0], filtered.get("status")
        archiving = new == "archived" and old != "archived"
        restoring = old == "archived" and new is not None and new != "archived"
        # Raising here rolls back before anything is written.
        text = _archive_reason(archive_reason, f"a {entity_type}") if archiving else ""
        cur.execute(
            f"UPDATE {table} SET {set_clause}, updated_at = %s WHERE id = %s",
            list(filtered.values()) + [now, entity_id],
        )
        if archiving:
            _insert_housekeeping_note_cur(
                cur, entity_type, entity_id, scoring_service.ARCHIVE_NOTE_PREFIX + text,
                actor_id, now,
            )
        elif restoring:
            _insert_housekeeping_note_cur(
                cur, entity_type, entity_id, scoring_service.RESTORE_NOTE, actor_id, now
            )
    return True


def update_contact(
    contact_id: int, *, archive_reason: str | None = None, actor_id: int | None = None,
    **fields,
) -> dict | None:
    """Update a contact. Setting ``status='archived'`` on a live contact needs a
    non-blank ``archive_reason`` and records it with ``actor_id`` (#239) — see
    _write_status_audited."""
    allowed = {"name", "email", "phone", "company", "title", "source", "status", "tags", "notes",
               "company_id", "owner_id"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if "tags" in filtered:
        filtered["tags"] = _normalize_tags(filtered["tags"] or "")
    if "status" in filtered and filtered["status"] not in CONTACT_STATUSES:
        filtered["status"] = "active"
    # #239: refuse a reasonless archive BEFORE the company-text resolution below, which
    # can auto-create a company — a refused write must leave nothing behind. An unlocked
    # peek; the locked re-check in _write_status_audited stays authoritative.
    if filtered.get("status") == "archived" and not (archive_reason or "").strip():
        current = pg_fetchone("SELECT status FROM contacts WHERE id = %s", (contact_id,))
        if current and current["status"] != "archived":
            _archive_reason(None, "a contact")
    # Issue #35: a company-text write with NO explicit company_id derives the link
    # from the text — non-blank resolves/auto-creates, blank unlinks (the link is
    # what the UI displays, so clearing the text has to clear the display too).
    # Key PRESENCE is the signal: an explicit company_id wins, INCLUDING an
    # explicit None, which means "unlink". ContactForm always sends company_id, so
    # that path is unaffected; crm_update_contact forwards exactly the keys the
    # model sent, so all three cases (absent / id / null) stay expressible.
    if "company" in filtered and "company_id" not in filtered:
        text = filtered["company"] or ""
        filtered["company_id"] = (
            resolve_or_create_company_ids([text]).get(text) if text.strip(_WS) else None
        )
    if not filtered:
        return get_contact(contact_id)
    if not _write_status_audited(
        "contacts", "contact", contact_id, filtered, archive_reason, actor_id
    ):
        return None
    scoring_service.score_on_event(contact_ids=(contact_id,))  # #18: status/company edits shift the score
    return get_contact(contact_id)


def delete_contact(contact_id: int) -> bool:
    """Delete a contact and its dependent activity/todos in one transaction.

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
        # #18: deals linked to this contact unlink (FK ON DELETE SET NULL), so their
        # relationship factor drops — capture them now (before the next execute) to rescore.
        cur.execute("SELECT id FROM deals WHERE contact_id = %s", (contact_id,))
        affected_deal_ids = {r[0] for r in cur.fetchall()}
        # This contact's activity rows (deleted below) may reference OTHER deals too; those
        # deals' recency input changes, so rescore them as well.
        cur.execute("SELECT DISTINCT deal_id FROM activity_log WHERE contact_id = %s AND deal_id IS NOT NULL", (contact_id,))
        affected_deal_ids |= {r[0] for r in cur.fetchall()}
        cur.execute("DELETE FROM activity_log WHERE contact_id = %s", (contact_id,))
        cur.execute("DELETE FROM todos WHERE contact_id = %s", (contact_id,))
        # crm_chatter, crm_field_values and crm_field_provenance are polymorphic (no
        # FK), so their rows are dropped explicitly — otherwise a reused contact SERIAL
        # id would inherit this contact's notes / custom-field values / AI badges.
        # NOTE: deals have no delete path today; if a delete_deal is ever added it MUST
        # do the same FOR UPDATE lock + these DELETEs for entity_type='deal', AND (per #18)
        # capture the deal's contact_id before delete and score_on_event(contact_ids=(...))
        # after commit — a removed deal changes its former contact's deal-linkage factor.
        # It must also DELETE the deal's deal_ai_touch_evidence row (#56, FK-less too).
        # Note attachments (#57) need NO line here: crm_chatter_attachments holds a real
        # FK to crm_chatter ON DELETE CASCADE, so the DELETE below takes them with it.
        # That is the whole reason it was given an FK where the polymorphic tables
        # around it could not have one. Pinned by an integration test.
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
    scoring_service.score_on_event(deal_ids=affected_deal_ids)  # #18: after commit (unlinked deals)
    return True


def get_contact_detail(contact_id: int) -> dict | None:
    """Full contact profile with associated deals, todos, and recent activity.

    The company_name join is inlined here (not in get_contact) so the linked
    company can be shown/linked on the detail page without changing get_contact's
    exact SQL.
    """
    contact = pg_fetchone(
        f"""SELECT {_CONTACT_LIST_SELECT}
           FROM contacts ct {_CONTACT_JOINS}
           WHERE ct.id = %s""",
        # last_contact_at is carried here too, so that after the detail page logs an
        # activity or adds a note its reload hands the list an updated value (#77) — the
        # list patches its row from exactly this body.
        (contact_id,),
    )
    if not contact:
        return None
    # Every rollup below ends on `id` so its order is TOTAL (issue #58). Ties are the
    # norm, not the exception, in all three: timestamps default to `now()` — which is
    # TRANSACTION start, so rows written together are byte-identical, and an import or
    # `seed_data` writes a whole batch that way — while the todo sort's leading keys are
    # a 0/1 flag and a `due_date` that is very often the empty string. Under the LIMITs,
    # an untotalled order lets a row show up twice or not at all between two reads.
    deals = pg_fetchall(
        f"SELECT * FROM deals WHERE contact_id = %s AND {LIVE_PREDICATE} "
        "ORDER BY updated_at DESC, id DESC",
        (contact_id,),
    )
    todos = pg_fetchall(
        f"SELECT * FROM todos WHERE contact_id = %s AND {LIVE_TODO_PREDICATE} "
        f"AND {NOT_DROPPED_TODO} ORDER BY completed ASC, due_date ASC, id ASC LIMIT 20",
        (contact_id,),
    )
    activity = pg_fetchall(
        "SELECT * FROM activity_log WHERE contact_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 20",
        (contact_id,),
    )
    return {**contact, "deals": deals, "todos": todos, "activity": activity}


# ── Companies ─────────────────────────────────────────────────────────────────

def create_company(
    name: str, domain: str = "", industry: str = "", phone: str = "",
    address: str = "", notes: str = "", source: str = "", status: str = "active",
    owner_id: int | None = None,
) -> dict:
    if status == "archived":  # #239 — see create_contact
        raise ValueError(
            "A company cannot be created already archived — create it, then archive it with a reason."
        )
    if status not in COMPANY_STATUSES:
        status = "active"
    row = pg_fetchone(
        """INSERT INTO companies (name, domain, industry, phone, address, notes, source, status, owner_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name.strip(_WS), domain, industry, phone, address, notes, source, status, owner_id),
    )
    return get_company(row["id"])


def get_company(company_id: int) -> dict | None:
    return pg_fetchone("SELECT * FROM companies WHERE id = %s", (company_id,))


def resolve_or_create_company_ids(names: list[str]) -> dict[str, int]:
    """Batch-resolve raw company-name spellings to company ids, auto-creating any
    that don't exist yet (issue #35).

    This is the single company-resolution primitive — the ongoing-ingestion
    counterpart to the one-shot backfill in the companies migration. The #61
    cake_os importer consumes it as-is (``from crm.service import
    resolve_or_create_company_ids``).

    Normalization is the ``uq_companies_name_ci`` contract: case-insensitive
    after trimming the six ASCII whitespace bytes. Names blank after that trim
    are ignored. Returns ``{raw spelling exactly as passed: company id}``, so a
    caller maps a row back with a plain ``.get(row_value)`` and never has to
    normalize; two spellings that normalize alike both appear as keys mapping to
    the same id.

    Two fixed statements regardless of ``len(names)`` — a 5000-row import costs
    2 queries here, not 5000.

    The case-folding is computed entirely IN SQL on both sides rather than with
    Python's ``str.lower()``: Python's Unicode case-folding can disagree with the
    database's ``LOWER()``, which would strand a name we just created. (Same
    class of drift the migration's whitespace comment guards against.)

    The two statements are autocommit, so a company created here survives even if
    the caller's contact write later fails. Accepted: the name genuinely appeared
    in the input, and a company with no contacts is valid, visible, deletable
    data — not corruption.

    Also accepted (single-user v1): if a company is DELETED by someone else in
    the window between the two statements, that name is simply absent from the
    returned map and its contact is written unlinked, keeping its free text — the
    display falls back to that text, so nothing is lost or wrong. A retry loop
    would close it; not worth the branch until the app is multi-user, when this
    becomes a get-or-create that locks the conflicting row.
    """
    # Auto-created companies are left UNASSIGNED (owner_id NULL), deliberately — this
    # is not an oversight in the #60 ownership sweep. A company that appears as a side
    # effect of linking a contact was never something anyone chose to own, and NULL is
    # a fully supported state meaning exactly that. Stamping the importer would
    # fabricate ownership of an organisation record they never picked up; the
    # consequence is simply that a bulk import puts contacts in your "Mine" and leaves
    # the companies for someone to claim. Pinned by
    # test_auto_created_companies_are_left_unassigned.
    # Exact-string dedupe, order-preserving (first spelling wins the stored
    # name). str.strip(_WS) matches btrim's byte set, so Python and SQL agree on
    # what counts as blank.
    unique = [n for n in dict.fromkeys(names) if n and n.strip(_WS)]
    if not unique:
        return {}

    # 1) Create the missing ones. The conflict target is the normalized-name
    #    expression index, NOT a bare ON CONFLICT: bare would also swallow a
    #    primary-key conflict, silently skipping an insert and stranding the
    #    contact unlinked. DISTINCT ON collapses same-key spellings inside the
    #    batch; WITH ORDINALITY makes first-seen-wins deterministic. Race-safe
    #    against a concurrent import by construction (no SELECT-then-INSERT
    #    window). Raw strings: the E'' escapes are Postgres syntax, not Python.
    pg_execute(
        r"""INSERT INTO companies (name)
            SELECT DISTINCT ON (LOWER(btrim(n, E' \t\n\r\f\x0b')))
                   btrim(n, E' \t\n\r\f\x0b')
            FROM unnest(%s::text[]) WITH ORDINALITY AS t(n, ord)
            ORDER BY LOWER(btrim(n, E' \t\n\r\f\x0b')), ord
            ON CONFLICT (LOWER(btrim(name, E' \t\n\r\f\x0b'))) DO NOTHING""",
        (unique,),
    )
    # 2) Map every raw spelling back to its id. The co.name side is exactly the
    #    uq_companies_name_ci expression, so the join is index-assisted.
    rows = pg_fetchall(
        r"""SELECT t.n AS raw, co.id AS id
            FROM unnest(%s::text[]) AS t(n)
            JOIN companies co
              ON LOWER(btrim(co.name, E' \t\n\r\f\x0b'))
               = LOWER(btrim(t.n, E' \t\n\r\f\x0b'))""",
        (unique,),
    )
    return {row["raw"]: row["id"] for row in rows}


def _company_search_where(
    query: str, status: str | None, owner_id: int | str | None = None
) -> tuple[str, list]:
    """Build the shared WHERE clause + params for company free-text search.

    ``owner_id`` (issue #60) belongs here rather than at each call site so the search
    and its COUNT can never disagree about what is being counted. It takes the same
    three values as every other owner filter — an id, None for everyone, or the
    ``UNASSIGNED`` sentinel (#190).
    """
    like = f"%{query}%"
    conditions = ["(name ILIKE %s OR domain ILIKE %s OR industry ILIKE %s OR notes ILIKE %s)"]
    params: list = [like, like, like, like]
    if status:
        conditions.append("status = %s")
        params.append(status)
    owner_sql = owner_condition("owner_id", owner_id, params)
    if owner_sql:
        conditions.append(owner_sql)
    return " AND ".join(conditions), params


def search_companies(
    query: str, status: str | None = None, limit: int = 20, offset: int = 0,
    owner_id: int | str | None = None,
) -> list[dict]:
    where, params = _company_search_where(query, status, owner_id)
    return pg_fetchall(
        f"SELECT * FROM companies WHERE {where} ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
        params + [limit, offset],
    )


def count_search_companies(
    query: str, status: str | None = None, owner_id: int | str | None = None
) -> int:
    """Total number of companies matching a search (for accurate pagination totals)."""
    where, params = _company_search_where(query, status, owner_id)
    row = pg_fetchone(f"SELECT COUNT(*) AS cnt FROM companies WHERE {where}", params)
    return row["cnt"] if row else 0


def list_companies(
    offset: int = 0, limit: int = 50, status: str | None = None, sort: str = "name",
    owner_id: int | str | None = None, after_id: int | None = None,
) -> dict:
    _check_assembly_cursor(after_id, sort, offset)
    allowed_sorts = {"name", "industry", "created_at", "updated_at", "id"}
    sort_col = sort if sort in allowed_sorts else "name"
    # Names/industry read best ascending; timestamps newest-first. Append an id
    # tie-breaker so offset/infinite-scroll pagination is stable.
    direction = "ASC" if sort_col in ("name", "industry") else "DESC"
    # "id" is the frontend's immutable assembly key (#77 — see _CONTACT_SORTS["id"]). It is
    # its own tie-breaker, so it is special-cased rather than emitting "id ASC, id ASC".
    order_by = "id ASC" if sort_col == "id" else f"{sort_col} {direction}, id {direction}"

    conditions = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    # Shared by the COUNT and the page query below — see list_contacts.
    owner_sql = owner_condition("owner_id", owner_id, params)
    if owner_sql:
        conditions.append(owner_sql)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Skipped on a cursor page — see list_contacts.
    total = _count_or_none(after_id, f"SELECT COUNT(*) AS cnt FROM companies {where}", params)

    # Window, not filter — see the same note in list_contacts.
    row_conditions = conditions + (["id > %s"] if after_id is not None else [])
    row_where = f"WHERE {' AND '.join(row_conditions)}" if row_conditions else ""
    rows = pg_fetchall(
        f"SELECT * FROM companies {row_where} ORDER BY {order_by} LIMIT %s OFFSET %s",
        params + ([after_id] if after_id is not None else []) + [limit, offset],
    )
    return {"companies": rows, "total": total, "limit": limit, "offset": offset}


def update_company(
    company_id: int, *, archive_reason: str | None = None, actor_id: int | None = None,
    **fields,
) -> dict | None:
    """Update a company. Setting ``status='archived'`` on a live company needs a
    non-blank ``archive_reason`` and records it with ``actor_id`` (#239) — see
    _write_status_audited."""
    allowed = {"name", "domain", "industry", "phone", "address", "notes", "source", "status"}
    # Drop None values: every one of those columns is NOT NULL, and a tool call
    # sending an explicit null (the HTTP route already filters these out) would
    # otherwise raise a NotNullViolation. None means "field not provided" here.
    filtered = {k: v for k, v in fields.items() if k in allowed and v is not None}
    # owner_id is the exception, and the reason is the column, not the caller: it is
    # NULLABLE, and NULL is a meaningful value there — "unassigned". Folding it into
    # the set above would make an owner impossible to CLEAR once set, since the drop
    # would swallow the only way to say so. Key presence is the signal, as in
    # update_contact's company_id.
    if "owner_id" in fields:
        filtered["owner_id"] = fields["owner_id"]
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
    if not _write_status_audited(
        "companies", "company", company_id, filtered, archive_reason, actor_id
    ):
        return None
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
        # notes would resurface on whatever company later reuses its SERIAL id. Their
        # #57 attachments ride along on the FK cascade — see delete_contact.
        cur.execute(
            "DELETE FROM crm_chatter WHERE entity_type = 'company' AND entity_id = %s",
            (company_id,),
        )
        # #18: contacts + deals unlink (FK ON DELETE SET NULL) — their company-linkage /
        # relationship factors change. Capture ids now (each before the next execute).
        cur.execute("SELECT id FROM contacts WHERE company_id = %s", (company_id,))
        affected_contact_ids = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id FROM deals WHERE company_id = %s", (company_id,))
        affected_deal_ids = [r[0] for r in cur.fetchall()]
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
    scoring_service.score_on_event(deal_ids=affected_deal_ids, contact_ids=affected_contact_ids)  # #18
    return True


def get_company_detail(company_id: int) -> dict | None:
    """Full company profile with rolled-up contacts, deals, and activity.

    Activity has no company_id column, so it's rolled up by joining through the
    company's own contacts and deals.
    """
    company = get_company(company_id)
    if not company:
        return None
    # Same rule as get_contact_detail: every rollup ends on `id` (issue #58). Names are
    # not unique either — two people at one company can share a name, and the importer
    # produces exactly that.
    contacts = pg_fetchall(
        "SELECT * FROM contacts WHERE company_id = %s ORDER BY name ASC, id ASC",
        (company_id,),
    )
    deals = pg_fetchall(
        f"""SELECT d.*, c.name AS contact_name
            FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
            WHERE d.company_id = %s AND {LIVE_PREDICATE_D}
            ORDER BY d.updated_at DESC, d.id DESC""",
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
            ORDER BY a.created_at DESC, a.id DESC LIMIT 20""",
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
    owner_id: int | None = None, deal_temperature: str | None = None,
) -> dict:
    # Coerce an unknown stage to 'lead' (mirrors update_deal's validation): a
    # deal with a stage outside DEAL_STAGES would be summed into the pipeline
    # value but never render in any Kanban column.
    if stage not in DEAL_STAGES:
        stage = "lead"
    probability = max(0, min(100, probability))  # keep the percentage in range
    # Creating a deal straight into won/lost settles its probability too — the edit
    # form's stage <select> offers those stages, so this is a reachable fourth close
    # path, not a theoretical one. (_write_deal_update covers the other three.)
    if stage in ("won", "lost"):
        probability = 100 if stage == "won" else 0
    # An explicit param rather than **kwargs, matching every other column here — and needed
    # rather than optional, because `crm_create_deal` forwards the model's raw kwargs into
    # this signature, so a temperature the model learned from `crm_update_deal`'s schema
    # would otherwise be a TypeError. Raises on a bad tier, like update_deal. Issue #125.
    deal_temperature = normalize_deal_temperature(deal_temperature)
    # company_id appended last (see create_contact); a bad FK -> ForeignKeyViolation.
    row = pg_fetchone(
        """INSERT INTO deals (title, contact_id, stage, value, notes, expected_close_date, probability, currency, company_id, owner_id, deal_temperature)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, contact_id, stage, value, notes, expected_close_date, probability, currency,
         company_id, owner_id, deal_temperature),
    )
    scoring_service.score_on_event(deal_ids=(row["id"],), contact_ids=(contact_id,))  # #18
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
        "SELECT * FROM activity_log WHERE deal_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 20",
        (deal_id,),
    )
    # custom_fields is embedded (issue #22 Q12a) so one read answers "tell me about
    # this deal" — previously the assistant needed a second crm_get_deal_fields call.
    return _embed_custom_fields([{**deal, "activity": activity}])[0]


# ── The board read, shared by all three of its bounding modes (issue #59) ─────
# One SELECT list and one FROM/JOIN block, so the unbounded board, a keyset page and
# the assistant tool's per-stage window cannot drift about which columns a deal has.
_PIPELINE_DEAL_COLS = """d.*, c.name AS contact_name, co.name AS company_name,
                   la.last_at AS last_activity_at"""

_PIPELINE_JOINS = """FROM deals d
            LEFT JOIN contacts c ON d.contact_id = c.id
            LEFT JOIN companies co ON d.company_id = co.id"""

# Issue #21's last-touch blend, WHOLE-CORPUS form: aggregates all of activity_log +
# un-archived deal chatter once, then joins. Affordable exactly when the statement reads
# the whole corpus in one pass (the unbounded board, the tool's window) — never once per
# page of a sweep; see the LATERAL twin below and _CONTACT_LAST_TOUCH_JOIN.
_PIPELINE_LAST_ACTIVITY_GROUPED = """LEFT JOIN (
                SELECT deal_id, MAX(created_at) AS last_at FROM (
                    SELECT deal_id, created_at FROM activity_log WHERE deal_id IS NOT NULL
                    UNION ALL
                    SELECT entity_id AS deal_id, created_at FROM crm_chatter
                    WHERE entity_type = 'deal' AND archived = 0
                ) events GROUP BY deal_id
            ) la ON la.deal_id = d.id"""

# The same two lanes, the same alias, per DEAL. Touches only the page's rows, which is
# what a reader that runs once per page of a corpus sweep must do (the rule
# _CONTACT_LAST_TOUCH_JOIN states for the contact list). idx_activity_deal and
# idx_crm_chatter_entity drive the lookup; neither carries created_at, so the MAX still
# reads that deal's own event rows — bounded by the page, where the grouped form is
# bounded by the whole table. A composite (deal_id, created_at) index is the next
# improvement if it ever measures, and is purely additive.
#
# ANY semantic edit to one twin must be made to the other; the integration suite pins
# that they return identical last_activity_at for the same deals.
_PIPELINE_LAST_ACTIVITY_LATERAL = """LEFT JOIN LATERAL (
                SELECT MAX(e.created_at) AS last_at FROM (
                    SELECT created_at FROM activity_log WHERE deal_id = d.id
                    UNION ALL
                    SELECT created_at FROM crm_chatter
                    WHERE entity_type = 'deal' AND entity_id = d.id AND archived = 0
                ) e
            ) la ON TRUE"""

# The board's PRESENTATION recency order — one definition for the unbounded board's
# ORDER BY, the window mode's partition ranking, and that window's outer ORDER BY. Ends
# on the id term issue #58 requires; test_order_by_fragment_constants_are_total checks it.
# (A keyset PAGE deliberately does NOT use this: its order is a cursor order, d.id ASC.)
_PIPELINE_RECENCY_ORDER = "d.updated_at DESC, d.id DESC"


def get_pipeline(
    stage: str | None = None,
    include_archived: bool = False,
    limit: int | None = None,
    after_id: int | None = None,
    limit_per_stage: int | None = None,
) -> dict:
    # Single query (optional stage WHERE) so the two branches can't drift. Beyond the
    # contact-name join, the board payload carries `company_name` (mirrors get_deal) for
    # keyword search, and a derived `last_activity_at` (issue #21) = the most recent of the
    # deal's genuine activity signals: explicit activity_log rows (calls/emails logged via
    # POST /api/crm/activity — never written by edits/stage-moves) blended with un-archived
    # deal chatter notes. The UNION-ALL/GROUP BY yields one row per deal; the LEFT JOIN
    # leaves `last_at` NULL when a deal has neither → the client's "no activity" bucket.
    # Scale note: the GROUPED join aggregates the whole activity_log + chatter before the
    # join (the stage WHERE can't push into it). That is the right plan for a statement
    # that reads the whole corpus in one pass, and the wrong one for a page of a sweep —
    # which is why issue #59 gave it a LATERAL twin and the keyset mode below uses that
    # instead. See _PIPELINE_LAST_ACTIVITY_GROUPED / _PIPELINE_LAST_ACTIVITY_LATERAL.
    #
    # Issue #59 added three bounding modes over ONE condition list, so the HTTP board and
    # the assistant's crm_get_pipeline share one implementation rather than each capping
    # its own way (which is how the two would later disagree about "newest per stage"):
    #   * default (limit and limit_per_stage both None) — unchanged, the whole board;
    #   * KEYSET PAGE (`limit`, optional `after_id`) — the board's transport. Ordered by
    #     the immutable PK because that is what makes a cursor meaningful; the frontend
    #     sweeps every page and reassembles the complete corpus before rendering, so this
    #     is invisible to the user and the facet model (#21/#77) is untouched;
    #   * PER-STAGE WINDOW (`limit_per_stage`) — the assistant tool's cap, in SQL rather
    #     than a Python trim over the whole board. Be precise about what that buys: the
    #     rows BUILT, transferred and held in memory now scale with the cap, while
    #     Postgres still scans and ranks the whole partition before the outer `rn` filter
    #     can apply. So this is equal-or-better than the trim it replaced, never worse —
    #     but it is not a smaller SCAN. Making the scan scale with the cap needs a
    #     lateral-per-stage rewrite, which is a real perf project, not this issue.
    #
    # There is deliberately NO owner_id parameter here, unlike list_contacts/
    # list_companies/list_todos (issue #60). The board is fetched in keyset pages since
    # #59, but it still assembles EVERY live deal client-side and every other facet
    # (#21: keyword, stage, value, close date, last activity) filters client-side over
    # that complete array — a server-side facet would be a second filtering model. Owner
    # joins them, so `d.owner_id` simply rides along in `d.*` and the picker resolves
    # names from the /api/users call the owner dropdowns need anyway. A server
    # parameter would buy nothing and cost a second code path — and worse, this
    # function returns `deals` AND a separately-computed `stage_summary`, so a filter
    # applied to one and not the other would show filtered cards under unfiltered
    # totals.
    # `include_archived` (issue #83) is the second sanctioned hole in the archived-deal
    # sweep, after crm_search_deals(include_archived=true) — and it is what makes an
    # accidental archive recoverable without an AI provider: the board's Archived facet
    # sets it, the card renders inert, and the deal sheet offers Restore.
    #
    # Scale note, distinct from the one above: the live board is bounded by OPEN WORKLOAD,
    # whereas the widened board is bounded by all-time history — every merge archives a
    # source, and junk archives never leave. So this branch grows monotonically where the
    # default one does not. Issue #83 named "a server-side cap on this branch" as the
    # upgrade path; #59 delivered it — `limit`/`after_id` page BOTH branches, and the
    # frontend carries the facet on every page of the sweep so the two corpora can never
    # interleave. (A dedicated archived-deals view is still out of scope.)
    #
    # It opens the DEALS QUERY ONLY. stage_summary below keeps LIVE_PREDICATE
    # unconditionally, which looks like exactly the one-sided filter the owner_id note
    # above forbids — but the asymmetry is the rule here, not a bug in it. Owner is a
    # symmetric facet: cards and totals must describe the same set or the page lies.
    # Archived is not: an archived deal must be FINDABLE (or it is unrecoverable) and must
    # never be MONEY (won + archived would book revenue no report can see). The client
    # mirrors that same split — every $ aggregate on the board derives from the live
    # subset — so cards and totals still agree about value.
    # Refuse the meaningless combinations BEFORE any query, so a bad call costs no round
    # trip and the router's ValueError -> 400 mapping needs no database. No `< 1` bounds
    # here: the route enforces them with Query(ge=...) and the tool clamps via
    # _bounded_limit, exactly as every sibling reader trusts its callers.
    if after_id is not None and limit is None:
        raise ValueError("after_id requires limit")
    if limit_per_stage is not None and (limit is not None or after_id is not None):
        raise ValueError("limit_per_stage cannot be combined with limit or after_id")

    conditions = [] if include_archived else [LIVE_PREDICATE_D]
    params: list = []
    if stage:
        conditions.append("d.stage = %s")
        params.append(stage)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    deals_truncated = False
    if limit is not None:
        # KEYSET PAGE. The cursor is appended to the ROW query only — it is the window,
        # not a filter (list_contacts states the same rule), so it never joins the shared
        # condition list that a COUNT would also read.
        #
        # ORDER BY d.id ASC because a cursor is only meaningful against an immutable,
        # append-only key: under `updated_at` a row edited mid-sweep would move across the
        # boundary and be duplicated or skipped. That is a TRANSPORT order — the caller
        # reassembles the corpus and restores presentation order (see pipelineAssembly.ts).
        #
        # The ORDER BY and LIMIT are literal text on purpose: #58's static scanner judges
        # this site directly, and hiding either behind a variable would make it undecidable
        # and force a registry entry instead of an answer.
        row_conditions = conditions + (["d.id > %s"] if after_id is not None else [])
        row_where = f"WHERE {' AND '.join(row_conditions)}" if row_conditions else ""
        deals = pg_fetchall(
            f"""SELECT {_PIPELINE_DEAL_COLS}
            {_PIPELINE_JOINS}
            {_PIPELINE_LAST_ACTIVITY_LATERAL}
            {row_where}
            ORDER BY d.id ASC
            LIMIT %s""",
            params + ([after_id] if after_id is not None else []) + [limit],
        )
    elif limit_per_stage is not None:
        # PER-STAGE WINDOW. Rank within each stage by the presentation order, then keep
        # ONE rank more than asked: that surplus row is the truncation probe and is
        # dropped, which makes `deals_truncated` exact without a second COUNT — the same
        # over-fetch-by-one rule #77 uses for hasMore. The inner alias is `d` so the outer
        # ORDER BY can reuse _PIPELINE_RECENCY_ORDER, and the global recency order
        # reproduces the trimmed-list order this mode replaced.
        rows = pg_fetchall(
            f"""SELECT * FROM (
                SELECT {_PIPELINE_DEAL_COLS},
                       ROW_NUMBER() OVER (PARTITION BY d.stage
                                          ORDER BY {_PIPELINE_RECENCY_ORDER}) AS rn
                {_PIPELINE_JOINS}
                {_PIPELINE_LAST_ACTIVITY_GROUPED}
                {where}
            ) d
            WHERE d.rn <= %s
            ORDER BY {_PIPELINE_RECENCY_ORDER}""",
            params + [limit_per_stage + 1],
        )
        deals = []
        for row in rows:
            if row.pop("rn") > limit_per_stage:
                deals_truncated = True  # this stage had more than the cap
                continue
            deals.append(row)
    else:
        deals = pg_fetchall(
            f"""SELECT {_PIPELINE_DEAL_COLS}
            {_PIPELINE_JOINS}
            {_PIPELINE_LAST_ACTIVITY_GROUPED}
            {where}
            -- This tiebreaker makes the recency order total (issue #58). It steadies the
            -- within-stage card order across refreshes here; #59's keyset pages order by
            -- d.id instead, and its window mode ranks partitions by this same constant.
            ORDER BY {_PIPELINE_RECENCY_ORDER}""",
            params,
        )

    if after_id is not None:
        # A continuation page of a corpus sweep never reads the aggregate. Re-running a
        # whole-table GROUP BY on every one of up to MAX_PAGES pages would multiply the
        # exact cost this issue exists to contain — for numbers PipelinePage provably
        # discards (its PipelineData reads only `deals`).
        #
        # Keyed on the CURSOR, not on `limit`, for the same reason _count_or_none is: a
        # FIRST page is indistinguishable from an ordinary bounded request whose caller may
        # legitimately want the envelope, so the sweep pays for the aggregate exactly once.
        stage_summary = None
        total_pipeline = None
    else:
        # Value summaries per stage (open stages only). NEVER opened by include_archived —
        # see the note above the deals query. Computed over every matching deal, so the
        # window mode's trimmed list still reports true counts and values.
        stage_summary = pg_fetchall(
            f"""SELECT stage, COUNT(*) AS count, COALESCE(SUM(value), 0) AS total_value
                FROM deals WHERE stage NOT IN ('won', 'lost') AND {LIVE_PREDICATE}
                GROUP BY stage"""
        )
        total_pipeline = sum(s["total_value"] for s in stage_summary)

    result = {"deals": deals, "stage_summary": stage_summary,
              "total_pipeline_value": total_pipeline}
    if limit_per_stage is not None:
        result["deals_truncated"] = deals_truncated
    return result


def list_deals(stage: str | None = None, contact_id: int | None = None, limit: int = 50) -> list[dict]:
    conditions = [LIVE_PREDICATE_D]
    params: list = []
    if stage:
        conditions.append("d.stage = %s")
        params.append(stage)
    # `is not None`, not truthiness: the ROUTE deliberately treats `?contact_id=0` as a
    # supplied filter (test_contact_id_zero_is_a_filter_not_a_fallthrough pins that), so a
    # truthiness test here silently dropped it and answered a filtered request with the
    # WHOLE deal list. `stage` keeps its truthiness test — there "" genuinely means absent.
    if contact_id is not None:
        conditions.append("d.contact_id = %s")
        params.append(contact_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    return pg_fetchall(
        f"""SELECT d.*, c.name AS contact_name
            FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
            {where} ORDER BY d.updated_at DESC, d.id DESC LIMIT %s""",
        params,
    )


# Every `deals` column `_write_deal_update` can be asked to write, mapped to its
# destination type for the cast in the distinctness test.
#
# NOTE: these VALUES are interpolated into SQL (`%s::{type}`), so they are type names, not
# data — keep them fixed literals here and never let a caller reach this map.
#
# This is deliberately NOT the same set as what a user or the assistant may write: it
# covers internal-only columns too (`lost_reason`, whose sole writer is `mark_deal_lost`),
# and it grows whenever a new internal write path routes through the chokepoint. The
# user-facing allowlist is `_DEAL_USER_WRITABLE`, hand-maintained and default-closed;
# deriving one from the other would mean declaring a type for an internal column silently
# made it writable by `crm_update_deal` and `PUT /api/crm/deals/{id}` in the same commit.
# A hermetic test asserts `_DEAL_USER_WRITABLE <= _DEAL_COLUMN_TYPES.keys()`, which keeps
# "no writable column without a declared type" without inverting the safe direction.
#
# The cast is not optional, because assignment context and comparison context do NOT agree
# and they disagree in opposite directions:
#   * INTEGER promotes on comparison. `probability = 40.1` STORES 40 (unchanged), but a
#     bare `probability IS DISTINCT FROM 40.1` promotes the stored 40 to float, calls it
#     distinct and fires the UPDATE — bumping `updated_at` for a write that changed
#     nothing, the exact harm #96 exists to stop.
#   * TEXT accepts an I/O conversion on assignment and has NO operator for comparison.
#     `title = 12345` stores '12345' and always has, but a bare
#     `title IS DISTINCT FROM 12345` raises `operator does not exist: text = integer`.
#     That path is live: `crm_update_deal` forwards raw, unvalidated LLM arguments, and a
#     psycopg2 error there escapes as the registry's generic "please try again", looping
#     the model on a permanent condition.
# Casting ONLY the comparison operand leaves assignment behavior — including its type
# errors, e.g. a boolean into an INTEGER column — as it was. (One SQLSTATE moves: a boolean
# into `value` now raises CannotCoerce 42846 from the cast rather than DatatypeMismatch
# 42804 from the SET. Both still raise, and nothing anywhere catches either code — they
# land in the same generic handler — so there is no behavioral difference.)
_DEAL_COLUMN_TYPES = {
    "title": "text", "stage": "text", "notes": "text", "currency": "text",
    "expected_close_date": "text", "lost_reason": "text", "deal_temperature": "text",
    "value": "float8",
    "probability": "int", "contact_id": "int", "company_id": "int", "owner_id": "int",
}

# The security boundary: what unvalidated input — `crm_update_deal` forwards the model's
# raw kwargs, and `PUT /api/crm/deals/{id}` its body — may write through `update_deal`.
# Hand-maintained and default-CLOSED on purpose: a column becomes writable here only by
# being typed out, never as a side effect of some other list growing. `lead_score` (never
# user/tool/assistant-writable) and `archived_at` (owned by `archive_deal`) are absent and
# must stay absent.
_DEAL_USER_WRITABLE = frozenset({
    "title", "stage", "value", "notes", "expected_close_date", "probability", "currency",
    "contact_id", "company_id", "owner_id",
    # issue #125. An INPUT, exactly like probability: a human (or the assistant on a human's
    # instruction) says how a deal feels, and #18's scoring reads it. `lead_score` itself
    # stays absent from this set, as it must.
    "deal_temperature",
})


def _classify_deal_update(
    deal_id: int, old_stage: str, archived_at, filtered: dict,
) -> tuple[dict, tuple[str, str] | None]:
    """Given a deal's locked pre-image and a validated column map, resolve the final
    column map plus the stage transition it implies (or None).

    This is the ONE definition of the rules that make a stage change correct, shared by
    ``_write_deal_update`` (one deal, raises) and ``bulk_move_deals`` (many deals,
    collects per-deal errors). Issue #55 needed those rules on a set-based path without
    a per-deal loop; keeping a second copy is the exact drift the blueprint had to fix
    later, so the rules moved here instead and both paths call it. Pure — no I/O — so
    the bulk path can classify a whole batch in memory inside its lock window.

    Never mutates ``filtered`` (copies, as the single-deal path always did). Raises
    ValueError for a stage change on an archived deal.
    """
    new_stage = filtered.get("stage", old_stage)
    # An archived deal is out of every list, board and aggregate — so closing one
    # would book revenue nothing can see (won + archived is absent from win rate
    # and avg deal size). Same stance merge_deals takes: restore it first.
    if new_stage != old_stage and archived_at is not None:
        raise ValueError(
            f"Cannot change the stage of archived deal #{deal_id} — restore it first"
        )
    if old_stage == "lost" and new_stage != "lost" and "lost_reason" not in filtered:
        filtered = {**filtered, "lost_reason": ""}
    # Closing a deal settles its win probability on every path that CHANGES the
    # stage — the Kanban drag, crm_update_deal_stage and the edit form all come
    # through here (create_deal handles the create-as-closed case itself).
    # This OVERRIDES a supplied probability on purpose: "probability" means chance
    # of winning, so it has exactly one correct value once the deal is decided, and
    # the edit form happily posts the old 30% alongside stage='won'. Only on the
    # TRANSITION though — editing probability on an already-closed deal stays the
    # caller's call.
    if new_stage != old_stage and new_stage in ("won", "lost"):
        filtered = {**filtered, "probability": 100 if new_stage == "won" else 0}
    # Copy unconditionally: when neither branch above fires, `filtered` is still the
    # caller's own dict, and bulk_move_deals then stamps `updated_at` into what it gets
    # back. That is safe today only because bulk builds a fresh literal per iteration —
    # returning a copy means it stays safe for the next caller too.
    return dict(filtered), (old_stage, new_stage) if new_stage != old_stage else None


def _write_deal_update(deal_id: int, filtered: dict) -> bool:
    """Apply a validated column map to one deal in a single transaction.

    Every deal COLUMN update funnels through here (create_deal and archive_deal are
    the two writes that don't — they have no old stage to transition from) so the
    three things that must happen together with a stage change actually do (issue #22):

    1. **Stage history** — a ``deal_stage_events`` row is appended in the SAME
       transaction as the ``UPDATE``, so the log can never disagree with
       ``deals.stage``.
    2. **Stale lost_reason** — a deal leaving the 'lost' stage has its reason
       cleared. cake_os shipped this bug and fixed it later; porting the fixed
       behavior means a reopened deal can't carry "budget cut" into the timeline
       or a win/loss read.
    3. **Serialization** — the old stage is read under ``SELECT ... FOR UPDATE``,
       so two concurrent moves can't both log a transition from the same old
       stage (AGENTS.md: a check-then-write spanning reads and updates is one
       transaction).
    4. **Lead scores** (#18) — after the write commits, the deal and its linked
       contact(s) are rescored. Hooked HERE rather than in each caller (where #18
       originally put it) so every funneled write rescores — including the #22
       lifecycle verbs (mark won/lost) #18 never knew about. On a re-link both the
       old and the new contact changed inputs. score_on_event never raises.
    5. **No-op writes touch nothing** (#96) — the UPDATE carries an
       ``IS DISTINCT FROM`` test over exactly the columns it is about to set, so a
       call that would write every column the value it already holds matches no row
       and ``updated_at`` does not move. ``LAST_TOUCH_SQL`` reads ``updated_at`` as a
       touch, so without this a redundant call — the assistant re-asserting a deal's
       current stage via ``crm_update_deal_stage``, or ``PUT /api/crm/deals/{id}``
       resaving an unchanged form — reset the deal's staleness clock and silently
       dropped it out of ``get_stale_deals`` and the heartbeat's nudges for a whole
       window with nothing changed. ``bulk_move_deals`` and ``archive_deal`` already
       guarded against exactly this; this path was the outlier.

    Rules 1 and 2 — and the archived-deal refusal and probability settling — are
    resolved by ``_classify_deal_update``, shared with ``bulk_move_deals`` (#55) so the
    single-deal and set-based paths cannot drift. This function owns the I/O: the lock,
    the write, the audit row, and the post-commit rescore.

    Returns False when the deal does not exist — and True for a no-op, which is why
    rule 5 needs no caller changes: False means "no such deal" and all four callers
    turn it into None (a 404 / a tool error), where a deal that already holds the
    requested state must still be returned.

    ``filtered`` must already be validated/clamped by the caller — this function writes
    what it is given, and must be non-empty (an empty map would build
    ``SET , updated_at = …``, and would also make the distinctness test below vacuously
    false). No caller can reach that today; the guard is here so a future one can't
    either.
    """
    if not filtered:
        raise ValueError("_write_deal_update requires at least one column to set")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT stage, archived_at, contact_id FROM deals WHERE id = %s FOR UPDATE",
            (deal_id,),
        )
        row = cur.fetchone()
        if row is None:
            return False
        old_stage, archived_at, old_contact_id = row[0], row[1], row[2]
        filtered, stage_event = _classify_deal_update(
            deal_id, old_stage, archived_at, filtered
        )
        # Rule 5. Postgres decides whether anything would actually change, not Python:
        # the row is already locked, so `IS DISTINCT FROM` over the very columns being SET
        # is an exact statement of "this write is a no-op", evaluated with each column's
        # own type semantics (see _DEAL_COLUMN_TYPES for why the cast is mandatory) and
        # handling NULL — an unlinked contact_id — the way `=` would not. Comparing a
        # pre-image in Python instead would be subtly wrong on types the tool layer can
        # reach: the assistant's arguments are not runtime schema-validated, and
        # `1.0 == True` is True in Python where Postgres REFUSES to assign a boolean to a
        # DOUBLE PRECISION column, which would turn an invalid write into a silent no-op.
        # `updated_at` is set but deliberately NOT part of the test: the question is
        # whether anything ELSE changed. Values bind twice — once to SET, once to compare.
        #
        # Checked HERE, not on the way in: _classify_deal_update can ADD columns
        # (lost_reason, probability), so this is the first point the final written map
        # exists. Named explicitly rather than left to a bare KeyError on the cast lookup.
        undeclared = set(filtered) - _DEAL_COLUMN_TYPES.keys()
        if undeclared:
            raise ValueError(
                f"_write_deal_update: no declared type for {sorted(undeclared)} — "
                "add it to _DEAL_COLUMN_TYPES"
            )
        set_clause = ", ".join(f"{k} = %s" for k in filtered)
        distinct_clause = " OR ".join(
            f"{k} IS DISTINCT FROM %s::{_DEAL_COLUMN_TYPES[k]}" for k in filtered
        )
        values = list(filtered.values())
        cur.execute(
            f"UPDATE deals SET {set_clause}, updated_at = %s "
            f"WHERE id = %s AND ({distinct_clause})",
            values + [_now(), deal_id] + values,
        )
        # 0 means "the row exists (we hold its lock) and no column would change".
        changed = cur.rowcount > 0
        # A stage event exists only when _classify_deal_update saw new_stage != old_stage,
        # so `stage` differs, so the row IS distinct and the UPDATE fired — gating on
        # `changed` too makes "no write, no history" structural instead of inferred.
        if stage_event and changed:
            cur.execute(
                "INSERT INTO deal_stage_events (deal_id, old_stage, new_stage) "
                "VALUES (%s, %s, %s)",
                (deal_id, stage_event[0], stage_event[1]),
            )
    # After commit, on purpose: a scoring read inside the transaction would see (and
    # lengthen) the FOR UPDATE window. Dedup/None-filtering is score_on_event's job.
    # Deliberately NOT gated on `changed`, unlike the stage event above — that is the one
    # place this path does not mirror bulk's skip, and it is the correct asymmetry.
    # score_on_event is swallowed on failure, and the daily refresh EXCLUDES terminal
    # deals that already carry a score (scoring_service: `AND NOT (stage IN ('won','lost')
    # AND lead_score IS NOT NULL)`), so a mark_deal_won whose rescore failed would keep a
    # stale score forever — re-calling mark_deal_won is its only repair route, and gating
    # would close it. It cannot reintroduce #96: recompute_deal writes lead_score /
    # lead_score_at and never updated_at.
    scoring_service.score_on_event(
        deal_ids=(deal_id,),
        contact_ids=(filtered.get("contact_id"), old_contact_id),
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
    include_archived: bool = False,
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
    # expected_close_date is TEXT NOT NULL DEFAULT '', so a plain sort puts every
    # UNDATED deal first — the exact opposite of "deals closing soon". NULLIF + NULLS
    # LAST pushes them to the end in both directions.
    sort_expr = (f"NULLIF(d.{sort_col}, '') {direction} NULLS LAST"
                 if sort_col == "expected_close_date" else f"d.{sort_col} {direction}")

    # One of the two reads that can surface an archived deal (the other is
    # get_pipeline(include_archived=True), issue #83's board facet), which makes this the
    # way back from an accidental archive or a wrong merge: without one of them a soft
    # archive is a one-way door, since every other list/board/rollup filters them out and
    # get_deal needs an id nothing would tell you. This one is the assistant's route back
    # and needs a provider; #83's is the keyless one.
    conditions = [] if include_archived else [LIVE_PREDICATE_D]
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
            {('WHERE ' + ' AND '.join(conditions)) if conditions else ''}
            ORDER BY {sort_expr}, d.id {direction}
            LIMIT %s""",
        params,
    )
    return _embed_custom_fields(rows)


def update_deal(deal_id: int, **fields) -> dict | None:
    # lost_reason is deliberately absent from _DEAL_USER_WRITABLE: mark_deal_lost is its
    # single writer, so a reason always arrives with the close (and its timeline note) and
    # can never be set on a deal that isn't lost.
    allowed = _DEAL_USER_WRITABLE
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if "stage" in filtered and filtered["stage"] not in DEAL_STAGES:
        return None
    if "probability" in filtered and filtered["probability"] is not None:
        filtered["probability"] = max(0, min(100, filtered["probability"]))
    # Raises ValueError on a bad tier rather than returning None like the stage check above:
    # the route turns that into a 400 naming the problem, where the `return None` path is a
    # 404 saying the deal does not exist. Issue #125.
    if "deal_temperature" in filtered:
        filtered["deal_temperature"] = normalize_deal_temperature(filtered["deal_temperature"])
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


# ── Bulk deal operations (issue #55) ─────────────────────────────────────────

# The ceiling on one bulk move, shared by the REST route and the agent tool (so the
# cap has one definition and one renderable message). 200 rather than the blueprint's
# 500 because CakeCRM sends ONE unchunked request: the cap has to bound both the
# FOR UPDATE window and the post-commit rescore, which runs one advisory-locked
# recompute per updated deal AND per linked contact. At single-user v1 scale 200
# covers a full column — usually the whole board. Raise it only alongside client-side
# chunking or asynchronous scoring.
BULK_MOVE_MAX = 200


def bulk_move_deals(deal_ids: list[int], stage: str) -> dict:
    """Move many deals to one stage in a single transaction, set-based.

    Returns ``{ok, updated, updated_ids, errors}``. Whole-request problems (bad stage,
    empty list, over the cap) come back as ``ok: False`` having touched no connection;
    per-deal problems ride ``errors`` while everything else still commits. That
    per-deal isolation is a deliberate contract difference from ``_write_deal_update``,
    which raises: one archived deal in a 50-deal selection must not sink the batch. (It is
    not the only difference — the closing paragraph covers the others.)

    Correctness comes from calling the SAME ``_classify_deal_update`` the single-deal
    path calls, once per locked row — pure in-memory work, no I/O — so the archived
    refusal, the lost_reason clearing and the probability settling cannot drift
    between the two paths (issue #55; the blueprint had to fix exactly that drift).

    Shape of the write: rows are locked ``ORDER BY id ... FOR UPDATE`` — ascending id,
    the same rule ``merge_deals`` documents, so two concurrent bulks queue instead of
    deadlocking and no ordering inversion exists against single-deal writers (which
    lock one row). Deals sharing an identical column map share ONE ``UPDATE``, and all
    the stage events are ONE multi-row INSERT on the SAME cursor and transaction — so
    "deals moved but the history is missing" is unreachable; a failure anywhere rolls
    back everything.

    A deal already in the target stage is skipped ENTIRELY — no write, so no
    ``updated_at`` bump. That is deliberate: ``LAST_TOUCH_SQL`` reads ``updated_at`` as
    a touch, so bumping it would reset the staleness clock on deals this call did not
    actually change. Since #96 the single-deal path holds the same invariant, reached a
    different way: ``_write_deal_update``'s UPDATE carries an ``IS DISTINCT FROM`` test,
    so a same-stage move there matches no row. The shared classifier guarantees the two
    paths agree on WHAT to write; each decides WHETHER to write for itself, and they now
    agree there too — an integration test pins a same-stage move through both paths to
    an unmoved ``updated_at`` and no stage event.

    The skip is load-bearing rather than theoretical. The *UI* never sends a same-stage
    move (``handleKanbanMove`` returns early on a same-column drop and the detail sheet
    checks ``deal.stage !== stage``), but two non-UI callers do: ``crm_update_deal_stage``
    re-asserting a deal's current stage (an easy assistant redundancy) and
    ``PUT /api/crm/deals/{id}`` with an unchanged form.

    Differences from the single-deal path that survive on purpose: this one raises vs
    collects ``errors`` (above); it rescores only ``updated_ids`` where the single-deal
    path rescores unconditionally (see ``_write_deal_update``'s note on why); and its
    no-op skip is decided in Python on the locked pre-image (``old_stage == stage``)
    rather than in SQL. That last one is sound HERE and only here: bulk writes exactly one
    caller-controlled column, ``stage``, always a ``DEAL_STAGES`` string validated before
    the connection opens, so there is no cross-type comparison to get wrong. The
    single-deal path writes an arbitrary column map from unvalidated tool arguments and
    must let Postgres judge.
    """
    if stage not in DEAL_STAGES:
        return {"ok": False, "updated": 0, "updated_ids": [], "errors": [f"Invalid stage: {stage}"]}
    # `= ANY(%s)` needs a *list* — psycopg2 cannot adapt a set — so dedupe with
    # dict.fromkeys, which also preserves the caller's request order.
    ids = list(dict.fromkeys(deal_ids or []))
    if not ids:
        return {"ok": False, "updated": 0, "updated_ids": [], "errors": ["No deal IDs provided"]}
    # Checked here rather than at each entry point so every caller — REST, agent tool, any
    # future one — gets it. The isinstance half matters as much as the range half: a bare
    # `i <= 0` raises TypeError on a str or None, which would make this a 500 instead of a
    # refusal and leave the guard depending on callers having type-checked first. `bool` is
    # excluded explicitly because isinstance(True, int) is True.
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids):
        return {"ok": False, "updated": 0, "updated_ids": [],
                "errors": ["Deal IDs must be positive integers"]}
    if len(ids) > BULK_MOVE_MAX:
        return {"ok": False, "updated": 0, "updated_ids": [],
                "errors": [f"Too many deals ({len(ids)}); max {BULK_MOVE_MAX} per bulk move"]}

    errors: list[str] = []
    write_plan: dict[int, dict] = {}
    stage_events: list[tuple[int, str, str]] = []
    contact_ids: list[int] = []
    now = _now()

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            # Bound the wait, same idiom and reason as dreaming.processor. This is the
            # longest lock-holding transaction in the app: Postgres locks the matched rows
            # one at a time in id order, so a batch blocked on the 50th deal is already
            # HOLDING the previous 49 — and without a timeout it holds them for as long as
            # the blocker lives. Every single-deal write, archive and rescore on those deals
            # then queues behind it until the 10-slot pool fills and get_connection's
            # semaphore stops serving requests at all. _write_deal_update has the same
            # exposure over one row; this has it over up to BULK_MOVE_MAX.
            cur.execute("SET LOCAL lock_timeout = '10s'")
            cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute(
                "SELECT id, stage, archived_at, contact_id FROM deals WHERE id = ANY(%s) "
                "ORDER BY id FOR UPDATE",
                (ids,),
            )
            # Positional access, matching _write_deal_update: the raw cursor returns tuples.
            # Converted BEFORE the next execute() — cursor.description is per-statement, so
            # deferring this would read the wrong column metadata.
            rows_by_id = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

            for did in ids:
                row = rows_by_id.get(did)
                if row is None:
                    errors.append(f"Deal {did} not found")
                    continue
                old_stage, archived_at, contact_id = row
                if old_stage == stage:
                    continue  # already there — see the docstring on why this writes nothing
                try:
                    fields, stage_event = _classify_deal_update(
                        did, old_stage, archived_at, {"stage": stage}
                    )
                except ValueError as e:
                    errors.append(str(e))
                    continue
                fields["updated_at"] = now
                write_plan[did] = fields
                if stage_event:
                    stage_events.append((did, stage_event[0], stage_event[1]))
                if contact_id:
                    contact_ids.append(contact_id)

            # Grouped set-based flush: deals sharing an identical column/value map share one
            # statement, so a plain stage move collapses to a single UPDATE regardless of
            # batch size (the realistic worst case is three groups — plain movers, movers
            # leaving 'lost', and movers closing). Column names come from
            # _classify_deal_update and are fixed literals (stage, lost_reason, probability,
            # updated_at), so the f-string interpolates only safe identifiers; values stay bound.
            groups: dict[tuple, list[int]] = {}
            for did, fields in write_plan.items():
                groups.setdefault(tuple(sorted(fields.items())), []).append(did)
            for shape, group_ids in groups.items():
                cur.execute(
                    f"UPDATE deals SET {', '.join(f'{c} = %s' for c, _ in shape)} WHERE id = ANY(%s)",
                    [v for _, v in shape] + [group_ids],
                )

            if stage_events:
                cur.execute(
                    "INSERT INTO deal_stage_events (deal_id, old_stage, new_stage) "
                    "SELECT * FROM unnest(%s::int[], %s::text[], %s::text[])",
                    ([e[0] for e in stage_events], [e[1] for e in stage_events],
                     [e[2] for e in stage_events]),
                )
    except (psycopg2.errors.LockNotAvailable, psycopg2.errors.QueryCanceled):
        # Either timeout fired, so the transaction rolled back and NOTHING was written.
        # Answered as a refusal rather than raising: an honest "nothing happened, try again"
        # is strictly better than a 500, which the board would have to treat as an unknown
        # outcome and refuse to revert.
        logger.warning("bulk_move_deals timed out waiting on row locks for %d deals", len(ids))
        return {"ok": False, "updated": 0, "updated_ids": [],
                "errors": ["Those deals are busy right now — try again in a moment"]}

    updated_ids = [did for did in ids if did in write_plan]
    # After commit, same rule and reason as _write_deal_update: a scoring read inside
    # the transaction would see and lengthen the FOR UPDATE window. score_on_event
    # dedupes, drops falsy ids, and never raises.
    scoring_service.score_on_event(deal_ids=updated_ids, contact_ids=contact_ids)
    return {"ok": True, "updated": len(updated_ids), "updated_ids": updated_ids, "errors": errors}


# ── Deal lifecycle: won / lost / archive / merge (issue #22) ──────────────────

MAX_LOST_REASON = 500
# #239: an archive reason is short prose, capped like a lost reason (the REST models
# reject past it; the service truncates on the tool path).
MAX_ARCHIVE_REASON = 500


def mark_deal_won(deal_id: int) -> dict | None:
    """Close a deal as won: stage='won', probability=100.

    Any lost_reason from an earlier close is cleared by _write_deal_update.
    """
    if not _write_deal_update(deal_id, {"stage": "won", "probability": 100}):
        return None
    return get_deal(deal_id)


def mark_deal_lost(
    deal_id: int, lost_reason: str = "", author_id: int | None = None
) -> dict | None:
    """Close a deal as lost: stage='lost', probability=0, reason recorded.

    The reason is stored on the deal (queryable, shown on the deal sheet) AND
    appended to the notes thread (visible where the user reads the deal's story).
    The note is best-effort and lands after the close commits — a chatter failure
    must never leave the deal un-closed.

    ``author_id`` is who TYPED the reason, threaded into the generated note (issue
    #128). The REST route passes the logged-in user; the assistant tool leaves it
    NULL, which is the honest answer there — Phase A does not thread identity into
    tool executors (see chatter_service.add_note). Without it a reason a rep typed
    themselves would post as unattributed and undercount that rep's own activity.
    """
    reason = (lost_reason or "").strip()[:MAX_LOST_REASON]
    if not _write_deal_update(
        deal_id, {"stage": "lost", "probability": 0, "lost_reason": reason}
    ):
        return None
    if reason:
        try:
            chatter_service.add_note(
                "deal", deal_id, f"Deal lost — {reason}", author_id=author_id
            )
        except Exception:
            logger.warning("lost-reason note failed for deal %s", deal_id, exc_info=True)
    return get_deal(deal_id)


def _insert_housekeeping_note_cur(
    cur, entity_type: str, entity_id: int, message: str, author_id: int | None, now
) -> None:
    """Append an archive/restore audit note on the CALLER's cursor (#239), so it commits
    with the state change or not at all.

    A direct INSERT rather than chatter_service.add_note, following provenance_service
    .confirm: the parent row is already locked here, and a housekeeping row must not
    schedule a touch recompute. The text is built from scoring_service's housekeeping
    constants, which every touch/staleness read excludes."""
    cur.execute(
        "INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, author_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (entity_type, entity_id, message, now, author_id),
    )


def _archive_reason(reason: str | None, what: str) -> str:
    """The trimmed, capped reason for an archive transition; ValueError when blank."""
    text = (reason or "").strip()[:MAX_ARCHIVE_REASON]
    if not text:
        raise ValueError(
            f"A reason is required to archive {what} — ask why, then try again with one."
        )
    return text


def archive_deal(
    deal_id: int, archived: bool = True, *, reason: str | None = None,
    actor_id: int | None = None,
) -> dict | None:
    """Soft-archive (or restore) a deal. Nothing is deleted — archived_at is set,
    and every list/board/rollup/aggregate stops counting the deal (LIVE_PREDICATE).

    Archiving records WHO and WHY (#239): a non-blank ``reason`` is required, and the
    reason plus ``actor_id`` land on the row (``archived_by``/``archived_reason``, which
    the deal sheet's banner reads) AND in the notes thread as an "Archived — <reason>"
    note, in ONE transaction. Restoring needs no reason; it clears all three columns and
    writes a "Restored from archive." note. ``archived_by`` may still be NULL on an
    archived row — an archive from before #239, a deleted user, or an unattended turn.

    Idempotent on the locked pre-image: archiving an already-archived deal (reason or
    not) keeps the original timestamp, actor and reason and writes no second note, and
    restoring a live deal writes nothing.
    """
    # updated_at is deliberately NOT bumped. LAST_TOUCH_SQL treats updated_at as a
    # touch, so an archive→restore round-trip would silently reset the deal's staleness
    # clock and drop it out of get_stale_deals and the heartbeat's nudges until someone
    # logged a real interaction. archived_at IS the state change; nothing else moved.
    # The audit note is excluded from LAST_TOUCH_SQL for the same reason.
    now = _now()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT archived_at, contact_id FROM deals WHERE id = %s FOR UPDATE", (deal_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        was_archived, contact_id = row[0] is not None, row[1]
        if archived and not was_archived:
            # Checked only on a real transition, so a repeat archive stays a no-op.
            # Raising here rolls the transaction back before anything is written.
            text = _archive_reason(reason, "a deal")
            cur.execute(
                "UPDATE deals SET archived_at = %s, archived_by = %s, archived_reason = %s "
                "WHERE id = %s",
                (now, actor_id, text, deal_id),
            )
            _insert_housekeeping_note_cur(
                cur, "deal", deal_id, scoring_service.ARCHIVE_NOTE_PREFIX + text, actor_id, now
            )
        elif not archived and was_archived:
            cur.execute(
                "UPDATE deals SET archived_at = NULL, archived_by = NULL, "
                "archived_reason = NULL WHERE id = %s",
                (deal_id,),
            )
            _insert_housekeeping_note_cur(
                cur, "deal", deal_id, scoring_service.RESTORE_NOTE, actor_id, now
            )
    # #18: an archived deal leaves the contact's deal-linkage aggregate (and a restore
    # puts it back), so the linked contact's score inputs just changed; the deal's own
    # stored score also refreshes so a restore doesn't resurface a stale number.
    scoring_service.score_on_event(deal_ids=(deal_id,), contact_ids=(contact_id,))
    return get_deal(deal_id)


def merge_deals(
    target_deal_id: int, source_deal_id: int, *, actor_id: int | None = None
) -> dict:
    """Fold ``source`` into ``target`` and archive the source. Raises ValueError on
    a self-merge or a missing deal.

    Restorable by construction — the source is soft-archived, never deleted, and its
    own notes/history stay on it, so a wrong merge can be undone by un-archiving
    (the copied rows on the target are then the only cleanup).

    What moves vs. what is copied:
      * ``activity_log`` + ``todos`` are **repointed** — a dated interaction and an
        open follow-up belong to exactly one deal, and the surviving deal is the one
        that still has work to do. (Todos would additionally drop out of list_todos
        with the archived source; activity would not, since history is never swept.)
      * notes are **copied** (annotated with the source id), because the source keeps
        its own thread for the restore case.
      * custom fields are **gap-filled** — the target's own values always win; the
        source only fills keys the target left blank. Merging must never overwrite
        data on the deal being kept.

    Deliberately NOT touched: the target's standard columns (title/value/stage/…).
    A merge is a consolidation of *history*, not a silent edit of the surviving deal.

    Who and why (#239): the source records ``actor_id`` and "Merged into deal #T" as its
    archive actor/reason and gets an "Archived — Merged into deal #T" note; the target's
    "Merged deal #N" note carries the actor as its author. The merge IS the reason, so
    none is asked for. The source is always live here (an archived one is refused above).
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
            "SELECT id, title, archived_at, contact_id FROM deals WHERE id IN (%s, %s) "
            "ORDER BY id FOR UPDATE",
            (target_deal_id, source_deal_id),
        )
        rows = cur.fetchall()
        titles = {r[0]: r[1] for r in rows}
        archived = {r[0] for r in rows if r[2] is not None}
        contact_by_deal = {r[0]: r[3] for r in rows}
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
            "UPDATE todos SET deal_id = %s, updated_at = %s WHERE deal_id = %s",
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
        # The copies carry the message text only — #57 attachments are NOT duplicated onto
        # them. Deliberate: the source deal is archived rather than deleted, so its notes
        # keep their attachments and stay readable, and copying multi-MB blobs to gap-fill
        # a merge would double the storage for a second view of the same files.
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
            "INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, author_id) "
            "VALUES ('deal', %s, %s, %s, %s)",
            (target_deal_id,
             f'Merged deal #{source_deal_id} ("{titles[source_deal_id]}") into this deal.',
             now, actor_id),
        )
        merge_reason = f"Merged into deal #{target_deal_id}"
        cur.execute(
            "UPDATE deals SET archived_at = %s, archived_by = %s, archived_reason = %s, "
            "updated_at = %s WHERE id = %s",
            (now, actor_id, merge_reason, now, source_deal_id),
        )
        # AFTER the note-copy statement above, so this audit note stays on the source and
        # is not copied onto the target.
        _insert_housekeeping_note_cur(
            cur, "deal", source_deal_id,
            scoring_service.ARCHIVE_NOTE_PREFIX + merge_reason, actor_id, now,
        )
    # The target just absorbed the source's evidence, so its touch count is stale —
    # force_write because the merged-in notes can move the watermark either way.
    touch_count_service.schedule_recompute(target_deal_id, force_write=True)
    # #18: activity/todos were repointed and the source archived — both deals'
    # interaction factors and both linked contacts' deal-linkage aggregates changed.
    scoring_service.score_on_event(
        deal_ids=(target_deal_id, source_deal_id),
        contact_ids=(contact_by_deal[target_deal_id], contact_by_deal[source_deal_id]),
    )
    return get_deal(target_deal_id)


# ── Todos ─────────────────────────────────────────────────────────────────────

def create_todo(
    title: str, description: str = "", due_date: str = "",
    contact_id: int | None = None, deal_id: int | None = None,
    priority: str = "medium", owner_id: int | None = None,
    *,
    status: str = "next_action",
    star: bool = False,
    context: str = "",
    tags: list[str] | None = None,
    repeat: str = "",
    auto_star_on_due: bool = False,
    project_id: int | None = None,
    source: str = "ui",
) -> dict:
    """Create a todo. The GTD keyword-only fields (#70) default to the normal-mode
    shape, so every existing caller is unchanged.

    `status` defaults to 'next_action', NOT 'inbox': a todo created from the CRM form
    or by crm_create_todo is already clarified work. Only a GTD *capture* means "I
    haven't thought about this yet", and those pass status='inbox' explicitly.

    `completed` is derived from `status` in the INSERT rather than taken as a
    parameter — the migration's coherence CHECK makes any other arrangement a
    constraint violation.
    """
    if priority not in TODO_PRIORITIES:
        priority = "medium"
    gtd_common.validate_status(status)
    if source not in gtd_common.TODO_SOURCES:
        raise gtd_common.ValidationError(
            f"Invalid source '{source}'. Valid: {', '.join(gtd_common.TODO_SOURCES)}"
        )
    row = pg_fetchone(
        """INSERT INTO todos (title, description, due_date, contact_id, deal_id, priority,
                              owner_id, status, star, context, tags, repeat, auto_star_on_due,
                              project_id, source, completed, completed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                   CASE WHEN %s = 'done' THEN 1 ELSE 0 END,
                   CASE WHEN %s = 'done' THEN now() END)
           RETURNING id""",
        (
            gtd_common.validate_title(title),
            gtd_common.validate_notes(description),
            gtd_common.validate_due(due_date),
            contact_id, deal_id, priority,
            owner_id,
            status,
            bool(star),
            gtd_common.validate_short(context, "context"),
            json.dumps(gtd_common.validate_tags(tags)),
            gtd_common.validate_repeat(repeat),
            bool(auto_star_on_due),
            project_id,
            source,
            status,
            status,
        ),
    )
    return get_todo(row["id"])


def get_todo(todo_id: int) -> dict | None:
    # Joined exactly like list_todos, because create_todo / update_todo / complete_todo all
    # return this row and the Todos list patches itself from those bodies (#77). Without the
    # joins a saved todo would lose its contact/deal label in the list; worse, re-linking a
    # todo to a different contact would leave the OLD name showing.
    return pg_fetchone(
        """SELECT t.*, c.name AS contact_name, d.title AS deal_title
           FROM todos t
           LEFT JOIN contacts c ON t.contact_id = c.id
           LEFT JOIN deals d ON t.deal_id = d.id
           WHERE t.id = %s""",
        (todo_id,),
    )


def list_todos(
    contact_id: int | None = None, deal_id: int | None = None,
    completed: bool | None = None, due_before: str | None = None,
    priority: str | None = None, limit: int = 50,
    owner_id: int | str | None = None, after_id: int | None = None,
    sort: str = "due",
) -> list[dict]:
    _check_assembly_cursor(after_id, sort)
    conditions = []
    params: list = []
    # On a todo, owner_id reads as "assigned to" (issue #60).
    owner_sql = owner_condition("t.owner_id", owner_id, params)
    if owner_sql:
        conditions.append(owner_sql)
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
    # A todo on an archived deal follows it out of view: archiving is the user's "stop
    # bothering me about this" gesture, and the heartbeat is told to nag about overdue
    # todos. Standalone todos (deal_id NULL) are untouched. Activity is deliberately NOT
    # swept the same way — see get_activity_log.
    conditions.append(f"(t.deal_id IS NULL OR {LIVE_PREDICATE_D})")  # see LIVE_TODO_PREDICATE
    conditions.append(NOT_DROPPED_TODO_T)  # see NOT_DROPPED_TODO
    # Window, not filter — see list_contacts. This list has no COUNT to disagree with.
    if after_id is not None:
        conditions.append("t.id > %s")
        params.append(after_id)
    where = f"WHERE {' AND '.join(conditions)}"
    params.append(limit)
    return pg_fetchall(
        f"""SELECT t.*, c.name AS contact_name, d.title AS deal_title
            FROM todos t
            LEFT JOIN contacts c ON t.contact_id = c.id
            LEFT JOIN deals d ON t.deal_id = d.id
            {where} ORDER BY {_TODO_SORTS.get(sort, _TODO_SORTS["due"])} LIMIT %s""",
        params,
    )


def _resolve_todo_project_id_cur(cur, name: str) -> int:
    """Id of the GTD project named `name`, creating it (active) if missing.

    ON CONFLICT DO NOTHING + re-SELECT so two concurrent first uses of the same new
    project name both succeed instead of one dying on the unique index. Matching is
    case-insensitive, so `#Groceries` and `#groceries` are one project.
    """
    cur.execute(
        "INSERT INTO todo_projects (name) VALUES (%s) ON CONFLICT (lower(name)) DO NOTHING",
        (name,),
    )
    cur.execute("SELECT id FROM todo_projects WHERE lower(name) = lower(%s)", (name,))
    return cur.fetchone()[0]


def _check_todo_project_id_cur(cur, project_id) -> int | None:
    if project_id in (None, "", 0):
        return None
    try:
        # bulk_update's `fields` is an untyped dict, so a non-numeric project_id
        # reaches here — it must be a 400, not a 500.
        pid = int(project_id)
    except (TypeError, ValueError):
        raise gtd_common.ValidationError(f"project_id must be an integer, got {project_id!r}")
    cur.execute("SELECT id FROM todo_projects WHERE id = %s", (pid,))
    if not cur.fetchone():
        raise gtd_common.ValidationError(f"Project id not found: {pid}")
    return pid


# Fields a todo update may set. The GTD half (#70) is inert in normal mode — the UI
# never sends those keys — but both modes write through this ONE allow-list so the
# two surfaces can never drift into different validation rules.
_TODO_UPDATE_FIELDS = {
    "title", "description", "due_date", "contact_id", "deal_id", "priority", "completed",
    "owner_id",
    "status", "star", "context", "tags", "repeat", "auto_star_on_due", "project_id",
    "project",
}


def _apply_todo_update_cur(cur, todo_id: int, fields: dict) -> bool:
    """Apply a validated field update to one todo inside the caller's transaction.

    THE single write path for `todos.completed` / `todos.status`. Locks the row
    (FOR UPDATE) so a completion transition is detected exactly once, then spawns the
    next occurrence of a repeating todo on that transition. Returns False if the todo
    does not exist.

    `completed` and `status` are two views of one fact (the migration enforces it with
    a CHECK), so this function always writes them together: `completed=1` ⇒
    status='done'; `completed=0` on a done row ⇒ back to 'next_action'. A caller may
    send either spelling — normal mode sends `completed`, GTD sends `status`.
    """
    cur.execute("SELECT status FROM todos WHERE id = %s FOR UPDATE", (todo_id,))
    row = cur.fetchone()
    if not row:
        return False
    prior_status = row[0]

    # Normalize `completed` into the status vocabulary BEFORE building the UPDATE, so
    # there is exactly one code path deciding the done-transition below. An explicit
    # `status` always wins — a caller that sends both means the status.
    fields = dict(fields)
    if "completed" in fields:
        completed = bool(fields.pop("completed"))
        if "status" not in fields:
            if completed:
                fields["status"] = "done"
            elif prior_status == "done":
                # Un-completing returns the todo to actionable. 'next_action' rather
                # than 'inbox': it was real work before it was finished.
                fields["status"] = "next_action"

    sets: list[str] = []
    params: list = []
    if "title" in fields:
        sets.append("title = %s")
        params.append(gtd_common.validate_title(fields["title"]))
    if "description" in fields:
        sets.append("description = %s")
        params.append(gtd_common.validate_notes(fields["description"]))
    if "due_date" in fields:
        sets.append("due_date = %s")
        params.append(gtd_common.validate_due(fields["due_date"]))
    if "contact_id" in fields:
        sets.append("contact_id = %s")
        params.append(fields["contact_id"])
    if "deal_id" in fields:
        sets.append("deal_id = %s")
        params.append(fields["deal_id"])
    if "owner_id" in fields:
        # Nullable on purpose (issue #60): an explicit None is "unassigned", which is
        # how an owner can be cleared at all.
        sets.append("owner_id = %s")
        params.append(fields["owner_id"])
    if "priority" in fields:
        priority = fields["priority"]
        sets.append("priority = %s")
        params.append(priority if priority in TODO_PRIORITIES else "medium")
    if "project_id" in fields:
        sets.append("project_id = %s")
        params.append(_check_todo_project_id_cur(cur, fields["project_id"]))
    elif "project" in fields:
        name = gtd_common.validate_short(fields["project"], "project")
        sets.append("project_id = %s")
        params.append(_resolve_todo_project_id_cur(cur, name) if name else None)
    if "context" in fields:
        sets.append("context = %s")
        params.append(gtd_common.validate_short(fields["context"], "context"))
    if "tags" in fields:
        sets.append("tags = %s::jsonb")
        params.append(json.dumps(gtd_common.validate_tags(fields["tags"])))
    if "star" in fields:
        sets.append("star = %s")
        params.append(bool(fields["star"]))
    if "repeat" in fields:
        sets.append("repeat = %s")
        params.append(gtd_common.validate_repeat(fields["repeat"]))
    if "auto_star_on_due" in fields:
        sets.append("auto_star_on_due = %s")
        params.append(bool(fields["auto_star_on_due"]))

    just_completed = False
    if "status" in fields:
        status = gtd_common.validate_status(fields["status"])
        sets.append("status = %s")
        params.append(status)
        # `completed` is derived, never taken from the caller — this is the pairing
        # the CHECK constraint verifies.
        sets.append("completed = %s")
        params.append(1 if status == "done" else 0)
        if status == "done":
            if prior_status != "done":
                sets.append("completed_at = now()")
                just_completed = True
        else:
            sets.append("completed_at = NULL")

    if not sets:
        return True
    sets.append("updated_at = %s")
    params.append(_now())
    cur.execute(
        f"UPDATE todos SET {', '.join(sets)} WHERE id = %s RETURNING *",
        (*params, todo_id),
    )
    # row_to_dict reads cur.description, so the row MUST be converted before the next
    # execute() on this cursor — the spawn below issues one.
    updated = row_to_dict(cur, cur.fetchone())
    if just_completed:
        _spawn_next_todo_occurrence_cur(cur, updated, prior_status)
    return True


def _spawn_next_todo_occurrence_cur(cur, todo: dict, prior_status: str) -> None:
    """Completing a repeating todo creates its next occurrence: same fields, due date
    advanced, star cleared (today's priority doesn't carry over).

    Reads the POST-update row, so edits saved together with the completion carry — in
    particular, clearing `repeat` in the same write must NOT spawn.

    The one exception to the cleared star is the opt-in `auto_star_on_due`: when the
    spawned occurrence is due TODAY (i.e. this one was completed exactly one interval
    late) it comes back already starred instead of waiting to be re-starred by hand.
    Two or more intervals late re-anchors past today and does not star.
    """
    if not todo.get("repeat"):
        return
    # A repeating todo that was dropped-then-completed has no sensible prior state to
    # return to; anything else keeps the status it was worked in.
    status = prior_status if prior_status not in gtd_common.FINISHED_STATUSES else "next_action"
    tags = todo.get("tags")
    auto_star = bool(todo.get("auto_star_on_due"))
    # ONE clock read, shared with the comparison below: two reads could straddle
    # midnight and disagree about whether the spawn is due today.
    today = today_local()
    spawn_due = gtd_common.next_due(todo["repeat"], todo.get("due_date"), today=today)
    cur.execute(
        """INSERT INTO todos (title, description, due_date, contact_id, deal_id, priority,
                              owner_id, status, star, context, tags, repeat, auto_star_on_due,
                              project_id, source, completed)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, 0)""",
        (
            todo["title"], todo["description"], spawn_due,
            todo["contact_id"], todo["deal_id"], todo["priority"],
            # The assignee carries to the next occurrence (issue #60): a repeating todo
            # someone owns must not come back unassigned.
            todo["owner_id"],
            status,
            auto_star and spawn_due == today.isoformat(),
            todo["context"],
            tags if isinstance(tags, str) else json.dumps(tags or []),
            todo["repeat"], auto_star, todo["project_id"], todo["source"],
        ),
    )


def complete_todo(todo_id: int) -> dict | None:
    """Mark a todo done. A thin adapter over the shared transition, so completing from
    the plain normal-mode checkbox spawns a repeating todo's next occurrence too."""
    return update_todo(todo_id, status="done")


def update_todo(todo_id: int, **fields) -> dict | None:
    filtered = {k: v for k, v in fields.items() if k in _TODO_UPDATE_FIELDS}
    if not filtered:
        return get_todo(todo_id)
    with get_connection() as conn:
        cur = conn.cursor()
        found = _apply_todo_update_cur(cur, todo_id, filtered)
    return get_todo(todo_id) if found else None


def delete_todo(todo_id: int) -> bool:
    return pg_execute("DELETE FROM todos WHERE id = %s", (todo_id,)) > 0


# ── Activity log ──────────────────────────────────────────────────────────────

def log_activity(activity: str, note: str = "", contact_id: int | None = None,
                 deal_id: int | None = None, actor_id: int | None = None) -> dict:
    """Record something that happened. ``actor_id`` is WHO DID IT (issue #60).

    Deliberately distinct from ownership: per-rep activity credits the person who did
    the work, even on a colleague's record. NULL is a real and supported value — the
    Gmail touch scan and the assistant's background turn both log activity that no
    human performed, and Phase A does not thread identity into tool executors (that
    is Phase B). Those rows roll up as "Unattributed" rather than being credited to
    whoever happens to own the record.
    """
    row = pg_fetchone(
        """INSERT INTO activity_log (activity, note, contact_id, deal_id, actor_id)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (activity, note, contact_id, deal_id, actor_id),
    )
    result = pg_fetchone("SELECT * FROM activity_log WHERE id = %s", (row["id"],)) or {}
    # An activity on a deal is fresh touch-count evidence — queue a recompute (O(1), never
    # raises; the insert has already committed via the pg_fetchone helpers).
    if deal_id:
        touch_count_service.schedule_recompute(deal_id)
    # #18: an activity changes the engagement/recency of its deal and/or contact.
    scoring_service.score_on_event(deal_ids=(deal_id,), contact_ids=(contact_id,))
    return result


def get_activity_log(contact_id: int | None = None, deal_id: int | None = None, limit: int = 20) -> list[dict]:
    """Activity rows, optionally scoped to a contact or deal.

    Archived deals are deliberately NOT filtered here, unlike list_todos: an activity
    row is the record of something that actually happened, not an outstanding work
    item, and reviewing an archived deal's history is exactly what you need before
    deciding to restore it. Passing deal_id for an archived deal must keep working.
    """
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
            {where} ORDER BY a.created_at DESC, a.id DESC LIMIT %s""",
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
    # RETURNING the links so #18 can rescore the affected deal/contact (their
    # interaction count / recency changed).
    row = pg_fetchone(
        "DELETE FROM activity_log WHERE id = %s RETURNING contact_id, deal_id", (activity_id,)
    )
    if row is None:
        return False
    scoring_service.score_on_event(deal_ids=(row["deal_id"],), contact_ids=(row["contact_id"],))
    return True


# ── Analytics ─────────────────────────────────────────────────────────────────

# ── Top deals ranking (issue #240) ─────────────────────────────────────────────
#
# "Qualified or later" is every open stage except `lead`. The blueprint keys this on a
# stage win-probability threshold because its stages are a user-editable table; ours are
# constants, so naming the one excluded stage is the whole rule.
TOP_DEALS_QUALIFIED_D = "d.stage <> 'lead'"

# An ATTENTION score, deliberately not expected value:
#     max(value, 0) × (0.4 + 0.6 × clamp(probability / 100, 0, 1))
# Value is the linear base and probability swings it by at most 60%, so a big likely deal
# beats a small near-certain one (plain value × probability ranks $150K@99% above
# $500K@20%), and a $0 or negative deal scores 0. Both columns are NOT NULL. Computed in
# SQL so the LIMIT applies after ranking — the score is not monotonic in value, so a
# value-ordered prefix would drop the deals it exists to surface.
TOP_DEAL_SCORE_SQL = (
    "(GREATEST(d.value, 0) * (0.4 + 0.6 * LEAST(GREATEST(d.probability, 0), 100) / 100.0))"
)


def get_dashboard_stats() -> dict:
    total_row = pg_fetchone("SELECT COUNT(*) AS cnt FROM contacts")
    total_contacts = total_row["cnt"] if total_row else 0

    contacts_by_status = {}
    for row in pg_fetchall("SELECT status, COUNT(*) AS count FROM contacts GROUP BY status"):
        contacts_by_status[row["status"]] = row["count"]

    # Company count for the dashboard stat row (issue #76). Unfiltered, matching
    # total_contacts: companies carry a status but no soft-archive, and the tile is a
    # "how much is in my CRM" headline, not a pipeline aggregate.
    companies_row = pg_fetchone("SELECT COUNT(*) AS cnt FROM companies")
    total_companies = companies_row["cnt"] if companies_row else 0

    pipeline_by_stage = pg_fetchall(
        f"""SELECT stage, COUNT(*) AS count, COALESCE(SUM(value), 0) AS total_value
            FROM deals WHERE {LIVE_PREDICATE} GROUP BY stage"""
    )
    total_pipeline_value = sum(
        r["total_value"] for r in pipeline_by_stage if r["stage"] not in ("won", "lost")
    )

    # Overdue = incomplete todos whose due date is strictly before TODAY. Date-only
    # TEXT comparison: matches the Todos page's client-side rule (a todo due today is
    # NOT overdue) and can never cast-error on a malformed row (unlike ::date).
    #
    # TODAY is the CONFIGURED-TIMEZONE day (#130), not the UTC one it used to be. A due
    # date carries the user's local calendar intent, so a UTC day marked work overdue
    # hours early every evening west of Greenwich — and this number renders inches from
    # the Today panel, which reads the local day. One screen cannot hold two todays.
    today = gtd_common.today_local_str()
    overdue_row = pg_fetchone(
        "SELECT COUNT(*) AS cnt FROM todos WHERE completed = 0 AND due_date != '' "
        f"AND due_date < %s AND {LIVE_TODO_PREDICATE} AND {NOT_DROPPED_TODO}",
        (today,),
    )
    overdue_todos = overdue_row["cnt"] if overdue_row else 0

    pending_row = pg_fetchone(
        f"SELECT COUNT(*) AS cnt FROM todos WHERE completed = 0 AND {LIVE_TODO_PREDICATE} "
        f"AND {NOT_DROPPED_TODO}"
    )
    pending_todos = pending_row["cnt"] if pending_row else 0

    recent_activity = get_activity_log(limit=10)

    # Top deals (#240, port of the blueprint's #603): open deals past the lead stage,
    # ranked by TOP_DEAL_SCORE_SQL rather than raw value, so a $1M lead that will never
    # close cannot sit above the $80K proposal closing this month.
    # company_name is joined for the same reason contact_name is: the dashboard hands these
    # rows straight to the deal sheet and on to DealForm, whose link pickers render the
    # NAME they arrive with (issue #123). Without the join the row carries a company_id and
    # no name, and the Company field renders blank — reading as "no company" on a deal that
    # has one, which is the exact hazard those pickers replaced a capped <select> to end.
    top_deals = pg_fetchall(
        f"""SELECT d.*, c.name AS contact_name, co.name AS company_name
            FROM deals d
            LEFT JOIN contacts c ON d.contact_id = c.id
            LEFT JOIN companies co ON d.company_id = co.id
            WHERE {OPEN_PREDICATE_D} AND {TOP_DEALS_QUALIFIED_D} AND {LIVE_PREDICATE_D}
            -- Scores tie constantly (round values, default probabilities), so without the
            -- trailing d.id the five deals can differ between two loads (issue #58).
            ORDER BY {TOP_DEAL_SCORE_SQL} DESC, d.updated_at DESC, d.id DESC LIMIT 5"""
    )

    return {
        "total_contacts": total_contacts,
        "total_companies": total_companies,
        "contacts_by_status": contacts_by_status,
        "pipeline_by_stage": pipeline_by_stage,
        "total_pipeline_value": total_pipeline_value,
        "overdue_todos": overdue_todos,
        "pending_todos": pending_todos,
        "recent_activity": recent_activity,
        "top_deals": top_deals,
    }


# ── Weekly Touches (issue #76) ────────────────────────────────────────────────
#
# Window resolution mirrors cake_os dashboard_service._resolve_touch_window so a
# later port diffs cleanly, but resolves UTC calendar days rather than Central, and
# stays UTC even though #130 moved the "today" decisions in this module onto the
# configured timezone. Those answer "is this todo overdue RIGHT NOW", which is a
# question about the user's calendar intent; this resolves a LABELLED absolute window
# the caller can name explicitly, where a fixed reference is the honest one. In UTC
# there is also no DST boundary, so the inclusive end-day bound is a plain +1 day
# instead of the blueprint's add-in-CT-then-convert dance.
#
# simplification: a UTC calendar day is not the viewer's calendar day, so a user
# several hours off UTC sees a window shifted by their offset. The UI labels the
# control "UTC" so the number is honest rather than surprising. Upgrade path if that
# stops being good enough: resolve the window against the viewer's local midnight
# instead of UTC — deferred because every other day-boundary in this app is already
# UTC, and a per-viewer window here would disagree with the overdue-todo count above it.
#
# That upgrade path used to name the blueprint's absolute-instant (ws/we) form as the
# shape to copy. #146 built it, and it is the wrong shape for the ROLLING window:
# membership below is "this deal's CURRENT most recent touch falls in the window", so
# freezing a now-relative upper bound silently drops any deal touched since — including
# one the user touches from the page that window is displaying.
#
# The rule is about the BOUND, not about the format: any bound defined relative to
# request time is re-resolved on every request. A fixed calendar range is a historical
# fact and is safe to pass between surfaces in either form — as days, or as the instants
# they resolve to — which is why the drill-down happily takes `start`/`end` today.
_TOUCH_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Rows shown under each rep, PER REP since #146 (it was one global cap while the card
# had a single implicit rep). The card is a KPI, not a deal list — the per-deal evidence
# drill-down is issue #56, and the uncapped per-rep list is the #146 detail page.
WEEKLY_TOUCHES_LIMIT = 10

# The drill-down is "uncapped" relative to the card's ten, not literally unbounded: a rep
# with a very large book would otherwise rank and serialize every open deal they own on one
# page load. This ceiling is high enough that no real book reaches it, and when one does the
# page SAYS so rather than silently showing a prefix — a hidden cap on a page whose whole
# contract is "the full list" would be the dishonest version. Pagination is the upgrade path
# if a book ever genuinely exceeds it.
WEEKLY_TOUCHES_DETAIL_MAX = 500

# The bucket name the drill-down URL uses for deals with no owner. `deals.owner_id` is
# nullable forever (#60) — the Gmail scan, the assistant and the CSV importer all
# legitimately produce it — so NULL is a bucket to name, not a row to drop.
TOUCH_OWNER_UNASSIGNED = UNASSIGNED

# `users.id` is a 32-bit SERIAL and `deals.owner_id` a 32-bit INTEGER, so an id past this
# reaches Postgres as an out-of-range comparison and surfaces as a 500 on what is really
# malformed user input.
_MAX_USER_ID = 2147483647


def _parse_touch_date(value: str) -> datetime:
    """Strict YYYY-MM-DD → that day's UTC midnight.

    The regex rejects shapes ``strptime`` would otherwise accept or coerce
    (``2026-6-1``, ``20260601``), and ``strptime`` itself rejects impossible dates
    (``2026-02-30``). Either way a malformed filter raises — surfacing as a 400 —
    rather than silently resolving to a window the user never asked for.
    """
    if not _TOUCH_DATE_RE.match(value or ""):
        raise ValueError(f"Invalid date '{value}'; expected YYYY-MM-DD")
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _resolve_touch_window(
    start: str | None, end: str | None
) -> tuple[datetime, datetime, str, bool]:
    """Resolve the [window_start, window_end) UTC bounds, display label, custom flag.

    Neither bound given → the rolling last 7 days. Both given → calendar days,
    inclusive of the whole end day, so the exclusive bound is the NEXT day's midnight.
    Exactly one given is an error, not a half-open range: a blank/whitespace param is
    a cleared filter (treated as absent), so "one present" can only mean the caller
    meant a custom range and lost half of it.
    """
    start = (start or "").strip() or None
    end = (end or "").strip() or None
    if start is None and end is None:
        now = datetime.now(timezone.utc)
        return now - timedelta(days=7), now, "Last 7 days", False
    if start is None or end is None:
        raise ValueError("Custom range requires both start and end dates")
    start_dt = _parse_touch_date(start)
    end_dt = _parse_touch_date(end)
    if end_dt < start_dt:
        raise ValueError("end date must be on or after start date")
    try:
        window_end = end_dt + timedelta(days=1)
    except OverflowError:
        # datetime.max is 9999-12-31, and the date picker's year spinner reaches it.
        # OverflowError is NOT a ValueError, so without this it escapes the router's
        # handler as an unhandled 500 on ordinary user input.
        raise ValueError(f"Invalid date '{end}'; out of range")
    return start_dt, window_end, f"{start} – {end}", True


def parse_touch_owner(raw: str | None) -> int | None:
    """The detail route's owner: a user id, or the literal ``unassigned`` for the NULL bucket.

    A REQUIRED param carrying a literal for NULL, deliberately unlike ``/dashboard/today``'s
    ``owner_id`` (where absent means everyone): the drill-down is always exactly ONE bucket,
    and the unowned bucket is one of them, so "absent" is never a state here.

    ASCII digits only — ``str.isdigit()`` accepts superscripts, which ``int()`` then rejects
    with an unhandled 500 rather than the 400 this is. The length bound matters for the
    same reason: past 4300 digits ``int()`` raises its own ValueError about
    ``sys.set_int_max_str_digits``, which the router would hand back to the caller as the
    400 detail — a Python implementation detail in place of a domain message. Ten digits
    covers every 32-bit id; the range check below rejects the rest.
    """
    value = (raw or "").strip()
    if value == TOUCH_OWNER_UNASSIGNED:
        return None
    if re.fullmatch(r"[0-9]{1,10}", value):
        owner_id = int(value)
        if 1 <= owner_id <= _MAX_USER_ID:
            return owner_id
    raise ValueError("owner must be a user id or 'unassigned'")


def _touch_owner_scope(owner_scoped: bool, owner_id: int | None) -> tuple[str, list]:
    """WHERE fragment + params for one owner bucket.

    An explicit flag rather than "owner_id=None means everyone": here None IS a bucket
    (Unassigned), so the two states have to stay distinguishable.
    """
    if not owner_scoped:
        return "", []
    if owner_id is None:
        return " AND d.owner_id IS NULL", []
    return " AND d.owner_id = %s", [owner_id]


def _touch_query(cur, sql: str, params: list) -> list[dict]:
    """Run one weekly-touches read, on a caller-supplied cursor or a pooled connection.

    The two reads have to be able to share ONE snapshot (see ``get_weekly_touch_detail``),
    and ``pg_fetchall`` takes a fresh connection per call by definition. ``row_to_dict`` is
    public for exactly this — a caller managing its own cursor inside a transaction.

    Rows are converted to dicts BEFORE this returns, which is what makes reusing one cursor
    for a second statement safe: ``row_to_dict`` reads ``cursor.description``, and the next
    ``execute`` replaces it.
    """
    if cur is None:
        return pg_fetchall(sql, params)
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [row_to_dict(cur, r) for r in rows]


def _touch_rep_rows(
    window_start: datetime,
    window_end: datetime,
    *,
    owner_scoped: bool,
    owner_id: int | None,
    cur=None,
) -> list[dict]:
    """One row per owner bucket: open deals, computed counts, deals touched in the window.

    This query alone defines the rep universe AND every total the payload reports. The
    universe is every owner of a live OPEN deal — a rep who touched nothing still gets a
    row, because seeing who did nothing is the point of a weekly accountability pull — plus
    every owner of a deal WON inside the window (issue #179), so a rep whose only deal
    closed this week is credited rather than erased. The ``w.in_window`` on the won branch
    is load-bearing: without it, a win outside the window — last month, or next week while
    you view this week — would keep its owner on the roster as a permanent zero row. The
    NULL owner is a bucket rather than an exclusion, which is what makes the totals the sums
    of the buckets.

    ``open_deals`` and ``touched_deals`` are two independent facts, NOT a ratio: a deal won
    this week was touched but is no longer open, so touches can legitimately exceed open
    deals, and the UI renders them side by side rather than as "N of M". ``open_deals``
    therefore carries its own ``OPEN_PREDICATE_D`` filter — it must keep meaning "currently
    open" now that the row set is wider than that.

    The window predicate is WRITTEN once, in its own LATERAL, and read twice (the roster
    clause and the ``touched_deals`` FILTER) — two textual copies of a predicate that must
    agree are exactly what ``_touched_deal_rows`` refuses below for the same reason.

    ``computed_deals`` sits OUTSIDE the window FILTER on purpose (#76's reasoning,
    unchanged): it counts non-NULL ``ai_touch_count`` across the whole row set, because it
    is the has-a-provider-ever-run gate. Scoping it to the window would turn "no provider
    configured" into "no touches this week" and hide the card during a quiet week on a fully
    configured install. Scoping it to OPEN deals only would be a new bug of the same shape:
    a rep who won their only deal this week would drive it to 0 and hide the very card that
    exists to credit that win. The zero-keys guarantee survives the widening — with no
    provider the worker never runs, so every count is NULL on won and open rows alike and
    the gate still reads 0 — and a won deal keeps the count it earned while open
    (touch_count_service skips closed deals).

    LEFT JOIN, never INNER: an INNER JOIN would drop the unowned bucket. No ORDER BY — rep
    order is the shaper's, so there is one definition of it and it is testable with no DB.

    ``cur`` lets a caller run this on its own cursor so it shares one snapshot with the
    rows query (the drill-down does); omitted, it takes a pooled connection of its own.
    """
    owner_sql, owner_params = _touch_owner_scope(owner_scoped, owner_id)
    return _touch_query(
        cur,
        f"""SELECT d.owner_id AS user_id, u.name, u.email,
                   COUNT(*) FILTER (WHERE {OPEN_PREDICATE_D}) AS open_deals,
                   COUNT(d.ai_touch_count) AS computed_deals,
                   COUNT(*) FILTER (WHERE w.in_window) AS touched_deals
            FROM deals d
            JOIN LATERAL (SELECT {TOUCH_AT_SQL} AS touch_at) t ON TRUE
            JOIN LATERAL (SELECT t.touch_at >= %s AND t.touch_at < %s
                             AND t.touch_at <> d.created_at AS in_window) w ON TRUE
            LEFT JOIN users u ON u.id = d.owner_id
            WHERE {LIVE_PREDICATE_D}
              AND ({OPEN_PREDICATE_D} OR (d.stage = 'won' AND w.in_window)){owner_sql}
            GROUP BY d.owner_id, u.name, u.email""",
        [window_start, window_end, *owner_params],
    )


def _touched_deal_rows(
    window_start: datetime,
    window_end: datetime,
    *,
    owner_scoped: bool,
    owner_id: int | None,
    per_rep_limit: int,
    cur=None,
) -> list[dict]:
    """The touched deals themselves, ranked and capped PER OWNER (not globally).

    The window filter lives in the inner query and the cap in the outer one, so ``rn`` ranks
    only deals that actually count — capping before filtering would silently return fewer
    than the limit. ``PARTITION BY d.owner_id`` puts every unowned deal in one partition,
    which is exactly the Unassigned bucket.

    A won deal is a member iff its WIN falls in the window — the same ``TOUCH_AT_SQL``
    instant the rep query counts by (issue #179), so a rep's number and the rows beneath it
    are one definition rather than two. ``touched_at`` is that instant, so the drill-down
    dates a won deal by its close rather than by a note typed on it afterwards. Unlike the
    rep query this one reads the window predicate ONCE, so it stays in the WHERE clause and
    needs no second LATERAL.

    A global ``LIMIT`` (what #76 had, when the card had one implicit rep) would leave rep
    rows showing a count with no rows beneath them and make the truncation line lie. Both
    surfaces share this one SQL string and differ only in the cap they pass — the card's
    ten, the drill-down's much larger ceiling — because two strings that have to agree
    about what a touched deal is would eventually stop agreeing.

    ``cur`` shares the caller's snapshot, exactly as in ``_touch_rep_rows``.
    """
    owner_sql, owner_params = _touch_owner_scope(owner_scoped, owner_id)
    return _touch_query(
        cur,
        f"""SELECT id, title, value, stage, owner_id,
                   touch_count, touched_at, contact_name, company_name
            FROM (
                SELECT d.id, d.title, d.value, d.stage, d.owner_id,
                       d.ai_touch_count AS touch_count,
                       t.touch_at AS touched_at,
                       c.name AS contact_name, co.name AS company_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY d.owner_id
                           ORDER BY d.ai_touch_count DESC NULLS LAST, d.id DESC
                       ) AS rn
                FROM deals d
                JOIN LATERAL (SELECT {TOUCH_AT_SQL} AS touch_at) t ON TRUE
                LEFT JOIN contacts c ON d.contact_id = c.id
                LEFT JOIN companies co ON d.company_id = co.id
                WHERE {LIVE_PREDICATE_D} AND ({OPEN_PREDICATE_D} OR d.stage = 'won')
                  AND t.touch_at >= %s AND t.touch_at < %s
                  AND t.touch_at <> d.created_at{owner_sql}
            ) ranked
            WHERE rn <= %s
            ORDER BY owner_id NULLS LAST, rn""",
        [window_start, window_end, *owner_params, per_rep_limit],
    )


def _touch_snapshot_reads(
    window_start: datetime,
    window_end: datetime,
    *,
    owner_scoped: bool,
    owner_id: int | None,
    per_rep_limit: int,
) -> tuple[list[dict], list[dict]]:
    """Both weekly-touches reads, on ONE snapshot.

    Every surface here puts a count and the rows behind it on the same screen, so two
    snapshots are a way to render a contradiction: a rep row with more deals under it than
    its own number admits, or — if a bucket vanished between the reads — every row dropped
    and 0 reported for a rep who has them. `REPEATABLE READ` makes all of those
    unrepresentable rather than merely unlikely. (Touches exceeding open deals is NOT one of
    those contradictions since #179 — a deal won this week was touched but is no longer
    open — which is why the two are rendered as separate facts.)

    It costs one pooled connection held across two reads instead of two taken in turn,
    which is the same total work; `core.postgres` bounds concurrency with a semaphore
    either way. `SET TRANSACTION` must be the first statement of the transaction, which is
    why it is issued before either read rather than inside the builders.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rep_rows = _touch_rep_rows(
            window_start, window_end,
            owner_scoped=owner_scoped, owner_id=owner_id, cur=cur,
        )
        deal_rows = _touched_deal_rows(
            window_start, window_end,
            owner_scoped=owner_scoped, owner_id=owner_id,
            per_rep_limit=per_rep_limit, cur=cur,
        )
    return rep_rows, deal_rows


def _shape_touch_reps(rep_rows: list[dict], deal_rows: list[dict]) -> list[dict]:
    """Attach ranked deal rows to their owner bucket. Pure — no DB, so tests reach all of it.

    The rep universe and every total come from ``rep_rows`` alone, so a headline is always
    internally consistent with the query that produced it — and because both callers read
    through ``_touch_snapshot_reads``, the rows beneath it describe the same instant.

    That makes the orphan branch below unreachable in production: a deal row whose owner has
    no aggregate bucket would need the two reads to disagree, which one snapshot forbids. It
    is kept because this is a PURE function over whatever rows it is handed, so a partial
    fixture should fail an assertion rather than a ``KeyError`` — and if the invariant above
    ever breaks, a dropped row and a log line beat a bucket with no counts rendering
    "0 of 0 touched" above real deals.
    """
    slots: dict[int | None, dict] = {}
    for row in rep_rows:
        user_id = row.get("user_id")
        user = None
        if user_id is not None:
            user = {"id": user_id, "name": row.get("name"), "email": row.get("email")}
        slots[user_id] = {
            "user_id": user_id,
            # One resolver, shared with the Team settings list: name → email → "User N",
            # and "Unassigned" for the NULL bucket. Deliberately NOT _shape_per_rep's
            # "Unattributed", which is the AUTHORSHIP word — this column is ownership.
            "name": users_service.display_name(user),
            # `or 0`, never .get(k, 0): a SQL NULL makes the key present-but-None, so the
            # default would never fire and None would reach the UI.
            "open_deals": int(row.get("open_deals") or 0),
            "touches": int(row.get("touched_deals") or 0),
            "deals": [],
        }

    for deal in deal_rows:
        slot = slots.get(deal.get("owner_id"))
        if slot is None:
            logger.warning(
                "weekly touches: dropping deal %s — owner %s has no aggregate bucket, "
                "which should be unreachable under the shared snapshot",
                deal.get("id"), deal.get("owner_id"),
            )
            continue
        # Appended in the query's rank order and never re-sorted here: that ORDER BY is what
        # the per-rep cap was computed against, so a second sort would show a different ten.
        slot["deals"].append(deal)

    # Busiest first on the number the card compares reps by, then pipeline size, then name
    # for a stable order between equals. Unassigned sinks last so a bucket that is nobody
    # never leads a table of people (the _shape_per_rep rule).
    return sorted(
        slots.values(),
        key=lambda s: (
            s["user_id"] is None,
            -s["touches"],
            -s["open_deals"],
            s["name"].casefold(),
            s["user_id"] or 0,
        ),
    )


def get_weekly_touches(start: str | None = None, end: str | None = None) -> dict:
    """Deals touched in the window, grouped per owner, keyed off #16's AI touch counts.

    #76 shipped this per DEAL because CakeCRM was single-user, keeping the blueprint's
    envelope so that "a later multi-user port is a re-grouping". #60 landed
    ``deals.owner_id`` and #146 is that re-grouping: ``reps`` now stands where the blueprint
    had it, each rep carrying their own capped deal rows so the #56 evidence drill-down
    still works straight from the card.

    Two different signals, deliberately — and the re-grouping changes neither:

    * **Window membership** is ``LAST_TOUCH_SQL`` — the same keyless GREATEST(edit, newest
      activity, newest live note) expression the "Needs a touch" panel uses via
      ``analytics_service.get_stale_deals``. It is exact, event-grained, and needs no
      provider. It is emphatically NOT ``deals.ai_touch_count_at``: that column is #16's
      stale-write-guard key (an evidence watermark that falls back to the deal's
      ``created_at`` and only advances when a provider answered and the CAS accepted), so
      using it here made every provider timeout silently delete a deal from a weekly
      accountability number — and disagreed with the stale-deal panel 200px below it.

      Creation is NOT a touch: ``create_deal`` leaves ``updated_at == created_at``, so
      without the ``last_touch <> created_at`` guard a fresh import or a sample-data load
      would report every new deal as worked. Note this makes ``LAST_TOUCH_SQL``'s floor on
      ``d.updated_at`` load-bearing: any future writer that bumps ``updated_at`` on a
      schedule (rather than on a real edit) would silently read as a touch — which is why
      ``archive_deal`` deliberately does not bump it.
    * **The number shown per deal** is #16's ``ai_touch_count``, and it is what supplies the
      zero-keys gate: with no provider the worker never runs, every count stays NULL,
      ``computed_deals`` is 0, and the card hides itself rather than rendering an empty or
      erroring panel (product rule: hidden affordance, never an error).
    * **A touch counts while the deal is open, up to and including the move into Won, and
      never after** (issue #179). ``TOUCH_AT_SQL`` freezes a won deal's instant at its
      journaled win, so the week a rep worked a deal and closed it credits every touch on
      it — including the close, the touch most worth crediting — instead of erasing the deal
      from the numbers on Friday. Consequently ``open_deals`` keeps meaning "currently open"
      and is NOT a denominator: a won deal is touched but not open, so ``touches`` can
      exceed it, and both surfaces render the two as separate facts rather than "N of M".
      Lost and archived deals stay excluded outright — ``bulk_move_deals`` can mark hundreds
      of deals Lost in one click, and a symmetric rule would mint that many touches.

    Because membership never depends on AI coverage, both counts are coverage-independent —
    a half-backfilled install can't report 1 touched beside 40 open when the user really
    touched 15.
    """
    window_start, window_end, label, custom = _resolve_touch_window(start, end)
    # ONE snapshot for both reads, exactly as the drill-down does — see
    # `_touch_snapshot_reads` for why a KPI cannot afford two.
    rep_rows, deal_rows = _touch_snapshot_reads(
        window_start, window_end,
        owner_scoped=False, owner_id=None, per_rep_limit=WEEKLY_TOUCHES_LIMIT,
    )
    reps = _shape_touch_reps(rep_rows, deal_rows)

    return {
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "label": label,
            "custom": custom,
        },
        "reps": reps,
        # Sums of the buckets, which partition the open-deal set because the unowned deals
        # are a bucket rather than an exclusion — so the headline is arithmetically the rows
        # beneath it, not a separate number that could drift from them.
        "total_touches": sum(r["touches"] for r in reps),
        "total_open_deals": sum(r["open_deals"] for r in reps),
        "computed_deals": sum(int(r.get("computed_deals") or 0) for r in rep_rows),
    }


def get_weekly_touch_detail(
    owner_id: int | None,
    start: str | None = None,
    end: str | None = None,
) -> dict | None:
    """One owner bucket's touched deals — the whole list, not the card's ten (#146).

    Bounded by ``WEEKLY_TOUCHES_DETAIL_MAX`` rather than literally unbounded, and the
    payload's ``truncated`` flag says so when the ceiling is reached, because a page whose
    contract is "the full list" must not quietly serve a prefix.

    Returns ``None`` for a user id that does not exist, which the router turns into a 404.
    The Unassigned bucket always resolves, and so does a real rep with nothing open and
    nothing won this window — they get a zero row, because "this rep touched nothing" is an
    answer, not a missing page.

    Shares both query builders AND the window resolver with the card, so there is exactly
    one SQL definition of a touched deal and one definition of the window it is counted in.

    **The window is RE-RESOLVED here, not forwarded as frozen instants, and that is the
    correction Stage 4 forced.** Freezing the card's exact bounds looks like it guarantees
    the page lists what the clicked number counted. It cannot, because membership under
    ``LAST_TOUCH_SQL`` is "this deal's CURRENT most recent touch falls in the window" — a
    statement about now, not a historical fact. So a touch made after the card rendered
    moves that deal's ``last_touch`` past a frozen upper bound and DELETES it from the page,
    including a touch the user makes from the page itself: log a call and the deal you just
    worked disappears from the list of deals you touched. Reproduced on Postgres, and
    covered by two integration tests.

    Re-resolving asks the same question the card asks, at the moment the page is opened, so
    the page is always internally consistent and always current. The cost is that a
    dashboard left open for an hour can show a number an hour staler than the page it links
    to — which is true, and is the honest version of the same disagreement.
    """
    # Validate the window before any DB read: a malformed link is a 400, and finding that
    # out should cost nothing.
    window_start, window_end, label, _custom = _resolve_touch_window(start, end)

    user = None
    if owner_id is not None:
        user = users_service.get_user(owner_id)
        if user is None:
            return None

    # One past the ceiling, so a full page can be told apart from a book that overflows it
    # without a second COUNT — the same probe idiom #56's evidence list uses.
    rep_rows, deal_rows = _touch_snapshot_reads(
        window_start, window_end,
        owner_scoped=True, owner_id=owner_id,
        per_rep_limit=WEEKLY_TOUCHES_DETAIL_MAX + 1,
    )

    truncated = len(deal_rows) > WEEKLY_TOUCHES_DETAIL_MAX
    deal_rows = deal_rows[:WEEKLY_TOUCHES_DETAIL_MAX]

    reps = _shape_touch_reps(rep_rows, deal_rows)
    rep = reps[0] if reps else {"user_id": owner_id, "open_deals": 0, "touches": 0, "deals": []}
    deals = rep.pop("deals")
    # Resolved from the user row rather than from the bucket, so a rep with no open deals
    # (and therefore no aggregate row) is still named. `user` carries a password hash — it
    # must never reach the payload; display_name reads only id/name/email.
    rep["name"] = users_service.display_name(user)

    return {
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "label": label,
        },
        "rep": rep,
        "deals": deals,
        "truncated": truncated,
    }


# Deal-age buckets for the aging distribution (#20, D1: read-time only, from
# created_at). max_days=None means open-ended. All four buckets are always
# returned (zero-filled) so the UI renders a stable frame. Boundaries and labels
# are aligned exactly — day 90 is "31-90", day 91 is "91+" (no off-by-one).
AGE_BUCKETS = ((0, 7, "0-7"), (8, 30, "8-30"), (31, 90, "31-90"), (91, None, "91+"))

# Open-deal predicate (DEAL_STAGES sentinels; no status column / CHECK exists).
# Public for the same single-source-of-truth reason as LIVE_PREDICATE above.
# This is the SQL statement of CLOSED_STAGES (defined beside DEAL_STAGES); the literal
# stays hand-written rather than interpolated — every deal-reading query embeds this
# string, and a test pins the two spellings in agreement instead.
OPEN_PREDICATE = "stage NOT IN ('won', 'lost')"
OPEN_PREDICATE_D = "d.stage NOT IN ('won', 'lost')"

# "When was this deal last touched" — the newest of: any edit (updated_at), any logged
# activity, any un-archived note. Requires the deals table aliased as `d`.
#
# Shared by get_analytics (below) and analytics_service.get_stale_deals (issue #22), so
# the dashboard's stale count and the assistant's stale-deal list can never disagree
# about what "touched" means. Change it here and both move together.
#
# Housekeeping notes are not touches (#239): the archive/restore audit notes and the
# provenance confirmations are state changes, so an archive→restore round trip must leave
# the clock where it was. The predicate is scoring_service's shared family, rendered with
# no `%` because this fragment is interpolated into parameterized statements. Excluding
# provenance confirmations here too is deliberate — the contact side (#77) already does,
# and one definition of "housekeeping" is the point.
LAST_TOUCH_SQL = f"""GREATEST(
    d.updated_at,
    COALESCE((SELECT MAX(a.created_at) FROM activity_log a
               WHERE a.deal_id = d.id), d.updated_at),
    COALESCE((SELECT MAX(ch.created_at) FROM crm_chatter ch
               WHERE ch.entity_type = 'deal' AND ch.entity_id = d.id
                 AND ch.archived = 0
                 AND {scoring_service.not_housekeeping_sql('ch.message')}), d.updated_at)
)"""

# "When did this deal earn its accountability touch" (issue #179). The rule, stated once:
# a deal currently in 'won' is dated by its recorded win; anything else is dated by its
# last touch. So a rep who works a deal Monday through Wednesday and closes it Thursday
# keeps that whole week — including the close, which is the touch most worth crediting —
# and a note typed on a deal that is already won moves nothing, because the deal's instant
# has frozen at the win.
#
# The win instant is read from the `deal_stage_events` journal (#22), which has recorded
# every stage transition — in both directions, in the same transaction as the stage write —
# since 2026-08-16. It is NOT a `deals.closed_at` column: a column would be a second copy
# of a fact the journal already holds, would start empty where the journal carries real
# history, and would need its own write path to keep in step. `MAX(changed_at)` mirrors the
# derivation already shipped in analytics_service.get_pipeline_analytics, so there is one
# spelling of "when was this won" in the codebase rather than two. (`changed_at` defaults to
# the transaction's `now()`, so under concurrent re-wins of ONE deal the greatest timestamp
# is not provably the last-committed one. Selecting by timestamp is the self-consistent
# choice when the value is then compared against a time window, and stage writes are
# serialized by _write_deal_update's SELECT ... FOR UPDATE.)
#
# A CASE, deliberately NOT LEAST(last_touch, won_at): Postgres LEAST ignores NULL operands,
# so LEAST would fall back to `updated_at` for a deal in 'won' with no journaled win — the
# one deal that must count for nothing. Those deals are real and enumerable: created
# straight into 'won' (create_deal writes no event — it has no old stage to leave), the
# demo seed's won/lost rows, and anything won before the journal existed. NULL here fails
# every window comparison, so they contribute nothing, exactly as they do today. That is
# the issue's own forward-only rule, and it agrees with the existing "creation is not a
# touch" guard.
#
# 'lost' is deliberately NOT mirrored: bulk_move_deals can mark a whole column lost in one
# click, and a symmetric rule would mint that many touches. Lost and archived deals stay
# excluded outright.
#
# Requires the deals table aliased as `d`. Shared by BOTH Weekly Touches builders, so the
# card and the drill-down cannot disagree about when a deal was touched.
TOUCH_AT_SQL = f"""CASE WHEN d.stage = 'won'
    THEN (SELECT MAX(e.changed_at) FROM deal_stage_events e
           WHERE e.deal_id = d.id AND e.new_stage = 'won')
    ELSE {LAST_TOUCH_SQL} END"""

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

# Chatter rows that are NOT somebody's work, and must never inflate a rep's numbers.
#
#   1. provenance_service.confirm inserts its audit note DIRECTLY, bypassing
#      add_note — confirming AI-populated fields would otherwise read as engagement.
#   2. merge_deals COPIES the source deal's notes onto the target and leaves the
#      originals in place on the archived source. Both copies are archived = 0, so
#      filtering on that alone counts one person's note twice.
#
# Both are identified by their marker prefix, which is the only thing that
# distinguishes them; `[` has no special meaning in SQL LIKE, only % and _.
#
# Housekeeping notes (provenance confirmations and, since #239, archive/restore audit
# notes) are excluded through scoring_service's shared, `%`-free predicate — the SAME
# family the #77 last-contact join and the engagement reads exclude, so no fourth copy
# can drift from the writers. The merge-copy marker is still a bound PARAMETER rather
# than inlined: psycopg2 interpolates `%` in any statement it is given parameters for,
# so a literal `'[Merged from deal #%'` in the SQL raises "IndexError: tuple index out
# of range" at execute time — a runtime 500, not a syntax error anything catches
# earlier.
_ACTIVITY_CHATTER_EXCLUSIONS = (
    f" AND {scoring_service.not_housekeeping_sql('ch.message')} AND ch.message NOT LIKE %s"
)
_MERGE_COPY_PATTERN = "[Merged from deal #%"


def _shape_per_rep(pipeline_rows: list[dict], activity_rows: list[dict]) -> list[dict]:
    """Merge the owner-scoped and actor-scoped halves into one row per person.

    Two DIFFERENT attributions in one table, which is the whole point (cake_os
    #1454/#1532): pipeline numbers follow OWNERSHIP, activity numbers follow who
    ACTED. A rep is credited for work on a colleague's record, so the row is
    mixed-attribution by design rather than by accident.

    ``records_touched`` is the number to compare reps on. ``activity_count`` is
    inflatable by a single bulk action.
    """
    merged: dict[int | None, dict] = {}

    def _slot(user_id, name="", email=""):
        if user_id not in merged:
            merged[user_id] = {
                "user_id": user_id,
                "name": (name or "").strip() or email or "",
                "email": email or "",
                "deals_open": 0, "open_value": 0.0,
                "deals_won": 0, "deals_lost": 0, "won_value": 0.0,
                "activity_count": 0, "records_touched": 0,
            }
        return merged[user_id]

    for r in pipeline_rows:
        slot = _slot(r.get("user_id"), r.get("name"), r.get("email"))
        slot["deals_open"] = int(r.get("deals_open") or 0)
        slot["open_value"] = _as_float(r.get("open_value"))
        slot["deals_won"] = int(r.get("deals_won") or 0)
        slot["deals_lost"] = int(r.get("deals_lost") or 0)
        slot["won_value"] = _as_float(r.get("won_value"))

    for r in activity_rows:
        slot = _slot(r.get("user_id"), r.get("name"), r.get("email"))
        slot["activity_count"] = int(r.get("activity_count") or 0)
        slot["records_touched"] = int(r.get("records_touched") or 0)

    for user_id, row in merged.items():
        if user_id is None:
            # Work nobody is recorded as having done: the Gmail touch scan, the
            # assistant's background turn, imported history, and — until Phase B
            # threads identity into tool executors — every assistant-performed write.
            # Surfaced as its own row rather than dropped, so the numbers still add up.
            row["name"] = "Unattributed"

    # Busiest first on the honest metric, then pipeline size; Unattributed sinks to
    # the bottom so it never leads a table of people.
    return sorted(
        merged.values(),
        key=lambda r: (r["user_id"] is None, -r["records_touched"], -r["open_value"]),
    )


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

    # ── Per-rep (issue #60) ──────────────────────────────────────────────────
    # Owner-scoped half. LEFT JOIN, not INNER: an unowned deal still belongs in the
    # totals, as its own "Unattributed" row rather than silently missing.
    per_rep_pipeline = pg_fetchall(
        f"""
        SELECT d.owner_id AS user_id, u.name, u.email,
               COUNT(*) FILTER (WHERE {OPEN_PREDICATE_D})                    AS deals_open,
               COALESCE(SUM(d.value) FILTER (WHERE {OPEN_PREDICATE_D}), 0)   AS open_value,
               COUNT(*) FILTER (WHERE d.stage = 'won')                       AS deals_won,
               COUNT(*) FILTER (WHERE d.stage = 'lost')                      AS deals_lost,
               COALESCE(SUM(d.value) FILTER (WHERE d.stage = 'won'), 0)      AS won_value
        FROM deals d
        LEFT JOIN users u ON u.id = d.owner_id
        WHERE {LIVE_PREDICATE_D}
        GROUP BY d.owner_id, u.name, u.email
        """
    )

    # Actor-scoped half, over the SAME window as the activity queries above.
    #
    # UNION ALL, never UNION: the two tables are disjoint event streams and every row
    # is a separate event, so UNION would dedupe identical rows and undercount.
    #
    # An activity may link BOTH a contact and a deal, and the CASE can name only one.
    # Deal-first precedence, deliberately: an activity on a deal is a deal touch, and
    # counting it as two records touched would double-credit one action. An activity
    # linked to NEITHER is a reachable path (log_activity requires neither id) — it
    # maps to 'activity' so activity_count stays total, and the FILTER then excludes
    # it from records_touched, which counts CRM records. An all-NULL row constructor
    # could not do that job: COUNT(DISTINCT (NULL, NULL)) counts as one value.
    per_rep_activity = pg_fetchall(
        f"""
        SELECT acts.actor AS user_id, u.name, u.email,
               COUNT(*) AS activity_count,
               COUNT(DISTINCT (acts.record_type, acts.record_id))
                   FILTER (WHERE acts.record_type <> 'activity') AS records_touched
        FROM (
            SELECT a.actor_id AS actor,
                   CASE WHEN a.deal_id    IS NOT NULL THEN 'deal'
                        WHEN a.contact_id IS NOT NULL THEN 'contact'
                        ELSE 'activity' END AS record_type,
                   COALESCE(a.deal_id, a.contact_id, a.id) AS record_id
              FROM activity_log a
             WHERE a.created_at >= %s AND a.created_at < %s
            UNION ALL
            -- entity_type is lowered so both branches emit the same record-type
            -- vocabulary: the branch above builds lowercase literals, so a raw
            -- value here could split ('deal', 10) from ('Deal', 10).
            SELECT ch.author_id, lower(btrim(ch.entity_type)), ch.entity_id
              FROM crm_chatter ch
             WHERE ch.created_at >= %s AND ch.created_at < %s
               AND ch.archived = 0{_ACTIVITY_CHATTER_EXCLUSIONS}
        ) acts
        LEFT JOIN users u ON u.id = acts.actor
        GROUP BY acts.actor, u.name, u.email
        """,
        (start_dt, end_dt, start_dt, end_dt,
         _MERGE_COPY_PATTERN),
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
        "per_rep": _shape_per_rep(per_rep_pipeline, per_rep_activity),
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
# crm_chatter_attachments (#57) belongs here on this tuple's own terms — it is the user's
# own uploaded bytes, and every reset path does clear it. It is deliberately absent from
# is_crm_empty/_crm_empty_in_txn, which is a DIFFERENT question and not a contradiction:
# those ask "is the CRM empty", and the FK to crm_chatter being ON DELETE CASCADE makes
# "attachments exist while crm_chatter is empty" unrepresentable, so counting it there
# could never change an answer.
_CRM_TABLES = (
    "companies", "contacts", "deals", "todos", "todo_projects", "activity_log",
    "crm_chatter", "crm_chatter_attachments", "crm_field_values", "crm_field_provenance",
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
    deals, todos, activity_log, crm_chatter, crm_field_values).

    Checking every table matters: deals/todos/activity/chatter/field-values can exist
    without contacts, and the fixed-id demo seed must never be inserted into a
    partially-populated CRM. crm_field_definitions is deliberately EXCLUDED — it is
    user configuration (like branding), not entity data; counting it would make a
    just-defined custom field suppress the first-run sample-data prompt.
    """
    row = pg_fetchone(
        """SELECT (SELECT COUNT(*) FROM companies)
                + (SELECT COUNT(*) FROM contacts)
                + (SELECT COUNT(*) FROM deals)
                + (SELECT COUNT(*) FROM todos)
                + (SELECT COUNT(*) FROM todo_projects)
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
                + (SELECT COUNT(*) FROM todos)
                + (SELECT COUNT(*) FROM todo_projects)
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
        # Folded in here rather than given its own endpoint: CrmLayout already
        # fetches demo-status on mount, so the todo mode costs zero extra requests.
        "todo_mode": get_todo_mode(),
    }


# ── Todo mode + public todo surfaces (#70) ────────────────────────────────────

def get_todo_mode() -> str:
    """'normal' or 'gtd'. NEVER raises.

    Read on every tool-registry build and on the Telegram hot path, so an unreadable
    row must degrade to a default rather than break the assistant — the same fail-safe
    posture as gmail.tools' connection check.

    That default is GTD since #102, and it follows the PRODUCT default deliberately:
    a row we cannot read tells us nothing about what the user chose, so the honest
    guess is the experience a new install gets, not the legacy one. The three thin
    `_todo_mode()` wrappers (assistant.identity, heartbeat.service, telegram.service)
    say the same thing, so there is one default rather than four.

    Reviewed and rejected twice: "an unmigrated database (column absent) lands here and
    would advertise GTD tools the schema cannot serve." A SERVING process cannot be in
    that state. `main.lifespan` calls `run_migrations()` unguarded before the app yields
    and `run_migrations` re-raises on any failure, so a migration that did not apply is
    a fatal boot error, not a degraded runtime — and #70's migration is what creates
    this column. The reachable callers of this except are the hermetic suite and any
    embedding that builds a registry with no pool, where GTD is simply the answer we
    want. If that startup contract ever changes, revisit this line first.
    """
    try:
        row = pg_fetchone("SELECT todo_mode FROM crm_meta WHERE id = 1")
    except Exception:
        logger.warning("crm_meta.todo_mode unreadable — defaulting to GTD mode")
        return "gtd"
    mode = (row or {}).get("todo_mode")
    return mode if mode in ("normal", "gtd") else "gtd"


def set_todo_mode(mode: str) -> dict:
    """Switch todo mode. Switching migrates NOTHING — GTD is a view over the same
    rows, so the change is instant and losslessly reversible in both directions."""
    if mode not in ("normal", "gtd"):
        raise gtd_common.ValidationError("todo_mode must be 'normal' or 'gtd'")
    pg_execute(
        "UPDATE crm_meta SET todo_mode = %s, updated_at = %s WHERE id = 1",
        (mode, _now()),
    )
    return {"ok": True, "todo_mode": mode}


def get_todo_public_settings() -> dict:
    """The three no-login-surface settings. Fail-safe like get_todo_mode: if the row
    can't be read, report the surfaces as OFF rather than guessing them open."""
    try:
        row = pg_fetchone(
            "SELECT todo_capture_token, todo_web_enabled, todo_web_token "
            "FROM crm_meta WHERE id = 1"
        ) or {}
    except Exception:
        logger.warning("crm_meta todo surface settings unreadable — reporting disabled")
        return {"todo_capture_token": "", "todo_web_enabled": False, "todo_web_token": ""}
    return {
        "todo_capture_token": row.get("todo_capture_token") or "",
        "todo_web_enabled": bool(row.get("todo_web_enabled")),
        "todo_web_token": row.get("todo_web_token") or "",
    }


def set_todo_public_settings(
    *,
    capture_token: str | None = None,
    web_enabled: bool | None = None,
    web_token: str | None = None,
) -> dict:
    """Update whichever of the three settings were supplied. Tokens are clamped by
    the caller (crm/todo_tokens.py) before they arrive — they are URL path segments,
    so an unclamped value could change which route matches."""
    sets: list[str] = []
    params: list = []
    if capture_token is not None:
        sets.append("todo_capture_token = %s")
        params.append(capture_token)
    if web_enabled is not None:
        sets.append("todo_web_enabled = %s")
        params.append(bool(web_enabled))
    if web_token is not None:
        sets.append("todo_web_token = %s")
        params.append(web_token)
    if sets:
        sets.append("updated_at = %s")
        params.append(_now())
        pg_execute(f"UPDATE crm_meta SET {', '.join(sets)} WHERE id = 1", params)
    return get_todo_public_settings()


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
    # #18: seed committed — backfill lead scores so demo pills render immediately.
    # Best-effort: a scoring hiccup must not fail the seed (the daily refresh repairs it).
    try:
        scoring_service.backfill_scores("null")
    except Exception:
        logger.warning("initial lead-score backfill after sample data failed", exc_info=True)
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
    # referenced table alone errors out). Both its writers — _write_deal_update and
    # bulk_move_deals (#55) — lock the deals row(s) first, so a later position can't
    # invert against either.
    # proactive_nudges (#22 Phase 3) goes last. Like crm_field_values it is polymorphic
    # and carries NO FK, so nothing cascades it — it MUST be swept explicitly or a
    # reseeded CRM inherits the old per-record nudge cooldowns and stays silent about
    # records it has never actually mentioned. Its only writer is a single-statement
    # upsert touching just this table, so its position can't invert against anything.
    # todo_projects (#70) is referenced BY todos (todos.project_id FK), so like
    # deal_stage_events it has to share the statement — truncating todos alone would
    # leave orphan projects, and truncating todo_projects alone errors on the FK.
    # A GTD project is entity data (the user's own outcomes), not configuration, so it
    # goes in BOTH variants — unlike crm_field_definitions.
    # deal_ai_touch_evidence (#56) trails everything. Same FK-less reasoning: nothing
    # cascades it, so missing it here would let a deal that reuses a truncated SERIAL id
    # inherit a deleted deal's per-event explanation. Its only writer
    # (touch_count_service._store_touch_count) locks the deals row first and then writes
    # this table — the same deals-before-it order TRUNCATE takes, so no inversion. RESTART
    # IDENTITY is a no-op for it: the PK is deal_id, so it owns no sequence.
    # crm_chatter_attachments (#57) sits immediately AFTER crm_chatter in both variants,
    # and is not optional: it holds a real FK to crm_chatter, so Postgres refuses to
    # truncate crm_chatter without it in the same statement (the deal_stage_events and
    # todo_projects rule). Position matches its writers — create_attachment and
    # delete_attachment both lock the crm_chatter row FIRST and then touch this table, so
    # a chatter-before-attachments TRUNCATE order can't invert against either.
    if include_definitions:
        cur.execute(
            "TRUNCATE companies, contacts, deals, activity_log, todos, todo_projects, "
            "crm_chatter, crm_chatter_attachments, crm_field_definitions, crm_field_values, "
            "crm_field_provenance, deal_stage_events, proactive_nudges, "
            "deal_ai_touch_evidence RESTART IDENTITY"
        )
    else:
        cur.execute(
            "TRUNCATE companies, contacts, deals, activity_log, todos, todo_projects, "
            "crm_chatter, crm_chatter_attachments, crm_field_values, crm_field_provenance, "
            "deal_stage_events, proactive_nudges, deal_ai_touch_evidence RESTART IDENTITY"
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
