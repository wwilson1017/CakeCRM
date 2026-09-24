"""Saved views — data service (Postgres).

A saved view is a named, team-visible snapshot of one collection surface's screen state
(facets, search text, sort, view mode). The payload is opaque here: the client coerces it
on restore exactly as it coerces a persisted sessionStorage envelope, so this layer only
validates size and type, never shape.

Three rules live in this module and nowhere else:

  * **Authorship, not ownership.** ``created_by`` records who saved a view. Every member may
    read and apply every view on a surface; only the creator or an admin may rename,
    overwrite or delete one. This is the repo's first per-row authorization rule, so it is
    decided in ONE place — ``_may_edit`` — and enforced inside the write transaction after a
    ``SELECT … FOR UPDATE`` pre-read, per the AGENTS.md check-then-write rule. A refusal
    returns ``code="forbidden"`` (→ 403), matching ``core.auth.require_admin``: the caller is
    authenticated and already sees the row in the list, so a 404 would only obscure it.

  * **The version is the client's.** ``version`` is the surface's ``CollectionStorage.version``
    at save time. The server never interprets it; the client refuses to apply a mismatched
    view. That is what stops an incompatible facet change from turning a shared view into a
    silently-empty filter.

  * **Writes read back inside their own transaction.** Every mutator returns the decorated row
    from the same transaction that wrote it. Committing and then re-reading through a second
    connection would let a concurrent delete or update return ``None`` or someone else's state.
"""

import json
import logging
import re

import psycopg2
from psycopg2.extras import Json

from core.postgres import get_connection, row_to_dict

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 80
MAX_SURFACE_CHARS = 64
MAX_PAYLOAD_BYTES = 65536
# A ceiling, not a quota. Any member may create views, this table is deliberately outside
# every CRM reset path, and nothing else would ever delete a row — so without a bound one
# scripted client can grow it without limit and no operator action short of manual SQL
# reclaims the space. It is also far past the point where the popover's list stops being
# usable. Checked inside the insert transaction; two racing inserts at the boundary can
# both pass, which a ceiling can afford and a quota could not.
MAX_VIEWS_PER_SURFACE = 100

# Every CollectionStorage.key in the frontend is lower snake_case (crm_pipeline, crm_contacts,
# crm_companies, crm_todos). Pinning the shape keeps a surface key from becoming a free-text
# column that silently forks one board's views into two.
_SURFACE_RE = re.compile(r"^[a-z0-9_]{1,%d}$" % MAX_SURFACE_CHARS)

# The six ASCII whitespace bytes btrim() strips by default — restated here so the Python-side
# normalisation and uq_saved_views_surface_name_ci agree on what " q3  pipeline " means.
_WS = " \t\n\r\f\v"

_UNSET = object()

_VIEW_SELECT = """
    SELECT v.id, v.surface, v.name, v.version, v.payload, v.created_by,
           v.created_at, v.updated_at,
           COALESCE(NULLIF(btrim(u.name), ''), u.email) AS created_by_name
      FROM saved_views v
      LEFT JOIN users u ON u.id = v.created_by
"""


# ── helpers ────────────────────────────────────────────────────────────────

def _err(message: str, code: str = "bad_request") -> dict:
    """A structured service error. ``code`` (bad_request|not_found|conflict|forbidden) drives
    the router's HTTP status without it having to substring-match the message."""
    return {"error": message, "code": code}


def _normalize_surface(raw) -> str | None:
    surface = str(raw or "").strip(_WS)
    return surface if _SURFACE_RE.fullmatch(surface) else None


def _normalize_name(raw) -> str | None:
    name = str(raw or "").strip(_WS)
    if not name or len(name) > MAX_NAME_CHARS:
        return None
    return name


def _check_payload(payload) -> str | None:
    """Return an error message, or None when the payload is storable.

    The size ceiling is defined HERE and only here (the table declares no size CHECK): two
    limits measured against two different serialisations would disagree.
    """
    if not isinstance(payload, dict):
        return "payload must be an object"
    # ensure_ascii=False so the measurement is the UTF-8 size Postgres actually stores.
    # The default would escape every non-ASCII character to \uXXXX and reject an accented
    # search term at a third of the real ceiling.
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return f"payload exceeds {MAX_PAYLOAD_BYTES} bytes"
    return None


def _check_version(version) -> str | None:
    # `type(...) is int` on purpose: bool is a subclass of int, and True would otherwise
    # persist as version 1 and silently match a real surface version.
    if type(version) is not int or version < 0:
        return "version must be a non-negative integer"
    return None


def _may_edit(created_by, actor: dict) -> bool:
    """The repo's first per-row authorization rule: creator or admin."""
    if actor.get("role") == "admin":
        return True
    return created_by is not None and created_by == actor.get("id")


