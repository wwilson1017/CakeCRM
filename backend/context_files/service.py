"""Context-file store — the ContextManager translation (issue #72 Phase 1).

Chatty's ``ContextManager`` is a directory of ``*.md`` files with GCS sync, atomic
writes, ``data_dir.glob()`` ordering and a ``_load-order.json`` sidecar. Every one of
those is a filesystem fact; CakeCRM has one Postgres database and an ephemeral disk. So
the *shape* is what ports: a small set of named markdown documents, two of them
privileged, the rest organized as topic files and per-day daily notes, discoverable by
manifest, bounded by a character cap.

What deliberately did NOT port: GCS sync, ``atomic_write``, ``_load-order.json``,
meetings/transcripts, and ``relevance_prefetch``/``_semantic_prefetch`` (the BM25 and
embedding prefetch — issue #72 Phase 5 territory).

``_first_headline`` ports verbatim: it is pure, and it runs on write so the manifests are
a plain column read rather than a re-parse of every file each turn.

Never raises across the tool/prompt boundary is NOT this module's job — it raises
normally and the callers (``prompt.build_prompt_block``, ``tools``) decide. That matches
``memory/service.py``.
"""

import logging
import os
import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.postgres import pg_execute, pg_fetchall, pg_fetchone

logger = logging.getLogger(__name__)

SOUL_FILE = "soul.md"
MEMORY_FILE = "MEMORY.md"
PROTECTED_FILES = frozenset({SOUL_FILE, MEMORY_FILE})

# Caps. Chatty's MAX_CONTEXT_CHARS is 200_000, which is far too generous for a prompt we
# want cached and which no single-user CRM needs; these mirror memory/service.py's habit
# of bounding everything that can reach the model or the tsquery parser.
MAX_FILE_CHARS = 100_000     # rejected at the tool boundary with a clear message
MAX_READ_CHARS = 40_000      # a read_context_file result handed back to the model
QUERY_MAX = 1_000            # bound tsquery parsing cost (memory/service._QUERY_MAX)
SEARCH_LIMIT_CAP = 50
LIST_LIMIT_CAP = 500
DAILY_MANIFEST_LIMIT = 30

_FILENAME_MAX = 120
_HEADLINE_MAX = 120

