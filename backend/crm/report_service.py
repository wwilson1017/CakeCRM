"""CRM — the Reports page's one-company rollup and its merged feed (issue #144).

Read-only. Two functions, both composed the way the rest of this package composes rollups:
N simple ``pg_fetchall`` calls assembled in Python, never one mega-JOIN. Ported from
``cake_os/backend/apps/crm/report_service.py`` (cake_os #2336) with three deliberate
divergences, each forced by a real difference in this schema:

1. **No company-level activity bucket.** ``activity_log`` here has no ``company_id`` column
   (only ``contact_id`` and ``deal_id``), so there is no such thing as an activity logged
   against the company itself. The blueprint's third activity read and its
   ``deal_id IS NULL AND contact_id IS NULL`` guard therefore have no counterpart, and the
   response carries **no** ``company.activities`` key at all. An always-empty list the UI
   always renders blank would be worse than an absent key — it would look like a feature
   that is broken rather than one that does not exist.

2. **The timeline is a genuine two-source merge.** The blueprint reads one unified chatter
   view whose two backing stores share a sequence. Here, notes live in ``crm_chatter`` and
   events in ``activity_log`` — two tables with INDEPENDENT ``SERIAL`` sequences — so the
   reader ``UNION ALL``s them and projects a ``source`` discriminator. See the ORDER BY
   comment in ``get_company_timeline``: this is the one place where the obvious port is a
   silent correctness bug.

3. **Archived records are opt-in, not always-on.** The blueprint always includes archived
   children and marks them. Here the deal sweep (``LIVE_PREDICATE``) is a repo-wide
   invariant with only two sanctioned holes (``search_deals(include_archived=True)`` and
   ``get_deal``), so this reader adds a third of the same shape rather than a fourth
   different one: ``include_archived`` defaults False and the UI offers a checkbox. Archived
   NOTES follow the ``chatter_service.get_chatter`` contract for the same reason. Contacts
   are the exception in both directions — ``contacts.status`` is not a sweep, an archived
   contact is still this company's history, so contacts are always included and rendered
   marked (which also matches ``get_company_detail``'s documented asymmetry).

Why this is not folded into ``service.get_company_detail``: that reader is the Companies
detail panel — 20 activities, no notes, no truncation flags, no pagination, and a different
archived policy it documents on purpose. This one is a report. Two consumers, two contracts.
"""

from core.postgres import pg_fetchall, pg_fetchone
from crm import attachment_service
from crm.service import LIVE_PREDICATE, LIVE_PREDICATE_D

# Defensive bound on BOTH child lists. An account's contacts and deals number in the tens, so
# this is a ceiling rather than a page — but it is never SILENT: each read takes one row past
# the cap purely as a probe, and the excess becomes an explicit `*_truncated` flag the page
# states in words. A report whose whole promise is "everything we know about this company"
# must not quietly under-report it.
# simplification: page the two sections if an account ever legitimately exceeds this.
ROLLUP_CHILD_CAP = 1000

# Newest N activities PER PARENT record — a `row_number()` window, deliberately not one global
# LIMIT, because a global cap lets one busy deal starve every quiet sibling of its history.
# Same +1 probe, surfaced per record as `activities_truncated`.
ACTIVITY_PER_RECORD_CAP = 50

# The timeline's page ceiling, mirrored by the route's `Query(le=...)`.
TIMELINE_MAX_LIMIT = 200


def _trim(rows: list[dict]) -> tuple[list[dict], bool]:
    """Drop the probe row, report whether it was there, and strip the window bookkeeping.

    ``rn`` is the window's own row-number column. ``SELECT *`` over the windowed subquery
    would otherwise carry it out to the client as a fake activity field.
    """
    truncated = len(rows) > ACTIVITY_PER_RECORD_CAP
    kept = rows[:ACTIVITY_PER_RECORD_CAP]
    for row in kept:
        row.pop("rn", None)
    return kept, truncated


def _group_by(rows: list[dict], key: str) -> dict[int, tuple[list[dict], bool]]:
    """Bucket already-sorted rows by parent id, then trim each bucket independently."""
    buckets: dict[int, list[dict]] = {}
    for row in rows:
        parent = row.get(key)
        if parent is not None:
            buckets.setdefault(parent, []).append(row)
    return {parent: _trim(bucket) for parent, bucket in buckets.items()}


