"""Gmail touch-scan heartbeat job (issue #17) — the whole feature lives here.

WHAT IT DOES. Every ``GMAIL_SCAN_INTERVAL_MINUTES`` (default 15), and only when Gmail
is connected, this reads the newest inbox messages of the last couple of days through
the EXISTING allow-listed ``gmail.client.call_gmail(ops.list_messages_op, ...)`` seam,
matches each message's sender address to a CRM contact by exact (case-insensitive)
email, and logs an ``'email'`` touch to ``activity_log`` so #16's touch counts see
exchanges nobody logged. Recurring senders with no matching contact surface as a
deduplicated "create contact?" alert.

TRUST GUARANTEE (absolute, see SECURITY.md / CLAUDE.md). This is READ ONLY. It calls
only the already-approved ``list_messages_op`` (no new Gmail op, no scope change) and
creates NOTHING in Gmail. There is no way to reach a draft/send surface from here.

IDEMPOTENCY. A dedicated ledger table (``gmail_scanned_messages``, keyed on the Gmail
internal message id) is the single idempotency authority: every message is claimed with
``INSERT ... ON CONFLICT DO NOTHING RETURNING`` and only a first-sight claim does any
work, so re-scans across overlapping windows / restarts never double-log a touch nor
double-count an unmatched sender. The claim + contact lookup + activity insert + ledger
finalize happen in ONE transaction per message, so a crash rolls back the claim and the
message self-heals on the next tick. (The lone ``message_id`` primary key is per-account;
reconnecting a *different* mailbox may leave stale ledger/unmatched rows — the same class
of accepted bound as a demo-data wipe leaving them; a rescan/reset tool is future work.)

NO FABRICATION. A touch is attributed to a deal ONLY when the matched contact has exactly
one open deal (``stage NOT IN ('won', 'lost')``); with zero or several open deals the
touch is contact-only (``deal_id`` NULL — still on the contact timeline and company
rollup, just not fed to #16). Guessing a deal would fabricate an attribution into a
derived metric, which #16 forbids.

SCHEDULING (tier-2). Driven by its OWN ``gmail_scan`` scheduler job (heartbeat/scheduler.py),
NOT as a ``maintenance_tick`` sibling: an inbox scan is slow / network-bound and a hung
request in ``maintenance_tick``'s shared ``max_instances=1`` slot would stall the dreaming
and lead-score passes — the same decoupling principle that split ``heartbeat_turn`` from
``maintenance_tick`` in #6. That decoupling protects those passes, not this job — so the
list call itself runs under a wall-clock deadline (``_SCAN_HTTP_DEADLINE``) on a worker
thread: a hung request abandons the pass and frees the ``gmail_scan`` slot for the next
tick instead of parking it forever. ``t.join(timeout)`` cannot TERMINATE a hung request
(only stop waiting on it), so a SINGLE in-flight worker is reused across passes: while one
is still alive the guard declines to spawn another, bounding leaked workers to at most ONE
at a time — never one per interval.

Since #64 the shared ``gmail/client.py`` transport is BOUNDED (an owned per-socket-op
stall timeout plus a per-call request budget), and this pass passes its own
``_SCAN_CALL_BUDGET``. So the ordinary silent-socket hang — the failure this machinery was
built for — now usually surfaces as a real ``GmailTimeoutError`` from inside the worker
BEFORE the join deadline fires, and the pass records that error instead of a generic join
timeout. The deadline and the single-in-flight guard are kept, not retired, because that
"usually" is load-bearing: the transport bounds one socket OPERATION, so a request stalling
separately on connect, TLS and read can still outlast the join deadline, and nothing there
bounds a DNS resolution stall, CPU starvation or an unforeseen SDK path. Stated honestly,
because it is the residual risk: a worker abandoned for one of those reasons is still
potentially unbounded, and ``_inflight`` limits concurrency rather than guaranteeing
recovery — while it is alive every later pass declines to scan at all. Gated ONLY on Gmail
being connected: no AI keys are needed (deal touches merely enqueue
``touch_count_service.schedule_recompute``).
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from alerts import service as alerts
from core.config import settings
from core.postgres import get_connection, pg_execute
from crm import touch_count_service
from gmail import ops, store
from gmail.client import call_gmail

logger = logging.getLogger(__name__)

_MAX_RESULTS = 50              # newest-first page cap; bounds Gmail HTTP to <=51 calls/scan
_SCAN_WINDOW_DAYS = 2          # floor; widened when the interval exceeds a day
_MINUTES_PER_DAY = 1440
_UNMATCHED_ALERT_THRESHOLD = 3
_MAX_NOTE_CHARS = 300          # bound untrusted subject/sender text persisted per touch
_MAX_ERROR_CHARS = 500
_DATE_SLACK = timedelta(days=1)   # tolerance around the window for the Date-header clamp
_SCAN_HTTP_DEADLINE = 90       # seconds; job-layer backstop behind the owned transport
                               # (see _SCAN_CALL_BUDGET) — abandons a pass that overruns
_SCAN_CALL_BUDGET = 60         # per-pass transport budget handed to call_gmail (#64).
                               # MUST satisfy
                               #   _SCAN_CALL_BUDGET + client._HTTP_TIMEOUT_SECONDS
                               #       < _SCAN_HTTP_DEADLINE
                               # so that in the COMMON case — a single stalled read on the
                               # request that was already in flight when the budget ran
                               # out — the worker gives up on its own before t.join()
                               # stops waiting, and nothing is leaked. call_gmail resolves
                               # the deadline before its own setup, so both clocks start
                               # at essentially the same instant and that margin is real.
                               # NECESSARY, NOT SUFFICIENT: _HTTP_TIMEOUT_SECONDS bounds
                               # one socket OPERATION, so a request that stalls separately
                               # on connect, on TLS and on read can still outlast the join
                               # deadline. That is precisely why the deadline and the
                               # single-in-flight guard below are kept rather than retired.
                               # Pinned by test_scan_budget_stays_inside_join_deadline.
                               # Smaller than the interactive default despite more requests:
                               # 51 cheap metadata reads, and failing fast on a background
                               # job that retries next tick costs nothing.
_ALERT_SOURCE = "gmail_touch_scan"


# ── public entrypoint ────────────────────────────────────────────────────────

def run_scan_if_due() -> dict | None:
    """Tick entrypoint (called by heartbeat.service._maybe_run_gmail_scan via the
    dedicated gmail_scan job). Returns a summary dict on a run, or None when Gmail is
    not connected (hidden affordance), when the claim is lost (throttled), or when the
    claim itself errors. NEVER raises — a scan failure must not disturb the scheduler."""
    try:
        if not store.is_connected():
            return None                       # FIRST gate, before ANY DB write
    except Exception:
        return None                           # fail-closed, mirroring get_gmail_tools()
    try:
        if not _claim_due():
            return None                       # throttled / lost the claim
    except Exception:
        logger.warning("gmail scan: claim failed", exc_info=True)
        return None
    try:
        summary = _run_scan()                 # slow Gmail HTTP happens here, OUTSIDE any txn
    except Exception as e:                     # incl. GmailAuthError mid-flight
        logger.warning("gmail scan failed", exc_info=True)
        _record_result("error", error=str(e)[:_MAX_ERROR_CHARS])
        return {"status": "error"}
    seen, errs = summary.get("seen", 0), summary.get("errors", 0)
    # A pass where EVERY message failed is a systemic problem (schema drift, poison batch)
    # — record it as 'error', not a clean 'ok'. Partial failures stay 'ok' but are noted.
    status = "error" if seen and errs >= seen else "ok"
    detail = f"{errs} of {seen} messages failed this pass" if errs else ""
    _record_result(status, error=detail, seen=seen, new=summary.get("new", 0),
                   logged=summary.get("logged", 0))
    return {"status": status, **summary}


# ── cadence claim (rowcount-UPDATE due-guard; heartbeat-turn idiom) ───────────

def _claim_due() -> bool:
    """Atomically claim this interval's scan slot. The WHERE clause is the due-guard
    AND the won-the-claim guard in one statement; 'running' is observability only (a
    crashed run self-heals once the interval elapses — no status-based wedge)."""
    return pg_execute(
        "UPDATE gmail_scan_state SET last_scan_at = now(), last_status = 'running' "
        "WHERE id = 1 AND (last_scan_at IS NULL "
        "OR last_scan_at <= now() - make_interval(mins => %s))",
        (settings.gmail_scan_interval_minutes,),
    ) == 1


def _window_days() -> int:
    """Day-granular scan window; kept >= the interval when set to a multi-day cadence."""
    return max(_SCAN_WINDOW_DAYS, settings.gmail_scan_interval_minutes // _MINUTES_PER_DAY + 1)


# Handle to the current/most-recent list worker, so a hung one is REUSED across passes
# rather than re-spawned (see _list_recent_inbox). Touched only by the single-instance
# gmail_scan job (max_instances=1, coalesce=True), so no lock is needed; the worker thread
# writes only its own result box, never this global.
_inflight: threading.Thread | None = None


def _list_recent_inbox() -> list[dict]:
    """Fetch recent inbox mail via the existing approved list op under ``_SCAN_CALL_BUDGET``,
    with a wall-clock join deadline on a DAEMON worker thread as a backstop and AT MOST ONE
    worker in flight across passes.

    Two bounds, and the inner one does most of the work. The transport budget (#64) makes the
    worker itself give up and RETURN — the arithmetic in ``_SCAN_CALL_BUDGET`` keeps that
    inside ``_SCAN_HTTP_DEADLINE`` for the common single-stall case — so the ordinary
    transport hang no longer reaches the join at all, and the pass records the real
    ``GmailTimeoutError`` rather than a generic deadline message.

    The join deadline remains for the cases that budget does not cover: a request stalling
    across several socket operations (connect, TLS, read — each bounded separately), a DNS
    resolution stall, CPU starvation, an unforeseen SDK path. On overrun we RAISE (abandoning the pass) rather
    than park the gmail_scan slot — the caller records an error and the next tick retries.
    ``t.join(timeout)`` only stops THIS caller waiting; it cannot terminate the worker. So a
    handle stays in the module-global ``_inflight`` and a later pass REUSES it rather than
    spawning a second, bounding leaked workers to one at a time. Be clear about what that
    does and does not promise: a worker abandoned here hit a cause the transport does not
    bound, so it may run indefinitely, and while it does every later pass declines to scan.
    ``_inflight`` caps concurrency; it does not guarantee recovery.

    A daemon thread (NOT a ThreadPoolExecutor) is deliberate: the executor registers an
    atexit hook that joins every worker it ever spawned, so a permanently-hung request
    would block interpreter shutdown forever. A daemon thread is force-killed at exit and
    never joined, so even a hung request can't wedge process shutdown."""
    global _inflight

    if _inflight is not None:
        if _inflight.is_alive():
            # A prior pass's request is still outstanding (persistent transport hang). NEVER
            # start a second worker — one leaked thread is the bound, not one per interval.
            raise TimeoutError(
                f"gmail list from a prior pass still hung past {_SCAN_HTTP_DEADLINE}s "
                "deadline — reusing the single in-flight worker (none spawned)"
            )
        _inflight = None   # the prior request finally finished; discard it and fetch fresh

    box: dict = {}

    def _worker():
        try:
            box["messages"] = call_gmail(
                ops.list_messages_op,
                budget_seconds=_SCAN_CALL_BUDGET,
                query=f"in:inbox newer_than:{_window_days()}d", max_results=_MAX_RESULTS,
            )
        except Exception as e:   # carry the real error back for the caller to record
            box["error"] = e

    t = threading.Thread(target=_worker, name="gmail-scan-list", daemon=True)
    _inflight = t
    t.start()
    t.join(timeout=_SCAN_HTTP_DEADLINE)
    if t.is_alive():
        # Leave _inflight = t so the NEXT pass reuses it rather than spawning another.
        raise TimeoutError(f"gmail list exceeded {_SCAN_HTTP_DEADLINE}s deadline")
    _inflight = None
    if "error" in box:
        raise box["error"]
    return box.get("messages", [])


# ── scan orchestration ───────────────────────────────────────────────────────

def _run_scan() -> dict:
    own_email = (store.get_row().get("email") or "").strip().lower()
    messages = _list_recent_inbox()
    seen, new, logged, errors = len(messages), 0, 0, 0
    recompute: set[int] = set()
    alert_pending: dict[str, int] = {}        # email -> count at crossing (dict dedups in-batch)

    for msg in messages:
        try:
            out = _process_message(msg, own_email)
        except Exception:
            errors += 1
            logger.warning("gmail scan: message %s failed", msg.get("id"), exc_info=True)
            continue                          # one bad message never aborts the batch
        if out["outcome"] == "duplicate":
            continue
        new += 1
        if out["outcome"] == "logged":
            logged += 1
            if out.get("deal_id"):
                recompute.add(out["deal_id"])
        elif out["outcome"] == "unmatched" and out.get("alert_email"):
            alert_pending[out["alert_email"]] = out["alert_count"]

    # ── post-commit side effects ONLY (never inside a per-message txn) ─────────
    for deal_id in sorted(recompute):
        if not touch_count_service.schedule_recompute(deal_id):
            # Best-effort: a full queue / setup failure just leaves #16's estimate to
            # self-heal on the deal's next CRM activity or a manual backfill.
            logger.debug("gmail scan: recompute not queued for deal %s", deal_id)
    for email, count in alert_pending.items():
        _fire_unmatched_alert(email, count)

    if errors:
        logger.warning("gmail scan: %d of %d messages failed this pass", errors, seen)
    return {"seen": seen, "new": new, "logged": logged, "errors": errors}


def _process_message(msg: dict, own_email: str) -> dict:
    """Process ONE message in a single transaction (claim + match + log / bump). All
    statements use RETURNING + fetchone()/fetchall() (never rowcount, never row_to_dict
    reuse). No pg_*/alerts/schedule_recompute call may run inside the `with` block —
    each opens its own pooled connection; side effects are returned and fired by the
    caller post-commit. A raise anywhere here rolls back the claim -> re-scan next tick."""
    mid = msg.get("id")
    if not mid:
        raise ValueError("gmail message missing id")   # never claim without a real id
    sender = _parse_sender(msg.get("from", ""))

    if not sender:
        skip_outcome = "skipped_invalid"
    elif own_email and sender == own_email:
        skip_outcome = "skipped_self"
    else:
        skip_outcome = ""

    with get_connection() as conn:
        cur = conn.cursor()

        # Claim (first-sight test + crash-rollback anchor).
        cur.execute(
            "INSERT INTO gmail_scanned_messages (message_id, sender_email, outcome) "
            "VALUES (%s, %s, %s) ON CONFLICT (message_id) DO NOTHING RETURNING message_id",
            (mid, sender, skip_outcome or "seen"),
        )
        if cur.fetchone() is None:
            return {"outcome": "duplicate"}    # already processed (txn commits as a no-op)
        if skip_outcome:
            return {"outcome": skip_outcome}   # claimed so it is never re-examined

        # Contact match (deterministic, index-backed; oldest contact wins on a tie).
        cur.execute(
            "SELECT id FROM contacts WHERE lower(email) = %s AND email <> '' "
            "ORDER BY id LIMIT 1",
            (sender,),
        )
        row = cur.fetchone()

        if row is not None:                    # ── matched branch ──
            contact_id = row[0]
            cur.execute(
                "SELECT id FROM deals WHERE contact_id = %s "
                "AND stage NOT IN ('won', 'lost') AND archived_at IS NULL "
                "ORDER BY id LIMIT 2",
                (contact_id,),
            )
            open_deals = cur.fetchall()
            deal_id = open_deals[0][0] if len(open_deals) == 1 else None  # no fabrication

            note = _build_note(sender, msg.get("subject", ""))
            occurred_at = _parse_occurred_at(msg.get("date", ""))
            cur.execute(
                "INSERT INTO activity_log (activity, note, contact_id, deal_id, created_at) "
                "VALUES ('email', %s, %s, %s, COALESCE(%s::timestamptz, now())) RETURNING id",
                (note, contact_id, deal_id, occurred_at),
            )
            activity_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE gmail_scanned_messages SET outcome = 'logged', contact_id = %s, "
                "deal_id = %s, activity_id = %s WHERE message_id = %s",
                (contact_id, deal_id, activity_id, mid),
            )
            return {"outcome": "logged", "deal_id": deal_id}

        # ── unmatched branch ──
        cur.execute(
            "INSERT INTO gmail_unmatched_correspondents (email) VALUES (%s) "
            "ON CONFLICT (email) DO UPDATE SET "
            "message_count = gmail_unmatched_correspondents.message_count + 1, "
            "last_seen_at = now() RETURNING message_count, alerted_at",
            (sender,),
        )
        count, alerted_at = cur.fetchone()
        cur.execute(
            "UPDATE gmail_scanned_messages SET outcome = 'unmatched' WHERE message_id = %s",
            (mid,),
        )
        needs_alert = count >= _UNMATCHED_ALERT_THRESHOLD and alerted_at is None
        return {"outcome": "unmatched",
                "alert_email": sender if needs_alert else None,
                "alert_count": count}


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_sender(from_header: str) -> str:
    """Lowercased addr-spec from a From header, or '' if it isn't a plausible address."""
    addr = parseaddr(from_header or "")[1].strip().lower()
    if "@" not in addr or any(c.isspace() for c in addr):
        return ""
    return addr


