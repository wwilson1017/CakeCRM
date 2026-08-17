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

from core.postgres import pg_fetchall, pg_fetchone
from crm.service import (
    LAST_TOUCH_SQL,
    LIVE_PREDICATE,
    LIVE_PREDICATE_D,
    OPEN_PREDICATE,
    OPEN_PREDICATE_D,
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

def get_stale_deals(stale_days: int = DEFAULT_DEAL_STALE_DAYS, limit: int = DEFAULT_LIMIT) -> dict:
    """Open deals nobody has touched in ``stale_days`` days, stalest first.

    Complements ``service.get_analytics``: that one answers "how many are stale" for
    the dashboard, this one answers "which ones, and what do I do about them" — so it
    carries the stage, value, owner-facing names, days in the current stage, and
    whether a follow-up task already exists (a deal with an open task is being
    handled, and nagging about it is noise).

    ``days_in_stage`` reads the newest ``deal_stage_events`` row and falls back to the
    deal's ``created_at`` for deals that predate the stage log — honest either way:
    a deal that has never moved HAS been in its stage since it was created.
    """
    stale_days = _bounded(stale_days, DEFAULT_DEAL_STALE_DAYS, 1, 365)
    limit = _bounded(limit, DEFAULT_LIMIT)
    rows = pg_fetchall(
        f"""
        SELECT d.id, d.title, d.stage, d.value, d.currency, d.expected_close_date,
               d.probability, d.contact_id, d.company_id,
               c.name  AS contact_name,
               co.name AS company_name,
               FLOOR(EXTRACT(EPOCH FROM (now() - {LAST_TOUCH_SQL})) / 86400.0)::int
                   AS days_since_touch,
               FLOOR(EXTRACT(EPOCH FROM (now() - COALESCE(
                   (SELECT MAX(e.changed_at) FROM deal_stage_events e
                     WHERE e.deal_id = d.id), d.created_at))) / 86400.0)::int
                   AS days_in_stage,
               EXISTS (SELECT 1 FROM tasks t
                        WHERE t.deal_id = d.id AND t.completed = 0) AS has_open_task
          FROM deals d
          LEFT JOIN contacts  c  ON d.contact_id = c.id
          LEFT JOIN companies co ON d.company_id = co.id
         WHERE {OPEN_PREDICATE_D} AND {LIVE_PREDICATE_D}
           AND {LAST_TOUCH_SQL} < now() - make_interval(days => %s)
         ORDER BY days_since_touch DESC, d.id ASC
         LIMIT %s
        """,
        (stale_days, limit),
    )
    # The count exists so a truncated list never understates the problem — but it is a
    # second full scan of the same non-sargable predicate (~150ms at 50k deals), so
    # only pay for it when the list actually WAS truncated. Under the limit, the rows
    # we already have are the exact answer.
    if len(rows) < limit:
        total_stale = len(rows)
    else:
        total_row = pg_fetchone(
            f"""SELECT COUNT(*) AS cnt FROM deals d
                 WHERE {OPEN_PREDICATE_D} AND {LIVE_PREDICATE_D}
                   AND {LAST_TOUCH_SQL} < now() - make_interval(days => %s)""",
            (stale_days,),
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
) -> dict:
    """Active contacts with no logged interaction in ``stale_days`` days.

    Contacts have no ``last_contacted_at`` column, so recency is derived from the
    contact's own ``activity_log`` rows and un-archived notes. A contact who has NEVER
    been contacted is included with ``days_since_contact: null`` and sorts first —
    "never" is the most urgent case, not a missing value to skip.

    Archived/inactive contacts are excluded: deliberately parked, not neglected.

    Scale note: the CTE derives a last-touch date for EVERY active contact before
    filtering (two correlated subqueries each) — accepted at single-user v1 scale, the
    same trade-off get_pipeline documents. If contact volume ever grows, index-driven
    LATERAL MAXes or a maintained last_contacted_at column are the upgrades.
    """
    stale_days = _bounded(stale_days, DEFAULT_CONTACT_STALE_DAYS, 1, 365)
    limit = _bounded(limit, DEFAULT_LIMIT)
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
                    AND ch.archived = 0)
            ) AS touched_at
              FROM contacts ct
        )
        SELECT ct.id, ct.name, ct.email, ct.company, ct.company_id, ct.status,
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
         ORDER BY lt.touched_at ASC NULLS FIRST, ct.id ASC
         LIMIT %s
        """,
        (stale_days, limit),
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
         ORDER BY COUNT(*) DESC, lower(btrim(title)) ASC
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
    out["unverified_fields"] = [] if not provenance_types else pg_fetchall(
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
             ORDER BY p.populated_at DESC
             LIMIT %s""",
        (provenance_types, limit),
    )
    out["gaps_returned"] = sum(len(v) for k, v in out.items()
                               if isinstance(v, list) and k != "unverified_fields")
    return out
