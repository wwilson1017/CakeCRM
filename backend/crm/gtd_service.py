"""Todo-GTD — service layer over the shared `todos` store.

GTD is a MODE, not a second store: every function here reads and writes the same
`todos` rows the rest of the CRM uses (issue #70). That is what keeps the dashboard
counts, the contact/deal rollups and the heartbeat's nudges aware of GTD todos —
and it is why the write path funnels through `crm.service._apply_todo_update_cur`
rather than issuing its own UPDATE: `completed` and `status` are bound by a CHECK
constraint and must always move together.

Ported from cake_os `apps/todo_gtd/service.py` (itself a Postgres port of chatty's
`core/todo/service.py`), minus the per-user owner scoping and minus the
Projects/CRM card-link columns — CakeCRM's `todos` already carries contact_id and
deal_id, which is the better link for this product.

Validation errors raise `gtd_common.ValidationError`; the router maps them to 400.
"""

import json
import logging
from datetime import date, datetime, timezone

from core import localtime
from core.postgres import get_connection, pg_execute, pg_fetchall, pg_fetchone
from crm import gtd_common, service
from crm.gtd_common import (
    MAX_BULK_IDS,
    MAX_TEXT_CHARS,
    PROJECT_FIELDS,
    TODO_FIELDS,
    TODO_STATUSES,
    ConflictError,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# The GTD read shape. `description` is aliased to `notes` because that is what the
# GTD vocabulary (and the ported UI) calls it, while the column keeps the CRM's
# existing name. The contact/deal joins are this product's answer to cake_os's
# card-link chip: a todo can point at a CRM record, and the row carries the label
# needed to render it.
_SELECT_TODO = (
    "SELECT t.*, t.description AS notes, p.name AS project_name, "
    "       c.name AS contact_name, d.title AS deal_title "
    "FROM todos t "
    "LEFT JOIN todo_projects p ON t.project_id = p.id "
    "LEFT JOIN contacts c ON t.contact_id = c.id "
    "LEFT JOIN deals d ON t.deal_id = d.id"
)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _todo_dict(d: dict) -> dict:
    # JSONB usually arrives as a parsed list; tolerate a raw string defensively.
    tags = d.get("tags")
    if isinstance(tags, str):
        try:
            d["tags"] = json.loads(tags)
        except ValueError:
            d["tags"] = []
    elif not isinstance(tags, list):
        d["tags"] = []
    d["star"] = bool(d.get("star"))
    d["auto_star_on_due"] = bool(d.get("auto_star_on_due"))
    return d


# ── Todos ─────────────────────────────────────────────────────────────────────

def create_todo(
    title: str,
    *,
    notes: str = "",
    project: str | None = None,
    project_id: int | None = None,
    context: str = "",
    tags: list[str] | None = None,
    status: str = "inbox",
    star=False,
    due_date: str | None = None,
    repeat: str = "",
    auto_star_on_due=False,
    contact_id: int | None = None,
    deal_id: int | None = None,
    source: str = "agent",
    owner_id: int | None = None,
) -> dict:
    """Create a todo. Defaults to the inbox — the GTD capture-first rule.

    A free-text `project` name resolves to (or creates) a project; an explicit
    `project_id` wins over it.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        if project_id is not None:
            pid = service._check_todo_project_id_cur(cur, project_id)
        elif project and str(project).strip():
            pid = service._resolve_todo_project_id_cur(
                cur, gtd_common.validate_short(project, "project")
            )
        else:
            pid = None
    # The STORE layer's `service.create_todo` owns the INSERT so there is ONE place that
    # derives `completed` from `status` — the pairing the CHECK constraint enforces. This
    # module's own `create_todo` is the GTD-vocabulary wrapper over it; since #169 the two
    # share a name, and every call here is module-qualified so the pair stays unambiguous.
    return _todo_dict(
        service.create_todo(
            title,
            description=notes,
            due_date=due_date or "",
            contact_id=contact_id,
            deal_id=deal_id,
            status=status,
            star=star,
            context=context,
            tags=tags,
            repeat=repeat,
            auto_star_on_due=auto_star_on_due,
            project_id=pid,
            source=source,
            owner_id=owner_id,
        )
    )


def get_todo(todo_id: int) -> dict | None:
    row = pg_fetchone(_SELECT_TODO + " WHERE t.id = %s", (todo_id,))
    return _todo_dict(row) if row else None


def _check_fields(fields: dict, valid_fields: frozenset = TODO_FIELDS) -> None:
    if not fields:
        raise ValidationError("No fields to update")
    unknown = set(fields) - valid_fields
    if unknown:
        raise ValidationError(
            f"Unknown fields: {', '.join(sorted(unknown))}. "
            f"Valid: {', '.join(sorted(valid_fields))}"
        )


def _to_todo_fields(fields: dict) -> dict:
    """Translate the GTD field vocabulary into the `todos` column names."""
    out = dict(fields)
    if "notes" in out:
        out["description"] = out.pop("notes")
    return out


def update_todo(todo_id: int, fields: dict) -> dict | None:
    """Update one todo. Returns the updated todo, or None if it does not exist."""
    _check_fields(fields)
    with get_connection() as conn:
        cur = conn.cursor()
        found = service._apply_todo_update_cur(cur, int(todo_id), _to_todo_fields(fields))
    return get_todo(todo_id) if found else None


def bulk_update(ids: list[int], fields: dict) -> dict:
    """Apply the same field update to many todos in one transaction.

    Per-id loop, never one `UPDATE ... WHERE id = ANY(...)`, so each repeating todo
    in a bulk-complete spawns its own next occurrence. Ids are processed in sorted
    order so two overlapping bulk updates lock rows in the same order and cannot
    deadlock.
    """
    _check_fields(fields)
    if not ids:
        raise ValidationError("ids is required")
    if len(ids) > MAX_BULK_IDS:
        # The HTTP model caps this too, but the agent tools bypass Pydantic — the
        # service is the one boundary both paths share.
        raise ValidationError(f"too many ids (max {MAX_BULK_IDS})")
    try:
        id_list = sorted(int(i) for i in ids)
    except (TypeError, ValueError):
        raise ValidationError("ids must be integers")
    todo_fields = _to_todo_fields(fields)
    updated: list[int] = []
    not_found: list[int] = []
    with get_connection() as conn:
        cur = conn.cursor()
        for todo_id in id_list:
            if service._apply_todo_update_cur(cur, todo_id, todo_fields):
                updated.append(todo_id)
            else:
                not_found.append(todo_id)
    return {"updated": updated, "not_found": not_found}


def delete_todo(todo_id: int) -> bool:
    return service.delete_todo(todo_id)


def list_todos(
    status: str | None = None,
    project: str | int | None = None,
    context: str | None = None,
    tag: str | None = None,
    starred: bool | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    search: str | None = None,
    limit: int = 100,
    owner_id: int | str | None = None,
    include_deferred: bool = False,
) -> list[dict]:
    """``include_deferred`` keeps todos waiting on a bring-back date (#261) in the result.
    The working lists leave it off; a record view or a project-health check turns it on,
    because a scheduled return is still that project's next action."""
    where: list[str] = []
    params: list = []
    # Same three values as every other owner filter (#190): an id, None for everyone, or
    # service.UNASSIGNED for the unowned pile.
    owner_sql = service.owner_condition("t.owner_id", owner_id, params)
    if owner_sql:
        where.append(owner_sql)
    if status:
        gtd_common.validate_status(status)
        where.append("t.status = %s")
        params.append(status)
    if project not in (None, ""):
        term = str(project).strip()
        if term.isdigit():
            pid = int(term)
        else:
            row = pg_fetchone(
                "SELECT id FROM todo_projects WHERE lower(name) = lower(%s)", (term,)
            )
            if not row:
                return []
            pid = row["id"]
        where.append("t.project_id = %s")
        params.append(pid)
    if context:
        where.append("lower(t.context) = lower(%s)")
        params.append(context.strip())
    if tag:
        # jsonb_exists is the `?` operator spelled as a function, so psycopg2 does
        # not mistake `?` for a placeholder.
        where.append("jsonb_exists(t.tags, %s)")
        params.append(tag)
    if starred is not None:
        where.append("t.star = %s")
        params.append(bool(starred))
    if due_before:
        where.append("t.due_date != '' AND t.due_date <= %s")
        params.append(gtd_common.validate_due(due_before))
    if due_after:
        where.append("t.due_date != '' AND t.due_date >= %s")
        params.append(gtd_common.validate_due(due_after))
    if search:
        esc = f"%{_escape_like(search)}%"
        where.append(
            "(t.title ILIKE %s OR t.description ILIKE %s OR t.context ILIKE %s "
            "OR t.tags::text ILIKE %s OR p.name ILIKE %s)"
        )
        params.extend([esc] * 5)
    # A todo on an archived deal follows it out of view, exactly like list_todos —
    # work items follow the deal in both modes.
    where.append(f"(t.deal_id IS NULL OR {service.LIVE_PREDICATE_D})")
    if not search and not include_deferred:
        # A todo waiting on a bring-back date (#261) is off every working list until its
        # day. Finished rows are unaffected, and a SEARCH still finds it — search is how a
        # deferred todo is reached (and its date changed) before it comes back.
        where.append(
            "(t.status IN ('done','dropped') OR "
            f"{service.brought_back_sql('t.bring_back_on', params)})"
        )
    sql = _SELECT_TODO + " WHERE " + " AND ".join(where)
    if status in gtd_common.FINISHED_STATUSES:
        # Finished lists grow forever — newest-finished first, or the LIMIT window
        # would freeze on the oldest rows and hide new completions. (Dropped rows
        # have no completed_at; updated_at marks the drop.)
        sql += " ORDER BY COALESCE(t.completed_at, t.updated_at) DESC, t.id DESC LIMIT %s"
    elif search and not status:
        # Global search spans every status, and repeating todos accumulate done
        # copies with identical titles — open todos must win the LIMIT window or the
        # live occurrence vanishes behind finished ones.
        sql += " ORDER BY (t.status IN ('done','dropped')) ASC, t.id DESC LIMIT %s"
    else:
        sql += " ORDER BY t.created_at ASC, t.id ASC LIMIT %s"
    n = 100 if limit is None else int(limit)  # `or` would turn an explicit 0 into 100
    params.append(max(1, min(n, 500)))
    return [_todo_dict(r) for r in pg_fetchall(sql, params)]


def today_view() -> list[dict]:
    """The daily home screen: open todos that are starred, due today, overdue, or whose
    bring-back date has arrived (#261).

    One query; the frontend groups it into sections. Due dates compare TEXT-on-TEXT
    against the configured-timezone date rather than a ::date cast, matching the rest
    of the CRM — it can never cast-error on a malformed row. A row whose bring-back date
    is still ahead is hidden even when starred or due: "bring it back on X" means not
    before X.

    # simplification: an arrived bring-back date keeps the todo on Today until it is
    # completed or the date is cleared; there is no sweep job that clears it.
    """
    params: list = []
    hidden = service.brought_back_sql("t.bring_back_on", params)
    today = params[0]
    return [
        _todo_dict(r)
        for r in pg_fetchall(
            _SELECT_TODO
            + " WHERE t.status NOT IN ('done','dropped')"
            "   AND (t.star OR (t.due_date != '' AND t.due_date <= %s)"
            "        OR t.bring_back_on <= %s)"
            f"  AND {hidden}"
            f"  AND (t.deal_id IS NULL OR {service.LIVE_PREDICATE_D})"
            " ORDER BY (t.due_date = '') ASC, t.due_date ASC, t.created_at ASC, t.id ASC",
            (today, today, today),
        )
    ]


# ── Projects ──────────────────────────────────────────────────────────────────

# open_count deliberately counts only unfinished todos — a project's badge answers
# "how much is left", not "how much was ever filed here".
_SELECT_PROJECT = (
    "SELECT p.*, (SELECT COUNT(*) FROM todos t WHERE t.project_id = p.id "
    "AND t.status NOT IN ('done','dropped')) AS open_count FROM todo_projects p"
)


def get_project(project_id: int) -> dict | None:
    return pg_fetchone(_SELECT_PROJECT + " WHERE p.id = %s", (project_id,))


def list_projects(status: str | None = None) -> list[dict]:
    sql = _SELECT_PROJECT
    params: list = []
    if status:
        gtd_common.validate_project_status(status)
        sql += " WHERE p.status = %s"
        params.append(status)
    sql += " ORDER BY lower(p.name) ASC"
    return pg_fetchall(sql, params)


def create_project(
    name: str, notes: str = "", status: str = "active", purpose: str = "", outcome: str = "",
) -> dict:
    name = gtd_common.validate_short(name, "name")
    if not name:
        raise ValidationError("name is required")
    notes = gtd_common.validate_notes(notes)
    purpose = gtd_common.validate_line(purpose, "purpose")
    outcome = gtd_common.validate_line(outcome, "outcome")
    gtd_common.validate_project_status(status)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO todo_projects (name, notes, status, purpose, outcome) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (lower(name)) DO NOTHING RETURNING id",
            (name, notes, status, purpose, outcome),
        )
        row = cur.fetchone()
        if row is None:
            raise ValidationError(f'Project "{name}" already exists')
        new_id = row[0]
    return get_project(new_id)


def update_project(project_id: int, fields: dict) -> dict | None:
    _check_fields(fields, PROJECT_FIELDS)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM todo_projects WHERE id = %s FOR UPDATE", (project_id,))
        if not cur.fetchone():
            return None
        sets: list[str] = []
        params: list = []
        if "name" in fields:
            name = gtd_common.validate_short(fields["name"], "name")
            if not name:
                raise ValidationError("name cannot be empty")
            cur.execute(
                "SELECT id FROM todo_projects WHERE lower(name) = lower(%s) AND id != %s",
                (name, project_id),
            )
            if cur.fetchone():
                raise ValidationError(f'Project "{name}" already exists')
            sets.append("name = %s")
            params.append(name)
        if "notes" in fields:
            sets.append("notes = %s")
            params.append(gtd_common.validate_notes(fields["notes"]))
        # One line each: trimmed, capped, inner whitespace collapsed. Clearing is a real
        # save: '' is the unset state, never refused like a blank name.
        for key in ("purpose", "outcome"):
            if key in fields:
                sets.append(f"{key} = %s")
                params.append(gtd_common.validate_line(fields[key], key))
        if "status" in fields:
            gtd_common.validate_project_status(fields["status"])
            sets.append("status = %s")
            params.append(fields["status"])
        sets.append("updated_at = now()")
        cur.execute(
            f"UPDATE todo_projects SET {', '.join(sets)} WHERE id = %s",
            (*params, project_id),
        )
    return get_project(project_id)


def delete_project(project_id: int) -> bool:
    """Delete a project. Its todos survive with project_id set to NULL (the FK is
    ON DELETE SET NULL) — deleting a grouping must never delete the work."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM todo_projects WHERE id = %s", (project_id,))
        return cur.rowcount > 0


# ── Filters / capture ─────────────────────────────────────────────────────────

def get_filters() -> dict:
    """Distinct contexts, the tag union, per-status counts (all statuses, zeros
    included), and the install's timezone — everything the UI needs in one round trip.

    ``tz`` is the zone ``today_view`` buckets due dates in (#259). The GTD client derives
    every "today" from it rather than from the browser's clock, so a page opened from
    another zone sorts Overdue vs Due today exactly as the server chose the rows — the
    #130 one-clock rule, applied to GTD. It is ``localtime.tz().key``, so a bogus
    ``TIMEZONE`` reports the same UTC fallback the server itself uses.
    """
    contexts = [
        r["context"]
        for r in pg_fetchall(
            "SELECT DISTINCT context FROM todos WHERE context != '' ORDER BY context"
        )
    ]
    tags: set[str] = set()
    for r in pg_fetchall(
        "SELECT DISTINCT jsonb_array_elements_text(tags) AS tag FROM todos"
    ):
        if isinstance(r.get("tag"), str) and r["tag"]:
            tags.add(r["tag"])
    counts = {s: 0 for s in TODO_STATUSES}
    # The open-status counts describe the working lists, so a todo waiting on a bring-back
    # date (#261) is not counted until its day — otherwise the Inbox badge would count an
    # item the Inbox does not show. Finished counts are history and keep every row.
    count_params: list = []
    deferred = service.brought_back_sql("bring_back_on", count_params)
    for r in pg_fetchall(
        "SELECT status, COUNT(*) AS n FROM todos "
        f"WHERE status IN ('done','dropped') OR {deferred} GROUP BY status",
        count_params,
    ):
        if r["status"] in counts:
            counts[r["status"]] = r["n"]
    return {
        "contexts": contexts,
        "tags": sorted(tags, key=str.lower),
        "status_counts": counts,
        "tz": localtime.tz().key,
    }


def rename_context(old: str, new: str, *, merge: bool = False) -> dict:
    """Rename a context on every todo carrying it (#280, port of cake_os #3569).

    A context is a string on each todo, not a record, so this rewrites the rows that
    hold exactly ``old`` — every status, finished included, or the old name would
    linger in the filter list. When ``new`` is already a context the two groups would
    MERGE, which cannot be told apart afterwards, so that raises ConflictError (409)
    and writes nothing until the caller repeats it with ``merge=True``.

    A context-only sibling of ``service._apply_todo_update_cur``: it never touches
    ``status``/``completed`` (so the CHECK that binds them cannot drift) and it does
    NOT bump ``updated_at`` — Review's stale list and Waiting's age read that column
    as "when I last touched this", and a rename changes no todo's substance.
    """
    old = gtd_common.validate_short(old, "context")
    new = gtd_common.validate_short(new, "new name")
    if not old:
        raise ValidationError("context is required")
    if not new:
        raise ValidationError("new name cannot be empty")
    if old == new:
        raise ValidationError(f'The context is already named "{new}"')
    with get_connection() as conn:
        cur = conn.cursor()
        # One rename at a time. The row locks below cover only the SOURCE rows, so two
        # renames of different contexts onto the same unused name would each see no
        # destination and merge without confirmation; an unused name has no row to lock.
        cur.execute("SELECT pg_advisory_xact_lock(2801)")
        cur.execute(
            "SELECT id FROM todos WHERE context = %s ORDER BY id FOR UPDATE", (old,)
        )
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            raise NotFoundError()
        cur.execute("SELECT EXISTS (SELECT 1 FROM todos WHERE context = %s)", (new,))
        merged = bool(cur.fetchone()[0])
        if merged and not merge:
            raise ConflictError(
                f'You already have a context named "{new}". '
                f'Merging moves every "{old}" todo into it.'
            )
        cur.execute("UPDATE todos SET context = %s WHERE id = ANY(%s)", (new, ids))
    return {"count": len(ids), "todo_ids": ids, "merged": merged}


def capture(text: str, source: str = "capture_web", owner_id: int | None = None) -> dict:
    """Deterministic quick capture: the full trimmed text becomes an inbox todo's
    title. No AI, no parsing — capture must work with zero keys and never surprise.

    ``owner_id`` stamps the seat the capture came from (#193). It is None for the PUBLIC
    ``/api/capture`` surface, which is unauthenticated and so has nobody to attribute —
    those rows stay unowned, exactly as before. Telegram passes the linked seat.
    """
    text = (text or "").strip()
    if not text:
        raise ValidationError("Nothing to capture")
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError(f"Capture text too long (max {MAX_TEXT_CHARS} characters)")
    return create_todo(text, status="inbox", source=source, owner_id=owner_id)


# ── Open-title lookups (the observer's todo dedupe, issue #72 Phase 4) ──────

# `status NOT IN ('done','dropped')` is the open predicate used throughout this module
# (list_todos, the project open_count). It is authoritative rather than `completed = 0`
# because create_todo DERIVES `completed` from `status` under a CHECK constraint, so
# status is the column that cannot drift.
_OPEN_TODO = "status NOT IN ('done','dropped')"


def list_open_todo_titles(days: int = 30, limit: int = 30) -> list[str]:
    """Recent open todo titles — the "already tracked, do not repeat" list for a prompt.

    This is a BUDGET, not a correctness check. Thirty titles is what fits comfortably in
    a light-tier prompt; it cannot prove anything about the 31st todo or about one opened
    a year ago, so the actual duplicate check on the write path is
    ``open_todo_with_title_exists`` below. Feeding the model the capped list is still
    worth it: it stops most duplicates before a call is even made.

    Deliberately NOT filtered to ``source='agent'``. The question the list answers is "is
    this already on the user's list", and a todo the user typed themselves answers it just
    as well as one the assistant captured.
    """
    rows = pg_fetchall(
        f"SELECT title FROM todos WHERE {_OPEN_TODO} "
        "  AND created_at >= now() - make_interval(days => %s) "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (max(1, int(days)), max(1, int(limit))),
    )
    return [r["title"] for r in rows if (r["title"] or "").strip()]


def open_todo_with_title_exists(title: str) -> bool:
    """True iff an OPEN todo already carries this title (case- and whitespace-insensitive).

    No day window and no source filter, on purpose: this is the check that actually
    prevents a duplicate, and an open todo from six months ago is still open work.

    BOTH sides are normalized, and they have to be. `gtd_common.validate_title` only
    strips the ends, so a stored title keeps whatever internal spacing its author typed —
    collapsing only the needle would make this comparison LESS permissive, not more: a
    human's "Call  Bob" would never match the observer's collapsed "Call Bob", and the
    very function whose job is to prevent a duplicate would create one. Case-folding and
    whitespace-collapsing both run in SQL, so there is exactly one rule and no chance of
    Python and Postgres disagreeing about it.

    No index supports the normalized comparison and none is added: `todos` is a
    single-user table and this runs at most three times per observed conversation.
    """
    clean = " ".join((title or "").split())
    if not clean:
        return False
    # EXISTS rather than `SELECT 1 ... LIMIT 1`: the question is whether ANY row matches,
    # so there is no ordering to define and no cap to make non-deterministic. (The LIMIT
    # form also reads to the issue #58 pagination guard as a capped reader whose ORDER BY
    # it cannot resolve, which it is right to object to — a capped read with no total
    # order is exactly the shape that guard exists to catch.)
    row = pg_fetchone(
        f"SELECT EXISTS (SELECT 1 FROM todos WHERE {_OPEN_TODO} "
        r"  AND lower(regexp_replace(btrim(title), '\s+', ' ', 'g')) = lower(%s)) AS found",
        (clean,),
    )
    return bool(row and row["found"])


# ── Weekly review (#263) ──────────────────────────────────────────────────────

# A review older than this is due. The Review page's hint and the packet's
# `review_due` both read it, so they cannot disagree.
REVIEW_DUE_DAYS = 7
# Same threshold as the Review page's stale list (frontend `STALE_DAYS`).
REVIEW_STALE_DAYS = 14
REVIEW_WAITING_DAYS = 7
REVIEW_SOMEDAY_DAYS = 30
# Rows per section. Every section reports its full count beside the capped list.
REVIEW_ITEM_CAP = 25

# Whole days elapsed since a timestamp column, as an integer.
_DAYS_SINCE = "FLOOR(EXTRACT(EPOCH FROM (now() - {col})) / 86400)::int"


def _days_overdue(due_date: str, today: str) -> int:
    """Days a TEXT `YYYY-MM-DD` due date is past `today` (0 = due today). Parsed in
    Python rather than cast in SQL, so a malformed row degrades to 0 instead of
    failing the whole read — the rest of the CRM never ::date-casts this column."""
    try:
        return max(0, (date.fromisoformat(today) - date.fromisoformat(due_date)).days)
    except (TypeError, ValueError):
        return 0


def _review_section(where: str, params: tuple, days_col: str, order: str,
                    overdue_vs: str | None = None) -> dict:
    """One packet section: the full count, whether the list was capped, and up to
    REVIEW_ITEM_CAP items of ints and titles.

    Each item carries `source` on purpose: it is what lets `delimiters.fence_public_rows`
    fence the title of a row a stranger typed on the public capture page (#204).
    """
    # The id tiebreak runs the way the sort does (#58), so a DESC section stays newest-first
    # among rows one transaction stamped with the same instant.
    id_dir = "DESC" if order.rstrip().upper().endswith("DESC") else "ASC"
    rows = pg_fetchall(
        "SELECT t.id, t.title, t.source, t.due_date, "
        f"{_DAYS_SINCE.format(col=days_col)} AS days, COUNT(*) OVER () AS total "
        "FROM todos t LEFT JOIN deals d ON t.deal_id = d.id "
        f"WHERE {where} AND (t.deal_id IS NULL OR {service.LIVE_PREDICATE_D}) "
        f"ORDER BY {order}, t.id {id_dir} LIMIT %s",
        (*params, REVIEW_ITEM_CAP),
    )
    total = int(rows[0]["total"]) if rows else 0
    return {
        "count": total,
        "truncated": total > len(rows),
        "items": [
            {"id": int(r["id"]), "title": r["title"] or "", "source": r["source"] or "",
             "days": (_days_overdue(r["due_date"], overdue_vs) if overdue_vs
                      else max(0, int(r["days"] or 0)))}
            for r in rows
        ],
    }


def review_status() -> dict:
    """When the weekly review was last marked done, and whether one is due.

    Counted in CALENDAR days on the configured local day, not elapsed 24-hour periods:
    the page words it as "today" / "yesterday", and a review marked at 23:50 must read
    as yesterday ten minutes later. The stored instant is converted with `zoneinfo`,
    the one timezone authority (AGENTS.md).
    """
    row = pg_fetchone("SELECT todo_last_review_at FROM crm_meta WHERE id = 1")
    stamp = (row or {}).get("todo_last_review_at")
    if not stamp:
        return {"review_due": True, "days_since_review": None}
    if isinstance(stamp, str):  # core.postgres hands timestamps back as ISO strings
        stamp = datetime.fromisoformat(stamp)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    reviewed_on = stamp.astimezone(localtime.tz()).date()
    days = max(0, (date.fromisoformat(gtd_common.today_local_str()) - reviewed_on).days)
    return {"review_due": days >= REVIEW_DUE_DAYS, "days_since_review": days}


def mark_review_done() -> dict:
    """Stamp the weekly review as done now. Returns the fresh `review_status()`."""
    pg_execute("UPDATE crm_meta SET todo_last_review_at = now() WHERE id = 1")
    return review_status()


def weekly_review() -> dict:
    """The data a weekly review walks through, one section per step of the
    `todos/weekly-review` help topic. Read-only, keyless, install-wide.

    Booleans, integers, ids and titles only (plus each row's `source` enum, which the
    #204 fence needs), so it is safe on the unattended on-ramp. `days` means, per
    section: inbox and someday — since captured; stale and waiting — since last
    touched; completed — since finished; due — days overdue (0 = due today).
    """
    today = gtd_common.today_local_str()
    stale = f"now() - interval '{REVIEW_STALE_DAYS} days'"
    waiting = f"now() - interval '{REVIEW_WAITING_DAYS} days'"
    someday = f"now() - interval '{REVIEW_SOMEDAY_DAYS} days'"
    due = _review_section(
        "t.status NOT IN ('done','dropped') AND t.due_date != '' AND t.due_date <= %s",
        (today,), "t.created_at", "t.due_date ASC", overdue_vs=today)
    # A next action on an archived deal is out of view everywhere else (list_todos, the
    # Review page's own stalled list), so it does not count as the project's next action.
    projects = pg_fetchall(
        "SELECT p.id, p.name, COUNT(*) OVER () AS total FROM todo_projects p "
        "WHERE p.status = 'active' AND NOT EXISTS (SELECT 1 FROM todos n "
        "WHERE n.project_id = p.id AND n.status = 'next_action' AND (n.deal_id IS NULL "
        f"OR EXISTS (SELECT 1 FROM deals d WHERE d.id = n.deal_id AND {service.LIVE_PREDICATE_D}))) "
        "ORDER BY lower(p.name) ASC, p.id ASC LIMIT %s",
        (REVIEW_ITEM_CAP,),
    )
    project_total = int(projects[0]["total"]) if projects else 0
    return {
        **review_status(),
        "inbox": _review_section("t.status = 'inbox'", (), "t.created_at", "t.created_at ASC"),
        "due_today_or_overdue": due,
        "stale_next_actions": _review_section(
            f"t.status = 'next_action' AND t.updated_at < {stale}", (),
            "t.updated_at", "t.updated_at ASC"),
        "waiting_follow_up": _review_section(
            f"t.status IN ('waiting_for','delegated') AND t.updated_at < {waiting}", (),
            "t.updated_at", "t.updated_at ASC"),
        "someday_old": _review_section(
            f"t.status = 'someday_maybe' AND t.created_at < {someday}", (),
            "t.created_at", "t.created_at ASC"),
        "completed_this_week": _review_section(
            "t.status = 'done' AND t.completed_at >= now() - interval '7 days'", (),
            "t.completed_at", "t.completed_at DESC"),
        "projects_without_next_action": {
            "count": project_total,
            "truncated": project_total > len(projects),
            "items": [{"id": int(p["id"]), "name": p["name"] or ""} for p in projects],
        },
        "thresholds": {
            "review_due_days": REVIEW_DUE_DAYS, "stale_days": REVIEW_STALE_DAYS,
            "waiting_days": REVIEW_WAITING_DAYS, "someday_days": REVIEW_SOMEDAY_DAYS,
        },
    }
