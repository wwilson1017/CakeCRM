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
  POST   /api/crm/companies             — create
  PUT    /api/crm/companies/:id         — update
  DELETE /api/crm/companies/:id         — delete (contacts/deals unlink, not deleted)

Deals:
  GET    /api/crm/deals                 — pipeline list / filtered
  GET    /api/crm/deals/:id             — detail
  POST   /api/crm/deals                 — create
  PUT    /api/crm/deals/:id             — update
  POST   /api/crm/deals/touch-count/backfill        — recompute AI touch counts (?scope=null|all&force=)
  GET    /api/crm/deals/touch-count/backfill/status — backfill progress

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
  GET    /api/crm/dashboard/weekly-touches — open deals touched in a window (?start, ?end)
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

import psycopg2
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from core.auth import get_current_user
from crm import (
    chatter_service,
    field_service,
    provenance_service,
    scoring_service,
    service as crm,
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


class CompanyCreate(BaseModel):
    name: str
    domain: str = ""
    industry: str = ""
    phone: str = ""
    address: str = ""
    notes: str = ""
    source: str = ""
    status: str = "active"


class CompanyUpdate(BaseModel):
    name: str | None = None
    domain: str | None = None
    industry: str | None = None
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    source: str | None = None
    status: str | None = None


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    due_date: str = ""
    contact_id: int | None = None
    deal_id: int | None = None
    priority: str = "medium"


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = None
    contact_id: int | None = None
    deal_id: int | None = None
    priority: str | None = None
    completed: int | None = None


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
    user=Depends(get_current_user),
):
    # #18: sort is allowlisted in the service layer (unknown -> updated_at); applied to
    # BOTH the search (?q=) and browse branches so the UI's active sort is never ignored.
    sort = sort or "updated_at"
    if q:
        contacts = crm.search_contacts(
            q, status=status or None, tags=tags or None, limit=limit, offset=offset, sort=sort,
        )
        total = crm.count_search_contacts(q, status=status or None, tags=tags or None)
        return {"contacts": contacts, "total": total}
    return crm.list_contacts(
        offset=offset, limit=limit,
        status=status or None, tags=tags or None, sort=sort,
    )


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


@router.post("/contacts")
async def create_contact(body: ContactCreate, user=Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    try:
        return crm.create_contact(**body.model_dump())
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced company does not exist") from None


@router.put("/contacts/{contact_id}")
async def update_contact(contact_id: int, body: ContactUpdate, user=Depends(get_current_user)):
    # exclude_unset so only fields the client actually sent are updated; allow an
    # explicit null ONLY for the nullable FK (company_id) so a contact can be
    # unlinked from its company. Other columns are NOT NULL — dropping their nulls.
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k == "company_id"
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
    user=Depends(get_current_user),
):
    if stage or contact_id:
        deals = crm.list_deals(stage=stage or None, contact_id=contact_id)
        return {"deals": deals, "count": len(deals)}
    return crm.get_pipeline()


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
        return crm.create_deal(**body.model_dump())
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or company does not exist") from None


@router.put("/deals/{deal_id}")
async def update_deal(deal_id: int, body: DealUpdate, user=Depends(get_current_user)):
    # exclude_unset so only fields the client actually sent are updated; allow an
    # explicit null ONLY for the nullable FKs (contact_id, company_id) so a deal
    # can be unlinked. Other columns are NOT NULL — dropping their nulls.
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k in ("contact_id", "company_id")
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


# ── AI touch counts (issue #16) ───────────────────────────────────────────────
# Recompute happens event-driven off note/activity writes; these endpoints are the
# operator repair/observability surface. The path prefix (/deals/touch-count/…) has a
# different segment count than /deals/{deal_id}, so there is no route collision.

@router.post("/deals/touch-count/backfill")
async def touch_count_backfill(
    scope: str = Query("null", pattern="^(null|all)$"),
    force: bool = False,
    user=Depends(get_current_user),
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
    """Backfill progress: remaining never-computed open deals + this process's queue depth."""
    return touch_count_service.backfill_status()


# ── Lead scores (issue #18) ───────────────────────────────────────────────────
# Scores are recomputed inline on write events + a daily heartbeat refresh; this is the
# operator repair/backfill surface. Two literal segments — no collision with /deals/{id}
# or the polymorphic /{entity_type}/{entity_id}/fields route.

@router.post("/scores/backfill")
async def scores_backfill(
    scope: str = Query("null", pattern="^(null|all)$"),
    user=Depends(get_current_user),
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
    user=Depends(get_current_user),
):
    tasks = crm.list_tasks(
        contact_id=contact_id, deal_id=deal_id,
        completed=completed, due_before=due_before or None,
        priority=priority or None, limit=limit,
    )
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/tasks")
async def create_task(body: TaskCreate, user=Depends(get_current_user)):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    try:
        return crm.create_task(**body.model_dump())
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or deal does not exist") from None


@router.put("/tasks/{task_id}")
async def update_task(task_id: int, body: TaskUpdate, user=Depends(get_current_user)):
    # exclude_unset + allow explicit null only for the nullable FKs so a task can
    # be unlinked from its contact/deal. Other columns are NOT NULL.
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k in ("contact_id", "deal_id")
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = crm.update_task(task_id, **updates)
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Referenced contact or deal does not exist") from None
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
        return crm.log_activity(**body.model_dump())
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
async def load_sample_data(user=Depends(get_current_user)):
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


@router.post("/demo-clear")
async def demo_clear(user=Depends(get_current_user)):
    """Clear example data (guarded: no-op unless sample data was loaded)."""
    return crm.clear_demo_data()


@router.post("/clear-all")
async def clear_all(body: ClearAllBody, user=Depends(get_current_user)):
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
        return chatter_service.add_note(entity_type, entity_id, body.message)
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
    user=Depends(get_current_user),
):
    if q:
        companies = crm.search_companies(q, status=status or None, limit=limit, offset=offset)
        total = crm.count_search_companies(q, status=status or None)
        return {"companies": companies, "total": total}
    return crm.list_companies(offset=offset, limit=limit, status=status or None, sort=sort)


@router.get("/companies/{company_id}")
async def get_company(company_id: int, user=Depends(get_current_user)):
    result = crm.get_company_detail(company_id)
    if not result:
        raise HTTPException(status_code=404, detail="Company not found")
    return result


@router.post("/companies")
async def create_company(body: CompanyCreate, user=Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    try:
        return crm.create_company(**body.model_dump())
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=400, detail="A company with that name already exists") from None


@router.put("/companies/{company_id}")
async def update_company(company_id: int, body: CompanyUpdate, user=Depends(get_current_user)):
    # All company columns are NOT NULL, so drop nulls (clear a field by sending "").
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
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
async def create_field_definition(body: FieldDefinitionCreate, user=Depends(get_current_user)):
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
    field_id: int, body: FieldDefinitionUpdate, user=Depends(get_current_user)
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
async def delete_field_definition(field_id: int, user=Depends(get_current_user)):
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
    # No email claim in CakeCRM JWTs (payload is {"sub","role"}); fall back to sub.
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