def _parse_occurred_at(date_header: str) -> datetime | None:
    """The email's Date as an aware datetime, or None (-> DB now()). Naive values (a
    ``-0000`` / zoneless header) are normalized to UTC before comparison so we never
    raise on aware-vs-naive; a spoofed/garbage or out-of-window date falls back to None
    so it cannot backdate the timeline arbitrarily."""
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    earliest = now - _DATE_SLACK - timedelta(days=_window_days())
    if dt < earliest or dt > now + _DATE_SLACK:
        return None
    return dt


def _build_note(sender: str, subject: str) -> str:
    note = f'Inbound email from {sender}: {subject or "(no subject)"}'
    return note[:_MAX_NOTE_CHARS]


def _fire_unmatched_alert(email: str, count: int) -> None:
    """Create the "create contact?" alert FIRST, stamp alerted_at second — a stamp
    failure can only cause a harmless dedup-refresh retry (create_alert is idempotent
    on (source, source_id)), never a lost suggestion. Never raises."""
    try:
        alerts.create_alert(
            title="Frequent email correspondent isn't in your CRM",
            message=(f"{email} has sent {count} recent emails but doesn't match any "
                     "contact. Create a contact to log these touches automatically."),
            source=_ALERT_SOURCE, source_id=email,
        )
        pg_execute(
            "UPDATE gmail_unmatched_correspondents SET alerted_at = now() "
            "WHERE email = %s AND alerted_at IS NULL",
            (email,),
        )
    except Exception:
        logger.warning("gmail scan: unmatched alert for %s failed", email, exc_info=True)


def _record_result(status: str, error: str = "", seen: int = 0,
                    new: int = 0, logged: int = 0) -> None:
    """Persist the last-run summary. Never raises (observability must not fail a scan)."""
    try:
        pg_execute(
            "UPDATE gmail_scan_state SET last_finished_at = now(), last_status = %s, "
            "last_error = %s, last_messages_seen = %s, last_messages_new = %s, "
            "last_touches_logged = %s WHERE id = 1",
            (status, error, seen, new, logged),
        )
    except Exception:
        logger.warning("gmail scan: recording result failed", exc_info=True)
