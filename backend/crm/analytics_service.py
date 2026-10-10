"""CRM — read-only sales-intelligence reads (issue #22).

The four questions a sales assistant needs to ask about a CRM that nobody has the
answers to today: *what is going cold*, *who have I not spoken to*, *what did we
enter twice*, and *what is missing*. Named to mirror
``cake_os/backend/apps/crm/analytics_service.py`` so the remaining ports diff cleanly.

Everything here is a **pure SQL read** — no AI provider is touched, so all of it
works with zero AI keys configured, and every tool built on it carries
``writes: False``. That flag is load-bearing twice over: it keeps the assistant's
confirmation gate quiet for reads, and it puts these functions inside
``assistant.background.background_allowlist()`` automatically, so the proactive
heartbeat can call them without any further wiring.

Deal reads exclude archived deals, and the live/open predicates are IMPORTED from
``crm.service`` rather than re-typed here: a second copy of "what counts as a live,
open deal" is exactly how a sweep site gets missed when the definition changes. An
archived deal is not "going stale", it is put away.
"""

import logging

from core.localtime import tz
from core.postgres import pg_fetchall, pg_fetchone
from crm import gtd_common, provenance_service, scoring_service
from crm.service import (
    LAST_TOUCH_SQL,
    LIVE_PREDICATE,
    LIVE_PREDICATE_D,
    NOT_DROPPED_TODO_T,
    OPEN_PREDICATE,
    OPEN_PREDICATE_D,
    OPEN_STAGES,
    owner_condition,
)

logger = logging.getLogger(__name__)

DEFAULT_DEAL_STALE_DAYS = 14
DEFAULT_CONTACT_STALE_DAYS = 30
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _bounded(value, default: int, low: int = 1, high: int = MAX_LIMIT) -> int:
    """Clamp an LLM- or client-supplied integer into a sane band."""
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


# ── Stale deals ───────────────────────────────────────────────────────────────

def get_stale_deals(
    stale_days: int = DEFAULT_DEAL_STALE_DAYS, limit: int = DEFAULT_LIMIT,
    owner_id: int | str | None = None,
) -> dict:
    """Open deals nobody has touched in ``stale_days`` days, stalest first.

    Complements ``service.get_analytics``: that one answers "how many are stale" for
    the dashboard, this one answers "which ones, and what do I do about them" — so it
    carries the stage, value, owner-facing names, days in the current stage, and
    whether a follow-up todo already exists (a deal with an open todo is being
    handled, and nagging about it is noise).

    ``days_in_stage`` reads the newest ``deal_stage_events`` row and falls back to the
    deal's ``created_at`` for deals that predate the stage log — honest either way:
    a deal that has never moved HAS been in its stage since it was created.

    ``owner_id`` (#190) narrows to one rep, or to the unowned pile via
    ``service.UNASSIGNED``; absent means everyone. It is applied to the truncation
    COUNT as well as the page, because a filtered list beside an unfiltered total
    does not fail, it just reports a number that disagrees with the rows.

    ``owner_id`` is also SELECTed unconditionally: it is the routing signal the
    owner-routed proactive nudges read, so the column has to be on the row whether or
    not the caller filtered by it.
    """
    stale_days = _bounded(stale_days, DEFAULT_DEAL_STALE_DAYS, 1, 365)
    limit = _bounded(limit, DEFAULT_LIMIT)
    params: list = [stale_days]
    owner_sql = owner_condition("d.owner_id", owner_id, params)
    owner_clause = f" AND {owner_sql}" if owner_sql else ""
    params.append(limit)
    rows = pg_fetchall(
        f"""
        SELECT d.id, d.title, d.stage, d.value, d.currency, d.expected_close_date,
               d.probability, d.contact_id, d.company_id, d.owner_id,
               c.name  AS contact_name,
               co.name AS company_name,
               FLOOR(EXTRACT(EPOCH FROM (now() - {LAST_TOUCH_SQL})) / 86400.0)::int
                   AS days_since_touch,
               FLOOR(EXTRACT(EPOCH FROM (now() - COALESCE(
                   (SELECT MAX(e.changed_at) FROM deal_stage_events e
                     WHERE e.deal_id = d.id), d.created_at))) / 86400.0)::int
                   AS days_in_stage,
               EXISTS (SELECT 1 FROM todos t
                        WHERE t.deal_id = d.id AND t.completed = 0
                          AND {NOT_DROPPED_TODO_T}) AS has_open_todo
          FROM deals d
          LEFT JOIN contacts  c  ON d.contact_id = c.id
          LEFT JOIN companies co ON d.company_id = co.id
         WHERE {OPEN_PREDICATE_D} AND {LIVE_PREDICATE_D}
           AND {LAST_TOUCH_SQL} < now() - make_interval(days => %s){owner_clause}
         ORDER BY days_since_touch DESC, d.id ASC
         LIMIT %s
        """,
        params,
    )
    # The count exists so a truncated list never understates the problem — but it is a
    # second full scan of the same non-sargable predicate (~150ms at 50k deals), so
    # only pay for it when the list actually WAS truncated. Under the limit, the rows
    # we already have are the exact answer.
    if len(rows) < limit:
        total_stale = len(rows)
    else:
        count_params: list = [stale_days]
        count_owner_sql = owner_condition("d.owner_id", owner_id, count_params)
        count_owner_clause = f" AND {count_owner_sql}" if count_owner_sql else ""
        total_row = pg_fetchone(
            f"""SELECT COUNT(*) AS cnt FROM deals d
                 WHERE {OPEN_PREDICATE_D} AND {LIVE_PREDICATE_D}
                   AND {LAST_TOUCH_SQL} < now() - make_interval(days => %s){count_owner_clause}""",
            count_params,
        )
        total_stale = (total_row or {}).get("cnt", 0)
    return {
        "stale_days": stale_days,
        "total_stale": total_stale,
        "deals": rows,
        "count": len(rows),
    }


