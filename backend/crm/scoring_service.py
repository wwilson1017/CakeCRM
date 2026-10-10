"""Lead scoring — a pure-algorithmic (NO AI) health score for deals and contacts.

Every deal and contact gets a 0-100 "how alive is this?" number computed by plain
arithmetic over data the CRM already has: pipeline stage, chatter engagement, deal
value, entity linkage, recency of the last touch, and age. It is always-on CRM core
(NOT gated on ``ai_ready``) because it makes no provider calls.

Design notes
------------
* **Dedicated columns, never ``deals.probability``.** ``probability`` is a live,
  user/assistant-editable, provenance-tracked "win probability" field; overwriting it
  from an auto-recompute would clobber deliberate human/assistant input with no guard.
  Lead score lives in its own ``deals.lead_score`` / ``contacts.lead_score`` columns and
  is never user/tool/assistant-writable (absent from every update allow-set and schema).
* **Generic constants only.** None of the numbers below are derived from any real
  customer's data — they are first-cut generic sales reasoning (an unlinked deal is a
  placeholder; a $0 deal is unqualified; silence past ~90 days is decay). The CAKE-OS
  blueprint's tuned constants (avg-won-age, unit brackets, win-rate multipliers) are
  proprietary and are deliberately NOT ported.
* **Multiplicative chain off a stage baseline**, clamped to [1, 99]; ``won`` / ``lost``
  short-circuit to 100 / 0. Multipliers are damped so a realistic hot deal lands in the
  high 80s, leaving headroom below the clamp so top deals still sort against each other.
* **Persisted + recomputed on write events, self-healing.** Recompute is inline pure
  math (no queue/worker/LLM). Each recompute serializes the entity with a per-entity
  transaction-level advisory lock, so concurrent recomputes cannot persist an older score
  over a newer one. A daily heartbeat refresh keeps the time-decay factors fresh, and a
  manual backfill endpoint/tool repairs drift.
* **Deliberate omissions:** no standalone contact "age" factor (recency subsumes it); the
  blueprint's ``last_contact_date`` / ``number_of_units`` custom-field factors are dropped
  (CakeCRM ships zero custom-field definitions) — ``last_contact_date`` is re-sourced
  natively as the newest chatter/activity timestamp. ``update_note`` / ``update_activity``
  are non-triggers (content-only edits don't change counts or timestamps).
* ``deal_temperature`` **was** on that dropped list for the same reason, and issue #125 took
  it off by removing the reason rather than working around it: it is a real ``deals`` column
  here, not a custom field, so the factor exists on every install instead of only on one
  where an admin happened to create a definition with the right key. Its multipliers and its
  neutral-when-unset default diverge from the blueprint deliberately —
  see ``_temperature_multiplier``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from core.postgres import (
    get_connection,
    pg_execute,
    pg_fetchall,
    pg_fetchone,
    row_to_dict,
)

logger = logging.getLogger(__name__)

# --- advisory lock keys (registered in core/postgres.py's registry comment) ---
# Per-entity recompute uses the two-int form pg_advisory_xact_lock(namespace, id);
# deals.id / contacts.id are SERIAL (int4) so they fit. The daily refresh is lock-free
# (see run_score_refresh_if_due): maintenance_tick is max_instances=1 and these per-entity
# locks are the only serialization it needs.
_DEAL_LOCK_NS = 1801
_CONTACT_LOCK_NS = 1802

_REFRESH_INTERVAL = timedelta(hours=24)
# Per-tick cap for the time-decay refresh. maintenance_tick is a shared, fast-and-bounded
# (T1) slot, so the refresh MUST NOT sweep the whole table on the tick it fires — it
# processes at most this many stale entities per pass and self-resumes on later ticks.
# Kept small (each entity is ~4 indexed round trips) so even on a high-latency remote
# Postgres a single drain tick stays well within the 60s interval and never delays the
# tick's other passes.
_REFRESH_BATCH = 100

# Housekeeping notes that provenance_service.confirm inserts directly into crm_chatter
# ("Confirmed AI-populated value for '<field>'.") are CRM audit rows, NOT customer engagement,
# so they are excluded from the engagement count/recency. Must mirror that message prefix.
#
# PUBLIC since #77: the contact list's derived `last_contact_at` and
# analytics_service.get_contact_staleness both answer the question "when did we last talk to
# this person?", and all three have to exclude the same rows or they contradict each other —
# the assistant would call a contact stale while the list showed it touched today.
#
# Since #239 this is a FAMILY, not one pattern: the archive/restore audit notes that
# archive_deal, merge_deals and update_contact/update_company write are state changes, not
# touches, so archiving a record must not reset its staleness clock either. Every writer
# builds its text from these constants, so the exclusion cannot drift from the writer.
#
# Rendered as `starts_with(...)` literals rather than bound LIKE params, because the one
# consumer that needs it most — service.LAST_TOUCH_SQL — is a param-less fragment
# interpolated into other parameterized statements, where a literal `%` raises at execute
# time. `starts_with` carries no `%`, and the assert below keeps it that way. Accepted
# residual, the same one the provenance prefix always had: a human note that happens to
# start with one of these prefixes is excluded too.
PROVENANCE_NOTE_PREFIX = "Confirmed AI-populated value for "
ARCHIVE_NOTE_PREFIX = "Archived — "
RESTORE_NOTE = "Restored from archive."
# #279: every set, clear or edit of a deal's closed_on ("Closed on: none → 2026-10-01").
CLOSED_ON_NOTE_PREFIX = "Closed on: "
HOUSEKEEPING_NOTE_PREFIXES = (
    PROVENANCE_NOTE_PREFIX, ARCHIVE_NOTE_PREFIX, RESTORE_NOTE, CLOSED_ON_NOTE_PREFIX,
)
assert all("%" not in p and "'" not in p for p in HOUSEKEEPING_NOTE_PREFIXES)


def not_housekeeping_sql(column: str) -> str:
    """An AND-able predicate excluding every housekeeping note. No `%` and no bind
    parameter, on purpose — see HOUSEKEEPING_NOTE_PREFIXES."""
    return " AND ".join(f"NOT starts_with({column}, '{p}')" for p in HOUSEKEEPING_NOTE_PREFIXES)


# Safety valve for a MANUAL backfill("all") of a huge dataset (runs off-thread via the
# endpoint/tool, never in the tick): bound + log rather than run unboundedly.
_REFRESH_MAX_ROWS = 20000

# Per-stage starting score for the multiplicative chain. Keyed by the same stage strings as
# crm.service.DEAL_STAGES, but a LOCAL literal on purpose: service.py imports this module for
# its trigger hooks, so importing service back would be a circular import. The open stages here
# also double as the "is this an open stage?" membership set in the contact deal-linkage factor.
STAGE_BASELINES = {"lead": 8, "qualified": 18, "proposal": 32, "negotiation": 45}
_TERMINAL_WON = "won"
_TERMINAL_LOST = "lost"


# ---------------------------------------------------------------------------
# Pure helpers (no DB, no clock except _days_since which takes ``now``)
# ---------------------------------------------------------------------------

def _clamp(score: float) -> int:
    """Clamp a non-terminal score into the [1, 99] band (0/100 are terminal-only)."""
    return int(max(1, min(99, round(score))))


def _days_since(value, now: datetime) -> float | None:
    """Days between ``value`` (ISO string / datetime / None) and ``now`` (aware).

    Returns None when ``value`` is falsy. Future timestamps clamp to 0.0 (freshest),
    never negative.
    """
    if not value:
        return None
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _safe_value(value) -> float:
    """Normalize a deal value to a non-negative float (None/negative/garbage -> 0)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


