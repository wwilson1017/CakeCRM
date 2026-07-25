"""Temporal-facts service — CRUD + full-text search over Postgres.

Ported from the *facts* half of Chatty's ``core/agents/memory/db.py`` and translated
to Postgres: SQLite FTS5 becomes a generated ``tsvector`` column searched with
``websearch_to_tsquery('simple', …)``; SQLite's ``?`` placeholders become ``%s``; all
access goes through ``core/postgres.py`` helpers. Everything here is synchronous
blocking psycopg2 — callers (the assistant engine, the tool registry) offload to a
thread. No AI, no provider SDKs.

Design notes:
- A fact is *live* iff ``valid_to IS NULL``. ``invalidate_fact`` sets ``valid_to``.
  ``valid_from`` is informational metadata, not a gating predicate (matches chatty).
- Retrieval tracking (``retrieval_count`` / ``last_retrieved_at``) is the usage signal
  the dreaming job scores on. It is throttled to once per hour per fact, and is only
  bumped for genuinely retrieved facts — the context builder tracks FTS *matches*, not
  confidence backfill (see ``memory/context.py``), so merely-surfaced filler can never
  keep a fact alive.
- Chatty's ``security/scanner`` injection-scan is out of scope; instead ``add_fact``
  hardens fields structurally (single-line, length-capped) since they are later
  injected into the system prompt, and the prompt block frames them as data.
"""

import logging
from datetime import date

from core.postgres import pg_execute, pg_fetchall, pg_fetchone
from memory.types import validate_memory_type

logger = logging.getLogger(__name__)

_FIELD_MAX = 500          # subject/predicate/object cap — these reach the system prompt
_QUERY_MAX = 1000         # FTS query cap — bound tsquery parsing cost
_SEARCH_LIMIT_CAP = 100   # chatty's search cap
_QUERY_LIMIT_CAP = 500    # chatty's query_facts cap

# Columns returned to callers (never expose search_tsv).
_FACT_COLS = "id, subject, predicate, object, valid_from, valid_to, confidence, memory_type, created_at"


def _date_error(value, field: str) -> dict | None:
    """Return an ``{"error": ...}`` dict if *value* is a non-ISO-date string, else None.

    Gives the model a clear, actionable message (vs a generic caught psycopg2 cast
    error) on the write paths. Empty/None means "use the DB default" and is fine.
    """
    if value in (None, ""):
        return None
    try:
        date.fromisoformat(str(value))
        return None
    except (TypeError, ValueError):
        return {"error": f"{field} must be a date in YYYY-MM-DD format"}


def _clean_field(value: str) -> str:
    """Collapse internal whitespace to single spaces and cap length.

    Facts are injected into the system prompt, so each field must stay single-line
    and bounded — replaces chatty's content scanner with structural hardening.
    """
    return " ".join(str(value or "").split())[:_FIELD_MAX]


def _clamp_confidence(confidence) -> float:
    try:
        return max(0.0, min(float(confidence), 1.0))
    except (TypeError, ValueError):
        return 1.0


# ---------------------------------------------------------------------------
# add_fact
# ---------------------------------------------------------------------------

def add_fact(
    subject: str,
    predicate: str,
    object_: str,
    valid_from: str | None = None,
    created_by: str = "assistant",
    source: str = "",
    confidence: float = 1.0,
    memory_type: str | None = None,
) -> dict:
    """Insert a temporal fact. Returns the fact dict, or ``{"error": …}``."""
    subject = _clean_field(subject)
    predicate = _clean_field(predicate)
    object_ = _clean_field(object_)
    if not subject:
        return {"error": "subject is required"}
    if not predicate:
        return {"error": "predicate is required"}
    if not object_:
        return {"error": "object is required"}
    date_err = _date_error(valid_from, "valid_from")
    if date_err:
        return date_err

    memory_type = validate_memory_type(memory_type)
    confidence = _clamp_confidence(confidence)

    row = pg_fetchone(
        """
        INSERT INTO memory_facts
            (subject, predicate, object, valid_from, created_by, source, confidence, memory_type)
        VALUES (%s, %s, %s, COALESCE(%s::date, CURRENT_DATE), %s, %s, %s, %s)
        RETURNING id, subject, predicate, object, valid_from, memory_type
        """,
        (subject, predicate, object_, valid_from, created_by, source, confidence, memory_type),
    )
    if not row:
        return {"error": "failed to add fact"}
    row["ok"] = True
    return row


# ---------------------------------------------------------------------------
# query_facts
# ---------------------------------------------------------------------------