def _decorate(row: dict | None, actor: dict) -> dict | None:
    """Add the caller-relative ``can_edit`` flag so the client needs no auth context."""
    if not row:
        return row
    row["can_edit"] = _may_edit(row.get("created_by"), actor)
    return row


def _select_view(cur, view_id: int) -> dict | None:
    """Read one decorated-shape row on an EXISTING cursor (same transaction as the write)."""
    cur.execute(_VIEW_SELECT + " WHERE v.id = %s", (view_id,))
    row = cur.fetchone()
    return row_to_dict(cur, row) if row is not None else None


# ── read ───────────────────────────────────────────────────────────────────

def list_views(surface, actor: dict):
    """Every view on a surface, oldest name first. Returns a list, or ``{error}``."""
    key = _normalize_surface(surface)
    if key is None:
        return _err("surface is required")
    with get_connection() as conn:
        cur = conn.cursor()
        # ORDER BY ends on v.id: the read is uncapped today, but a total order means a later
        # LIMIT cannot reintroduce a nondeterministic page.
        cur.execute(_VIEW_SELECT + " WHERE v.surface = %s ORDER BY LOWER(v.name), v.id", (key,))
        rows = [row_to_dict(cur, r) for r in cur.fetchall()]
    return [_decorate(row, actor) for row in rows]


# ── create ─────────────────────────────────────────────────────────────────

def create_view(surface, name, version, payload, actor: dict) -> dict:
    """Save the current screen state under a name. Returns the row, or ``{error}``."""
    key = _normalize_surface(surface)
    if key is None:
        return _err("surface is required")
    clean_name = _normalize_name(name)
    if clean_name is None:
        return _err(f"name is required and must be at most {MAX_NAME_CHARS} characters")
    problem = _check_version(version) or _check_payload(payload)
    if problem:
        return _err(problem)

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM saved_views WHERE surface = %s", (key,))
            if cur.fetchone()[0] >= MAX_VIEWS_PER_SURFACE:
                return _err(
                    f"This page already has {MAX_VIEWS_PER_SURFACE} saved views. "
                    "Delete one before saving another.",
                    "conflict",
                )
            cur.execute(
                """INSERT INTO saved_views (surface, name, version, payload, created_by)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (key, clean_name, version, Json(payload), actor.get("id")),
            )
            view_id = cur.fetchone()[0]
            row = _select_view(cur, view_id)
    except psycopg2.errors.UniqueViolation:
        return _err(f'A view named "{clean_name}" already exists on this page.', "conflict")
    return _decorate(row, actor)


# ── update / delete (creator-or-admin) ─────────────────────────────────────

def update_view(view_id: int, actor: dict, *, name=_UNSET, payload=_UNSET, version=_UNSET) -> dict:
    """Rename a view and/or overwrite its captured state. Creator or admin only.

    ``payload`` and ``version`` must move together — a payload without the version it was
    captured under is exactly the stale-view failure the stamp exists to prevent.
    """
    sets: list[str] = []
    params: list = []

    if name is not _UNSET:
        clean_name = _normalize_name(name)
        if clean_name is None:
            return _err(f"name is required and must be at most {MAX_NAME_CHARS} characters")
        sets.append("name = %s")
        params.append(clean_name)

    if (payload is _UNSET) != (version is _UNSET):
        return _err("payload and version must be updated together")
    if payload is not _UNSET:
        problem = _check_version(version) or _check_payload(payload)
        if problem:
            return _err(problem)
        sets.append("payload = %s")
        params.append(Json(payload))
        sets.append("version = %s")
        params.append(version)

    if not sets:
        return _err("No fields to update")

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT created_by FROM saved_views WHERE id = %s FOR UPDATE", (view_id,))
            pre = cur.fetchone()
            if pre is None:
                return _err("View not found", "not_found")
            if not _may_edit(pre[0], actor):
                return _err("Only the view's creator or an admin can change it", "forbidden")
            cur.execute(
                f"UPDATE saved_views SET {', '.join(sets)}, updated_at = now() WHERE id = %s",
                (*params, view_id),
            )
            row = _select_view(cur, view_id)
    except psycopg2.errors.UniqueViolation:
        return _err("A view with that name already exists on this page.", "conflict")
    return _decorate(row, actor)


def delete_view(view_id: int, actor: dict) -> dict:
    """Delete a view. Creator or admin only."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT created_by FROM saved_views WHERE id = %s FOR UPDATE", (view_id,))
        pre = cur.fetchone()
        if pre is None:
            return _err("View not found", "not_found")
        if not _may_edit(pre[0], actor):
            return _err("Only the view's creator or an admin can delete it", "forbidden")
        cur.execute("DELETE FROM saved_views WHERE id = %s", (view_id,))
    return {"ok": True, "id": view_id}