# --- deal factor multipliers ---

def _engagement_multiplier(count: int) -> float:
    if count <= 0:
        return 0.6
    if count <= 2:
        return 0.9
    if count <= 5:
        return 1.05
    if count <= 10:
        return 1.15
    return 1.25


def _value_multiplier(value: float) -> float:
    # Monotonic non-decreasing: a larger deal is never penalised.
    if value <= 0:
        return 0.7
    if value < 10_000:
        return 0.9
    if value < 50_000:
        return 1.05
    if value < 100_000:
        return 1.15
    return 1.2


def _relationship_multiplier(has_contact: bool, has_company: bool) -> float:
    if has_contact and has_company:
        return 1.15
    if has_contact or has_company:
        return 1.0
    return 0.6


def _recency_multiplier(days: float | None) -> float:
    if days is None:
        return 0.7  # no touch signal at all -> mild penalty
    if days <= 7:
        return 1.1
    if days <= 14:
        return 1.05
    if days <= 30:
        return 1.0
    if days <= 60:
        return 0.85
    if days <= 90:
        return 0.7
    return 0.5


def _age_multiplier(days: float | None) -> float:
    if days is None:
        return 1.0
    if days < 30:
        return 1.05
    if days <= 90:
        return 1.0
    if days <= 180:
        return 0.9
    if days <= 365:
        return 0.75
    return 0.6