def get_company_rollup(company_id: int, include_archived: bool = False) -> dict | None:
    """Everything the CRM knows about one company, in one read.

    Returns ``None`` when the company does not exist — that is the router's 404 seam.

    Every contact and every deal carries BOTH ``activities`` and ``activities_truncated``,
    never an absent key: ``[]`` means "none", absent would read as "not loaded".
    """
    company = pg_fetchone("SELECT * FROM companies WHERE id = %s", (company_id,))
    if not company:
        return None

    # Own query rather than `service.list_contacts`, which has no company_id filter.
    # No status filter: an archived contact is still this company's history, rendered marked.
    # (name, id) because names are not unique — #58's total-order rule.
    contacts = pg_fetchall(
        """SELECT * FROM contacts WHERE company_id = %s
           ORDER BY name ASC, id ASC LIMIT %s""",
        (company_id, ROLLUP_CHILD_CAP + 1),
    )
    contacts_truncated = len(contacts) > ROLLUP_CHILD_CAP
    contacts = contacts[:ROLLUP_CHILD_CAP]

    deal_conditions = ["d.company_id = %s"] + ([] if include_archived else [LIVE_PREDICATE_D])
    deals = pg_fetchall(
        f"""SELECT d.*, c.name AS contact_name
            FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id
            WHERE {" AND ".join(deal_conditions)}
            ORDER BY d.updated_at DESC, d.id DESC LIMIT %s""",
        (company_id, ROLLUP_CHILD_CAP + 1),
    )
    deals_truncated = len(deals) > ROLLUP_CHILD_CAP
    deals = deals[:ROLLUP_CHILD_CAP]

    deal_ids = [d["id"] for d in deals]
    contact_ids = [c["id"] for c in contacts]

    # Activities are attributed to their MOST SPECIFIC parent, so one logged call that names
    # both a deal and a contact renders exactly once. The window's inner ORDER BY is what
    # decides which rows survive the per-parent cap; the outer one re-sorts because a window
    # subquery does not propagate its inner ordering to the outer result.
    deal_acts = (
        pg_fetchall(
            """SELECT * FROM (
                 SELECT a.*, c.name AS contact_name, row_number() OVER (
                          PARTITION BY a.deal_id
                          ORDER BY a.created_at DESC, a.id DESC) AS rn
                   FROM activity_log a LEFT JOIN contacts c ON a.contact_id = c.id
                  WHERE a.deal_id = ANY(%s)
               ) w WHERE w.rn <= %s ORDER BY w.created_at DESC, w.id DESC""",
            (deal_ids, ACTIVITY_PER_RECORD_CAP + 1),
        )
        if deal_ids
        else []
    )

    # A row naming one of THIS company's deals already appeared above, so it is excluded here.
    # A row naming a deal of ANOTHER company (a contact who moved) still belongs to this
    # contact and stays — `<> ALL(empty)` is TRUE, so a company with no deals keeps every one.
    contact_acts = (
        pg_fetchall(
            """SELECT * FROM (
                 SELECT a.*, row_number() OVER (
                          PARTITION BY a.contact_id
                          ORDER BY a.created_at DESC, a.id DESC) AS rn
                   FROM activity_log a
                  WHERE a.contact_id = ANY(%s)
                    AND (a.deal_id IS NULL OR a.deal_id <> ALL(%s))
               ) w WHERE w.rn <= %s ORDER BY w.created_at DESC, w.id DESC""",
            (contact_ids, deal_ids, ACTIVITY_PER_RECORD_CAP + 1),
        )
        if contact_ids
        else []
    )

    by_deal = _group_by(deal_acts, "deal_id")
    by_contact = _group_by(contact_acts, "contact_id")
    for contact in contacts:
        contact["activities"], contact["activities_truncated"] = by_contact.get(
            contact["id"], ([], False)
        )
    for deal in deals:
        acts, truncated = by_deal.get(deal["id"], ([], False))
        deal["activities"] = acts
        deal["activities_truncated"] = truncated
        # The list is already newest-first. This is the newest ACTIVITY only — deliberately
        # not blended with chatter the way the pipeline board's last_activity_at is: the
        # collapsed row says "last activity", and the notes are in the timeline below.
        deal["last_activity_at"] = acts[0]["created_at"] if acts else None

    return {
        "company": company,
        "contacts": contacts,
        "deals": deals,
        "contacts_truncated": contacts_truncated,
        "deals_truncated": deals_truncated,
    }