def query_facts(
    subject: str | None = None,
    predicate: str | None = None,
    as_of: str | None = None,
    memory_type: str | None = None,
    include_expired: bool = False,
    include_archived: bool = False,
    limit: int = 50,
    track_retrieval: bool = True,
) -> list[dict]:
    """Query temporal facts with optional filters. ``as_of`` gives a point-in-time view."""
    try:
        limit = max(1, min(int(limit), _QUERY_LIMIT_CAP))
    except (TypeError, ValueError):
        limit = 50

    # _FACT_COLS is a module constant (no user data) — the f-string is safe.
    sql = f"SELECT {_FACT_COLS} FROM memory_facts WHERE 1=1"
    params: list = []

    if subject and subject.strip():
        sql += " AND subject ILIKE '%%' || %s || '%%'"
        params.append(subject.strip())
    if predicate and predicate.strip():
        sql += " AND predicate ILIKE '%%' || %s || '%%'"
        params.append(predicate.strip())
    if memory_type:
        mt = validate_memory_type(memory_type)
        if mt:
            sql += " AND memory_type = %s"
            params.append(mt)
    if not include_archived:
        sql += " AND archived_at IS NULL"
    if as_of:
        sql += " AND valid_from <= %s::date AND (valid_to IS NULL OR valid_to >= %s::date)"
        params.extend([as_of, as_of])
    elif not include_expired:
        sql += " AND valid_to IS NULL"

    sql += " ORDER BY confidence DESC, created_at DESC LIMIT %s"
    params.append(limit)

    results = pg_fetchall(sql, tuple(params))
    if track_retrieval and results:
        track_retrieval_for([r["id"] for r in results])
    return results


# ---------------------------------------------------------------------------
# search_facts (full-text)
# ---------------------------------------------------------------------------

def search_facts(
    query: str,
    memory_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
    track_retrieval: bool = True,
) -> list[dict]:
    """Full-text search across live facts. Returns [] on a blank/empty query."""
    if not query or not query.strip():
        return []
    query = query.strip()[:_QUERY_MAX]
    try:
        limit = max(1, min(int(limit), _SEARCH_LIMIT_CAP))
    except (TypeError, ValueError):
        limit = 20

    # websearch_to_tsquery never raises on hostile/malformed input (unlike
    # to_tsquery); the query text is always a bound parameter, so injection is
    # structurally impossible. An all-stopword/empty parse simply matches nothing.
    sql = (
        "SELECT id, subject, predicate, object, valid_from, valid_to, confidence, memory_type, "
        "created_at, ts_rank(search_tsv, q) AS rank "
        "FROM memory_facts, websearch_to_tsquery('simple', %s) AS q "
        "WHERE search_tsv @@ q AND valid_to IS NULL AND archived_at IS NULL"
    )
    params: list = [query]
    if memory_type:
        mt = validate_memory_type(memory_type)
        if mt:
            sql += " AND memory_type = %s"
            params.append(mt)
    if date_from:
        sql += " AND valid_from >= %s::date"
        params.append(date_from)
    if date_to:
        sql += " AND valid_from <= %s::date"
        params.append(date_to)
    sql += " ORDER BY rank DESC, created_at DESC LIMIT %s"
    params.append(limit)

    results = pg_fetchall(sql, tuple(params))
    if track_retrieval and results:
        track_retrieval_for([r["id"] for r in results])
    return results


# ---------------------------------------------------------------------------
# invalidate_fact
# ---------------------------------------------------------------------------

def invalidate_fact(fact_id: int, valid_to: str | None = None) -> dict:
    """Set ``valid_to`` on a fact (removing it from the live set). Idempotent-ish."""
    try:
        fact_id = int(fact_id)
    except (TypeError, ValueError):
        return {"error": "fact_id must be an integer"}
    date_err = _date_error(valid_to, "valid_to")
    if date_err:
        return date_err

    row = pg_fetchone(
        """
        UPDATE memory_facts
        SET valid_to = COALESCE(%s::date, CURRENT_DATE), updated_at = now()
        WHERE id = %s
        RETURNING id, valid_to
        """,
        (valid_to, fact_id),
    )
    if not row:
        return {"error": f"Fact {fact_id} not found"}
    row["ok"] = True
    return row


# ---------------------------------------------------------------------------
# retrieval tracking (dreaming's usage signal)
# ---------------------------------------------------------------------------

def track_retrieval_for(fact_ids: list[int]) -> None:
    """Bump retrieval_count / last_retrieved_at for *fact_ids*, throttled to once
    per hour per fact. Fire-and-forget — never raises (a tracking failure must not
    break a chat turn or a tool call)."""
    if not fact_ids:
        return
    try:
        pg_execute(
            """
            UPDATE memory_facts
            SET retrieval_count = retrieval_count + 1,
                last_retrieved_at = now(),
                updated_at = now()
            WHERE id = ANY(%s)
              AND (last_retrieved_at IS NULL OR last_retrieved_at < now() - interval '1 hour')
            """,
            (list(fact_ids),),
        )
    except Exception:
        # Warning, not debug: silent tracking failure makes every fact look
        # never-retrieved, which would make the dreaming job over-archive.
        logger.warning("retrieval tracking failed", exc_info=True)