# Deal temperature (issue #125) — the rep's own read on a deal, and the ONLY factor in this
# module a human sets directly. `deals.deal_temperature` is a dedicated column; see its
# migration header for why it is not the blueprint's custom field.
#
# TWO DELIBERATE DIVERGENCES FROM THE BLUEPRINT, both worth stating because a future reader
# comparing the two files will notice the numbers do not match.
#
# 1. UNSET IS NEUTRAL (1.0), where the blueprint defaults an unset field to Cold and its
#    0.4x. NULL here means nobody has triaged the deal — it is not a judgment, and only a
#    judgment should move a score. Porting the Cold default literally would have multiplied
#    EVERY existing deal's score by 0.4 at the next daily refresh: an install-wide silent
#    change to a number people sort by, caused by a deploy rather than by any user action.
#    (The blueprint is not even self-consistent here: it reads the EAV row with a bare
#    `field_vals.get(key, "Cold")`, so a never-scored deal gets 0.4x while a deal explicitly
#    CLEARED back to unset stores '' and falls through to its unmatched-key default instead.)
#
# 2. RESCALED to this module's range. The blueprint's Hot is 2.5x, where the widest spread
#    among the five factors above is engagement's 0.6-1.25. At 2.5x the score saturates
#    across the ordinary middle of the range: with typical secondary factors (~1.27 combined)
#    a hot `proposal` deal computes 101 and a hot `negotiation` 143 — both clamp to 99, so
#    the two become indistinguishable. At 1.6x the same deals land at 65 and 91. Temperature
#    is still the single strongest factor here, which is the point of the feature; a deal
#    strong on EVERY factor does still reach the clamp, which is correct.
_TEMPERATURE_MULTIPLIERS = {"hot": 1.6, "warm": 1.1, "cold": 0.5}


def _temperature_multiplier(value: str | None) -> float:
    """Multiplier for a stored temperature. 1.0 for unset AND for anything unrecognised.

    Failing an unknown value toward NEUTRAL is the safe direction: the migration's CHECK
    makes one unrepresentable, so reaching this branch means the column drifted from this
    table, and a score that silently halves is worse than one that ignores a value it
    cannot read.
    """
    if not value:
        return 1.0
    return _TEMPERATURE_MULTIPLIERS.get(value.strip().lower(), 1.0)


# --- contact factor multipliers ---

_CONTACT_STATUS_BASE = {"active": 30, "inactive": 15, "archived": 5}


def _contact_status_base(status: str | None) -> int:
    # Unknown status -> conservative "inactive" band rather than full "active".
    return _CONTACT_STATUS_BASE.get((status or "").lower(), _CONTACT_STATUS_BASE["inactive"])


def _interactions_multiplier(count: int) -> float:
    if count <= 0:
        return 0.6
    if count <= 2:
        return 0.9
    if count <= 5:
        return 1.1
    if count <= 10:
        return 1.2
    return 1.3


def _contact_recency_multiplier(days: float | None) -> float:
    if days is None:
        return 0.55
    if days <= 7:
        return 1.15
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.8
    return 0.55


def _deal_link_multiplier(stages: list[str]) -> float:
    """Strongest signal from a contact's linked deals (reads raw stages, never scores)."""
    norm = [(s or "").lower() for s in stages]
    open_stages = [s for s in norm if s in STAGE_BASELINES]
    if "negotiation" in open_stages:
        return 1.6
    if "proposal" in open_stages:
        return 1.45
    if "qualified" in open_stages:
        return 1.25
    if "lead" in open_stages:
        return 1.1
    if _TERMINAL_WON in norm:
        return 1.5
    if _TERMINAL_LOST in norm:  # only lost deals — a died opportunity is the weakest signal
        return 0.65
    return 0.75  # no deals yet (untapped, not dead)


