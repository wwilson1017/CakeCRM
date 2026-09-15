"""Todo-GTD — service layer over the shared `tasks` store.

GTD is a MODE, not a second store: every function here reads and writes the same
`tasks` rows the rest of the CRM uses (issue #70). That is what keeps the dashboard
counts, the contact/deal rollups and the heartbeat's nudges aware of GTD todos —
and it is why the write path funnels through `crm.service._apply_task_update_cur`
rather than issuing its own UPDATE: `completed` and `status` are bound by a CHECK
constraint and must always move together.

Ported from cake_os `apps/todo_gtd/service.py` (itself a Postgres port of chatty's
`core/todo/service.py`), minus the per-user owner scoping and minus the
Projects/CRM card-link columns — CakeCRM's `tasks` already carries contact_id and
deal_id, which is the better link for this product.

Validation errors raise `gtd_common.ValidationError`; the router maps them to 400.
"""

import json
import logging

from core.postgres import get_connection, pg_fetchall, pg_fetchone
from crm import gtd_common, service
from crm.gtd_common import (
    MAX_BULK_IDS,
    MAX_TEXT_CHARS,
    PROJECT_FIELDS,
    TODO_FIELDS,
    TODO_STATUSES,
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
    "FROM tasks t "
    "LEFT JOIN task_projects p ON t.project_id = p.id "
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
            pid = service._check_task_project_id_cur(cur, project_id)
        elif project and str(project).strip():
            pid = service._resolve_task_project_id_cur(
                cur, gtd_common.validate_short(project, "project")
            )
        else:
            pid = None
    # create_task owns the INSERT so there is ONE place that derives `completed`
    # from `status` — the pairing the CHECK constraint enforces.
    return _todo_dict(
        service.create_task(
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


def _to_task_fields(fields: dict) -> dict:
    """Translate the GTD field vocabulary into the `tasks` column names."""
    out = dict(fields)
    if "notes" in out:
        out["description"] = out.pop("notes")
    return out


def update_todo(todo_id: int, fields: dict) -> dict | None:
    """Update one todo. Returns the updated todo, or None if it does not exist."""
    _check_fields(fields)
    with get_connection() as conn:
        cur = conn.cursor()
        found = service._apply_task_update_cur(cur, int(todo_id), _to_task_fields(fields))
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
    task_fields = _to_task_fields(fields)
    updated: list[int] = []
    not_found: list[int] = []
    with get_connection() as conn:
        cur = conn.cursor()
        for todo_id in id_list:
            if service._apply_task_update_cur(cur, todo_id, task_fields):
                updated.append(todo_id)
            else:
                not_found.append(todo_id)
    return {"updated": updated, "not_found": not_found}


def delete_todo(todo_id: int) -> bool:
    return service.delete_task(todo_id)


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
) -> list[dict]:
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
                "SELECT id FROM task_projects WHERE lower(name) = lower(%s)", (term,)
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
    # A todo on an archived deal follows it out of view, exactly like list_tasks —
    # work items follow the deal in both modes.
    where.append(f"(t.deal_id IS NULL OR {service.LIVE_PREDICATE_D})")
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
    """The daily home screen: open todos that are starred, due today, or overdue.

    One query; the frontend groups it into sections. Date comparison is TEXT-on-TEXT
    against the configured-timezone date rather than a ::date cast, matching the rest
    of the CRM — it can never cast-error on a malformed row.
    """
    today = gtd_common.today_local_str()
    return [
        _todo_dict(r)
        for r in pg_fetchall(
            _SELECT_TODO
            + " WHERE t.status NOT IN ('done','dropped')"
            "   AND (t.star OR (t.due_date != '' AND t.due_date <= %s))"
            f"  AND (t.deal_id IS NULL OR {service.LIVE_PREDICATE_D})"
            " ORDER BY (t.due_date = '') ASC, t.due_date ASC, t.created_at ASC, t.id ASC",
            (today,),
        )
    ]


# ── Projects ──────────────────────────────────────────────────────────────────

# open_count deliberately counts only unfinished todos — a project's badge answers
# "how much is left", not "how much was ever filed here".
_SELECT_PROJECT = (
    "SELECT p.*, (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id "
    "AND t.status NOT IN ('done','dropped')) AS open_count FROM task_projects p"
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


def create_project(name: str, notes: str = "", status: str = "active") -> dict:
    name = gtd_common.validate_short(name, "name")
    if not name:
        raise ValidationError("name is required")
    notes = gtd_common.validate_notes(notes)
    gtd_common.validate_project_status(status)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO task_projects (name, notes, status) VALUES (%s, %s, %s) "
            "ON CONFLICT (lower(name)) DO NOTHING RETURNING id",
            (name, notes, status),
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
        cur.execute("SELECT id FROM task_projects WHERE id = %s FOR UPDATE", (project_id,))
        if not cur.fetchone():
            return None
        sets: list[str] = []
        params: list = []
        if "name" in fields:
            name = gtd_common.validate_short(fields["name"], "name")
            if not name:
                raise ValidationError("name cannot be empty")
            cur.execute(
                "SELECT id FROM task_projects WHERE lower(name) = lower(%s) AND id != %s",
                (name, project_id),
            )
            if cur.fetchone():
                raise ValidationError(f'Project "{name}" already exists')
            sets.append("name = %s")
            params.append(name)
        if "notes" in fields:
            sets.append("notes = %s")
            params.append(gtd_common.validate_notes(fields["notes"]))
        if "status" in fields:
            gtd_common.validate_project_status(fields["status"])
            sets.append("status = %s")
            params.append(fields["status"])
        sets.append("updated_at = now()")
        cur.execute(
            f"UPDATE task_projects SET {', '.join(sets)} WHERE id = %s",
            (*params, project_id),
        )
    return get_project(project_id)


def delete_project(project_id: int) -> bool:
    """Delete a project. Its todos survive with project_id set to NULL (the FK is
    ON DELETE SET NULL) — deleting a grouping must never delete the work."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM task_projects WHERE id = %s", (project_id,))
        return cur.rowcount > 0


# ── Filters / capture ─────────────────────────────────────────────────────────

def get_filters() -> dict:
    """Distinct contexts, the tag union, and per-status counts (all statuses, zeros
    included) — everything the UI's facet bars need in one round trip."""
    contexts = [
        r["context"]
        for r in pg_fetchall(
            "SELECT DISTINCT context FROM tasks WHERE context != '' ORDER BY context"
        )
    ]
    tags: set[str] = set()
    for r in pg_fetchall(
        "SELECT DISTINCT jsonb_array_elements_text(tags) AS tag FROM tasks"
    ):
        if isinstance(r.get("tag"), str) and r["tag"]:
            tags.add(r["tag"])
    counts = {s: 0 for s in TODO_STATUSES}
    for r in pg_fetchall("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
        if r["status"] in counts:
            counts[r["status"]] = r["n"]
    return {"contexts": contexts, "tags": sorted(tags, key=str.lower), "status_counts": counts}


def capture(text: str, source: str = "capture_web") -> dict:
    """Deterministic quick capture: the full trimmed text becomes an inbox todo's
    title. No AI, no parsing — capture must work with zero keys and never surprise."""
    text = (text or "").strip()
    if not text:
        raise ValidationError("Nothing to capture")
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError(f"Capture text too long (max {MAX_TEXT_CHARS} characters)")
    return create_todo(text, status="inbox", source=source)


# ── Open-title lookups (the observer's task dedupe, issue #72 Phase 4) ──────

# `status NOT IN ('done','dropped')` is the open predicate used throughout this module
# (list_todos, the project open_count). It is authoritative rather than `completed = 0`
# because create_task DERIVES `completed` from `status` under a CHECK constraint, so
# status is the column that cannot drift.
_OPEN_TASK = "status NOT IN ('done','dropped')"


def list_open_task_titles(days: int = 30, limit: int = 30) -> list[str]:
    """Recent open task titles — the "already tracked, do not repeat" list for a prompt.

    This is a BUDGET, not a correctness check. Thirty titles is what fits comfortably in
    a light-tier prompt; it cannot prove anything about the 31st task or about one opened
    a year ago, so the actual duplicate check on the write path is
    ``open_task_with_title_exists`` below. Feeding the model the capped list is still
    worth it: it stops most duplicates before a call is even made.

    Deliberately NOT filtered to ``source='agent'``. The question the list answers is "is
    this already on the user's list", and a todo the user typed themselves answers it just
    as well as one the assistant captured.
    """
    rows = pg_fetchall(
        f"SELECT title FROM tasks WHERE {_OPEN_TASK} "
        "  AND created_at >= now() - make_interval(days => %s) "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (max(1, int(days)), max(1, int(limit))),
    )
    return [r["title"] for r in rows if (r["title"] or "").strip()]


def open_task_with_title_exists(title: str) -> bool:
    """True iff an OPEN task already carries this exact title (case-insensitively).

    No day window and no source filter, on purpose: this is the check that actually
    prevents a duplicate, and an open task from six months ago is still open work. The
    comparison runs entirely in SQL so there is one case-folding rule. Whitespace is
    collapsed on the NEEDLE only, because ``gtd_common.validate_title`` merely strips — a
    stored title keeps whatever internal spacing its author used. That is deliberately the
    permissive direction: it can only ever match MORE, and "already tracked" is the
    answer we want when a human typed the same line with a stray double space.
    """
    clean = " ".join((title or "").split())
    if not clean:
        return False
    return pg_fetchone(
        f"SELECT 1 AS ok FROM tasks WHERE {_OPEN_TASK} AND lower(title) = lower(%s) LIMIT 1",
        (clean,),
    ) is not None
