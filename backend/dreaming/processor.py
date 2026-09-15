"""Dreaming processor — score live facts and topic files, soft-archive the dormant ones.

Pure-algorithmic (no AI, no provider SDKs). The whole cycle runs in ONE transaction
on ONE connection (the repo's check-then-write rule): SELECT ... FOR UPDATE the live
facts, score them in Python, ``UPDATE ... RETURNING`` the ones to archive, then INSERT
the audit row — commit once. Deriving the archived set from ``RETURNING`` (not the
candidate list) keeps the audit honest under races.

Since issue #72 Phase 4 the cycle has a SECOND unit: live ``topic`` context files, scored
by ``scorer.score_file`` from the read signal ``context_files.service.track_read_for``
records. It runs inside this same transaction, inheriting the advisory lock, the 03:00
slot guard, the ``SET LOCAL`` timeouts and the audit row — no new job, no new lock, no
second due-guard. Two deliberate divergences from the fact pass:

- The file UPDATE does **not** touch ``updated_at``. That column is both the
  write-recency signal ``score_file`` reads and the optimistic-concurrency token the
  Memory editor and confirmed tool writes send back, so bumping it here would inflate
  every archived file's score and 409 an editor that happens to be open.
- The exemptions (``kind``, ``is_protected``) are re-asserted from the GENERATED columns
  rather than from a Python set, because that is exactly what those columns were created
  for. Daily notes are excluded for the same reason chatty's scorer never saw them: a
  daily note is a dated log reachable by name, and archiving one would only remove it
  from ``search_context_files`` while saving nothing in the prompt.

Archival is soft and reversible either way: ``read_file`` still returns archived rows and
any ``write_file``/``append_daily_note`` un-archives the name.

Two entrypoints:
- ``run_dreaming_cycle()`` — run one cycle unconditionally (manual/tests).
- ``run_dreaming_if_due()`` — the scheduler-agnostic contract issue #6 consumes AND the
  interim lifespan loop calls: a transaction-scoped advisory lock (one cycle at a time,
  even across app instances) + a due-guard against the last *successful* run. Safe to
  call on every heartbeat tick; it self-noops until due.
"""

import json
import logging
import time

from core.postgres import get_connection
from dreaming import scorer
from dreaming.schedule import is_due, now_local
from memory.types import TIER_1_ALWAYS_KEEP

logger = logging.getLogger(__name__)

# Transaction-scoped advisory lock key — distinct from the migration runner's
# pg_advisory_lock(1) (see core/postgres.py). Guards against two cycles overlapping.
DREAMING_ADVISORY_LOCK_KEY = 20260705

# Belt-and-suspenders: the scorer already keeps young facts >= stale, but never let a
# scorer bug archive something fresh. Re-asserted in SQL too.
MIN_AGE_DAYS_FOR_ARCHIVE = 30

# NULL last_retrieved_at (never retrieved) must stay NULL (→ recency 0), so it can't be
# collapsed to 0 by GREATEST (which ignores NULLs and would read as "retrieved today").
_SELECT_LIVE = """
    SELECT id, subject, predicate, object, memory_type, retrieval_count, confidence,
           CASE WHEN last_retrieved_at IS NULL THEN NULL
                ELSE (GREATEST(0, EXTRACT(EPOCH FROM (now() - last_retrieved_at)) / 86400.0))::double precision
           END AS days_since_retrieved,
           (GREATEST(0, EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0))::double precision AS days_old
    FROM memory_facts
    WHERE valid_to IS NULL AND archived_at IS NULL
    ORDER BY id
    FOR UPDATE
"""