# ── Contact staleness ─────────────────────────────────────────────────────────

def get_contact_staleness(
    stale_days: int = DEFAULT_CONTACT_STALE_DAYS, limit: int = DEFAULT_LIMIT,
    owner_id: int | str | None = None,
) -> dict:
    """Active contacts with no logged interaction in ``stale_days`` days.

    Contacts have no ``last_contacted_at`` column, so recency is derived from the
    contact's own ``activity_log`` rows and un-archived notes. A contact who has NEVER
    been contacted is included with ``days_since_contact: null`` and sorts first —
    "never" is the most urgent case, not a missing value to skip.

    Provenance housekeeping notes are excluded (#77): the assistant confirming an
    AI-populated field writes a ``crm_chatter`` row, and counting it as contact meant a
    record could stop looking stale without anyone having talked to the person.
    ``scoring_service`` already excluded them from engagement; the Contacts list's derived
    ``last_contact_at`` uses the same predicate, so all three now agree.

    Archived/inactive contacts are excluded: deliberately parked, not neglected.

    ``owner_id`` (#190) narrows to one rep, or to the unowned pile via
    ``service.UNASSIGNED``; absent means everyone. There is no second COUNT query here
    to keep in step, unlike ``get_stale_deals``. ``owner_id`` is SELECTed
    unconditionally for the same reason it is there: it is the owner-routed nudge's
    routing signal, not only a filter.

    Scale note: the CTE derives a last-touch date for EVERY active contact before
    filtering (two correlated subqueries each) — accepted at single-user v1 scale, the
    same trade-off get_pipeline documents. If contact volume ever grows, index-driven
    LATERAL MAXes or a maintained last_contacted_at column are the upgrades.
    """
    stale_days = _bounded(stale_days, DEFAULT_CONTACT_STALE_DAYS, 1, 365)
    limit = _bounded(limit, DEFAULT_LIMIT)
    # The owner filter's own param sits between the staleness window and the limit,
    # which is where its condition appears in the statement. The housekeeping exclusion
    # is a rendered predicate with no placeholder (#239).
    params: list = [stale_days]
    owner_sql = owner_condition("ct.owner_id", owner_id, params)
    owner_clause = f" AND {owner_sql}" if owner_sql else ""
    params.append(limit)
    rows = pg_fetchall(
        f"""
        -- GREATEST ignores NULLs (returning NULL only when every argument is NULL),
        -- so a contact with notes but no logged activity still gets a real date, and
        -- one with neither correctly comes out NULL = "never contacted".
        WITH last_touch AS (
            SELECT ct.id AS contact_id, GREATEST(
                (SELECT MAX(a.created_at) FROM activity_log a
                  WHERE a.contact_id = ct.id),
                (SELECT MAX(ch.created_at) FROM crm_chatter ch
                  WHERE ch.entity_type = 'contact' AND ch.entity_id = ct.id
                    AND ch.archived = 0
                    AND {scoring_service.not_housekeeping_sql('ch.message')})
            ) AS touched_at
              FROM contacts ct
        )
        SELECT ct.id, ct.name, ct.email, ct.company, ct.company_id, ct.status,
               ct.owner_id,
               co.name AS company_name,
               lt.touched_at AS last_contact_at,
               FLOOR(EXTRACT(EPOCH FROM (now() - lt.touched_at)) / 86400.0)::int
                   AS days_since_contact,
               (SELECT COUNT(*) FROM deals d
                 WHERE d.contact_id = ct.id
                   AND {LIVE_PREDICATE_D} AND {OPEN_PREDICATE_D}) AS open_deals
          FROM contacts ct
          JOIN last_touch lt ON lt.contact_id = ct.id
          LEFT JOIN companies co ON ct.company_id = co.id
         WHERE ct.status = 'active'
           AND (lt.touched_at IS NULL OR lt.touched_at < now() - make_interval(days => %s))
           {owner_clause}
         ORDER BY lt.touched_at ASC NULLS FIRST, ct.id ASC
         LIMIT %s
        """,
        params,
    )
    return {"stale_days": stale_days, "contacts": rows, "count": len(rows)}