def _company_link_multiplier(company_id, company_text) -> float:
    if company_id:
        return 1.15
    if company_text and str(company_text).strip():
        return 1.05
    return 0.9


def _completeness_multiplier(email, phone) -> float:
    have = bool(email and str(email).strip()) + bool(phone and str(phone).strip())
    if have == 2:
        return 1.1
    if have == 1:
        return 1.0
    return 0.85


# ---------------------------------------------------------------------------
# Pure score composition (inputs already read from the DB)
# ---------------------------------------------------------------------------

def _compose_deal(deal: dict, chatter_count: int, last_touch, now: datetime) -> dict:
    stage = (deal.get("stage") or "").lower()
    if stage == _TERMINAL_WON:
        return {"score": 100, "factors": {"terminal": {"value": "won", "multiplier": None}}}
    if stage == _TERMINAL_LOST:
        return {"score": 0, "factors": {"terminal": {"value": "lost", "multiplier": None}}}

    baseline = STAGE_BASELINES.get(stage, STAGE_BASELINES["lead"])
    recency_days = _days_since(last_touch, now)
    if recency_days is None:
        recency_days = _days_since(deal.get("created_at"), now)
    age_days = _days_since(deal.get("created_at"), now)

    m_eng = _engagement_multiplier(chatter_count)
    m_val = _value_multiplier(_safe_value(deal.get("value")))
    m_rel = _relationship_multiplier(bool(deal.get("contact_id")), bool(deal.get("company_id")))
    m_rec = _recency_multiplier(recency_days)
    m_age = _age_multiplier(age_days)
    temperature = deal.get("deal_temperature")
    m_temp = _temperature_multiplier(temperature)

    score = _clamp(baseline * m_eng * m_val * m_rel * m_rec * m_age * m_temp)
    return {
        "score": score,
        "factors": {
            "stage_baseline": {"value": stage or "lead", "multiplier": baseline},
            "engagement": {"value": chatter_count, "multiplier": m_eng},
            "value": {"value": _safe_value(deal.get("value")), "multiplier": m_val},
            "relationships": {
                "value": f"contact={bool(deal.get('contact_id'))} company={bool(deal.get('company_id'))}",
                "multiplier": m_rel,
            },
            "recency": {"value": None if recency_days is None else round(recency_days, 1), "multiplier": m_rec},
            "age": {"value": None if age_days is None else round(age_days, 1), "multiplier": m_age},
            # Emitted even when unset, with value None and multiplier 1.0, so a breakdown
            # says "nobody has triaged this" rather than staying silent about a factor that
            # exists. Issue #125.
            "temperature": {"value": temperature, "multiplier": m_temp},
        },
    }


def _compose_contact(
    contact: dict, interaction_count: int, last_touch, linked_stages: list[str], now: datetime
) -> dict:
    base = _contact_status_base(contact.get("status"))
    recency_days = _days_since(last_touch, now)
    if recency_days is None:
        recency_days = _days_since(contact.get("created_at"), now)

    m_int = _interactions_multiplier(interaction_count)
    m_rec = _contact_recency_multiplier(recency_days)
    m_deal = _deal_link_multiplier(linked_stages)
    m_co = _company_link_multiplier(contact.get("company_id"), contact.get("company"))
    m_prof = _completeness_multiplier(contact.get("email"), contact.get("phone"))

    score = _clamp(base * m_int * m_rec * m_deal * m_co * m_prof)
    return {
        "score": score,
        "factors": {
            "status_base": {"value": (contact.get("status") or "").lower() or "inactive", "multiplier": base},
            "interactions": {"value": interaction_count, "multiplier": m_int},
            "recency": {"value": None if recency_days is None else round(recency_days, 1), "multiplier": m_rec},
            "deal_linkage": {"value": ",".join(linked_stages) or "none", "multiplier": m_deal},
            "company_linkage": {"value": bool(contact.get("company_id")) or bool(contact.get("company")), "multiplier": m_co},
            "profile_completeness": {"value": f"email={bool(contact.get('email'))} phone={bool(contact.get('phone'))}", "multiplier": m_prof},
        },
    }


