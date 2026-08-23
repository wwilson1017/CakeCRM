"""Temporal-facts service — CRUD + full-text search over Postgres.

Ported from the *facts* half of Chatty's ``core/agents/memory/db.py`` and translated
to Postgres: SQLite FTS5 becomes a generated ``tsvector`` column searched with an
OR-of-keywords ``to_tsquery('simple', …)`` (query text is tokenized to word-char
lexemes, so it is injection-safe and a conversational query matches on any keyword
rather than requiring all of them); SQLite's ``?`` placeholders become ``%s``; all
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
import re
from datetime import date

from core.postgres import pg_execute, pg_fetchall, pg_fetchone
from memory.types import MEMORY_TYPES, validate_memory_type

logger = logging.getLogger(__name__)

_FIELD_MAX = 500          # subject/predicate/object cap — these reach the system prompt
_QUERY_MAX = 1000         # FTS query cap — bound tsquery parsing cost
_SEARCH_LIMIT_CAP = 100   # chatty's search cap
_QUERY_LIMIT_CAP = 500    # chatty's query_facts cap

# Search tokenization: unicode-aware word chars (keeps "Li"/"US"/"IT"/"José" and
# non-Latin scripts), distinct lexemes >= 2 chars, OR-joined into a to_tsquery. Terms
# are word-chars only, so the joined query is injection-safe (no tsquery operators can
# appear from user input) and every plain-language query becomes an OR-of-keywords —
# "what do we know about Dana" matches "Dana Chen — works at — Acme" instead of ANDing
# every conversational word (which under the `simple` config would match nothing).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_SEARCH_MIN_TOKEN_LEN = 2
_SEARCH_MAX_TERMS = 20

# English function words dropped from search queries. The `simple` FTS config keeps no
# stopwords (so names like "Will"/"US" stay searchable) — but that means a conversational
# query would otherwise OR-match facts on filler like "at"/"to"/"is" (which appear in
# predicates: "works at", "reports to"), and every such match is retrieval-tracked, which
# would keep facts perpetually "used" and stop dreaming from ever archiving them. So we
# strip filler on the QUERY side only, with a fallback (below) when a query is ALL filler
# so bare lookups still work. Deliberately EXCLUDES name/acronym homographs (will, may,
# mark, us, it, in, on, no, …) so those stay searchable as entities.
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "or", "but", "of", "to", "at", "by", "for", "from",
    "with", "about", "as", "is", "am", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "this", "that", "these", "those",
    "we", "our", "you", "your", "they", "them", "their", "he", "she", "him", "her",
    "his", "what", "who", "whom", "when", "where", "why", "how", "which", "whose",
    "if", "then", "than", "there", "here", "out", "over", "just", "only", "also",
    "very", "too", "not", "all", "any", "some", "each", "every", "into", "so", "me", "my",
})

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


def _or_tsquery(text: str) -> str:
    """Distinct significant lexemes (unicode word-chars, len >= 2), function words
    dropped, OR-joined for ``to_tsquery('simple', ...)``. Falls back to the unfiltered
    tokens when a query is entirely filler (so a bare "is"/"at" still searches something).
    Returns '' when there is nothing usable. The output is only word-chars separated by
    ' | ', so it is injection-safe as a tsquery input."""
    content: list[str] = []
    content_set: set[str] = set()
    first: list[str] = []          # distinct tokens incl. filler — the all-filler fallback
    first_set: set[str] = set()
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) < _SEARCH_MIN_TOKEN_LEN:
            continue
        if tok not in first_set and len(first) < _SEARCH_MAX_TERMS:
            first_set.add(tok)
            first.append(tok)
        if tok not in _STOPWORDS and tok not in content_set:
            content_set.add(tok)
            content.append(tok)
            if len(content) >= _SEARCH_MAX_TERMS:
                break   # enough content terms — scanned past filler, trailing entities kept
    terms = content or first    # fall back to filler when the query was entirely filler
    return " | ".join(terms)


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
    # Reject a nonblank-but-invalid type loudly: silently NULLing a typo'd 'decison'
    # would strip a tier-1 fact's archival protection without the model noticing.
    if memory_type and validate_memory_type(memory_type) is None:
        return {"error": "memory_type must be one of: " + ", ".join(sorted(MEMORY_TYPES))}

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
    """Full-text search across live facts (OR-of-keywords). Returns [] on no usable query."""
    if not query or not query.strip():
        return []
    tsquery = _or_tsquery(query[:_QUERY_MAX])
    if not tsquery:
        return []   # query was all noise / too-short tokens — no DB hit
    try:
        limit = max(1, min(int(limit), _SEARCH_LIMIT_CAP))
    except (TypeError, ValueError):
        limit = 20

    # The tsquery is built from word-chars-only lexemes we extracted (no operators
    # from user input can survive), and is still bound as a %s parameter — so
    # to_tsquery cannot be injected or raise a syntax error on it.
    sql = (
        "SELECT id, subject, predicate, object, valid_from, valid_to, confidence, memory_type, "
        "created_at, ts_rank(search_tsv, q) AS rank "
        "FROM memory_facts, to_tsquery('simple', %s) AS q "
        "WHERE search_tsv @@ q AND valid_to IS NULL AND archived_at IS NULL"
    )
    params: list = [tsquery]
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
    """Set ``valid_to`` on a *live* fact (removing it from the live set).

    Idempotent: only a fact whose ``valid_to`` is NULL is updated, so a repeated call
    can never rewrite an already-set validity window forward (which would corrupt
    point-in-time queries). Re-invalidating an already-invalidated fact returns its
    existing ``valid_to`` unchanged.
    """
    try:
        fact_id = int(fact_id)
    except (TypeError, ValueError):
        return {"error": "fact_id must be an integer"}
    date_err = _date_error(valid_to, "valid_to")
    if date_err:
        return date_err

    # GREATEST(...,valid_from): never write a valid_to earlier than valid_from — that
    # would violate the valid-window CHECK and dead-end the tool in a retry loop. The
    # sneaky case is a future-dated fact invalidated today (CURRENT_DATE < valid_from);
    # clamping to valid_from makes it a zero-duration (never-really-valid) fact.
    row = pg_fetchone(
        """
        UPDATE memory_facts
        SET valid_to = GREATEST(COALESCE(%s::date, CURRENT_DATE), valid_from), updated_at = now()
        WHERE id = %s AND valid_to IS NULL
        RETURNING id, valid_to
        """,
        (valid_to, fact_id),
    )
    if row:
        row["ok"] = True
        return row

    # No row updated: the fact is missing, or already invalidated. Disambiguate so a
    # repeat call is idempotent rather than a misleading "not found".
    existing = pg_fetchone("SELECT id, valid_to FROM memory_facts WHERE id = %s", (fact_id,))
    if not existing:
        # Structured flag alongside the message so the REST layer (#72) can answer 404
        # instead of 400 without matching on the wording — rephrasing this string must
        # never change an HTTP status. Tool callers ignore the extra key.
        return {"error": f"Fact {fact_id} not found", "not_found": True}
    existing["ok"] = True
    existing["already_invalidated"] = True
    return existing


def delete_fact(fact_id: int) -> bool:
    """Hard-delete a fact. Returns False when there was no such row.

    Deliberately NOT exposed as an agent tool — the assistant's way to retire a fact is
    ``invalidate_fact``, which preserves the temporal record so point-in-time queries
    stay honest. This exists only for the human's Memory page (issue #72): a user who
    finds something wrong, private, or captured in error needs it GONE, not
    valid_to-stamped and still readable.
    """
    try:
        fact_id = int(fact_id)
    except (TypeError, ValueError):
        return False
    return bool(pg_execute("DELETE FROM memory_facts WHERE id = %s", (fact_id,)))


# ---------------------------------------------------------------------------
# retrieval tracking (dreaming's usage signal)
# ---------------------------------------------------------------------------

def track_retrieval_for(fact_ids: list[int]) -> None:
    """Bump retrieval_count / last_retrieved_at for *fact_ids*, throttled to once
    per hour per fact. Fire-and-forget — never raises (a tracking failure must not
    break a chat turn or a tool call).

    Only *live* (non-archived, non-expired) facts are bumped, so a fact the nightly
    dreaming cycle archived between selection and this call is simply skipped. The
    converse — dreaming archiving a fact a concurrent chat just surfaced — is a benign,
    low-probability edge (a nightly job vs. daytime chat) and is recoverable via
    ``include_archived``; selection and tracking are deliberately NOT one transaction,
    to keep the chat hot-path lock-free.
    """
    if not fact_ids:
        return
    try:
        # Lock the target rows via an ORDER BY id + FOR UPDATE SKIP LOCKED sub-select:
        # ORDER BY id gives a consistent lock order with the dreaming cycle's
        # `SELECT ... ORDER BY id FOR UPDATE` (no deadlock), and SKIP LOCKED means this
        # chat hot-path bump never blocks behind a running dreaming transaction — a
        # skipped row is exactly the already-documented benign retrieval-vs-archival race.
        pg_execute(
            """
            UPDATE memory_facts
            SET retrieval_count = retrieval_count + 1,
                last_retrieved_at = now(),
                updated_at = now()
            WHERE id IN (
                SELECT id FROM memory_facts
                WHERE id = ANY(%s)
                  AND valid_to IS NULL AND archived_at IS NULL
                  AND (last_retrieved_at IS NULL OR last_retrieved_at < now() - interval '1 hour')
                ORDER BY id
                FOR UPDATE SKIP LOCKED
            )
            """,
            (list(fact_ids),),
        )
    except Exception:
        # Warning, not debug: silent tracking failure makes every fact look
        # never-retrieved, which would make the dreaming job over-archive.
        logger.warning("retrieval tracking failed", exc_info=True)