# ── Duplicate detection ───────────────────────────────────────────────────────

# Duplicate matching is deliberately EXACT-after-normalization (trim + lowercase),
# never fuzzy. A false positive here invites the assistant to merge two real, distinct
# records — destructive and hard to notice — so the bar is "these are the same string
# typed twice", and near-misses are left for a human to spot.
_DUP_GROUP_SQL = """
    SELECT {key_expr} AS match_value, COUNT(*) AS count,
           ARRAY_AGG(id ORDER BY id) AS ids
      FROM {table}
     WHERE {key_filter}
     GROUP BY {key_expr}
    HAVING COUNT(*) > 1
     ORDER BY COUNT(*) DESC, {key_expr} ASC
     LIMIT %s
"""


def _duplicate_groups(table: str, key_expr: str, key_filter: str, limit: int) -> list[dict]:
    return pg_fetchall(
        _DUP_GROUP_SQL.format(table=table, key_expr=key_expr, key_filter=key_filter),
        (limit,),
    )


def _label_rows(table: str, label_col: str, ids: list[int]) -> dict[int, str]:
    """{id: display label} for the ids in the duplicate groups, in one query."""
    if not ids:
        return {}
    placeholders = ",".join("%s" for _ in ids)
    rows = pg_fetchall(
        f"SELECT id, {label_col} AS label FROM {table} WHERE id IN ({placeholders})",
        list(ids),
    )
    return {r["id"]: r["label"] for r in rows}


def _shape_groups(groups: list[dict], table: str, label_col: str, match_on: str) -> list[dict]:
    ids = [i for g in groups for i in (g.get("ids") or [])]
    labels = _label_rows(table, label_col, ids)
    return [
        {
            "match_on": match_on,
            "value": g["match_value"],
            "count": g["count"],
            # `or ""`, not get(i, ""): a present key holding None skips the default.
            "records": [{"id": i, "label": labels.get(i) or ""} for i in (g.get("ids") or [])],
        }
        for g in groups
    ]


