"""
CakeCRM — CRM REST API for the frontend.

All endpoints require JWT auth (Depends(get_current_user)). The CRM is
first-class core — there is no enable gate and no lazy DB init (the schema is
owned by backend/migrations). Ported from chatty's crm_lite router.

Contacts:
  GET    /api/crm/contacts              — paginated list / search (?q=, ?sort=lead_score|name|…)
  GET    /api/crm/contacts/:id          — full detail
  POST   /api/crm/contacts              — create
  PUT    /api/crm/contacts/:id          — update
  DELETE /api/crm/contacts/:id          — delete

Companies:
  GET    /api/crm/companies             — paginated list / search (?q=)
  GET    /api/crm/companies/:id         — full detail (rolled-up contacts/deals/activity)
  POST   /api/crm/companies             — create (400s on a case/whitespace duplicate)
  POST   /api/crm/companies/resolve     — get-or-create by name (the #35 resolver over REST)
  PUT    /api/crm/companies/:id         — update
  DELETE /api/crm/companies/:id         — delete (contacts/deals unlink, not deleted)

Deals:
  GET    /api/crm/deals                 — pipeline list / filtered (?include_archived= on the
                                          board; ?sort=id&limit=&after_id= for its keyset page)
  GET    /api/crm/deals/:id             — detail
  POST   /api/crm/deals                 — create
  PUT    /api/crm/deals/:id             — update
  POST   /api/crm/deals/:id/restore     — un-archive a soft-archived deal
  POST   /api/crm/deals/:id/mark-lost   — close as lost, recording a written reason
  POST   /api/crm/deals/bulk-move       — move many deals to one stage (one transaction)
  POST   /api/crm/deals/touch-count/backfill        — recompute AI touch counts (?scope=null|all&force=)
  GET    /api/crm/deals/touch-count/backfill/status — backfill progress
  GET    /api/crm/deals/:id/touch-count/evidence    — per-event verdicts behind the count

Tasks:
  GET    /api/crm/tasks                 — filtered list
  POST   /api/crm/tasks                 — create
  PUT    /api/crm/tasks/:id             — update
  PUT    /api/crm/tasks/:id/complete    — mark done
  DELETE /api/crm/tasks/:id             — delete

Activity:
  GET    /api/crm/activity              — log
  POST   /api/crm/activity              — log new
  PUT    /api/crm/activity/:id          — edit
  DELETE /api/crm/activity/:id          — delete

Chatter (notes threads on a deal or contact):
  GET    /api/crm/chatter/:type/:id     — notes for an entity (?include_archived)
  POST   /api/crm/chatter/:type/:id/note        — append a note
  PATCH  /api/crm/chatter/note/:id      — edit a note
  POST   /api/crm/chatter/note/:id/archive      — soft-archive a note
  POST   /api/crm/chatter/note/:id/unarchive    — restore an archived note
  POST   /api/crm/chatter/note/:id/attachments  — attach one file to a note (multipart)
  GET    /api/crm/chatter/attachments/:id/thumb — server-generated thumbnail (auth)
  GET    /api/crm/chatter/attachments/:id/file  — original bytes (auth)
  DELETE /api/crm/chatter/attachments/:id       — remove an attachment

Custom fields (user-defined fields on contacts/companies/deals):
  GET    /api/crm/fields                — list definitions (?entity_type=)
  POST   /api/crm/fields                — create a definition
  PUT    /api/crm/fields/:id            — update a definition
  DELETE /api/crm/fields/:id            — delete a definition (values cascade)
  GET    /api/crm/:type/:id/fields      — an entity's field values (defs + values)
  PUT    /api/crm/:type/:id/fields      — set an entity's field values

Provenance (AI-written field badges on a deal or contact):
  GET    /api/crm/provenance/:type/:id           — live badge rows (unconfirmed + not stale)
  POST   /api/crm/provenance/:type/:id/confirm   — confirm a field's AI value (clears badge)

Lead scores (issue #18):
  POST   /api/crm/scores/backfill       — recompute stored lead scores (?scope=null|all)

Other:
  GET    /api/crm/dashboard             — summary stats
  GET    /api/crm/dashboard/today       — ranked "what needs me today" list (?owner_id)
  GET    /api/crm/dashboard/weekly-touches — open deals touched in a window (?start, ?end)
  GET    /api/crm/dashboard/weekly-touches/detail — one rep's touched deals, in full
         (?owner=<id|unassigned>, ?start, ?end)
  GET    /api/crm/analytics             — win/loss, activity volume, deal aging (?days, ?stale_days)
  GET    /api/crm/demo-status           — first-run onboarding / sample-data state
  POST   /api/crm/load-sample-data      — seed fictional demo data (first run)
  POST   /api/crm/dismiss-onboarding    — dismiss the first-run prompt
  POST   /api/crm/dismiss-ai-prompt     — dismiss the 'add an AI key' nudge
  POST   /api/crm/demo-clear            — clear example data (guarded)
  POST   /api/crm/clear-all             — wipe ALL CRM data (confirmation phrase)
  POST   /api/crm/import                — CSV import (contacts, keyless)
  POST   /api/crm/smart-import/parse    — AI-powered parse (any format)
  POST   /api/crm/smart-import/confirm  — confirm & insert parsed contacts
"""

import csv
import io
import logging
from urllib.parse import quote

import psycopg2
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StrictInt, field_validator

from core.auth import get_current_user, require_admin
from crm import (
    attachment_service,
    chatter_service,
    field_service,
    gtd_common,
    provenance_service,
    report_service,
    scoring_service,
    service as crm,
    today_service,
    todo_tokens,
    touch_count_service,
)
from crm.smart_import import csv_cell

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 1_048_576  # 1 MB cap on uploaded files (CSV + smart-import)
MAX_IMPORT_ROWS = 5000  # cap CSV rows processed per request (matches smart-import's contact cap)


def _resolve_companies_or_fallback(names: list[str], context: str) -> dict[str, int]:
    """Batch-resolve company names for a bulk import, degrading safely (issue #35).

    Both import loops pre-resolve companies in one batch (2 queries total) so a
    5000-row file doesn't issue a lookup per row. Shared here because the
    degrade path is the subtle part and must not drift between the two callers:
    a single malformed cell (a NUL byte, say) makes the whole batch statement
    raise, which would turn one bad row into a failed import. Returning an empty
    map instead lets each row resolve inside create_contact, where the loops'
    existing per-row try/except still turns a bad value into one row error —
    preserving the pre-#35 fault isolation exactly.
    """
    try:
        return crm.resolve_or_create_company_ids(names)
    except Exception as e:
        logger.warning("%s: batch company resolution failed, falling back per row: %s", context, e)
        return {}


# ── Request models ────────────────────────────────────────────────────────────