def get_company_timeline(
    company_id: int,
    limit: int = 100,
    offset: int = 0,
    include_archived: bool = False,
) -> dict | None:
    """The company's merged notes + activities feed, newest first, LIMIT/OFFSET paged.

    Returns ``None`` when the company does not exist — the router's 404 seam.

    ``has_more`` comes from a ``limit + 1`` probe row rather than a second ``COUNT``: a count
    taken beside a page goes stale the moment anyone writes, and this feed is read while
    people are working in the CRM.

    Housekeeping notes written by ``provenance_service.confirm`` and ``merge_deals``' copies
    are ordinary chatter rows and appear here, exactly as ``NotesThread`` already shows them.
    Do not "fix" that with ``_ACTIVITY_CHATTER_EXCLUSIONS`` — that list exists for per-rep
    COUNTS, where double-counting is the bug; here they are history a reader wants.
    """
    company = pg_fetchone(
        "SELECT id, name, status FROM companies WHERE id = %s", (company_id,)
    )
    if not company:
        return None
    limit = max(1, min(int(limit), TIMELINE_MAX_LIMIT))
    offset = max(0, int(offset))

    note_archived = "" if include_archived else " AND ch.archived = 0"
    deal_live = "" if include_archived else f" AND {LIVE_PREDICATE}"

    rows = pg_fetchall(
        f"""SELECT * FROM (
              SELECT 'note' AS source, ch.id, ch.entity_type, ch.entity_id,
                     NULL::text AS activity, ch.message, ch.created_at, ch.updated_at,
                     ch.archived, ch.author_id AS actor_id
                FROM crm_chatter ch
               WHERE ((ch.entity_type = 'company' AND ch.entity_id = %s)
                  OR (ch.entity_type = 'contact' AND ch.entity_id IN (
                        SELECT id FROM contacts WHERE company_id = %s))
                  OR (ch.entity_type = 'deal' AND ch.entity_id IN (
                        SELECT id FROM deals WHERE company_id = %s{deal_live}))){note_archived}
              UNION ALL
              SELECT 'activity' AS source, a.id,
                     CASE WHEN a.deal_id IS NOT NULL THEN 'deal' ELSE 'contact' END
                       AS entity_type,
                     COALESCE(a.deal_id, a.contact_id) AS entity_id,
                     a.activity, a.note AS message, a.created_at,
                     NULL::timestamptz AS updated_at, 0 AS archived, a.actor_id
                FROM activity_log a
               WHERE a.contact_id IN (SELECT id FROM contacts WHERE company_id = %s)
                  OR a.deal_id IN (SELECT id FROM deals WHERE company_id = %s{deal_live})
            ) t
            -- (created_at, source, id) is a TOTAL order across this two-table merge, and all
            -- three terms are load-bearing. `id` ALONE is not unique here: crm_chatter and
            -- activity_log have independent SERIAL sequences, so note #7 and activity #7 both
            -- exist. Under LIMIT/OFFSET that tie duplicates one row onto two pages and drops
            -- another entirely. The #58 determinism scanner cannot catch it — it judges the
            -- FINAL term's name, and `id` is in its allowlist — so `source` is what actually
            -- makes (source, id) unique, while `id` stays last so the static sweep still
            -- proves the shape on its own merits.
            -- Pinned by test_timeline_order_is_total_across_both_sources.
            ORDER BY t.created_at DESC, t.source DESC, t.id DESC
            LIMIT %s OFFSET %s""",
        (company_id, company_id, company_id, company_id, company_id, limit + 1, offset),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Hydration is scoped to the ids THIS page cites, de-duplicated and order-preserving, so
    # per-page work stays proportional to the page rather than to the whole account. Each read
    # is guarded, because an empty `= ANY('{}')` is a wasted round trip.
    page_contact_ids = list(
        dict.fromkeys(r["entity_id"] for r in rows if r["entity_type"] == "contact")
    )
    page_deal_ids = list(
        dict.fromkeys(r["entity_id"] for r in rows if r["entity_type"] == "deal")
    )
    contact_rows = (
        pg_fetchall(
            "SELECT id, name, status FROM contacts WHERE id = ANY(%s)", (page_contact_ids,)
        )
        if page_contact_ids
        else []
    )
    # No live filter on this read on purpose: it labels whatever the page already returned,
    # including an archived deal reached through one of its contacts.
    deal_rows = (
        pg_fetchall(
            "SELECT id, title, archived_at FROM deals WHERE id = ANY(%s)", (page_deal_ids,)
        )
        if page_deal_ids
        else []
    )
    # The two archived axes are different columns: companies and contacts carry a `status`
    # string, deals carry `archived_at`.
    names = {
        "company": {company["id"]: (company["name"], company["status"] == "archived")},
        "contact": {r["id"]: (r["name"], r["status"] == "archived") for r in contact_rows},
        "deal": {r["id"]: (r["title"], r["archived_at"] is not None) for r in deal_rows},
    }
    attachments = attachment_service.list_for_notes(
        [r["id"] for r in rows if r["source"] == "note"]
    )
    for row in rows:
        # The fallback covers a child that moved companies mid-request. A read-only report
        # labels the row and moves on; it never raises.
        name, archived = names.get(row["entity_type"], {}).get(
            row["entity_id"], (f"{row['entity_type']} #{row['entity_id']}", False)
        )
        row["source_name"] = name
        row["source_archived"] = archived
        if row["source"] == "note":
            row["attachments"] = attachments.get(row["id"], [])

    return {"entries": rows, "has_more": has_more}