# Topic files only (Decision C). Protected files (soul.md / MEMORY.md) and daily notes
# are excluded by the GENERATED columns, not by a Python list. NULL last_read_at
# (never read by a read tool) must stay NULL -> recency 0, the same NULL-preservation
# the fact SELECT does for last_retrieved_at.
_SELECT_LIVE_FILES = """
    SELECT id, filename, read_count,
           CASE WHEN last_read_at IS NULL THEN NULL
                ELSE (GREATEST(0, EXTRACT(EPOCH FROM (now() - last_read_at)) / 86400.0))::double precision
           END AS days_since_read,
           (GREATEST(0, EXTRACT(EPOCH FROM (now() - updated_at)) / 86400.0))::double precision AS days_since_written,
           (GREATEST(0, EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0))::double precision AS days_old
    FROM assistant_context_files
    WHERE archived_at IS NULL AND kind = 'topic' AND is_protected = FALSE
    ORDER BY id
    FOR UPDATE
"""

# No `updated_at = now()` here — see the module docstring. The exemptions and the
# minimum age are re-asserted in SQL so a scorer bug can never archive a protected
# file, a daily note or a file created days ago.
_ARCHIVE_FILES = """
    UPDATE assistant_context_files
    SET archived_at = now()
    WHERE id = ANY(%s)
      AND archived_at IS NULL
      AND kind = 'topic' AND is_protected = FALSE
      AND created_at <= now() - make_interval(days => %s)
    RETURNING id, filename
"""

_ARCHIVE = """
    UPDATE memory_facts
    SET archived_at = now(), updated_at = now()
    WHERE id = ANY(%s)
      AND archived_at IS NULL AND valid_to IS NULL
      AND (memory_type IS NULL OR memory_type != ALL(%s))
      AND created_at <= now() - make_interval(days => %s)
    RETURNING id
"""

# finished_at uses clock_timestamp() (real completion time), NOT now() (which is the
# transaction START time) — a cycle that begins at 02:59 and commits after 03:00 must
# record finished_at > 03:00 so the slot-based due-guard sees it ran for the new slot.
_INSERT_RUN = """
    INSERT INTO dreaming_runs (finished_at, status, facts_scored, facts_archived,
                               files_scored, files_archived, details, duration_ms)
    VALUES (clock_timestamp(), 'ok', %s, %s, %s, %s, %s::jsonb, %s)
"""


def _run_cycle(conn) -> dict:
    """Score + archive + audit within the caller's transaction. Returns a summary."""
    start = time.monotonic()
    cur = conn.cursor()

    # Bound the transaction so it can never hang scheduler shutdown / the pool close:
    # lock_timeout caps the FOR UPDATE lock wait, statement_timeout caps any single
    # statement's execution. On timeout the statement raises, the cycle rolls back, and
    # it is retried on the next run.
    cur.execute("SET LOCAL lock_timeout = '10s'")
    cur.execute("SET LOCAL statement_timeout = '30s'")
    cur.execute(_SELECT_LIVE)
    rows = cur.fetchall()

    scored: list[dict] = []
    candidate_ids: list[int] = []
    for row in rows:
        (fid, subject, _predicate, _object, memory_type, retrieval_count, confidence,
         days_since_retrieved, days_old) = row
        result = scorer.score_fact(days_since_retrieved, retrieval_count, days_old, confidence)
        scored.append({
            "id": fid,
            "subject": subject,
            "memory_type": memory_type,
            "score": result["score"],
            "classification": result["classification"],
            "signals": result["signals"],
        })
        if (
            result["classification"] == "dormant"
            and (memory_type is None or memory_type not in TIER_1_ALWAYS_KEEP)
            and float(days_old or 0.0) >= MIN_AGE_DAYS_FOR_ARCHIVE
        ):
            candidate_ids.append(fid)

    archived_ids: list[int] = []
    if candidate_ids:
        cur.execute(_ARCHIVE, (candidate_ids, sorted(TIER_1_ALWAYS_KEEP), MIN_AGE_DAYS_FOR_ARCHIVE))
        archived_ids = [r[0] for r in cur.fetchall()]

    file_scored, archived_files = _score_files(cur)

    scored.sort(key=lambda x: x["score"], reverse=True)
    file_scored.sort(key=lambda x: x["score"], reverse=True)
    duration_ms = int((time.monotonic() - start) * 1000)
    details = json.dumps({
        "scores": scored[:10],
        "archived": archived_ids,
        "file_scores": file_scored[:10],
        "archived_files": archived_files,
    })
    cur.execute(_INSERT_RUN, (len(scored), len(archived_ids),
                              len(file_scored), len(archived_files), details, duration_ms))

    summary = {
        "facts_scored": len(scored),
        "facts_archived": len(archived_ids),
        "archived": archived_ids,
        "files_scored": len(file_scored),
        "files_archived": len(archived_files),
        "archived_files": archived_files,
        "duration_ms": duration_ms,
    }
    logger.info(
        "dreaming: facts scored=%d archived=%d · files scored=%d archived=%d (%dms)",
        summary["facts_scored"], summary["facts_archived"],
        summary["files_scored"], summary["files_archived"], duration_ms,
    )
    return summary