def find_duplicate_contacts(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Contacts sharing an email, or sharing a name. Email first — it is the stronger
    signal, and two people can legitimately share a name."""
    limit = _bounded(limit, DEFAULT_LIMIT)
    by_email = _duplicate_groups(
        "contacts", "lower(btrim(email))", "btrim(email) <> ''", limit)
    by_name = _duplicate_groups(
        "contacts", "lower(btrim(name))", "btrim(name) <> ''", limit)
    return (_shape_groups(by_email, "contacts", "name", "email")
            + _shape_groups(by_name, "contacts", "email", "name"))


def find_duplicate_companies(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Companies sharing a domain. Domain only, on purpose: ``uq_companies_name_ci`` is
    already a unique index on ``LOWER(btrim(name, …))``, so two companies whose names
    differ only in case or surrounding whitespace cannot both exist — a name pass here
    could never return a group, only cost a query."""
    limit = _bounded(limit, DEFAULT_LIMIT)
    by_domain = _duplicate_groups(
        "companies", "lower(btrim(domain))", "btrim(domain) <> ''", limit)
    return _shape_groups(by_domain, "companies", "name", "domain")


def find_duplicate_deals(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Live deals sharing a title AND the same contact — the actual double-entry
    shape. Title alone is a false-positive machine ("Q1 renewal" across ten
    customers is not a duplicate)."""
    limit = _bounded(limit, DEFAULT_LIMIT)
    groups = pg_fetchall(
        f"""
        SELECT lower(btrim(title)) AS match_value, contact_id, COUNT(*) AS count,
               ARRAY_AGG(id ORDER BY id) AS ids
          FROM deals
         WHERE btrim(title) <> '' AND contact_id IS NOT NULL AND {LIVE_PREDICATE}
         GROUP BY lower(btrim(title)), contact_id
        HAVING COUNT(*) > 1
         -- The group key is the PAIR, so ordering by title alone leaves groups tied
         -- whenever two contacts each double-entered the same deal title — and under
         -- the LIMIT that decides arbitrarily which of them the user is shown.
         -- contact_id completes the key, making the order total (issue #58).
         ORDER BY COUNT(*) DESC, lower(btrim(title)) ASC, contact_id ASC
         LIMIT %s
        """,
        (limit,),
    )
    # Label with stage+value so the two same-titled cards are actually tellable apart.
    return _shape_groups(groups, "deals", "title || ' (' || stage || ')'", "title+contact")


def find_duplicates(entity_type: str = "all", limit: int = DEFAULT_LIMIT) -> dict:
    """Duplicate groups across contacts / companies / deals."""
    wanted = ("contact", "company", "deal") if entity_type == "all" else (entity_type,)
    out: dict = {}
    if "contact" in wanted:
        out["contacts"] = find_duplicate_contacts(limit)
    if "company" in wanted:
        out["companies"] = find_duplicate_companies(limit)
    if "deal" in wanted:
        out["deals"] = find_duplicate_deals(limit)
    if not out:
        return {"error": f"Unknown entity_type: {entity_type!r}. Use contact, company, deal, or all."}
    # groups_RETURNED, not total: these are len() of already-limited lists, unlike
    # get_stale_deals' total_stale which is counted before its LIMIT. Naming them the
    # same would tell the model two different things under one word.
    out["groups_returned"] = sum(len(v) for v in out.values() if isinstance(v, list))
    return out


# ── Data-gap scan ─────────────────────────────────────────────────────────────

# Per entity: (gap label, SQL predicate). Only fields worth chasing — a missing
# `notes` is not a gap, a missing email on a contact you are trying to sell to is.
#
# `btrim(col) = ''` is deliberately NOT COALESCE-wrapped: every column named below is
# declared TEXT NOT NULL DEFAULT '' (deals.value is DOUBLE PRECISION NOT NULL DEFAULT
# 0), so these expressions can never see NULL and three-valued logic never applies. If
# any of them is ever made nullable, wrap it — a NULL would otherwise make the row
# vanish from the scan entirely rather than show up as a gap.
_CONTACT_GAPS = (
    ("email", "btrim(email) = ''"),
    ("phone", "btrim(phone) = ''"),
    ("company_link", "company_id IS NULL"),
    ("title", "btrim(title) = ''"),
)
_COMPANY_GAPS = (
    ("domain", "btrim(domain) = ''"),
    ("industry", "btrim(industry) = ''"),
    ("phone", "btrim(phone) = ''"),
)
_DEAL_GAPS = (
    ("value", "value <= 0"),
    ("expected_close_date", "btrim(expected_close_date) = ''"),
    ("contact_link", "contact_id IS NULL"),
)


def _scan_entity(table: str, label_col: str, gaps: tuple, extra_where: str, limit: int) -> list[dict]:
    """Rows in `table` missing at least one of `gaps`, worst-first.

    The array of missing labels is built once in an inner SELECT so the outer query
    can sort on it — ORDER BY resolves against the *input* columns, so
    `cardinality(missing_fields)` only works one level up.
    """
    missing_expr = " || ".join(
        f"(CASE WHEN {pred} THEN ARRAY['{label}'] ELSE ARRAY[]::text[] END)"
        for label, pred in gaps
    )
    any_missing = " OR ".join(f"({pred})" for _, pred in gaps)
    return pg_fetchall(
        f"""SELECT * FROM (
              SELECT id, {label_col} AS label, {missing_expr} AS missing_fields
                FROM {table}
               WHERE ({any_missing}){extra_where}
            ) gaps
            ORDER BY cardinality(missing_fields) DESC, id ASC
            LIMIT %s""",
        (limit,),
    )


def scan_gaps(entity_type: str = "all", limit: int = DEFAULT_LIMIT) -> dict:
    """Records with missing fields worth filling in, worst-first.

    Pure heuristics over what is already in the database — this tool finds the holes,
    it never invents values to fill them. The assistant closes a gap by asking the
    user (or reading it out of an existing record/email) and then calling the normal
    ``crm_update_*`` tool, which is a confirmed write.

    Also surfaces ``unverified_fields``: standard fields the assistant itself wrote
    that no human has confirmed yet (``crm_field_provenance``). Those are the values
    most worth a second look, and nothing else exposes them to the model.
    """
    limit = _bounded(limit, DEFAULT_LIMIT)
    wanted = ("contact", "company", "deal") if entity_type == "all" else (entity_type,)
    out: dict = {}
    if "contact" in wanted:
        out["contacts"] = _scan_entity(
            "contacts", "name", _CONTACT_GAPS, " AND status = 'active'", limit)
    if "company" in wanted:
        out["companies"] = _scan_entity(
            "companies", "name", _COMPANY_GAPS, " AND status = 'active'", limit)
    if "deal" in wanted:
        out["deals"] = _scan_entity(
            "deals", "title", _DEAL_GAPS,
            f" AND {LIVE_PREDICATE} AND {OPEN_PREDICATE}", limit)
    if not out:
        return {"error": f"Unknown entity_type: {entity_type!r}. Use contact, company, deal, or all."}

    # Only ask about entity types provenance can actually have: 'company' is not in
    # provenance_service.VALID_ENTITY_TYPES, so including it would be a permanently
    # empty predicate that reads as if company provenance were a real thing.
    provenance_types = [w for w in wanted if w in ("contact", "deal")]
    # Over-fetch: confirmed_at IS NULL is only half the badge state — the rows are then
    # filtered for staleness, and the stale case is common (every reopened lost deal
    # strands a lost_reason snapshot), so fetching exactly `limit` would routinely
    # return a near-empty list.
    candidates = [] if not provenance_types else pg_fetchall(
        f"""SELECT p.entity_type, p.entity_id, p.field_name, p.value_snapshot,
                   p.populated_at
              FROM crm_field_provenance p
             WHERE p.confirmed_at IS NULL AND p.entity_type = ANY(%s)
               -- An archived deal disappears from every other read; its unconfirmed
               -- fields must go with it, or merge_deals' archived source keeps asking
               -- the user to verify a deal that no longer exists to them.
               AND (p.entity_type <> 'deal' OR EXISTS (
                     SELECT 1 FROM deals d
                      WHERE d.id = p.entity_id AND {LIVE_PREDICATE_D}))
             -- One assistant tool call stamps every field it wrote with the same
             -- transaction `now()`, so p.id is what keeps this over-fetched window
             -- (and therefore the [:limit] slice below) stable across reads (#58).
             ORDER BY p.populated_at DESC, p.id DESC
             LIMIT %s""",
        (provenance_types, limit * 3),
    )
    out["unverified_fields"] = provenance_service.filter_live(candidates)[:limit]
    out["gaps_returned"] = sum(len(v) for k, v in out.items()
                               if isinstance(v, list) and k != "unverified_fields")
    return out


# ── Deal health (issue #22 Phase 2) ───────────────────────────────────────────
#
# Composes #18's lead score with the operational signals a rep actually acts on.
# It deliberately does NOT recompute any scoring maths: scoring_service owns that
# model, and a second copy would drift the moment #18's weights are tuned.

# A deal sitting this long in one stage is "stuck" regardless of how recently it
# was touched — activity without progression is the classic false-comfort signal.
STUCK_IN_STAGE_DAYS = 30


def _health_flags(row: dict, stale_days: int) -> list[str]:
    """Derive the actionable flags from one health row. Pure — no DB, no AI — so the
    hermetic tests exercise every branch without a database."""
    flags = []
    days_since_touch = row.get("days_since_touch")
    if days_since_touch is not None and days_since_touch >= stale_days:
        flags.append("stale")
    days_in_stage = row.get("days_in_stage")
    if days_in_stage is not None and days_in_stage >= STUCK_IN_STAGE_DAYS:
        flags.append("stuck_in_stage")
    if not row.get("open_todos"):
        flags.append("no_next_step")
    if row.get("overdue_todos"):
        flags.append("overdue_todo")
    if not row.get("contact_id"):
        flags.append("missing_contact")
    if not row.get("company_id"):
        flags.append("missing_company")
    return flags


def get_deal_health(deal_id: int, stale_days: int = DEFAULT_DEAL_STALE_DAYS) -> dict | None:
    """One deal's health: #18's lead score + factors, plus the operational signals.

    Answers "should I worry about this deal, and why" in a single read — the score
    says how promising it looks, the flags say what is actually wrong with it. The
    two are complementary: a high-scoring deal nobody has touched in three weeks is
    exactly the one worth surfacing, and neither half says that alone.

    Returns None when the deal does not exist. Archived deals ARE returned (with
    ``archived: true``) for the same reason ``get_deal`` resolves them — you need to
    be able to look at one to decide whether to restore it.
    """
    stale_days = _bounded(stale_days, DEFAULT_DEAL_STALE_DAYS, 1, 365)
    # Date-only TEXT comparison for overdue, matching get_dashboard_stats: a todo due
    # today is not overdue, and a malformed row can never cast-error the way ::date can.
    # That day is the CONFIGURED-TIMEZONE one since #130 — it moved here in the same
    # sweep, because "is this todo overdue" must not depend on which report asked.
    today = gtd_common.today_local_str()
    row = pg_fetchone(
        f"""
        SELECT d.id, d.title, d.stage, d.value, d.currency, d.probability,
               d.expected_close_date, d.lost_reason, d.contact_id, d.company_id,
               (d.archived_at IS NOT NULL) AS archived,
               c.name  AS contact_name,
               co.name AS company_name,
               FLOOR(EXTRACT(EPOCH FROM (now() - {LAST_TOUCH_SQL})) / 86400.0)::int
                   AS days_since_touch,
               FLOOR(EXTRACT(EPOCH FROM (now() - COALESCE(
                   (SELECT MAX(e.changed_at) FROM deal_stage_events e
                     WHERE e.deal_id = d.id), d.created_at))) / 86400.0)::int
                   AS days_in_stage,
               FLOOR(EXTRACT(EPOCH FROM (now() - d.created_at)) / 86400.0)::int AS age_days,
               (SELECT COUNT(*) FROM todos t
                 WHERE t.deal_id = d.id AND t.completed = 0
                   AND {NOT_DROPPED_TODO_T})::int AS open_todos,
               (SELECT COUNT(*) FROM todos t
                 WHERE t.deal_id = d.id AND t.completed = 0 AND {NOT_DROPPED_TODO_T}
                   AND t.due_date != '' AND t.due_date < %s)::int AS overdue_todos
          FROM deals d
          LEFT JOIN contacts  c  ON d.contact_id = c.id
          LEFT JOIN companies co ON d.company_id = co.id
         WHERE d.id = %s
        """,
        (today, deal_id),
    )
    if not row:
        return None

    # #18 owns the score. A missing score row is not an error here — score_deal reads
    # live and only returns None for a deal that vanished between our two queries.
    scored = scoring_service.score_deal(deal_id) or {}
    return {
        "deal": row,
        "score": scored.get("score"),
        "factors": scored.get("factors", {}),
        "stale_days": stale_days,
        "flags": _health_flags(row, stale_days),
    }


# ── Pipeline analytics (issue #22 Phase 2) ────────────────────────────────────
#
# The half `service.get_analytics` (#20) had to drop. #20 shipped win/loss, activity
# volume and read-time aging, but every stage-duration metric — conversion, time in
# stage — was cut for lack of a stage-change audit trail. Phase 1 created that trail
# (`deal_stage_events`), so this reads it and answers the funnel questions.
#
# The two tools are complementary, not overlapping: crm_analytics = outcomes and
# activity, crm_get_pipeline_analytics = movement through the funnel.
#
# Two honesty guards, because a funnel that quietly understates is worse than no funnel.
#
# 1. The log only starts when Phase 1 landed, so a 90-day window can cover a 3-day log.
#    Every response carries `history_since`/`history_days`/`history_covers_window`.
# 2. `create_deal` writes no stage event (it has no old stage to transition from — see
#    service._write_deal_update), so a deal created directly into 'lead' never counts as
#    having ENTERED lead. "Entered" therefore means "transitioned into", and the first
#    stage will read low on a CRM where deals are created rather than moved in. Neither
#    is worth distorting the data to hide; both are stated in the tool description so the
#    assistant explains them instead of reporting a misleading zero.

DEFAULT_ANALYTICS_WINDOW_DAYS = 90


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 1)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 1)


def _shape_stage_durations(rows: list[dict]) -> list[dict]:
    """Group completed stage intervals into per-stage avg/median days. Pure shaper."""
    by_stage: dict[str, list[float]] = {}
    for r in rows:
        days = r.get("days")
        if days is None:
            continue
        by_stage.setdefault(r.get("stage") or "", []).append(float(days))
    out = []
    for stage in OPEN_STAGES:
        vals = by_stage.get(stage, [])
        out.append({
            "stage": stage,
            "samples": len(vals),
            "avg_days": round(sum(vals) / len(vals), 1) if vals else None,
            "median_days": _median(vals),
        })
    return out


def _shape_conversion(rows: list[dict]) -> list[dict]:
    """Turn one row per (deal, first entry into a stage) into per-stage outcomes.

    An entry's outcome is read from the deal's CURRENT stage: still sitting in the
    stage it entered, advanced to another open stage, won, or lost. Pure shaper.
    """
    buckets: dict[str, dict] = {
        s: {"stage": s, "entered": 0, "still_here": 0, "advanced": 0, "won": 0, "lost": 0}
        for s in OPEN_STAGES
    }
    for r in rows:
        stage = r.get("stage") or ""
        if stage not in buckets:
            continue
        b = buckets[stage]
        b["entered"] += 1
        current = (r.get("current_stage") or "").lower()
        if current == "won":
            b["won"] += 1
        elif current == "lost":
            b["lost"] += 1
        elif current == stage:
            b["still_here"] += 1
        else:
            b["advanced"] += 1
    out = []
    for stage in OPEN_STAGES:
        b = buckets[stage]
        entered = b["entered"]
        # Progression = got out of this stage in the right direction (moved on OR won).
        progressed = b["advanced"] + b["won"]
        b["progression_rate"] = round(progressed / entered, 3) if entered else None
        b["win_rate"] = round(b["won"] / entered, 3) if entered else None
        out.append(b)
    return out


def get_pipeline_analytics(window_days: int = DEFAULT_ANALYTICS_WINDOW_DAYS) -> dict:
    """Funnel movement from the stage-change log: time in stage, conversion, velocity.

    Complements ``service.get_analytics`` (win/loss, activity, aging) rather than
    repeating it. Archived deals are excluded throughout — an archived deal did not
    "convert", it was put away.
    """
    window_days = _bounded(window_days, DEFAULT_ANALYTICS_WINDOW_DAYS, 7, 365)

    # Completed stage intervals: how long a deal sat in a stage before leaving it.
    # LEAD() over the deal's own event chain gives the exit time; a NULL next event
    # means the deal is still in that stage, which is an OPEN interval and would bias
    # the average downward, so those rows are excluded rather than clamped to now().
    duration_rows = pg_fetchall(
        f"""
        SELECT stage, EXTRACT(EPOCH FROM (next_at - changed_at)) / 86400.0 AS days
          FROM (
            SELECT e.new_stage AS stage, e.changed_at,
                   LEAD(e.changed_at) OVER (
                       PARTITION BY e.deal_id ORDER BY e.changed_at, e.id) AS next_at
              FROM deal_stage_events e
              JOIN deals d ON d.id = e.deal_id
             WHERE {LIVE_PREDICATE_D}
          ) s
         WHERE next_at IS NOT NULL
           AND changed_at >= now() - make_interval(days => %s)
        """,
        (window_days,),
    )

    # One row per (deal, stage) — the FIRST time that deal entered that stage inside
    # the window. DISTINCT ON keeps a deal that bounced back into a stage from being
    # counted twice in the same denominator.
    conversion_rows = pg_fetchall(
        f"""
        SELECT DISTINCT ON (e.deal_id, e.new_stage)
               e.new_stage AS stage, e.deal_id, d.stage AS current_stage
          FROM deal_stage_events e
          JOIN deals d ON d.id = e.deal_id
         WHERE {LIVE_PREDICATE_D}
           AND e.changed_at >= now() - make_interval(days => %s)
         ORDER BY e.deal_id, e.new_stage, e.changed_at, e.id
        """,
        (window_days,),
    )

    # #279: velocity dates a win by its Closed on day when someone recorded one (reps
    # backdate wins), else by the move into won. The DATE becomes an instant at local
    # midnight; zoneinfo names the zone, Postgres only applies it. Conversion needs no
    # such change: it buckets entries into OPEN stages and reads the outcome from the
    # deal's current stage, so no win date enters it.
    zone = tz().key

    # Velocity: deals won inside the window, and how long they took from their first
    # recorded stage event. Deals that predate the log have no first event, so they
    # are simply absent — never counted with a fabricated start date. A Closed on day
    # earlier than the first event (a same-day win, or a backdate) counts as 0 days.
    velocity_row = pg_fetchone(
        f"""
        SELECT COUNT(*) AS won_count,
               AVG(GREATEST(EXTRACT(EPOCH FROM (won_at - first_at)), 0) / 86400.0)
                   AS avg_days_to_won
          FROM (
            SELECT e.deal_id,
                   MIN(e.changed_at) AS first_at,
                   COALESCE(d.closed_on::timestamp AT TIME ZONE %s,
                            MAX(e.changed_at) FILTER (WHERE e.new_stage = 'won')) AS won_at
              FROM deal_stage_events e
              JOIN deals d ON d.id = e.deal_id
             WHERE {LIVE_PREDICATE_D}
             GROUP BY e.deal_id, d.closed_on
          ) s
         WHERE won_at IS NOT NULL
           AND won_at >= now() - make_interval(days => %s)
        """,
        (zone, window_days),
    )

    history_row = pg_fetchone(
        "SELECT MIN(changed_at) AS since, "
        "FLOOR(EXTRACT(EPOCH FROM (now() - MIN(changed_at))) / 86400.0)::int AS days "
        "FROM deal_stage_events"
    )
    history_since = (history_row or {}).get("since")
    history_days = (history_row or {}).get("days")

    velocity = velocity_row or {}
    avg_days_to_won = velocity.get("avg_days_to_won")
    return {
        "window_days": window_days,
        # The log started when Phase 1 landed. When history_days < window_days the
        # funnel below covers less ground than the window implies — say so.
        "history_since": history_since,
        "history_days": history_days,
        "history_covers_window": bool(history_days is not None and history_days >= window_days),
        "time_in_stage": _shape_stage_durations(duration_rows),
        "conversion": _shape_conversion(conversion_rows),
        "velocity": {
            "won_in_window": int(velocity.get("won_count") or 0),
            "avg_days_to_won": round(float(avg_days_to_won), 1) if avg_days_to_won is not None else None,
        },
    }