# ---------------------------------------------------------------------------
# Read-only live scoring (for the assistant tool + display; no persistence, no lock)
# ---------------------------------------------------------------------------

def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _newest(*values):
    """Return the max of ISO-string/None timestamps (lexicographic ISO ordering is chronological)."""
    present = [v for v in values if v]
    return max(present) if present else None


# The factor input reads, defined ONCE and shared by the read-only (score_*) and persisting
# (recompute_*) paths via a `q1`/`qall` fetch-primitive pair, so the two can never drift.
def _read_deal_score(deal_id: int, q1, now: datetime) -> dict | None:
    deal = q1("SELECT * FROM deals WHERE id = %s", (deal_id,))
    if deal is None:
        return None
    chat = q1(
        "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM crm_chatter "
        "WHERE entity_type = 'deal' AND entity_id = %s AND archived = 0 AND "
        + not_housekeeping_sql("message"),
        (deal_id,),
    ) or {}
    # Engagement counts BOTH notes and logged activities (symmetric with contact scoring) —
    # activity_log is the CRM's primary touch surface, so a deal worked only via logged calls
    # must not read as zero-engagement.
    act = q1("SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM activity_log WHERE deal_id = %s", (deal_id,)) or {}
    count = int(chat.get("cnt") or 0) + int(act.get("cnt") or 0)
    return _compose_deal(deal, count, _newest(chat.get("newest"), act.get("newest")), now)


def _read_contact_score(contact_id: int, q1, qall, now: datetime) -> dict | None:
    contact = q1("SELECT * FROM contacts WHERE id = %s", (contact_id,))
    if contact is None:
        return None
    chat = q1(
        "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM crm_chatter "
        "WHERE entity_type = 'contact' AND entity_id = %s AND archived = 0 AND "
        + not_housekeeping_sql("message"),
        (contact_id,),
    ) or {}
    act = q1(
        "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM activity_log WHERE contact_id = %s",
        (contact_id,),
    ) or {}
    # archived_at IS NULL = service.LIVE_PREDICATE (literal here: service imports this
    # module, so importing back would cycle). #22's soft-archive sweep applies to every
    # deal AGGREGATE, and this is one: a merged-away or archived deal must not keep
    # feeding the contact's deal-linkage factor forever.
    stages = [r["stage"] for r in qall(
        "SELECT stage FROM deals WHERE contact_id = %s AND archived_at IS NULL", (contact_id,))]
    count = int(chat.get("cnt") or 0) + int(act.get("cnt") or 0)
    return _compose_contact(contact, count, _newest(chat.get("newest"), act.get("newest")), stages, now)


def score_deal(deal_id: int, now: datetime | None = None) -> dict | None:
    """Compute a deal's score live (no persistence). Returns None if the deal is missing."""
    return _read_deal_score(deal_id, pg_fetchone, _now(now))


def score_contact(contact_id: int, now: datetime | None = None) -> dict | None:
    """Compute a contact's score live (no persistence). Returns None if missing."""
    return _read_contact_score(contact_id, pg_fetchone, pg_fetchall, _now(now))


# ---------------------------------------------------------------------------
# Persisting recompute (serialized per entity with a transaction-level advisory lock)
# ---------------------------------------------------------------------------

def _fetch_one(cur, sql, params=()):
    """Execute + fetch + convert immediately (before the cursor is reused — see
    core.postgres.row_to_dict gotcha)."""
    cur.execute(sql, params)
    row = cur.fetchone()
    return row_to_dict(cur, row) if row else None


def _fetch_all(cur, sql, params=()):
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [row_to_dict(cur, r) for r in rows]


def recompute_deal(deal_id: int, now: datetime | None = None) -> int | None:
    """Recompute and persist a deal's ``lead_score``. Returns the score, or None if missing.

    Holds a per-deal transaction advisory lock across read->compute->write so a concurrent
    recompute cannot overwrite a newer score with an older one. Never bumps ``updated_at``.
    Uses the SAME reads as ``score_deal`` (via ``_read_deal_score``), on the locked cursor.
    """
    now = _now(now)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_DEAL_LOCK_NS, deal_id))
        result = _read_deal_score(deal_id, lambda s, p=(): _fetch_one(cur, s, p), now)
        if result is None:
            return None
        cur.execute(
            "UPDATE deals SET lead_score = %s, lead_score_at = %s WHERE id = %s",
            (result["score"], now, deal_id),
        )
    return result["score"]