def _score_files(cur) -> tuple[list[dict], list[str]]:
    """The topic-file pass, inside the caller's transaction and cursor.

    Returns ``(scored, archived_filenames)``. Like the fact pass, the archived set comes
    from ``RETURNING`` rather than the candidate list, so a row another transaction
    un-archived (a write lands between the SELECT and the UPDATE) is never claimed in the
    audit. Raising here rolls the WHOLE cycle back, facts included — deliberate: one
    transaction means one outcome, and a half-recorded cycle would make the audit lie.
    """
    cur.execute(_SELECT_LIVE_FILES)
    rows = cur.fetchall()

    scored: list[dict] = []
    candidate_ids: list[int] = []
    for (fid, filename, read_count, days_since_read, days_since_written, days_old) in rows:
        result = scorer.score_file(days_since_read, read_count, days_since_written, days_old)
        scored.append({
            "id": fid,
            "filename": filename,
            "score": result["score"],
            "classification": result["classification"],
            "signals": result["signals"],
        })
        if (
            result["classification"] == "dormant"
            and float(days_old or 0.0) >= MIN_AGE_DAYS_FOR_ARCHIVE
        ):
            candidate_ids.append(fid)

    archived_files: list[str] = []
    if candidate_ids:
        cur.execute(_ARCHIVE_FILES, (candidate_ids, MIN_AGE_DAYS_FOR_ARCHIVE))
        archived_files = [r[1] for r in cur.fetchall()]
    return scored, archived_files


def _record_error(message: str) -> None:
    """Best-effort: log a failed cycle to dreaming_runs in its own transaction so a
    failure is observable AND does not count as a successful run (which would suppress
    the next attempt)."""
    try:
        with get_connection() as conn:
            conn.cursor().execute(
                "INSERT INTO dreaming_runs (finished_at, status, facts_scored, facts_archived, error, duration_ms) "
                "VALUES (clock_timestamp(), 'error', 0, 0, %s, 0)",
                (str(message)[:2000],),
            )
    except Exception:
        logger.debug("failed to record dreaming error row", exc_info=True)


def run_dreaming_cycle() -> dict:
    """Run one dreaming cycle unconditionally (manual / tests). Raises on failure."""
    try:
        with get_connection() as conn:
            return _run_cycle(conn)
    except Exception as e:
        _record_error(str(e))
        raise


def run_dreaming_if_due(now=None) -> dict | None:
    """Run a cycle iff no other cycle holds the advisory lock AND one is due (no
    successful run since the most recent scheduled slot). The stable contract issue
    #6's loop consumes. Returns the summary, ``None`` (skipped), or an error dict.
    Never raises — the caller's scheduler loop must survive any failure.
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (DREAMING_ADVISORY_LOCK_KEY,))
            if not cur.fetchone()[0]:
                return None  # another cycle is running
            cur.execute("SELECT max(finished_at) FROM dreaming_runs WHERE status = 'ok'")
            last_ok = cur.fetchone()[0]  # aware datetime | None
            if not is_due(last_ok, now or now_local()):
                return None
            return _run_cycle(conn)
    except Exception as e:
        logger.exception("dreaming cycle failed")
        _record_error(str(e))
        return {"status": "error", "error": str(e)}
