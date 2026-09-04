"""CRM — the Reports page's one-company rollup and its merged feed (issue #144).

Read-only, and that is a property of the whole module: nothing here writes, and the page
built on it embeds everything an expanded row needs so it never has to ask again. Two
functions, composed the way the rest of this package composes rollups — N simple
``pg_fetchall`` calls assembled in Python, never one mega-JOIN.

Ported from ``cake_os/backend/apps/crm/report_service.py`` (cake_os #2336) with four
divergences, each forced by a real difference here:

1. **No company-level activity bucket.** ``activity_log`` has no ``company_id`` column
   (only ``contact_id`` and ``deal_id``), so there is no such thing as an activity logged
   against the company itself. The blueprint's third activity read has no counterpart, and
   the response carries **no** ``company.activities`` key — an always-empty list the UI
   always renders blank would look like a broken feature rather than an absent one.

2. **The timeline is a genuine two-source merge.** The blueprint reads one unified chatter
   view whose two backing stores share a sequence. Here notes live in ``crm_chatter`` and
   events in ``activity_log`` — two tables with INDEPENDENT ``SERIAL`` sequences — so the
   reader ``UNION ALL``s them and projects a ``source`` discriminator. See the ORDER BY
   comment in ``get_company_timeline``: that is the one place where the obvious port is a
   silent correctness bug.

3. **The headline numbers are their own aggregate, not a reduction of the capped lists.**
   The blueprint derives its chips client-side from the returned children, which makes a
   capped list quietly change a headline number — worst of all when the archive toggle
   lets archived deals displace live ones inside the same window, so enabling *more*
   history could make the open-deal count go *down*. ``summary`` is therefore computed
   over the full tables and is exact regardless of every cap below it.

4. **Custom fields and tasks ride the payload, batched.** The blueprint fetches two known
   field keys for deals only; this issue's load-bearing requirement is EVERY field on
   EVERY expanded entity. Definitions are read once per entity type and values once per
   entity type (``field_service``'s existing batch reader), so an expanded row renders
   every field — including the ones nobody has filled in — from data it already has. That
   is what keeps "Expand all" from turning one click into ~150 HTTP requests.

**The payload ceiling, stated rather than implied.** At the documented maxima this response
embeds 200 deals and 200 contacts, each with up to 25 activities, each deal with up to 25
open tasks — so ~15,000 child rows plus the custom fields, and free text makes the byte size
unbounded even though the row counts are not. Realistic accounts are in the tens and the
truncation flags say so when they are not, but a very large account WILL produce a large
response. The upgrade path is to make the per-record activity and task lists lazy (fetched on
expand, as the blueprint fetched custom fields) rather than to raise or lower the caps; that
would leave only the two child lists in the initial payload.

**The per-parent caps bound the OUTPUT, not the database work.** Each `row_number()` window
ranks every matching row for all selected parents and only then filters to `cap + 1`, so one
deal with an enormous history still costs a full scan and sort of its rows. The bounded shape
is a `LATERAL (… ORDER BY created_at DESC, id DESC LIMIT cap + 1)` per parent — the same
GROUPED-vs-LATERAL split `service.get_pipeline` documents, whose rule ("LATERAL for a
per-page read inside a capped reader") points here. It is deliberately not done yet, for the
reason #59 gave for the same call: there is no measured need, and the supporting composite
index (`activity_log(deal_id, created_at DESC, id DESC)`) does not exist either — today's
indexes are single-column, so a LATERAL would still sort within each parent. Both belong to
one measured perf change, not to this reader's first version.

**What ``include_archived`` means, precisely** — the name is broad and the behaviour is
not, so read this rather than inferring it. It widens exactly two things: **archived
deals** (``deals.archived_at``) and **archived notes** (``crm_chatter.archived``). It does
NOT govern contacts: ``contacts.status`` is not a sweep, an archived contact is still this
company's history, so contacts are ALWAYS returned and rendered marked (this also matches
``service.get_company_detail``'s documented asymmetry). ``summary.contact_count`` is
narrower still — it counts ``status = 'active'`` only, so BOTH ``inactive`` and ``archived``
are out, because the chip it feeds says "Active contacts" and that word has to be true. The
section below lists every contact and states its own total. Two different questions, each
answered honestly, rather than one number that fits neither label.

Archived deals being opt-in makes this the **third** sanctioned hole in the
``LIVE_PREDICATE`` sweep, in the same shape as the other two
(``search_deals(include_archived=)`` and #83's ``get_pipeline(include_archived=)``).

Why this is not folded into ``service.get_company_detail``: that reader is the Companies
detail panel — 20 activities, no notes, no truncation flags, no pagination, a different
archived policy it documents on purpose. This one is a report. Two consumers, two
contracts.
"""

