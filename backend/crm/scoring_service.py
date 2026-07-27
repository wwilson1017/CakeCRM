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
  blueprint's ``deal_temperature`` / ``last_contact_date`` / ``number_of_units`` custom-field
  factors are dropped (CakeCRM ships zero custom-field definitions) — ``last_contact_date``
  is re-sourced natively as the newest chatter/activity timestamp. ``update_note`` /
  ``update_activity`` are non-triggers (content-only edits don't change counts or timestamps).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from core.postgres import get_connection, pg_fetchall, pg_fetchone, row_to_dict

logger = logging.getLogger(__name__)

# --- advisory lock keys (registered in core/postgres.py's registry comment) ---
# Per-entity recompute uses the two-int form pg_advisory_xact_lock(namespace, id);
# deals.id / contacts.id are SERIAL (int4) so they fit. The daily refresh holds a
# session-level pg_try_advisory_lock across its whole run to prevent overlap.
_DEAL_LOCK_NS = 1801
_CONTACT_LOCK_NS = 1802
_REFRESH_LOCK_KEY = 20260718  # distinct from migration(1)/telegram(720770)/dreaming(20260705)

_REFRESH_INTERVAL = timedelta(hours=24)
# Per-tick cap for the time-decay refresh. reminder_tick is a shared, fast-and-bounded
# (T1) slot, so the refresh MUST NOT sweep the whole table on the tick it fires — it
# processes at most this many stale entities per pass and self-resumes on later ticks.
_REFRESH_BATCH = 500
# Safety valve for a MANUAL backfill("all") of a huge dataset (runs off-thread via the
# endpoint/tool, never in the tick): bound + log rather than run unboundedly.
_REFRESH_MAX_ROWS = 20000

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

    score = _clamp(baseline * m_eng * m_val * m_rel * m_rec * m_age)
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


def score_deal(deal_id: int, now: datetime | None = None) -> dict | None:
    """Compute a deal's score live (no persistence). Returns None if the deal is missing."""
    now = _now(now)
    deal = pg_fetchone("SELECT * FROM deals WHERE id = %s", (deal_id,))
    if deal is None:
        return None
    agg = pg_fetchone(
        "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM crm_chatter "
        "WHERE entity_type = 'deal' AND entity_id = %s AND archived = 0",
        (deal_id,),
    ) or {}
    act = pg_fetchone(
        "SELECT MAX(created_at) AS newest FROM activity_log WHERE deal_id = %s", (deal_id,)
    ) or {}
    last_touch = _newest(agg.get("newest"), act.get("newest"))
    return _compose_deal(deal, int(agg.get("cnt") or 0), last_touch, now)


def score_contact(contact_id: int, now: datetime | None = None) -> dict | None:
    """Compute a contact's score live (no persistence). Returns None if missing."""
    now = _now(now)
    contact = pg_fetchone("SELECT * FROM contacts WHERE id = %s", (contact_id,))
    if contact is None:
        return None
    chat = pg_fetchone(
        "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM crm_chatter "
        "WHERE entity_type = 'contact' AND entity_id = %s AND archived = 0",
        (contact_id,),
    ) or {}
    act = pg_fetchone(
        "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM activity_log WHERE contact_id = %s",
        (contact_id,),
    ) or {}
    stages = [r["stage"] for r in pg_fetchall("SELECT stage FROM deals WHERE contact_id = %s", (contact_id,))]
    count = int(chat.get("cnt") or 0) + int(act.get("cnt") or 0)
    last_touch = _newest(chat.get("newest"), act.get("newest"))
    return _compose_contact(contact, count, last_touch, stages, now)


def _newest(*values):
    """Return the max of ISO-string/None timestamps (lexicographic ISO ordering is chronological)."""
    present = [v for v in values if v]
    return max(present) if present else None


# ---------------------------------------------------------------------------
# Persisting recompute (serialized per entity with a transaction-level advisory lock)
# ---------------------------------------------------------------------------

def _fetch_one(cur, sql, params):
    """Execute + fetch + convert immediately (before the cursor is reused — see
    core.postgres.row_to_dict gotcha)."""
    cur.execute(sql, params)
    row = cur.fetchone()
    return row_to_dict(cur, row) if row else None


def _fetch_all(cur, sql, params):
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [row_to_dict(cur, r) for r in rows]


def recompute_deal(deal_id: int, now: datetime | None = None) -> int | None:
    """Recompute and persist a deal's ``lead_score``. Returns the score, or None if missing.

    Holds a per-deal transaction advisory lock across read->compute->write so a concurrent
    recompute cannot overwrite a newer score with an older one. Never bumps ``updated_at``.
    """
    now = _now(now)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_DEAL_LOCK_NS, deal_id))
        deal = _fetch_one(cur, "SELECT * FROM deals WHERE id = %s", (deal_id,))
        if deal is None:
            return None
        agg = _fetch_one(
            cur,
            "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM crm_chatter "
            "WHERE entity_type = 'deal' AND entity_id = %s AND archived = 0",
            (deal_id,),
        ) or {}
        act = _fetch_one(cur, "SELECT MAX(created_at) AS newest FROM activity_log WHERE deal_id = %s", (deal_id,)) or {}
        result = _compose_deal(deal, int(agg.get("cnt") or 0), _newest(agg.get("newest"), act.get("newest")), now)
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
        contact = _fetch_one(cur, "SELECT * FROM contacts WHERE id = %s", (contact_id,))
        if contact is None:
            return None
        chat = _fetch_one(
            cur,
            "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM crm_chatter "
            "WHERE entity_type = 'contact' AND entity_id = %s AND archived = 0",
            (contact_id,),
        ) or {}
        act = _fetch_one(
            cur,
            "SELECT COUNT(*) AS cnt, MAX(created_at) AS newest FROM activity_log WHERE contact_id = %s",
            (contact_id,),
        ) or {}
        stages = [r["stage"] for r in _fetch_all(cur, "SELECT stage FROM deals WHERE contact_id = %s", (contact_id,))]
        count = int(chat.get("cnt") or 0) + int(act.get("cnt") or 0)
        result = _compose_contact(contact, count, _newest(chat.get("newest"), act.get("newest")), stages, now)
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
    seeding/new rows); scope='all' rescores every row (drift repair + the daily refresh)."""
    if scope not in ("null", "all"):
        raise ValueError(f"invalid scope: {scope!r} (expected 'null' or 'all')")
    now = _now(now)
    where = "WHERE lead_score IS NULL" if scope == "null" else ""
    deal_ids = [r["id"] for r in pg_fetchall(f"SELECT id FROM deals {where} ORDER BY id")]
    contact_ids = [r["id"] for r in pg_fetchall(f"SELECT id FROM contacts {where} ORDER BY id")]

    total = len(deal_ids) + len(contact_ids)
    capped = False
    if total > _REFRESH_MAX_ROWS:
        capped = True
        logger.warning(
            "lead-score backfill(%s): %d candidates exceed cap %d; processing the first %d, "
            "remainder deferred to the next run / a manual backfill.",
            scope, total, _REFRESH_MAX_ROWS, _REFRESH_MAX_ROWS,
        )
        # Bound the work: fill from deals first, then contacts.
        deal_ids = deal_ids[:_REFRESH_MAX_ROWS]
        contact_ids = contact_ids[: max(0, _REFRESH_MAX_ROWS - len(deal_ids))]

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


def _stale_ids(cur, table: str, cutoff: datetime, limit: int) -> list[int]:
    """Up to ``limit`` ids of rows whose score is stale (never scored, or last scored before
    ``cutoff``). ``table`` is a module-internal literal ('deals'/'contacts'), never user input.
    Ids are extracted immediately (before the cursor is reused — row_to_dict gotcha)."""
    if limit <= 0:
        return []
    cur.execute(
        f"SELECT id FROM {table} WHERE lead_score_at IS NULL OR lead_score_at < %s LIMIT %s",
        (cutoff, limit),
    )
    return [r[0] for r in cur.fetchall()]


def run_score_refresh_if_due(now: datetime | None = None) -> dict | None:
    """Refresh the STALEST scores whose time-decay factors (recency/age) have drifted — rows
    not recomputed within _REFRESH_INTERVAL (or never scored). BOUNDED to _REFRESH_BATCH
    entities per call so it stays fast inside reminder_tick's shared slot (T1); any remainder
    is picked up on later ticks (self-resuming). Because event writes keep active rows fresh,
    this only ever touches dormant rows. A session-level advisory lock (held across the pass)
    prevents overlap with a manual backfill or another tick. Returns a summary, or None when
    nothing is stale / the lock is busy."""
    now = _now(now)
    cutoff = now - _REFRESH_INTERVAL
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_REFRESH_LOCK_KEY,))
        if not cur.fetchone()[0]:
            return None  # another refresh/backfill holds the lock
        try:
            deal_ids = _stale_ids(cur, "deals", cutoff, _REFRESH_BATCH)
            contact_ids = _stale_ids(cur, "contacts", cutoff, _REFRESH_BATCH - len(deal_ids))
            if not deal_ids and not contact_ids:
                return None  # nothing stale — the common cheap path
            # recompute_* each take their own pooled connection + per-entity xact lock; the
            # session lock on `conn` stays held (its cursor is idle) so no overlap can start.
            d = _run_batch(recompute_deal, deal_ids, now, "deal")
            c = _run_batch(recompute_contact, contact_ids, now, "contact")
            cur.execute("UPDATE crm_meta SET scores_refreshed_at = %s WHERE id = 1", (now,))
            return {"deals_scored": d, "contacts_scored": c, "batch": len(deal_ids) + len(contact_ids)}
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_REFRESH_LOCK_KEY,))