class ContactCreate(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    company: str = ""
    title: str = ""
    source: str = ""
    status: str = "active"
    tags: str = ""
    notes: str = ""
    company_id: int | None = None
    owner_id: int | None = None


class ContactUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    title: str | None = None
    source: str | None = None
    status: str | None = None
    tags: str | None = None
    notes: str | None = None
    company_id: int | None = None
    owner_id: int | None = None


class DealCreate(BaseModel):
    title: str
    contact_id: int | None = None
    stage: str = "lead"
    value: float = 0
    notes: str = ""
    expected_close_date: str = ""
    probability: int = 0
    currency: str = "USD"
    company_id: int | None = None
    owner_id: int | None = None
    # Issue #125. Left as a plain `str | None` rather than a Literal so a bad value is
    # answered by service.normalize_deal_temperature's sentence naming the valid tiers,
    # not by Pydantic's generic enum error — and so the REST boundary and the agent tool
    # (whose args nothing validates) fail the same way, through one normalizer.
    deal_temperature: str | None = None


class DealUpdate(BaseModel):
    title: str | None = None
    contact_id: int | None = None
    stage: str | None = None
    value: float | None = None
    notes: str | None = None
    expected_close_date: str | None = None
    probability: int | None = None
    currency: str | None = None
    company_id: int | None = None
    owner_id: int | None = None
    deal_temperature: str | None = None  # issue #125; explicit null clears it (see below)


class DealMarkLost(BaseModel):
    # A Pydantic cap HERE, unlike BulkDealMove below, and the difference is what the
    # service does when the limit is exceeded: bulk_move_deals REFUSES with a sentence
    # worth surfacing, while mark_deal_lost silently TRUNCATES at MAX_LOST_REASON. A
    # rep's typed prose losing its tail with no feedback is data loss, so the REST
    # boundary rejects instead. The service cap stays for the agent-tool path.
    lost_reason: str = Field("", max_length=crm.MAX_LOST_REASON)


class BulkDealMove(BaseModel):
    # StrictInt, not int: Pydantic's lax mode coerces JSON `true` to 1, `1.0` to 1 and
    # "3" to 3, so a malformed body would silently move deal #1. Only the model can catch
    # that — by the time the service runs, the bool has already become a real int.
    # Positivity is checked in the service instead, so every caller gets it.
    deal_ids: list[StrictInt]
    stage: str
    # Deliberately no Pydantic max_length on deal_ids: the service's BULK_MOVE_MAX is
    # the single definition of the cap, shared with the agent-tool path, and its
    # refusal is a renderable sentence where a Pydantic 422 detail array is not.


class CompanyCreate(BaseModel):
    name: str
    domain: str = ""
    industry: str = ""
    phone: str = ""
    address: str = ""
    notes: str = ""
    source: str = ""
    status: str = "active"
    owner_id: int | None = None


class CompanyResolve(BaseModel):
    name: str


class CompanyUpdate(BaseModel):
    name: str | None = None
    domain: str | None = None
    industry: str | None = None
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    source: str | None = None
    status: str | None = None
    owner_id: int | None = None


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    due_date: str = ""
    contact_id: int | None = None
    deal_id: int | None = None
    priority: str = "medium"
    owner_id: int | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = None
    contact_id: int | None = None
    deal_id: int | None = None
    priority: str | None = None
    completed: int | None = None
    owner_id: int | None = None


class ActivityCreate(BaseModel):
    activity: str
    note: str = ""
    contact_id: int | None = None
    deal_id: int | None = None


class ActivityUpdate(BaseModel):
    activity: str | None = None
    note: str | None = None


class ChatterNoteBody(BaseModel):
    message: str


class ChatterNoteUpdate(BaseModel):
    message: str


class ClearAllBody(BaseModel):
    confirmation: str


class ProvenanceConfirmBody(BaseModel):
    field_name: str


class SmartImportConfirm(BaseModel):
    contacts: list[dict]

    @field_validator("contacts")
    @classmethod
    def validate_contacts(cls, v):
        if not v:
            raise ValueError("No contacts to import")
        if len(v) > 5000:
            raise ValueError("Too many contacts (max 5000)")
        return v


class FieldDefinitionCreate(BaseModel):
    entity_type: str
    name: str
    field_key: str = ""              # blank → the service derives a slug from name
    field_type: str
    dropdown_options: list[str] | None = None
    is_required: bool = False


class FieldDefinitionUpdate(BaseModel):
    name: str | None = None
    dropdown_options: list[str] | None = None
    is_required: bool | None = None
    # Bounded so an out-of-range value 400s at the Pydantic layer rather than
    # overflowing the INTEGER column into an unhandled 500.
    display_order: int | None = Field(default=None, ge=0, le=1_000_000)


class FieldValuesUpdate(BaseModel):
    values: dict[str, str]


# ── Contacts ──────────────────────────────────────────────────────────────────

@router.get("/contacts")
async def list_contacts(
    q: str = "", status: str = "", tags: str = "", sort: str = "",
    limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0),
    owner_id: int | None = None, after_id: int | None = Query(None, ge=0, le=2_147_483_647),
    user=Depends(get_current_user),
):
    # owner_id absent = everyone, so an install that never assigns owners behaves
    # exactly as before. "Mine" is just this param set to the caller's own id — no
    # separate flag, no magic value.
    # #18: sort is allowlisted in the service layer (unknown -> updated_at); applied to
    # BOTH the search (?q=) and browse branches so the UI's active sort is never ignored.
    # #77: after_id is the list page's keyset cursor and is only valid with sort=id (the
    # service refuses any other pairing rather than paginating wrong).
    sort = sort or "updated_at"
    if q:
        # search_contacts has no cursor, so honouring `after_id` here is impossible —
        # and silently dropping it looks exactly like a client stuck re-reading page one,
        # which is the failure _check_assembly_cursor exists to prevent. Refuse instead.
        if after_id is not None:
            raise HTTPException(status_code=400, detail="after_id cannot be combined with q")
        contacts = crm.search_contacts(
            q, status=status or None, tags=tags or None, limit=limit, offset=offset, sort=sort,
            owner_id=owner_id,
        )
        total = crm.count_search_contacts(
            q, status=status or None, tags=tags or None, owner_id=owner_id
        )
        return {"contacts": contacts, "total": total}
    try:
        return crm.list_contacts(
            offset=offset, limit=limit, status=status or None, tags=tags or None,
            sort=sort, owner_id=owner_id, after_id=after_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/tags")
async def list_tags(user=Depends(get_current_user)):
    """Return all distinct tag labels currently used by contacts."""
    return {"tags": crm.list_distinct_tags()}


@router.get("/contacts/{contact_id}")
async def get_contact(contact_id: int, user=Depends(get_current_user)):
    result = crm.get_contact_detail(contact_id)
    if not result:
        raise HTTPException(status_code=404, detail="Contact not found")
    return result


def _create_payload(body, user: dict) -> dict:
    """model_dump() for a create, defaulting the owner to the caller (issue #60).

    Pydantic's model_dump() collapses "field absent" and "field explicitly null" into
    the same None, so model_fields_set is the only way to tell them apart — and here
    they mean opposite things:

      owner_id absent   -> you created it, so it is yours (the common case; every
                           pre-#60 client and every existing test hits this path)
      owner_id: null    -> deliberately unassigned
      owner_id: <id>    -> assigned to that person

    Assignment to a DEACTIVATED user is allowed on purpose: reassigning a departed
    rep's records to their own name is how history stays honest. The UI simply does
    not offer inactive users in the picker.
    """
    data = body.model_dump()
    if "owner_id" not in body.model_fields_set:
        data["owner_id"] = user["id"]
    return data


@router.post("/contacts")
async def create_contact(body: ContactCreate, user=Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    try:
        return crm.create_contact(**_create_payload(body, user))
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced company does not exist") from None


@router.put("/contacts/{contact_id}")
async def update_contact(contact_id: int, body: ContactUpdate, user=Depends(get_current_user)):
    # exclude_unset so only fields the client actually sent are updated; allow an
    # explicit null ONLY for the nullable FK (company_id) so a contact can be
    # unlinked from its company. Other columns are NOT NULL — dropping their nulls.
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k in ("company_id", "owner_id")
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = crm.update_contact(contact_id, **updates)
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced company does not exist") from None
    if not result:
        raise HTTPException(status_code=404, detail="Contact not found")
    return result


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: int, user=Depends(get_current_user)):
    if not crm.delete_contact(contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"deleted": True, "contact_id": contact_id}


# ── Deals ─────────────────────────────────────────────────────────────────────

@router.get("/deals")
async def list_deals(
    stage: str = "", contact_id: int | None = None,
    include_archived: bool = False,
    sort: str = "",
    limit: int | None = Query(None, ge=1, le=1000),
    after_id: int | None = Query(None, ge=0, le=2_147_483_647),
    user=Depends(get_current_user),
):
    """Pipeline board payload, or a filtered deal list when stage/contact_id is given.

    `include_archived` (issue #83) applies to the BOARD payload only — it is the one
    opt-in hole in the archived-deal sweep that lets the UI find and restore an
    accidentally archived deal. It is refused rather than ignored alongside
    stage/contact_id: that branch is a different service function which keeps the sweep,
    and silently dropping an advertised flag is worse than saying no.

    `limit`/`after_id` (issue #59) are the board's OPT-IN keyset page. Omitting them
    returns the whole board exactly as before. The frontend sweeps every page and
    reassembles the complete corpus before rendering, so paging is transport only and
    the client-side facet model is unchanged.

    On a CONTINUATION page (`after_id` set) `stage_summary` and `total_pipeline_value`
    come back as `null`: the sweep pays for that whole-table aggregate once, on its first
    page, instead of on every one of up to 200 pages.
    """
    # `contact_id is not None`, not a truthiness test: `?contact_id=0` is falsy, so a
    # truthiness test would drop it through to the board branch — returning the whole
    # pipeline for a request that asked to filter, and slipping past the refusal below.
    if stage or contact_id is not None:
        if include_archived:
            raise HTTPException(
                status_code=400,
                detail="include_archived is not supported with stage or contact_id",
            )
        # Same reasoning as include_archived: `list_deals` has no cursor, and silently
        # dropping an advertised paginator looks exactly like a client stuck re-reading
        # page one — the failure the #77 cursor rules exist to prevent. Refuse instead.
        if limit is not None or after_id is not None:
            raise HTTPException(
                status_code=400,
                detail="limit and after_id are not supported with stage or contact_id",
            )
        deals = crm.list_deals(stage=stage or None, contact_id=contact_id)
        return {"deals": deals, "count": len(deals)}
    # The shared assembly wire format (assemblyPage.ts) always sends `sort=id`, and that
    # parameter is what makes the cursor meaningful. Accepting `sort=updated_at` here
    # while still returning id order would be a silently-ignored pagination input, so a
    # paginated request must say `id` or say nothing. Unpaginated callers are unaffected.
    if (limit is not None or after_id is not None) and sort not in ("", "id"):
        raise HTTPException(
            status_code=400,
            detail="pipeline pages are ordered by id; pass sort=id or omit it",
        )
    try:
        return crm.get_pipeline(
            include_archived=include_archived, limit=limit, after_id=after_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: int, user=Depends(get_current_user)):
    result = crm.get_deal_detail(deal_id)
    if not result:
        raise HTTPException(status_code=404, detail="Deal not found")
    return result


@router.post("/deals")
async def create_deal(body: DealCreate, user=Depends(get_current_user)):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    try:
        return crm.create_deal(**_create_payload(body, user))
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or company does not exist") from None
    except ValueError as e:
        # e.g. an invalid deal_temperature (issue #125) — a refusal the caller can act on,
        # matching PUT /deals/{deal_id} rather than surfacing as a 500.
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.put("/deals/{deal_id}")
async def update_deal(deal_id: int, body: DealUpdate, user=Depends(get_current_user)):
    # exclude_unset so only fields the client actually sent are updated; allow an
    # explicit null ONLY for the nullable FKs (contact_id, company_id) so a deal
    # can be unlinked. Other columns are NOT NULL — dropping their nulls.
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k in ("contact_id", "company_id", "owner_id", "deal_temperature")
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = crm.update_deal(deal_id, **updates)
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or company does not exist") from None
    except ValueError as e:
        # e.g. a stage change on an archived deal — a refusal the caller can act on,
        # not a server fault.
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not result:
        raise HTTPException(status_code=404, detail="Deal not found or invalid stage")
    return result


# Placed right after PUT /deals/{deal_id}, the route it batches. No path collision:
# create is POST /deals and the touch-count routes have three segments, so there is no
# other POST /deals/{something} for "bulk-move" to shadow.
@router.post("/deals/bulk-move")
async def bulk_move_deals(body: BulkDealMove, user=Depends(get_current_user)):
    """Move many deals to one stage in a single transaction (issue #55).

    Always HTTP 200 on a reached handler: whole-request refusals come back as
    ``{"ok": false, "errors": [...]}`` and per-deal problems ride ``errors`` alongside
    ``ok: true``. That split is what lets the board tell "the server refused, nothing
    was written" apart from "the request never completed, the outcome is unknown" —
    only transport and 5xx failures throw at the client.

    Off the event loop because the post-commit rescore recomputes one lead score per
    updated deal and linked contact, bounded by BULK_MOVE_MAX.
    """
    return await run_in_threadpool(crm.bulk_move_deals, body.deal_ids, body.stage)


# Restore is the recoverability half of issue #83: archiving a deal was reachable only
# through the assistant, so on a keyless install an accidental archive (or `merge_deals`'
# source-archival) was permanent. ARCHIVE deliberately gets no route here — the gate scope
# is view + restore only; a UI archive affordance lands with the deal-detail parity port.
#
# Sync `def` on purpose, which makes this the one non-async handler in the file: the work
# is blocking psycopg2 plus a lead-score recompute, and FastAPI runs a sync endpoint in a
# threadpool instead of on the event loop. Its `async def` neighbours do the same blocking
# work directly on the loop — that is pre-existing and out of scope here, not a convention
# worth propagating (bulk-move already opts out via run_in_threadpool for the same reason).
# No path collision — /deals/touch-count/backfill shares the segment count but differs in
# its terminal segment.
#
# Restoring is idempotent (restoring a live deal is a no-op NULL write) and
# member-accessible: ownership is not access control here, and this is ordinary record
# CRUD, the same tier as PUT /deals/{id}. Note that restoring a deal that was archived by
# a MERGE is not an undo — the merge already repointed activity/tasks, copied notes and
# gap-filled custom fields onto the target; restore only makes the source visible again.
@router.post("/deals/{deal_id}/restore")
def restore_deal(deal_id: int, user=Depends(get_current_user)):
    result = crm.archive_deal(deal_id, archived=False)
    if not result:
        raise HTTPException(status_code=404, detail="Deal not found")
    return result


# Closing a deal WITH a reason (issue #128). Before this, `lost_reason` had no human
# writer at all: the field renders on the deal sheet but `_DEAL_USER_WRITABLE` excludes
# it (mark_deal_lost is its single writer), so on a keyless install a rep could read a
# lost reason and never type one.
#
# This delegates to that same lifecycle verb rather than widening _DEAL_USER_WRITABLE,
# which is what preserves the invariant it was excluded for — a reason can still only
# arrive WITH the close, never be pasted onto a deal that isn't lost. It also zeroes
# probability and appends the timeline note, which `PUT /deals/{id}` with {stage: lost}
# does not, so the explicit Mark Lost action takes this route even when the reason is
# blank; drag and bulk-move keep using PUT.
#
# Sync `def` like restore_deal above: blocking psycopg2 plus a chatter write and a
# lead-score recompute, which FastAPI runs in a threadpool for a sync endpoint.
#
# No path collision — /deals/touch-count/backfill shares the segment count but differs
# in its terminal segment, the same reasoning restore_deal already documents.
@router.post("/deals/{deal_id}/mark-lost")
def mark_deal_lost(deal_id: int, body: DealMarkLost, user=Depends(get_current_user)):
    try:
        # author_id credits the note to whoever typed the reason (#60: authorship is not
        # ownership). The assistant tool leaves it NULL; a human route must not.
        result = crm.mark_deal_lost(
            deal_id, lost_reason=body.lost_reason, author_id=user["id"]
        )
    except ValueError as e:
        # _write_deal_update refuses a stage change on an archived deal — a refusal the
        # caller can act on, not a server fault. Same mapping as PUT /deals/{id}.
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not result:
        raise HTTPException(status_code=404, detail="Deal not found")
    return result


# ── AI touch counts (issue #16) ───────────────────────────────────────────────
# Recompute happens event-driven off note/activity writes; these endpoints are the
# operator repair/observability surface. The path prefix (/deals/touch-count/…) has a
# different segment count than /deals/{deal_id}, so there is no route collision.

@router.post("/deals/touch-count/backfill")
async def touch_count_backfill(
    scope: str = Query("null", pattern="^(null|all)$"),
    force: bool = False,
    user=Depends(require_admin),
):
    """Backfill AI touch counts. scope=null (default) computes never-computed open deals;
    scope=all re-computes every open deal (repair). force bypasses the process-local
    cooldown. Degrades to {"started": false} with no AI provider. Enqueue-and-return —
    the in-process worker drains asynchronously (candidate SELECT is offloaded)."""
    try:
        return await run_in_threadpool(touch_count_service.start_backfill, scope, force)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/deals/touch-count/backfill/status")
async def touch_count_backfill_status(user=Depends(get_current_user)):
    """Backfill progress: remaining never-computed open deals + this process's queue depth,
    plus per-line verdict health (ok/fallback/failed) since process start (issue #56)."""
    return touch_count_service.backfill_status()


@router.get("/deals/{deal_id}/touch-count/evidence")
async def touch_count_evidence(deal_id: int, user=Depends(get_current_user)):
    """Every evidence event behind a deal's AI touch count, with its verdict (issue #56).

    Reads stored facts only — never re-runs AI. `verdict_state` says how current the
    explanation is (current/stale/superseded/none). Deterministic stage-move rows come from
    deal_stage_events and need no provider; field edits have no event log in CakeCRM at all
    and so are absent. No pagination: bounded by construction (MAX_CHATTER_EVIDENCE +
    MAX_ACTIVITY_EVIDENCE + MAX_STAGE_EVENT_ROWS + 1). Counts written before #56 report
    verdict_state "none" — POST /deals/touch-count/backfill?scope=all is the repair that
    fills them in."""
    result = await run_in_threadpool(touch_count_service.get_touch_evidence, deal_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    return result


# ── Lead scores (issue #18) ───────────────────────────────────────────────────
# Scores are recomputed inline on write events + a daily heartbeat refresh; this is the
# operator repair/backfill surface. Two literal segments — no collision with /deals/{id}
# or the polymorphic /{entity_type}/{entity_id}/fields route.

@router.post("/scores/backfill")
async def scores_backfill(
    scope: str = Query("null", pattern="^(null|all)$"),
    user=Depends(require_admin),
):
    """Recompute stored lead scores. scope=null (default) scores only never-scored rows;
    scope=all rescores every deal + contact (drift repair). Pure-algorithmic and
    synchronous — returns the final counts (no queue, so no status endpoint)."""
    try:
        return await run_in_threadpool(scoring_service.backfill_scores, scope)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    contact_id: int | None = None, deal_id: int | None = None,
    completed: bool | None = None, due_before: str = "",
    priority: str = "", limit: int = Query(50, ge=1, le=1000),
    owner_id: int | None = None, after_id: int | None = Query(None, ge=0, le=2_147_483_647),
    sort: str = "",
    user=Depends(get_current_user),
):
    # #77: `sort=id` + `after_id` is the list page's keyset sweep. Every existing caller
    # omits both and keeps the historical due order (now with an id tie-breaker, so a
    # LIMIT window is deterministic among tasks sharing a due date).
    try:
        tasks = crm.list_tasks(
            contact_id=contact_id, deal_id=deal_id,
            completed=completed, due_before=due_before or None,
            priority=priority or None, limit=limit, owner_id=owner_id,
            after_id=after_id, sort=sort or "due",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/tasks")
async def create_task(body: TaskCreate, user=Depends(get_current_user)):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    try:
        return crm.create_task(**_create_payload(body, user))
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or deal does not exist") from None
    except gtd_common.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.put("/tasks/{task_id}")
async def update_task(task_id: int, body: TaskUpdate, user=Depends(get_current_user)):
    # exclude_unset + allow explicit null only for the nullable FKs so a task can
    # be unlinked from its contact/deal. Other columns are NOT NULL.
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k in ("contact_id", "deal_id", "owner_id")
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = crm.update_task(task_id, **updates)
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or deal does not exist") from None
    except gtd_common.ValidationError as e:
        # Since #70 the task write path validates its inputs (a malformed due_date
        # used to be stored verbatim). Bad input is the caller's, so it must surface
        # as 400 — an uncaught ValidationError here would be a 500.
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@router.put("/tasks/{task_id}/complete")
async def complete_task(task_id: int, user=Depends(get_current_user)):
    result = crm.complete_task(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, user=Depends(get_current_user)):
    if not crm.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"deleted": True, "task_id": task_id}


# ── Activity ──────────────────────────────────────────────────────────────────

@router.get("/activity")
async def get_activity(
    contact_id: int | None = None, deal_id: int | None = None,
    limit: int = Query(20, ge=1, le=1000), user=Depends(get_current_user),
):
    activities = crm.get_activity_log(contact_id=contact_id, deal_id=deal_id, limit=limit)
    return {"activities": activities, "count": len(activities)}


@router.post("/activity")
async def log_activity(body: ActivityCreate, user=Depends(get_current_user)):
    if not body.activity.strip():
        raise HTTPException(status_code=400, detail="Activity type is required")
    try:
        # actor_id is the human on this request. Only the REST paths stamp it: the
        # assistant's tool executors do not thread identity in Phase A, so their
        # writes stay NULL and roll up as "Unattributed" rather than being credited
        # to whoever owns the record (issue #60).
        return crm.log_activity(**body.model_dump(), actor_id=user["id"])
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or deal does not exist") from None


@router.put("/activity/{activity_id}")
async def update_activity(activity_id: int, body: ActivityUpdate, user=Depends(get_current_user)):
    result = crm.update_activity(activity_id, activity=body.activity, note=body.note)
    if not result:
        raise HTTPException(status_code=404, detail="Activity not found")
    return result


@router.delete("/activity/{activity_id}")
async def delete_activity(activity_id: int, user=Depends(get_current_user)):
    if not crm.delete_activity(activity_id):
        raise HTTPException(status_code=404, detail="Activity not found")
    return {"ok": True}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(user=Depends(get_current_user)):
    return crm.get_dashboard_stats()


@router.get("/dashboard/today")
async def dashboard_today(
    owner_id: int | None = Query(None),
    user=Depends(get_current_user),
):
    """The Today panel (issue #130): one ranked list of what needs attention today.

    `owner_id` absent means everyone (the `list_tasks` idiom — no separate flag or
    magic value); present means that person's view, which deliberately INCLUDES
    unassigned tasks, because someone has to catch them. Reminders carry no owner
    column at all and appear in every scope.

    Pure SQL, so the ranking is identical with zero AI providers configured. Rank 2 of
    the ladder is reserved for the hot-deals follow-up (#125) and is never emitted yet.
    """
    return today_service.get_today(owner_id=owner_id)


@router.get("/dashboard/weekly-touches")
async def weekly_touches(
    start: str | None = Query(None),
    end: str | None = Query(None),
    user=Depends(get_current_user),
):
    """Open deals touched in the window, from #16's AI touch counts (issue #76).

    Omit both params for the rolling last-7-days window; pass BOTH start and end
    (YYYY-MM-DD, UTC calendar days, end inclusive) for a custom range.

    With no AI provider configured the counts are still real — membership is keyless —
    but `computed_deals` is 0, which is the client's signal to hide the card rather
    than render rows of blank estimates. Never an error either way.
    """
    try:
        return crm.get_weekly_touches(start=start, end=end)
    except ValueError as e:
        # Malformed / half-specified range — the caller's input, not a server fault.
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/dashboard/weekly-touches/detail")
async def weekly_touches_detail(
    owner: str = Query(..., description="A user id, or the literal 'unassigned'"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    user=Depends(get_current_user),
):
    """One rep's touched open deals — the whole list, not the card's ten (issue #146).

    `owner` is REQUIRED and carries a literal `unassigned` for the NULL bucket, unlike
    `/dashboard/today`'s absent-means-everyone `owner_id`: this drill-down is always
    exactly one bucket, and the unowned deals are one of them.

    `start`/`end` are the SAME UTC calendar days the card takes, so a custom range picked
    on the dashboard is asked here as the same question; omitting both is the rolling
    default, re-resolved at this request rather than inherited as frozen instants (see
    `get_weekly_touch_detail` for why freezing is not merely unnecessary but wrong).

    Not admin-gated: ownership is an assignment, not access control (#60), so every member
    sees every rep's row.
    """
    try:
        result = crm.get_weekly_touch_detail(
            owner_id=crm.parse_touch_owner(owner), start=start, end=end
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    return result


@router.get("/analytics")
async def analytics(
    days: int = Query(30, ge=7, le=365),
    stale_days: int = Query(14, ge=1, le=365),
    user=Depends(get_current_user),
):
    """Keyless SQL analytics for the enriched dashboard (issue #20): win/loss,
    activity volume, and read-time deal aging. Returns the full superset; the
    assistant's crm_analytics tool returns the summarize_analytics() trim."""
    return crm.get_analytics(days=days, stale_days=stale_days)


# ── First-run / sample data ───────────────────────────────────────────────────

@router.get("/demo-status")
async def demo_status(user=Depends(get_current_user)):
    """Drive the first-run 'load sample data?' prompt and the example-data banner."""
    return crm.get_demo_status()


@router.post("/load-sample-data")
async def load_sample_data(user=Depends(require_admin)):
    """Seed fictional demo data on first run (idempotent — no-op if CRM has data)."""
    return crm.load_sample_data()


@router.post("/dismiss-onboarding")
async def dismiss_onboarding(user=Depends(get_current_user)):
    """User chose to start fresh — stop showing the first-run prompt."""
    return crm.dismiss_onboarding()


@router.post("/dismiss-ai-prompt")
async def dismiss_ai_prompt(user=Depends(get_current_user)):
    """Dismiss the 'add an AI key to hire your assistant' nudge (durable)."""
    return crm.dismiss_ai_prompt()


# ── Task mode + no-login todo surfaces (#70) ──────────────────────────────────

class TaskModeBody(BaseModel):
    mode: str = Field(max_length=16)


@router.post("/task-mode")
async def set_task_mode(body: TaskModeBody, user=Depends(require_admin)):
    """Switch between normal tasks and Todo-GTD mode.

    Switching migrates nothing — GTD is a view over the same task rows — so this is
    instant and reversible in both directions.

    Admin-only since #102, on the same rule as `/api/assistant/identity`: `task_mode`
    lives on the `crm_meta` singleton, so one member flipping it changes the task
    experience for EVERYONE on the install. #102 made this reachable in practice by
    turning GTD on everywhere, which is what surfaced the gap.
    """
    try:
        return crm.set_task_mode(body.mode)
    except gtd_common.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


class TodoSurfacesBody(BaseModel):
    """All-optional: an omitted field is left untouched.

    `capture_token`/`web_token` accept a literal '' to turn the surface tokenless,
    which for capture means PUBLIC — hence `regenerate` as the safe way to get a
    fresh secret without ever transiting a caller-chosen one.
    """
    capture_token: str | None = Field(default=None, max_length=128)
    web_enabled: bool | None = None
    web_token: str | None = Field(default=None, max_length=128)
    regenerate_capture: bool = False
    regenerate_web: bool = False


@router.get("/todo-surfaces")
async def get_todo_surfaces(user=Depends(require_admin)):
    """Current state of the two no-login todo surfaces, including their live URLs.

    Returns the tokens themselves: they ARE the credential, and the settings page has
    to render a copyable link — which is exactly why this is **admin-only** since #102
    (it was merely authenticated before, and the `mode === 'gtd'` UI gate was the only
    thing keeping it off a normal-mode member's screen).
    """
    return _todo_surfaces_payload()


@router.post("/todo-surfaces")
async def update_todo_surfaces(body: TodoSurfacesBody, user=Depends(require_admin)):
    """Enable/disable the public todo app and set or rotate either token.

    Admin-only since #102. Enabling the web app mints an unauthenticated URL granting
    read+write over the whole todo store, and that token has NO lifecycle tie to the
    account that created it — deactivating that user does not revoke the link, the way
    `token_epoch`/`is_active` revoke their JWT. A credential that outlives its creator's
    account belongs behind the install-configuration gate.
    """
    capture_token = body.capture_token
    web_token = body.web_token
    if body.regenerate_capture:
        capture_token = todo_tokens.mint_token()
    elif capture_token is not None:
        capture_token = todo_tokens.clamp_token(capture_token)
    if body.regenerate_web:
        web_token = todo_tokens.mint_token()
    elif web_token is not None:
        web_token = todo_tokens.clamp_token(web_token)
    crm.set_todo_public_settings(
        capture_token=capture_token,
        web_enabled=body.web_enabled,
        web_token=web_token,
    )
    return _todo_surfaces_payload()


def _todo_surfaces_payload() -> dict:
    """One definition of what a todo surface link looks like — the settings UI must
    never assemble these paths itself, or the two would drift."""
    s = crm.get_todo_public_settings()
    capture_token = s["todo_capture_token"]
    web_token = s["todo_web_token"]
    return {
        **s,
        "capture_path": f"/capture/{capture_token}" if capture_token else "/capture",
        "capture_public": not capture_token,
        "web_path": (f"/todo/{web_token}" if web_token else "/todo") if s["todo_web_enabled"] else None,
        "web_public": s["todo_web_enabled"] and not web_token,
    }


@router.post("/demo-clear")
async def demo_clear(user=Depends(require_admin)):
    """Clear example data (guarded: no-op unless sample data was loaded)."""
    return crm.clear_demo_data()


@router.post("/clear-all")
async def clear_all(body: ClearAllBody, user=Depends(require_admin)):
    """Wipe ALL CRM data — deliberate real-data reset, gated by a confirmation phrase."""
    if body.confirmation != "clear crm":
        raise HTTPException(status_code=400, detail="Invalid confirmation phrase")
    return crm.clear_all()


# ── CSV Import (keyless — no AI provider needed) ──────────────────────────────

@router.post("/import")
async def import_csv(file: UploadFile = File(...), user=Depends(get_current_user)):
    """Import contacts from a CSV file.

    Expected columns (case-insensitive, flexible matching):
    name (required), email, phone, company, title, source, tags, notes
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = await file.read(MAX_UPLOAD_BYTES + 1)  # bounded — don't buffer oversized uploads
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")
    try:
        text = content.decode("utf-8-sig")  # Handle BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no headers")

    # Normalize headers to lowercase
    field_map = {f.strip().lower(): f for f in reader.fieldnames}

    # Map common column name variations
    column_aliases = {
        "name": ["name", "full_name", "full name", "contact_name", "contact name"],
        "email": ["email", "email_address", "email address", "e-mail"],
        "phone": ["phone", "phone_number", "phone number", "tel", "telephone"],
        "company": ["company", "company_name", "company name", "organization", "org"],
        "title": ["title", "job_title", "job title", "position", "role"],
        "source": ["source", "lead_source", "lead source", "origin"],
        "tags": ["tags", "labels", "categories"],
        "notes": ["notes", "note", "comments", "description"],
    }

    def _resolve(target: str) -> str | None:
        for alias in column_aliases.get(target, []):
            if alias in field_map:
                return field_map[alias]
        return None

    name_col = _resolve("name")
    if not name_col:
        raise HTTPException(status_code=400, detail="CSV must have a 'name' column")
    company_col = _resolve("company")

    # The row loop is synchronous psycopg2 (two round-trips per contact); run it
    # off the event loop so a large import can't freeze the single-process app
    # (and its health checks) on the Railway deploy target.
    def _import_rows() -> tuple[int, int, list[str]]:
        imported = skipped = 0
        errors: list[str] = []
        # Materialize the lazy reader up to the cap (already bounded by the 1MB
        # upload limit and MAX_IMPORT_ROWS) so companies can be resolved in one
        # batch below instead of once per row. The cap check still counts EVERY
        # row read — imported, skipped, or errored — exactly as before.
        rows: list[tuple[int, dict]] = []
        for i, row in enumerate(reader, start=2):  # Row 2+ (after header)
            if i - 2 >= MAX_IMPORT_ROWS:  # count every row read (imported/skipped/errored)
                errors.append(f"Import capped at {MAX_IMPORT_ROWS} rows — split the file and import the rest.")
                break
            rows.append((i, row))

        def _company_of(row: dict) -> str:
            return csv_cell(row, company_col)

        company_ids = _resolve_companies_or_fallback(
            [_company_of(row) for _, row in rows if (row.get(name_col) or "").strip()],
            "CSV import",
        )

        for i, row in rows:
            name = (row.get(name_col) or "").strip()
            if not name:
                skipped += 1
                continue
            company = _company_of(row)
            try:
                crm.create_contact(
                    name=name,
                    email=csv_cell(row, _resolve("email")),
                    phone=csv_cell(row, _resolve("phone")),
                    company=company,
                    company_id=company_ids.get(company),  # pre-resolved: no per-row lookup
                    title=csv_cell(row, _resolve("title")),
                    source=csv_cell(row, _resolve("source")),
                    tags=csv_cell(row, _resolve("tags")),
                    notes=csv_cell(row, _resolve("notes")),
                    owner_id=user["id"],
                )
                imported += 1
            except Exception as e:
                logger.debug("CSV import row %d failed: %s", i, e)
                errors.append(f"Row {i}: could not import — check the data and try again")
                if len(errors) > 50:
                    break
        return imported, skipped, errors

    imported, skipped, errors = await run_in_threadpool(_import_rows)
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── Smart Import (AI-powered; degrades gracefully with no provider) ───────────

@router.post("/smart-import/parse")
async def smart_import_parse(file: UploadFile = File(...), user=Depends(get_current_user)):
    """Parse contacts from any file format, using AI only when needed."""
    from crm.smart_import import parse_contacts

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read(MAX_UPLOAD_BYTES + 1)  # bounded — don't buffer oversized uploads
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    result = await parse_contacts(text, file.filename)
    return {
        "contacts": result.contacts,
        "ai_used": result.ai_used,
        "warnings": result.warnings,
    }


@router.post("/smart-import/confirm")
async def smart_import_confirm(body: SmartImportConfirm, user=Depends(get_current_user)):
    """Import previously parsed contacts into the CRM."""
    contacts = body.contacts

    def _confirm_rows() -> tuple[int, int, list[str]]:
        imported = skipped = 0
        errors: list[str] = []

        def _name_of(entry: dict) -> str:
            return str(entry.get("name") or entry.get("email") or entry.get("phone") or "").strip()

        def _company_of(entry: dict) -> str:
            return str(entry.get("company", "") or "").strip()

        company_ids = _resolve_companies_or_fallback(
            [_company_of(entry) for entry in contacts if _name_of(entry)],
            "Smart import",
        )

        for i, entry in enumerate(contacts):
            # Fall back to email/phone as the name so email-only entries the
            # parser kept (and showed in the preview) are actually importable,
            # not silently dropped — the preview→confirm contract.
            name = _name_of(entry)
            if not name:
                skipped += 1
                continue
            company = _company_of(entry)
            try:
                crm.create_contact(
                    name=name,
                    email=str(entry.get("email", "") or "").strip(),
                    phone=str(entry.get("phone", "") or "").strip(),
                    company=company,
                    company_id=company_ids.get(company),  # pre-resolved: no per-row lookup
                    title=str(entry.get("title", "") or "").strip(),
                    source=str(entry.get("source", "") or "").strip(),
                    tags=str(entry.get("tags", "") or "").strip(),
                    notes=str(entry.get("notes", "") or "").strip(),
                    owner_id=user["id"],
                )
                imported += 1
            except Exception as e:
                logger.debug("Smart import contact %d failed: %s", i + 1, e)
                errors.append(f"Contact {i + 1}: could not import — check the data and try again")
                if len(errors) > 50:
                    break
        return imported, skipped, errors

    # Off the event loop — see /import.
    imported, skipped, errors = await run_in_threadpool(_confirm_rows)
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── Chatter / notes ───────────────────────────────────────────────────────────
# Threaded free-text notes on a deal or contact, rendered alongside the activity
# timeline. Validation (entity type/existence, non-empty message) lives in
# chatter_service and surfaces here as ValueError → 400.

# ── Note attachments (issue #57) ──────────────────────────────────────────────
# Registered BEFORE /chatter/{entity_type}/{entity_id} deliberately. DELETE
# /chatter/attachments/{id} has the same three-segment shape as that GET, and while the
# methods differ today, a literal-prefix route one refactor away from being shadowed by a
# wildcard is not a thing to leave to luck. All four carry get_current_user — these bytes
# are private CRM content, and the frontend fetches them with the Bearer token rather than
# pointing a bare <img src> at an open URL (there is no cookie auth to make that work,
# and an unauthenticated media URL is exactly what the issue rules out).
#
# All four are sync `def`: they do blocking psycopg2 work, and the upload additionally runs
# Pillow. FastAPI runs a sync handler in its threadpool, so nothing here occupies the event
# loop — the same reason core.auth.get_current_user and the login handlers are sync. The
# neighbouring chatter routes are `async def` because they only hand off to a service.

# The routes below serve stored bytes back to a browser, so both headers are load-bearing:
# nosniff stops a mislabelled body being re-interpreted as markup, and an attachment
# disposition stops direct navigation rendering anything in the app's origin at all. The UI
# never navigates to these URLs — it fetches them and renders object URLs — so the
# disposition costs nothing.
_MEDIA_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # NOT `immutable`, and not a long max-age: `TRUNCATE ... RESTART IDENTITY` reuses
    # attachment ids, so a cached /attachments/1/file could otherwise be served for a
    # DIFFERENT attachment after a CRM reset — and a fresh immutable response is never
    # revalidated, so a hard-deleted attachment would stay viewable in that browser.
    # `no-cache` still stores the response; it just makes every reuse revalidate, which is
    # what turns the ETag below into a real bandwidth win without the staleness.
    "Cache-Control": "private, no-cache",
    # The response varies by who asked, so a shared cache must never cross-serve it.
    "Vary": "Authorization",
}


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: `*`, a comma-separated list, and weak validators.

    Raw string equality would miss every one of those forms and silently disable the 304
    path — the fast path is the whole point, so it has to actually fire.
    """
    if not if_none_match:
        return False
    for raw in if_none_match.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        if candidate == "*":
            return True
        # A weak validator compares equal to its strong twin here: the bytes behind an id
        # never change, so there is no semantic distinction left to preserve.
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == etag:
            return True
    return False


def _content_disposition(filename: str) -> str:
    """`attachment` disposition with both the ASCII fallback and the RFC 5987 form.

    quote() must be called with safe="" — its default leaves `/` unescaped, which is not
    the RFC 5987 encoding. The service has already normalized the name, so it carries no
    quotes, backslashes, control characters or path segments.
    """
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip()
    # A name that is entirely non-ASCII strips down to nothing, or to a bare extension
    # ("写真.png" -> ".png") — which a legacy client would save as a hidden dotfile. Give
    # the fallback a real basename; clients that understand filename* never see it.
    #
    # Gated on the name having actually LOST characters, not merely on the fallback
    # starting with a dot: a file genuinely named ".htaccess" survives normalization intact
    # and must keep its name, where an earlier version rewrote it to "attachment.htaccess".
    if ascii_name != filename and (not ascii_name or ascii_name.startswith(".")):
        ascii_name = f"attachment{ascii_name}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.post("/chatter/note/{note_id}/attachments")
def add_note_attachment(
    note_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Attach one file to an existing note (multipart, field `file`)."""
    # Bounded read: take cap+1 bytes and reject if it came back over, rather than trusting
    # Content-Length. The repo-wide idiom (assistant/router.py, the CSV import above).
    file.file.seek(0)
    data = file.file.read(attachment_service.MAX_ATTACHMENT_BYTES + 1)
    if len(data) > attachment_service.MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Attachments are limited to "
                   f"{attachment_service.MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.",
        )
    try:
        return attachment_service.create_attachment(
            note_id, data=data, filename=file.filename, uploaded_by=user["id"],
        )
    except attachment_service.AttachmentError as e:
        raise HTTPException(status_code=_ATTACHMENT_STATUS[e.code], detail=str(e)) from None


# Code → status in one table, so reworded copy can never move a status by accident.
_ATTACHMENT_STATUS = {
    "note_not_found": 404,
    "note_archived": 404,
    "limit_exceeded": 400,
    "file_too_large": 413,
    "file_empty": 400,
}


@router.get("/chatter/attachments/{attachment_id}/thumb")
def get_note_attachment_thumb(
    attachment_id: int,
    if_none_match: str | None = Header(None, alias="If-None-Match"),
    user=Depends(get_current_user),
):
    """The server-generated thumbnail — the ONLY image bytes a list view ever fetches."""
    # Metadata first so a revalidation never reads the blob. The thumb ETag is distinct
    # from the file's so the two resources can't cross-satisfy each other.
    meta = attachment_service.get_meta(attachment_id)
    if meta is None or not meta["has_thumb"]:
        raise HTTPException(status_code=404, detail="Attachment not found")
    etag = f'"{meta["sha256"]}-thumb"'
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={**_MEDIA_HEADERS, "ETag": etag})
    row = attachment_service.get_thumb(attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return Response(
        content=row["thumb_data"],
        media_type=row["thumb_mime"],
        headers={**_MEDIA_HEADERS, "ETag": etag,
                 "Content-Disposition": _content_disposition(meta["filename"])},
    )


@router.get("/chatter/attachments/{attachment_id}/file")
def get_note_attachment_file(
    attachment_id: int,
    if_none_match: str | None = Header(None, alias="If-None-Match"),
    user=Depends(get_current_user),
):
    """The original bytes. Fetched only on an explicit open/download, never in a list."""
    meta = attachment_service.get_meta(attachment_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    etag = f'"{meta["sha256"]}"'
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={**_MEDIA_HEADERS, "ETag": etag})
    row = attachment_service.get_file(attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return Response(
        content=row["data"],
        media_type=row["mime_type"],
        headers={**_MEDIA_HEADERS, "ETag": etag,
                 "Content-Disposition": _content_disposition(row["filename"])},
    )


@router.delete("/chatter/attachments/{attachment_id}")
def delete_note_attachment(attachment_id: int, user=Depends(get_current_user)):
    if not attachment_service.delete_attachment(attachment_id):
        raise HTTPException(status_code=404, detail="Attachment not found")
    return {"ok": True}


@router.get("/chatter/{entity_type}/{entity_id}")
async def get_chatter(
    entity_type: str,
    entity_id: int,
    # Tighter cap (200) than the contacts/deals lists (1000): a single entity's
    # notes thread is a bounded, human-authored feed, not a bulk dataset.
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_archived: bool = False,
    user=Depends(get_current_user),
):
    try:
        notes = chatter_service.get_chatter(
            entity_type, entity_id, limit=limit, offset=offset, include_archived=include_archived,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"notes": notes, "count": len(notes)}


@router.post("/chatter/{entity_type}/{entity_id}/note")
async def add_chatter_note(
    entity_type: str, entity_id: int, body: ChatterNoteBody, user=Depends(get_current_user),
):
    try:
        return chatter_service.add_note(
            entity_type, entity_id, body.message, author_id=user["id"]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.patch("/chatter/note/{note_id}")
async def update_chatter_note(note_id: int, body: ChatterNoteUpdate, user=Depends(get_current_user)):
    try:
        result = chatter_service.update_note(note_id, body.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not result:
        raise HTTPException(status_code=404, detail="Note not found")
    return result


@router.post("/chatter/note/{note_id}/archive")
async def archive_chatter_note(note_id: int, user=Depends(get_current_user)):
    if not chatter_service.archive_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"ok": True}


@router.post("/chatter/note/{note_id}/unarchive")
async def unarchive_chatter_note(note_id: int, user=Depends(get_current_user)):
    if not chatter_service.unarchive_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"ok": True}


# ── Field provenance (issue #16) ──────────────────────────────────────────────
# "AI" badges on the fields the assistant populated. GET returns only live-badge rows
# (unconfirmed AND not stale). A human confirm clears the badge; a 200 {stale:true} means
# the value was edited since the AI wrote it (the shared api() client throws a status-less
# Error, so the stale outcome is a payload branch the hook reads, not a 409).

@router.get("/provenance/{entity_type}/{entity_id}")
async def get_provenance(entity_type: str, entity_id: int, user=Depends(get_current_user)):
    try:
        rows = provenance_service.get_provenance(entity_type, entity_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"provenance": rows, "count": len(rows)}


@router.post("/provenance/{entity_type}/{entity_id}/confirm")
async def confirm_provenance(
    entity_type: str, entity_id: int, body: ProvenanceConfirmBody,
    user=Depends(get_current_user),
):
    try:
        result = provenance_service.confirm(entity_type, entity_id, body.field_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="No provenance for that field")
    if result.get("stale"):
        return {"confirmed": False, "stale": True}
    return {"confirmed": True, "provenance": result}


# ── Companies ─────────────────────────────────────────────────────────────────
# Appended as a self-contained block so a keep-both merge with the frontend-shell
# work stays trivial. Each endpoint declares its own get_current_user dependency
# (the blanket auth test iterates every route and asserts it).

@router.get("/companies")
async def list_companies(
    q: str = "", status: str = "", sort: str = "name",
    limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0),
    owner_id: int | None = None, after_id: int | None = Query(None, ge=0, le=2_147_483_647),
    user=Depends(get_current_user),
):
    if q:
        # See list_contacts: the search branch cannot honour a cursor, so it says so.
        if after_id is not None:
            raise HTTPException(status_code=400, detail="after_id cannot be combined with q")
        companies = crm.search_companies(
            q, status=status or None, limit=limit, offset=offset, owner_id=owner_id
        )
        total = crm.count_search_companies(q, status=status or None, owner_id=owner_id)
        return {"companies": companies, "total": total}
    try:
        return crm.list_companies(
            offset=offset, limit=limit, status=status or None, sort=sort,
            owner_id=owner_id, after_id=after_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/companies/{company_id}")
async def get_company(company_id: int, user=Depends(get_current_user)):
    result = crm.get_company_detail(company_id)
    if not result:
        raise HTTPException(status_code=404, detail="Company not found")
    return result


@router.get("/companies/{company_id}/report")
async def company_report(
    company_id: int,
    include_archived: bool = False,
    user=Depends(get_current_user),
):
    """The Reports page's one-company rollup (issue #144).

    Deliberately not ``get_company_detail``: that reader serves the Companies detail panel
    (20 activities, no notes, no truncation flags). This one is the report — every contact,
    every live deal, each child's newest activities with per-record truncation flags.

    ``include_archived`` widens BOTH archived axes at once (deals by ``archived_at``, notes
    by ``crm_chatter.archived``) so the page and its timeline never disagree about which
    records exist. Contacts are always included and rendered marked — ``contacts.status`` is
    not a sweep, and an archived contact is still this company's history.

    Keyless and behind ``get_current_user``: any member may read any record (#60 — ownership
    is an assignment, not access control).
    """
    result = report_service.get_company_rollup(company_id, include_archived=include_archived)
    if result is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return result


@router.get("/companies/{company_id}/timeline")
async def company_timeline(
    company_id: int,
    limit: int = Query(100, ge=1, le=report_service.TIMELINE_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    include_archived: bool = False,
    user=Depends(get_current_user),
):
    """The rollup's merged notes + activities feed, newest first (issue #144).

    LIMIT/OFFSET paged with a ``limit + 1`` probe driving ``has_more`` — never a second
    COUNT, which would go stale beside the page it describes.
    """
    result = report_service.get_company_timeline(
        company_id, limit=limit, offset=offset, include_archived=include_archived
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return result


@router.post("/companies")
async def create_company(body: CompanyCreate, user=Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    try:
        return crm.create_company(**_create_payload(body, user))
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=400, detail="A company with that name already exists") from None


@router.post("/companies/resolve")
async def resolve_company(body: CompanyResolve, user=Depends(get_current_user)):
    """Get-or-create a company by name — the #35 resolver exposed over REST (issue #123).

    The deal form's inline quick-create needs get-or-create keyed on the
    ``uq_companies_name_ci`` normalization, which ``POST /companies`` deliberately does
    NOT provide: that route INSERTs unconditionally and surfaces a case/whitespace
    duplicate as a 400. That is the right answer for the full "New Company" form, where
    you asked to create a company that already exists, and the wrong one for a picker
    whose entire job is to land you on the existing record.

    Delegates to ``resolve_or_create_company_ids`` verbatim rather than matching the name
    here. The normalization is an index expression, and the primitive's docstring warns
    that Python's case-folding can disagree with the database's ``LOWER()`` — a second
    spelling of that rule in this file (or a third in the frontend) is exactly how a
    company we just created gets stranded and a duplicate appears anyway. It is also
    race-safe by construction, which a SELECT-then-INSERT here would not be.

    The raw name is passed through untrimmed: the primitive's contract is
    ``{raw spelling exactly as passed: id}``, so looking the result up by the same string
    keeps trimming a single rule owned by SQL. The blank guard mirrors ``create_company``
    above rather than inventing its own.

    A company created here is left UNASSIGNED, which is the primitive's deliberate rule
    (pinned by ``test_auto_created_companies_are_left_unassigned``), not an oversight in
    this route. The quick-created CONTACT does get the caller as owner, because it goes
    through ``POST /contacts``, where ``_create_payload`` applies the usual default.
    """
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    company_id = crm.resolve_or_create_company_ids([body.name]).get(body.name)
    company = crm.get_company(company_id) if company_id is not None else None
    if not company:
        # The primitive yields no id only in its documented single-user race: the row was
        # deleted between its INSERT and its read-back. Nothing was linked, so refuse
        # rather than hand back a half-answer the form would store as a company_id.
        raise HTTPException(status_code=409, detail="Could not resolve that company — please try again")
    return company


@router.put("/companies/{company_id}")
async def update_company(company_id: int, body: CompanyUpdate, user=Depends(get_current_user)):
    # All company columns are NOT NULL, so drop nulls (clear a field by sending "").
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k == "owner_id"
    }
    if "name" in updates and not updates["name"].strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = crm.update_company(company_id, **updates)
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=400, detail="A company with that name already exists") from None
    if not result:
        raise HTTPException(status_code=404, detail="Company not found")
    return result


@router.delete("/companies/{company_id}")
async def delete_company(company_id: int, user=Depends(get_current_user)):
    if not crm.delete_company(company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    return {"deleted": True, "company_id": company_id}


# ── Custom fields ─────────────────────────────────────────────────────────────
# User-defined fields on contacts/companies/deals (two-table EAV in field_service).
# Appended as a self-contained block; each route declares its own get_current_user
# dependency (the blanket auth test iterates every route and asserts it). Validation
# (entity/field-type checks, value coercion, entity existence) lives in field_service
# and surfaces here as ValueError → 400. The /{entity_type}/{entity_id}/fields paths
# don't shadow existing routes: no other route ends in the literal "fields", and the
# existing 3-segment routes have literal first segments (/chatter/…, /tasks/…).

@router.get("/fields")
async def list_field_definitions(entity_type: str | None = None, user=Depends(get_current_user)):
    try:
        return field_service.list_field_definitions(entity_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.post("/fields")
async def create_field_definition(body: FieldDefinitionCreate, user=Depends(require_admin)):
    try:
        return field_service.create_field_definition(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(
            status_code=400,
            detail="A field with that name or key already exists for this entity type",
        ) from None


@router.put("/fields/{field_id}")
async def update_field_definition(
    field_id: int, body: FieldDefinitionUpdate, user=Depends(require_admin)
):
    # exclude_unset so an explicit "dropdown_options": null clears options while an
    # unsent key is left untouched (matches the service's "key present" semantics).
    try:
        result = field_service.update_field_definition(
            field_id, body.model_dump(exclude_unset=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(
            status_code=400, detail="A field with that name already exists for this entity type"
        ) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Field not found")
    return result


@router.delete("/fields/{field_id}")
async def delete_field_definition(field_id: int, user=Depends(require_admin)):
    if not field_service.delete_field_definition(field_id):
        raise HTTPException(status_code=404, detail="Field not found")
    return {"ok": True}


@router.get("/{entity_type}/{entity_id}/fields")
async def get_field_values(entity_type: str, entity_id: int, user=Depends(get_current_user)):
    # 404 a missing entity (consistent with PUT on this path and every other
    # per-entity GET) rather than returning definitions with all-null values.
    if entity_type not in field_service.VALID_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid entity_type: {entity_type}")
    if not field_service.entity_exists(entity_type, entity_id):
        raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
    return field_service.get_field_values(entity_type, entity_id)


@router.put("/{entity_type}/{entity_id}/fields")
async def set_field_values(
    entity_type: str, entity_id: int, body: FieldValuesUpdate, user=Depends(get_current_user)
):
    # Since #60 the dependency returns a live user row, so this resolves to a real
    # address. The sub fallback stays for the degenerate case of a row with no email.
    editor = user.get("email") or user.get("sub") or ""
    # Offloaded to a thread (like the bulk CSV import): this write holds an entity
    # FOR UPDATE lock while doing up to 200 upserts, so it must not block the loop.
    try:
        return await run_in_threadpool(
            field_service.set_field_values, entity_type, entity_id, body.values, editor
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except psycopg2.errors.ForeignKeyViolation:
        # A definition was deleted concurrently (the FOR SHARE lock narrows but the
        # 400 is the correct backstop, never a 500).
        raise HTTPException(status_code=400, detail="One or more fields no longer exist") from None