from core.postgres import pg_fetchall, pg_fetchone
from crm import attachment_service, field_service
from crm.service import (
    LIVE_PREDICATE,
    LIVE_PREDICATE_D,
    NOT_DROPPED_TASK,
    OPEN_PREDICATE,
)

# Bound on BOTH child lists. Never SILENT: each read takes one row past the cap purely as
# a probe, and the excess becomes an explicit `*_truncated` flag the page states in words.
# It is 200 rather than the blueprint's 1000 because every child here carries its
# activities, its custom fields and (for a deal) its tasks — so the cap bounds a payload,
# not just a row count, and 1000 children would permit a response nobody wants to receive.
# The headline numbers in `summary` are computed over the FULL tables, so this cap changes
# what you can scroll through, never what the company is reported to be worth.
# simplification: page the two sections if an account legitimately exceeds this.
ROLLUP_CHILD_CAP = 200

# Newest N activities PER PARENT record — a `row_number()` window, deliberately not one
# global LIMIT, because a global cap lets one busy deal starve every quiet sibling of its
# history. Same +1 probe, surfaced per record as `activities_truncated`.
ACTIVITY_PER_RECORD_CAP = 25

# Open tasks shown inside an expanded deal, same windowing and the same reason.
TASKS_PER_DEAL_CAP = 25

# The timeline's page ceiling, mirrored by the route's `Query(le=...)`.
TIMELINE_MAX_LIMIT = 200


def _trim(rows: list[dict], cap: int) -> tuple[list[dict], bool]:
    """Drop the probe row, report whether it was there, strip the window bookkeeping.

    ``rn`` is the window's own row-number column. ``SELECT *`` over the windowed subquery
    would otherwise carry it out to the client as a fake field on the record.
    """
    truncated = len(rows) > cap
    kept = rows[:cap]
    for row in kept:
        row.pop("rn", None)
    return kept, truncated


def _group_by(rows: list[dict], key: str, cap: int) -> dict[int, tuple[list[dict], bool]]:
    """Bucket already-sorted rows by parent id, then trim each bucket independently."""
    buckets: dict[int, list[dict]] = {}
    for row in rows:
        parent = row.get(key)
        if parent is not None:
            buckets.setdefault(parent, []).append(row)
    return {parent: _trim(bucket, cap) for parent, bucket in buckets.items()}


def _custom_fields(entity_type: str, entity_ids: list[int]) -> dict[int, list[dict]]:
    """Every DEFINED field for each entity, in display order, value or None.

    Two queries per entity type regardless of how many entities there are: the definitions
    once, the non-empty values once. Empty fields are reconstructed here rather than
    fetched, because "every field" includes the ones nobody filled in — a values-only read
    cannot express an unset field, and that is precisely what this report has to show.
    """
    definitions = field_service.list_field_definitions(entity_type)
    if not definitions or not entity_ids:
        return {}
    values = field_service.get_field_values_batch(entity_type, entity_ids)
    out: dict[int, list[dict]] = {}
    for entity_id in entity_ids:
        owned = values.get(entity_id) or {}
        out[entity_id] = [
            {
                "field_key": d["field_key"],
                "name": d["name"],
                "field_type": d["field_type"],
                "value": owned.get(d["field_key"]),
            }
            for d in definitions
        ]
    return out