_DATE_HEADING_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_HEADING_RE = re.compile(r"^\d{1,2}:\d{2}\s*(am|pm)?", re.IGNORECASE)
# Slug charset for the single path segment after an optional topics/ or daily/ prefix:
# must open with a letter or digit, then letters/digits/'.'/'_'/'-'. \w is unicode-aware
# in Python 3, so 'topics/café-suppliers.md' is a legal name — the same stance
# memory/service.py's tokenizer takes ("keeps 'José' and non-Latin scripts").
_SEGMENT_RE = re.compile(r"^[^\W_][\w.-]*$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_LIVE = "archived_at IS NULL"
_COLUMNS = "id, filename, kind, content, headline, is_protected, written_by, created_at, updated_at"
_META_COLUMNS = "id, filename, kind, headline, is_protected, written_by, created_at, updated_at, length(content) AS size_chars"


class ContextFileError(Exception):
    """A caller-facing validation failure. ``code`` maps to an HTTP status in the router."""

    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# ── time ──────────────────────────────────────────────────────────────────────────
# The repo rule (dreaming/schedule.py) is an explicit TIMEZONE env, never process-local
# time — which is UTC on Railway regardless of intent, so a "local" day boundary would
# silently be a UTC one. Chatty hardcodes America/Chicago; that is its deployment's fact,
# not ours.
# simplification: a 3-line local helper rather than a shared core/localtime module,
# because PR #70 introduces backend/core/localtime.py and creating the same path twice is
# a guaranteed conflict. Collapse this into that module once it merges.
def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("TIMEZONE") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def now_local() -> datetime:
    return datetime.now(_tz())


def today_str() -> str:
    return now_local().strftime("%Y-%m-%d")


# ── filenames ─────────────────────────────────────────────────────────────────────

def normalize_filename(filename: str) -> str:
    """Validate and canonicalize a context-file name, or raise ``ContextFileError``.

    Grammar: ``soul.md`` | ``MEMORY.md`` | ``topics/<slug>.md`` | ``daily/<ISO date>.md``.
    A bare ``<slug>.md`` is NORMALIZED to ``topics/<slug>.md`` rather than rejected — the
    model reaches for plain names and a hard error there is pure friction.

    The filename is a DB key, not a path, so traversal is not the threat; consistency is.
    A store where ``Soul.md`` and ``soul.md`` are different rows means two identities, and
    the UNIQUE index would happily hold both.
    """
    if not isinstance(filename, str):
        raise ContextFileError("filename must be a string")
    # NFC first: 'topics/café.md' can arrive pre- or post-composed and must be ONE row.
    name = unicodedata.normalize("NFC", filename).strip()
    if not name:
        raise ContextFileError("filename is required")
    if len(name) > _FILENAME_MAX:
        raise ContextFileError(f"filename must be at most {_FILENAME_MAX} characters")
    if _CONTROL_RE.search(name) or "\\" in name or ".." in name:
        raise ContextFileError("filename may not contain control characters, '\\' or '..'")
    # The extension is matched AND stored case-insensitively: otherwise 'x.MD' and 'x.md'
    # are two rows under a UNIQUE index, which is the same duplicate-identity problem the
    # protected names have below.
    lowered = name.lower()
    if not lowered.endswith(".md"):
        raise ContextFileError("filename must end with .md")

    # Case-insensitive match on the two protected names, then canonicalize the case, so
    # 'SOUL.MD' and 'memory.md' can never mint a second identity file.
    for protected in PROTECTED_FILES:
        if lowered == protected.lower():
            return protected

    parts = name.split("/")
    if len(parts) == 1:
        prefix, segment = "topics", parts[0]
    elif len(parts) == 2:
        prefix, segment = parts[0].lower(), parts[1]
        if prefix not in ("topics", "daily"):
            raise ContextFileError("only 'topics/' and 'daily/' folders exist")
    else:
        raise ContextFileError("filename may contain at most one '/'")

    stem = segment[: -len(".md")]
    if not stem:
        raise ContextFileError("filename needs a name before '.md'")
    if not _SEGMENT_RE.match(segment):
        raise ContextFileError(
            "filename may use only letters, numbers, '.', '_' and '-', and must end with .md"
        )
    segment = f"{stem}.md"

    if prefix == "daily":
        # BOTH checks are needed. The regex alone would accept '2026-02-30', a day that
        # does not exist. fromisoformat alone accepts '20260821' and ISO week forms like
        # '2026-W34-5' — each a *different* filename for a day whose canonical name is
        # hyphenated, so a second row would shadow a day that read_daily_note (which
        # formats %Y-%m-%d) can never reach.
        if not _ISO_DAY_RE.match(stem):
            raise ContextFileError("daily notes are named daily/YYYY-MM-DD.md")
        try:
            date.fromisoformat(stem)
        except ValueError:
            raise ContextFileError("daily notes are named daily/YYYY-MM-DD.md") from None
    return f"{prefix}/{segment}"


def daily_filename(day: str | None = None) -> str:
    """The canonical filename for a day's note (today when ``day`` is None)."""
    return normalize_filename(f"daily/{day or today_str()}.md")


def _first_headline(content: str) -> str:
    """A short headline for a markdown body — ported verbatim from chatty (it is pure).

    Preference order: an explicit ``Headline:`` line, then the first heading that isn't a
    bare date or timestamp, then the first non-empty body line.
    """
    if not content:
        return ""
    heading_fallback = ""
    text_fallback = ""
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("---"):
            continue
        if line.lower().startswith("headline:"):
            headline = line.split(":", 1)[1].strip()
            if headline:
                return headline[:_HEADLINE_MAX]
        if line.startswith("#"):
            stripped = line.lstrip("#").strip()
            if not stripped:
                continue
            if _DATE_HEADING_RE.match(stripped) or _TIME_HEADING_RE.match(stripped):
                continue
            if not heading_fallback:
                heading_fallback = stripped[:_HEADLINE_MAX]
            continue
        if not text_fallback:
            text_fallback = line[:_HEADLINE_MAX]
    return heading_fallback or text_fallback


# ── reads ─────────────────────────────────────────────────────────────────────────

def list_files(kind: str | None = None, include_archived: bool = False, limit: int = 200) -> list[dict]:
    """File metadata (no bodies), newest first. Bodies are fetched one at a time."""
    where = [] if include_archived else [_LIVE]
    params: list[object] = []
    if kind:
        where.append("kind = %s")
        params.append(kind)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(max(1, min(int(limit or 200), LIST_LIMIT_CAP)))
    # `id` closes the order (issue #58). `is_protected` is a two-value flag and the
    # migration seeds soul.md + MEMORY.md in ONE transaction, so both protected rows
    # share a `now()` `updated_at` exactly — the head of this list is a guaranteed tie,
    # not a rare one. (The topic/daily manifests below need no such term: they already
    # end on `filename`, which is UNIQUE.)
    return pg_fetchall(
        f"SELECT {_META_COLUMNS} FROM assistant_context_files {clause} "
        f"ORDER BY is_protected DESC, updated_at DESC, id DESC LIMIT %s",
        tuple(params),
    )


def read_file(filename: str) -> dict | None:
    """One file with its body, or None. Archived files ARE returned — the Memory UI must
    still be able to show and restore something dreaming put away.

    A blank ``soul.md`` resolves to the built-in ``identity.DEFAULT_SOUL``. Resolving it
    HERE rather than only where the prompt is built is what keeps the three views
    honest: otherwise the system prompt carries the default text while the assistant's
    own ``read_context_file`` and the Memory editor both show an empty file — so Baker
    would be governed by identity text it cannot see, and the user would be shown a
    document that is not what is running.
    """
    name = normalize_filename(filename)
    row = pg_fetchone(
        f"SELECT {_COLUMNS}, archived_at FROM assistant_context_files WHERE filename = %s",
        (name,),
    )
    if row and name == SOUL_FILE and not (row.get("content") or "").strip():
        from assistant.identity import DEFAULT_SOUL
        row["content"] = DEFAULT_SOUL
    return row


def read_daily_note(day: str | None = None) -> str:
    row = pg_fetchone(
        "SELECT content FROM assistant_context_files WHERE filename = %s",
        (daily_filename(day),),
    )
    return (row or {}).get("content") or ""


def search_files(query: str, limit: int = 20) -> list[dict]:
    """FTS over filename + content, most relevant first.

    Reuses ``memory.service``'s OR-of-keywords tokenizer rather than re-deriving one, so
    both stores answer the same query the same way — and so the tokenizer's tuning (the
    'simple' config, the punctuation split) has exactly one definition.
    """
    from memory.service import _or_tsquery

    text = (query or "").strip()[:QUERY_MAX]
    if not text:
        return []
    tsquery = _or_tsquery(text)
    if not tsquery:
        return []
    capped = max(1, min(int(limit or 20), SEARCH_LIMIT_CAP))
    return pg_fetchall(
        f"SELECT {_META_COLUMNS}, ts_rank(search_tsv, to_tsquery('simple', %s)) AS rank "
        f"FROM assistant_context_files "
        f"WHERE {_LIVE} AND search_tsv @@ to_tsquery('simple', %s) "
        f"ORDER BY rank DESC, updated_at DESC, id DESC LIMIT %s",
        (tsquery, tsquery, capped),
    )


def topic_manifest() -> list[dict]:
    """Live topic files as ``{filename, headline, updated_at}`` — what makes an
    un-loaded file discoverable. Chatty renders this from a directory glob."""
    return pg_fetchall(
        f"SELECT filename, headline, updated_at FROM assistant_context_files "
        f"WHERE {_LIVE} AND kind = 'topic' ORDER BY updated_at DESC, filename LIMIT %s",
        (LIST_LIMIT_CAP,),
    )


def daily_manifest(limit: int = DAILY_MANIFEST_LIMIT) -> list[dict]:
    """Recent daily notes, newest first, EXCLUDING today.

    Today is excluded for the same reason chatty excludes it: today's note is injected in
    full elsewhere in the prompt, so listing it again is pure duplication.
    """
    return pg_fetchall(
        f"SELECT filename, headline, updated_at FROM assistant_context_files "
        f"WHERE {_LIVE} AND kind = 'daily' AND filename <> %s "
        f"ORDER BY filename DESC LIMIT %s",
        (daily_filename(), max(1, min(int(limit or DAILY_MANIFEST_LIMIT), LIST_LIMIT_CAP))),
    )


# ── writes ────────────────────────────────────────────────────────────────────────

def write_file(filename: str, content: str, written_by: str = "assistant",
               expected_updated_at: str | None = None) -> dict:
    """Create or overwrite a file; returns the stored row.

    ``expected_updated_at`` is an optimistic-concurrency precondition: the caller sends
    the ``updated_at`` it loaded, and a mismatch raises ``conflict`` (409) instead of
    silently discarding whatever was written in between. Two callers supply it — the
    REST/UI editor from the browser, and a CONFIRMED tool write, for which the engine
    stamps the version at proposal time and injects it on approval
    (``context_files.tools.pending_binding``). Note the confirmation gate is the reason
    that second caller needs this, not a substitute for it: the gate is what creates a
    human-length gap between composing the overwrite and running it, and the user can
    edit the same file in the Memory page inside that gap. An UNCONFIRMED power-mode
    write passes None, having no such gap.

    The precondition is enforced INSIDE the UPDATE, not by a read-then-write, for two
    reasons. A separate SELECT is a TOCTOU window — an append landing between the check
    and the write would be silently discarded, which is the exact failure the token
    exists to prevent. And the comparison has to happen in Postgres: psycopg returns a
    ``datetime`` whose ``str()`` is space-separated ('2026-08-21 10:00:00+00'), while the
    browser round-trips the ISO 'T' form FastAPI serialized, so comparing the two as
    Python strings rejects every legitimate save. ``%s::timestamptz`` parses both.

    A write to an archived name UNARCHIVES it. The UNIQUE(filename) index is global, so
    without this a name dreaming had put away could never be reused.
    """
    name = normalize_filename(filename)
    # Reject, never coerce. Silently turning a non-string into "" means a schema-invalid
    # but parseable call like {"content": []} ERASES the file — and for an ordinary topic
    # file that runs unconfirmed in power mode.
    if not isinstance(content, str):
        raise ContextFileError("content must be a string")
    body = content
    if len(body) > MAX_FILE_CHARS:
        raise ContextFileError(
            f"content is {len(body)} characters; the limit is {MAX_FILE_CHARS}. "
            "Split it into smaller topic files.",
            code="too_large",
        )
    if expected_updated_at is not None:
        try:
            updated = pg_execute(
                "UPDATE assistant_context_files SET "
                "  content = %s, headline = %s, written_by = %s, "
                "  archived_at = NULL, updated_at = now() "
                "WHERE filename = %s AND updated_at = %s::timestamptz",
                (body, _first_headline(body), written_by, name, expected_updated_at),
            )
        except Exception as exc:   # an unparseable token is a client error, not a 500
            raise ContextFileError(
                "Invalid version token; reload the file before saving.", code="conflict",
            ) from exc
        if not updated:
            # Zero rows: the file changed under us, or it is gone. Either way the editor
            # must reload rather than resurrect a deleted file from a stale buffer.
            raise ContextFileError(
                "This file changed since you opened it. Reload before saving.",
                code="conflict",
            )
        return read_file(name) or {}
    pg_execute(
        "INSERT INTO assistant_context_files (filename, content, headline, written_by) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (filename) DO UPDATE SET "
        "  content = EXCLUDED.content, headline = EXCLUDED.headline, "
        "  written_by = EXCLUDED.written_by, archived_at = NULL, updated_at = now()",
        (name, body, _first_headline(body), written_by),
    )
    return read_file(name) or {}


def append_daily_note(content: str, day: str | None = None,
                      written_by: str = "assistant") -> dict:
    """Append a timestamped entry to a day's note, creating it if absent.

    ONE statement, so there is no check-then-write race: two concurrent appends serialize
    on the conflicting row and both survive. Entry format ports chatty verbatim
    (``### {h:mm am/pm}``) except the zone abbreviation, which is emitted from the
    configured TIMEZONE via %Z rather than a hardcoded 'CT' literal.

    Note the guarantee is append-vs-append only: a whole-file overwrite racing an append
    still last-writes-wins, which is why the REST editor carries an updated_at
    precondition.
    """
    body = (content or "").strip()
    if not body:
        raise ContextFileError("daily note entry is empty")
    if len(body) > MAX_FILE_CHARS:
        raise ContextFileError(
            f"entry is {len(body)} characters; the limit is {MAX_FILE_CHARS}.",
            code="too_large",
        )
    name = daily_filename(day)
    day_str = name[len("daily/"): -len(".md")]
    # %I is zero-padded on every platform; strip it for readability, as chatty does.
    stamp = now_local().strftime("%I:%M %p %Z").lstrip("0")
    entry = f"\n### {stamp}\n\n{body}\n"
    first = f"# {day_str}\n{entry}"
    # The upsert's WHERE only guards the CONFLICT branch. On a fresh note there is no
    # conflict, so a body sitting exactly at the cap would slip past with the date and
    # timestamp scaffolding pushing it over.
    if len(first) > MAX_FILE_CHARS:
        raise ContextFileError(
            f"entry is too long; the limit is {MAX_FILE_CHARS} characters.",
            code="too_large",
        )

    # The headline is set on INSERT only, never on append: _first_headline skips bare
    # date and time headings, so it resolves to the FIRST entry's first line — which no
    # later append changes. Recomputing it would need the merged body back from the DB,
    # turning an atomic upsert into a read-modify-write for a value that cannot differ.
    # The size guard lives in the WHERE of the upsert, not in Python: checking the merged
    # length beforehand would need a read first, reopening the race this single statement
    # exists to close. Capping only the new entry (as this first did) lets a note grow
    # without bound across many valid appends until Postgres fails building search_tsv.
    appended = pg_execute(
        "INSERT INTO assistant_context_files (filename, content, headline, written_by) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (filename) DO UPDATE SET "
        "  content = rtrim(assistant_context_files.content, E' \\n\\t') || %s, "
        "  written_by = EXCLUDED.written_by, archived_at = NULL, updated_at = now() "
        "WHERE length(assistant_context_files.content) + %s <= %s",
        (name, first, _first_headline(first), written_by,
         "\n" + entry, len(entry) + 1, MAX_FILE_CHARS),
    )
    if not appended:
        # An INSERT always reports one row, so zero means the conflict path ran and its
        # WHERE rejected the append — the note is full.
        raise ContextFileError(
            f"Today's note has reached the {MAX_FILE_CHARS}-character limit; "
            "record this in a topic file instead.",
            code="too_large",
        )
    return {"filename": name, "date": day_str, "ok": True}


def delete_file(filename: str) -> bool:
    """Hard-delete a file. Protected files are refused — soul.md and MEMORY.md are the
    two documents whose absence would change who Baker is."""
    name = normalize_filename(filename)
    if name in PROTECTED_FILES:
        raise ContextFileError(f"{name} is protected and cannot be deleted", code="forbidden")
    return bool(pg_execute(
        "DELETE FROM assistant_context_files WHERE filename = %s", (name,)
    ))