def recompute_contact(contact_id: int, now: datetime | None = None) -> int | None:
    """Recompute and persist a contact's ``lead_score`` (locked; no ``updated_at`` bump)."""
    now = _now(now)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_CONTACT_LOCK_NS, contact_id))
        result = _read_contact_score(
            contact_id, lambda s, p=(): _fetch_one(cur, s, p), lambda s, p=(): _fetch_all(cur, s, p), now
        )
        if result is None:
            return None
        cur.execute(
            "UPDATE contacts SET lead_score = %s, lead_score_at = %s WHERE id = %s",
            (result["score"], now, contact_id),
        )
    return result["score"]


# ---------------------------------------------------------------------------
# Event facade — the single trigger choke point. NEVER raises.
# ---------------------------------------------------------------------------

def score_on_event(deal_ids=(), contact_ids=()) -> None:
    """Recompute the given entities after a write commits. Deduped, per-id isolated,
    and wrapped so a scoring failure can never break the triggering write."""
    try:
        for did in _unique(deal_ids):
            try:
                recompute_deal(did)
            except Exception:
                logger.warning("lead-score recompute failed for deal %s", did, exc_info=True)
        for cid in _unique(contact_ids):
            try:
                recompute_contact(cid)
            except Exception:
                logger.warning("lead-score recompute failed for contact %s", cid, exc_info=True)
    except Exception:  # outer backstop — score_on_event must never raise into a write path
        logger.warning("score_on_event failed", exc_info=True)


def _unique(ids):
    seen = set()
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            yield i


# ---------------------------------------------------------------------------
# Batch backfill + daily time-decay refresh
# ---------------------------------------------------------------------------

def backfill_scores(scope: str = "null", now: datetime | None = None) -> dict:
    """Recompute scores in bulk. scope='null' scores only never-scored rows (fast, for
    seeding/new rows); scope='all' rescores every row (drift repair). The candidate ids are
    bounded IN SQL (``LIMIT``) so even scope='all' on a huge table never materializes the whole
    id set in memory; if the cap is hit, ``capped`` is true and a repeat call continues."""
    if scope not in ("null", "all"):
        raise ValueError(f"invalid scope: {scope!r} (expected 'null' or 'all')")
    now = _now(now)
    where = "WHERE lead_score IS NULL" if scope == "null" else ""
    # Oldest-scored first (NULLs first): each recompute bumps lead_score_at, so a re-run after
    # hitting the cap genuinely PROGRESSES to the next rows rather than re-scoring the same ids.
    order = "ORDER BY lead_score_at ASC NULLS FIRST, id"
    deal_ids = [r["id"] for r in pg_fetchall(
        f"SELECT id FROM deals {where} {order} LIMIT %s", (_REFRESH_MAX_ROWS,))]
    remaining = _REFRESH_MAX_ROWS - len(deal_ids)
    contact_ids = [r["id"] for r in pg_fetchall(
        f"SELECT id FROM contacts {where} {order} LIMIT %s", (remaining,))] if remaining > 0 else []
    capped = len(deal_ids) + len(contact_ids) >= _REFRESH_MAX_ROWS
    if capped:
        logger.warning("lead-score backfill(%s) hit the %d-row cap; run it again to continue.",
                       scope, _REFRESH_MAX_ROWS)

    deals_scored = _run_batch(recompute_deal, deal_ids, now, "deal")
    contacts_scored = _run_batch(recompute_contact, contact_ids, now, "contact")
    errors = (len(deal_ids) - deals_scored) + (len(contact_ids) - contacts_scored)
    return {"deals_scored": deals_scored, "contacts_scored": contacts_scored, "errors": errors, "capped": capped}


def _run_batch(fn, ids, now, label) -> int:
    ok = 0
    for i in ids:
        try:
            if fn(i, now=now) is not None:
                ok += 1
        except Exception:
            logger.warning("lead-score backfill failed for %s %s", label, i, exc_info=True)
    return ok