def get_company_rollup(company_id: int, include_archived: bool = False) -> dict | None:
    """Everything the CRM knows about one company, in one read.

    Returns ``None`` when the company does not exist — that is the router's 404 seam.

    Every contact and every deal carries BOTH ``activities`` and ``activities_truncated``,
    plus ``custom_fields``; a deal additionally carries ``tasks``/``tasks_truncated`` and
    ``last_activity_at``. None of those keys is ever absent: ``[]`` means "none", absent
    would read as "not loaded", and an expanded row must be able to tell the difference.
    """
    company = pg_fetchone("SELECT * FROM companies WHERE id = %s", (company_id,))
    if not company:
        return None

    # Exact headline numbers over the FULL tables — deliberately not a reduction of the
    # capped lists below, so no cap and no archive toggle can move them. Open = live AND
    # in a non-terminal stage: two columns, two axes, because there is no `status` here.
    #
    # `contact_count` is `status = 'active'`, NOT "not archived". `CONTACT_STATUSES` has
    # three values, so the two differ by `inactive` — and the chip this feeds is labelled
    # "Active contacts", which would be a lie about an inactive contact. The section below
    # lists every contact and states its own total, so the two numbers are different
    # questions answered honestly rather than one number that fits neither label.
    #
    # Both deal figures come from ONE scan of the filtered set; two sub-selects with the
    # same WHERE would scan it twice for no reason.
    #
    # `open_deal_currency` is the single currency every open deal agrees on, or NULL when
    # they do not agree (and when there are no open deals at all). It exists because
    # `deals.currency` is USER-WRITABLE — it is in `service._DEAL_USER_WRITABLE` — so
    # USD-only is a convention here, NOT an enforced invariant, and a bare SUM across
    # currencies is a number that is simply false. The rest of the app sums anyway and
    # prefixes '$' (`service.get_company_detail` documents that as a single-currency sum),
    # but "every other screen does it" is consistency, not correctness, and this report
    # states an account's value as a headline. So the sum still ships — it is right in the
    # overwhelmingly common single-currency case — and the UI declines to render it as one
    # figure when the currencies disagree, rather than inventing a total nobody owes.
    summary = pg_fetchone(
        f"""SELECT d.open_deal_count, d.open_deal_value, d.open_deal_currency,
                   c.contact_count
              FROM (SELECT COUNT(*) AS open_deal_count,
                           COALESCE(SUM(value), 0) AS open_deal_value,
                           CASE WHEN COUNT(DISTINCT currency) = 1 THEN MIN(currency) END
                             AS open_deal_currency
                      FROM deals
                     WHERE company_id = %s AND {LIVE_PREDICATE} AND {OPEN_PREDICATE}) d,
                   (SELECT COUNT(*) AS contact_count
                      FROM contacts
                     WHERE company_id = %s AND status = 'active') c""",
        (company_id, company_id),
    )

    # Own query rather than `service.list_contacts`, which has no company_id filter.
    # No status filter: an archived contact is still this company's history, shown marked.
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

    # The two activity buckets are MUTUALLY EXCLUSIVE, and that is the whole design: a row
    # naming a deal belongs to the deal bucket and nowhere else, a row naming no deal
    # belongs to its contact. So one logged call renders exactly once.
    #
    # The contact bucket therefore tests `deal_id IS NULL`, NOT "is not one of the deals we
    # happen to be showing". Testing membership of the displayed set looks equivalent and
    # is not: a deal excluded because it is ARCHIVED (or because it fell past the child
    # cap) is not in that set, so its activities would silently reappear under the contact
    # — showing archived history while archived history is switched off. Two review rounds
    # landed on this; keep the predicate absolute.
    #
    # The consequence, which is NOT the blueprint's: because the rule is absolute, a deal
    # wins globally rather than only among the deals on screen. An activity naming this
    # company's contact AND another company's deal therefore belongs to that DEAL — it
    # appears once, on the other company's rollup and timeline, and on neither of this
    # company's. The blueprint's version dropped such a row from both companies (its
    # predicates were per-company), so this is strictly better: every activity has exactly
    # one home. It is reachable from the contact's own page either way.
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
    contact_acts = (
        pg_fetchall(
            """SELECT * FROM (
                 SELECT a.*, row_number() OVER (
                          PARTITION BY a.contact_id
                          ORDER BY a.created_at DESC, a.id DESC) AS rn
                   FROM activity_log a
                  WHERE a.contact_id = ANY(%s) AND a.deal_id IS NULL
               ) w WHERE w.rn <= %s ORDER BY w.created_at DESC, w.id DESC""",
            (contact_ids, ACTIVITY_PER_RECORD_CAP + 1),
        )
        if contact_ids
        else []
    )

    # Open tasks per deal. `list_tasks`' own predicates are restated rather than reused
    # because that reader takes no id list; NOT_DROPPED_TASK is imported so the "adding a
    # task READER means adding NOT_DROPPED_TASK" rule has one definition. The live-deal
    # half of LIVE_TASK_PREDICATE is already satisfied: `deal_ids` carries the archive
    # policy, so an archived deal's tasks appear only when archived deals were asked for.
    deal_tasks = (
        pg_fetchall(
            f"""SELECT * FROM (
                  SELECT tasks.*, row_number() OVER (
                           PARTITION BY tasks.deal_id
                           ORDER BY tasks.due_date ASC, tasks.id ASC) AS rn
                    FROM tasks
                   WHERE tasks.deal_id = ANY(%s)
                     AND tasks.completed = 0 AND {NOT_DROPPED_TASK}
                ) w WHERE w.rn <= %s ORDER BY w.due_date ASC, w.id ASC""",
            (deal_ids, TASKS_PER_DEAL_CAP + 1),
        )
        if deal_ids
        else []
    )

    deal_fields = _custom_fields("deal", deal_ids)
    contact_fields = _custom_fields("contact", contact_ids)
    company_fields = _custom_fields("company", [company_id])

    by_deal = _group_by(deal_acts, "deal_id", ACTIVITY_PER_RECORD_CAP)
    by_contact = _group_by(contact_acts, "contact_id", ACTIVITY_PER_RECORD_CAP)
    tasks_by_deal = _group_by(deal_tasks, "deal_id", TASKS_PER_DEAL_CAP)

    for contact in contacts:
        contact["activities"], contact["activities_truncated"] = by_contact.get(
            contact["id"], ([], False)
        )
        contact["custom_fields"] = contact_fields.get(contact["id"], [])
    for deal in deals:
        acts, truncated = by_deal.get(deal["id"], ([], False))
        deal["activities"] = acts
        deal["activities_truncated"] = truncated
        # The list is already newest-first. This is the newest ACTIVITY only —
        # deliberately not blended with chatter the way the pipeline board's
        # last_activity_at is: the collapsed row says "last activity", and the notes are
        # in the timeline below.
        deal["last_activity_at"] = acts[0]["created_at"] if acts else None
        deal["tasks"], deal["tasks_truncated"] = tasks_by_deal.get(deal["id"], ([], False))
        deal["custom_fields"] = deal_fields.get(deal["id"], [])

    return {
        "company": company,
        "company_custom_fields": company_fields.get(company_id, []),
        "summary": summary,
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

    ``has_more`` comes from a ``limit + 1`` probe row rather than a second ``COUNT``: a
    count taken beside a page goes stale the moment anyone writes, and this feed is read
    while people are working in the CRM.

    **The paging contract, stated narrowly because the broad version would be false.** The
    ordering is a total order, so paging is deterministic *while the matching set is
    unchanged* — that is what makes a page reproducible and what stops two rows sharing a
    timestamp from swapping between reads. It is NOT immune to concurrent writes: under
    ``OFFSET``, a row deleted or archived behind the cursor shifts everything after it up
    by one, and that row is skipped. The client dedupes on ``(source, id)``, which hides
    duplicates from insertions but cannot recover a skip. Refreshing is the repair.
    Keyset paging on the same triple is the upgrade path, deliberately not taken here: the
    blueprint and the issue both specify offset paging, and this is a read-only report
    someone scrolls once.

    Cost, stated honestly: Postgres finds and sorts the company's whole matching history
    for every page, and deep offsets pay for the rows they skip. Work is proportional to
    the matching corpus, not to the page.

    Housekeeping notes written by ``provenance_service.confirm`` and ``merge_deals``'
    copies are ordinary chatter rows and appear here, exactly as ``NotesThread`` already
    shows them. Do not "fix" that with ``_ACTIVITY_CHATTER_EXCLUSIONS`` — that list exists
    for per-rep COUNTS, where double-counting is the bug; here they are history.
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

    # The activity branch's two halves are mutually exclusive for the same reason the
    # rollup's buckets are: a row naming a deal is that deal's, and reaches the feed only
    # if that deal passes the archive policy. Selecting it via its CONTACT instead would
    # smuggle an archived deal's history in with archived history switched off, and the
    # CASE below would then label it with the archived deal's own name.
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
               WHERE (a.deal_id IS NULL
                      AND a.contact_id IN (SELECT id FROM contacts WHERE company_id = %s))
                  OR a.deal_id IN (
                        SELECT id FROM deals WHERE company_id = %s{deal_live})
            ) t
            -- (created_at, source, id) is a TOTAL order across this two-table merge, and
            -- all three terms are load-bearing. `id` ALONE is not unique here: crm_chatter
            -- and activity_log have independent SERIAL sequences, so note #7 and activity
            -- #7 both exist, and under LIMIT/OFFSET that tie puts one row on two pages and
            -- drops another. The #58 determinism scanner cannot catch it — it judges the
            -- FINAL term's name, and `id` is in its allowlist — so `source` is what
            -- actually makes (source, id) unique, while `id` stays last so the static
            -- sweep still proves the shape on its own merits.
            -- Pinned by test_timeline_order_is_total_across_both_sources.
            ORDER BY t.created_at DESC, t.source DESC, t.id DESC
            LIMIT %s OFFSET %s""",
        (company_id, company_id, company_id, company_id, company_id, limit + 1, offset),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Hydration is scoped to the ids THIS page cites, de-duplicated and order-preserving.
    # Each read is guarded, because an empty `= ANY('{}')` is a wasted round trip.
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
    # No live filter on this read on purpose: it LABELS whatever the page already returned.
    # Under include_archived the page legitimately carries archived deals, and they must
    # come back named rather than as "deal #12".
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