def _stale_ids(table: str, cutoff: datetime, limit: int, extra: str = "") -> list[int]:
    """Up to ``limit`` ids of rows whose score is stale (never scored, or last scored before
    ``cutoff``), OLDEST-scored first (NULLs first) so a backlog drains fairly with no row ever
    starved. Bounded IN SQL (``LIMIT``) and index-backed by ``idx_<table>_lead_score_at``.
    ``table`` and ``extra`` are module-internal literals ('deals'/'contacts' + a fixed
    predicate), never user input."""
    if limit <= 0:
        return []
    rows = pg_fetchall(
        f"SELECT id FROM {table} WHERE (lead_score_at IS NULL OR lead_score_at < %s) {extra} "
        f"ORDER BY lead_score_at ASC NULLS FIRST, id LIMIT %s",
        (cutoff, limit),
    )
    return [r["id"] for r in rows]


def run_score_refresh_if_due(now: datetime | None = None) -> dict | None:
    """Refresh the STALEST scores whose time-decay factors (recency/age) have drifted — rows
    not recomputed within _REFRESH_INTERVAL (or never scored). Three properties keep it cheap,
    bounded, and correct inside maintenance_tick's shared slot (T1):

    * a **coarse due-gate** on ``crm_meta.scores_refreshed_at`` (like dreaming) — on ~every tick
      the window is closed and this returns None WITHOUT scanning either table;
    * a **per-tick batch cap** (``_REFRESH_BATCH``, applied in SQL) — once the window opens it
      recomputes at most that many stale rows, OLDEST first, and the remainder rolls to the next
      tick (self-resuming, no starvation);
    * it is **lock-free and safe under arbitrary concurrent entry** — the scheduled tick,
      ``POST /api/heartbeat/run-now`` (``tick()`` on a threadpool thread), and even multiple
      processes can all run it at once. Safety does NOT depend on serialization: each recompute
      is independently atomic under its own per-entity ``pg_advisory_xact_lock``, and the stamp
      is idempotent. Concurrency only costs redundant work — never corruption. (An earlier design
      used a session lock; it was dropped because it could leak on a rolled-back transaction and
      nest pooled connections.)

    The 24h window is stamped closed only on a **clean drain** (fewer than the cap AND zero
    per-row failures), so a backlog or a transient error keeps retrying on later ticks. Returns
    a summary, or None when not due / nothing stale."""
    now = _now(now)
    cutoff = now - _REFRESH_INTERVAL
    # Coarse due-gate: a single-row read; on ~every tick the window is closed -> return early.
    # A future stamp (clock stepped back after a stamp) counts as due, so the gate can never
    # wedge closed waiting for real time to catch up to a bogus timestamp.
    meta = pg_fetchone("SELECT scores_refreshed_at FROM crm_meta WHERE id = 1")
    last = meta.get("scores_refreshed_at") if meta else None
    if last is not None:
        last_dt = datetime.fromisoformat(last) if isinstance(last, str) else last
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if last_dt <= now and now - last_dt < _REFRESH_INTERVAL:
            return None
    # Terminal deals (won=100 / lost=0) have no time-decay factor, so a scored terminal deal
    # never needs refreshing — exclude it so it doesn't consume drain capacity every day. A
    # stage change out of terminal re-triggers via the event path.
    deal_ids = _stale_ids("deals", cutoff, _REFRESH_BATCH,
                          extra="AND NOT (stage IN ('won', 'lost') AND lead_score IS NOT NULL)")
    contact_ids = _stale_ids("contacts", cutoff, _REFRESH_BATCH - len(deal_ids))
    total = len(deal_ids) + len(contact_ids)
    if total == 0:
        # Window open but nothing stale (events kept everything fresh) — reset the 24h gate.
        pg_execute("UPDATE crm_meta SET scores_refreshed_at = %s WHERE id = 1", (now,))
        return None
    d = _run_batch(recompute_deal, deal_ids, now, "deal")
    c = _run_batch(recompute_contact, contact_ids, now, "contact")
    errors = (len(deal_ids) - d) + (len(contact_ids) - c)
    if total < _REFRESH_BATCH and errors == 0:
        pg_execute("UPDATE crm_meta SET scores_refreshed_at = %s WHERE id = 1", (now,))
    return {"deals_scored": d, "contacts_scored": c, "batch": total, "errors": errors}
