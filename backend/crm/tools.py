"""CakeCRM — CRM agent tools (24 tools).

Contacts, deals, tasks, activities, chatter/notes, and analytics — all accessible
to the AI assistant for managing customer relationships conversationally. The CRM
is first-class core, so these tools are collected UNCONDITIONALLY (no enable gate);
the assistant engine (a later issue) consumes them via get_crm_tools().
"""

from collections.abc import Callable

import psycopg2

from crm import chatter_service, service as crm

# ═══════════════════════════════════════════════════════════════════════════════
# Tool Definitions (schema only — sent to the AI provider)
# ═══════════════════════════════════════════════════════════════════════════════

CRM_TOOL_DEFS = [
    # ── Contacts (6 tools) ────────────────────────────────────────────────────
    {
        "name": "crm_find_contact",
        "description": (
            "Search CRM contacts by name, email, company, or notes. "
            "Use this when the user mentions a person or company and you need to look them up."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (name, email, company, or keyword)"},
                "status": {"type": "string", "description": "Filter by status: active, inactive, archived"},
                "tags": {"type": "string", "description": "Exact tag label, case-insensitive; comma-separate multiple tags"},
            },
            "required": ["query"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_create_contact",
        "description": (
            "Create a new contact in the CRM. Use when the user mentions a new customer, prospect, "
            "or person they want to track."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name"},
                "email": {"type": "string", "default": ""},
                "phone": {"type": "string", "default": ""},
                "company": {"type": "string", "default": ""},
                "title": {"type": "string", "description": "Job title", "default": ""},
                "source": {"type": "string", "description": "How they found you: referral, website, cold_call, social, event, other", "default": ""},
                "status": {"type": "string", "description": "active, inactive, or archived", "default": "active"},
                "tags": {"type": "string", "description": "Comma-separated tags", "default": ""},
                "notes": {"type": "string", "default": ""},
                "company_id": {"type": "integer", "description": "ID of a linked company (optional). Set to link this contact to a company."},
            },
            "required": ["name"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_update_contact",
        "description": (
            "Update an existing contact's information. Use when the user wants to change a "
            "contact's details like email, phone, company, status, or tags."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Contact ID to update"},
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "company": {"type": "string"},
                "title": {"type": "string"},
                "source": {"type": "string"},
                "status": {"type": "string", "description": "active, inactive, or archived"},
                "tags": {"type": "string"},
                "notes": {"type": "string"},
                "company_id": {"type": ["integer", "null"], "description": "ID of a linked company; pass null to unlink this contact from its company."},
            },
            "required": ["contact_id"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_get_contact",
        "description": (
            "Get a contact's full profile including their deals, tasks, and recent activity. "
            "Use this to see everything about a specific customer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Contact ID"},
            },
            "required": ["contact_id"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_list_contacts",
        "description": (
            "List contacts with optional filtering. Use to browse the customer list or "
            "see contacts by status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter: active, inactive, archived"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
            "required": [],
        },
        "kind": "integration",
    },
    {
        "name": "crm_delete_contact",
        "description": "Delete a contact and all their associated activities and tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Contact ID to delete"},
            },
            "required": ["contact_id"],
        },
        "kind": "integration",
    },

    # ── Deals (5 tools) ──────────────────────────────────────────────────────
    {
        "name": "crm_get_pipeline",
        "description": (
            "Get the deal pipeline with value summaries per stage. "
            "Use when the user asks about their pipeline, deals, or sales status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "description": "Filter by stage: lead, qualified, proposal, negotiation, won, lost",
                },
            },
            "required": [],
        },
        "kind": "integration",
    },
    {
        "name": "crm_create_deal",
        "description": (
            "Create a new deal/opportunity. Use when the user mentions a potential sale, "
            "project, or business opportunity with a customer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Deal title"},
                "contact_id": {"type": "integer", "description": "Associated contact ID"},
                "stage": {"type": "string", "description": "Pipeline stage (default: lead)", "default": "lead"},
                "value": {"type": "number", "description": "Deal value in dollars", "default": 0},
                "notes": {"type": "string", "default": ""},
                "expected_close_date": {"type": "string", "description": "Expected close date (YYYY-MM-DD)", "default": ""},
                "probability": {"type": "integer", "description": "Win probability 0-100%", "default": 0},
                "currency": {"type": "string", "default": "USD"},
                "company_id": {"type": "integer", "description": "ID of a linked company (optional)."},
            },
            "required": ["title"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_update_deal",
        "description": (
            "Update a deal's details — value, stage, close date, probability, notes, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "integer", "description": "Deal ID to update"},
                "title": {"type": "string"},
                "stage": {"type": "string", "description": "lead, qualified, proposal, negotiation, won, lost"},
                "value": {"type": "number"},
                "notes": {"type": "string"},
                "expected_close_date": {"type": "string", "description": "YYYY-MM-DD"},
                "probability": {"type": "integer", "description": "0-100"},
                "currency": {"type": "string"},
                "contact_id": {"type": "integer"},
                "company_id": {"type": ["integer", "null"], "description": "ID of a linked company; pass null to unlink this deal from its company."},
            },
            "required": ["deal_id"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_update_deal_stage",
        "description": "Move a deal to a new pipeline stage. Quick way to advance or close a deal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "integer"},
                "stage": {"type": "string", "description": "New stage: lead, qualified, proposal, negotiation, won, lost"},
            },
            "required": ["deal_id", "stage"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_get_deal",
        "description": "Get full details for a specific deal including contact info and activity history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "integer", "description": "Deal ID"},
            },
            "required": ["deal_id"],
        },
        "kind": "integration",
    },

    # ── Activities (2 tools) ──────────────────────────────────────────────────
    {
        "name": "crm_log_activity",
        "description": (
            "Log a touchpoint (call, email, meeting, follow_up) against a contact or deal — a dated "
            "record of an interaction. Use after the user mentions interacting with a customer. For "
            "free-form commentary you may later edit or archive (a notes thread), use crm_add_note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity": {"type": "string", "description": "Activity type: call, email, meeting, note, follow_up"},
                "note": {"type": "string", "description": "Details about the activity", "default": ""},
                "contact_id": {"type": "integer", "description": "Contact ID (optional)"},
                "deal_id": {"type": "integer", "description": "Deal ID (optional)"},
            },
            "required": ["activity"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_get_activity_log",
        "description": "Get the activity history for a contact or deal, or recent activity across the CRM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Filter by contact"},
                "deal_id": {"type": "integer", "description": "Filter by deal"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": [],
        },
        "kind": "integration",
    },

    # ── Tasks (3 tools) ──────────────────────────────────────────────────────
    {
        "name": "crm_create_task",
        "description": (
            "Create a follow-up task or reminder. Use when the user mentions needing to "
            "follow up, check in, or do something by a certain date for a customer or deal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "What needs to be done"},
                "description": {"type": "string", "default": ""},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)", "default": ""},
                "contact_id": {"type": "integer", "description": "Associated contact"},
                "deal_id": {"type": "integer", "description": "Associated deal"},
                "priority": {"type": "string", "description": "low, medium, or high", "default": "medium"},
            },
            "required": ["title"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_list_tasks",
        "description": (
            "List CRM tasks with filters. Use to check what follow-ups are due, "
            "what's overdue, or what tasks exist for a customer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Filter by contact"},
                "deal_id": {"type": "integer", "description": "Filter by deal"},
                "completed": {"type": "boolean", "description": "Filter: true=done, false=pending"},
                "due_before": {"type": "string", "description": "Show tasks due before this date (YYYY-MM-DD)"},
                "priority": {"type": "string", "description": "Filter: low, medium, high"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
        "kind": "integration",
    },
    {
        "name": "crm_complete_task",
        "description": "Mark a CRM task as completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "Task ID to complete"},
            },
            "required": ["task_id"],
        },
        "kind": "integration",
    },

    # ── Analytics (1 tool) ────────────────────────────────────────────────────
    {
        "name": "crm_dashboard",
        "description": (
            "Get a CRM summary dashboard with pipeline value, contact counts, overdue tasks, "
            "recent activity, and top deals. Use when the user asks for an overview, summary, "
            "or 'how's my pipeline'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "kind": "integration",
    },

    # ── Chatter / notes (2 tools) ─────────────────────────────────────────────
    {
        "name": "crm_add_note",
        "description": (
            "Add a free-form note to a deal or contact — editable, archivable commentary shown "
            "in the entity's notes thread alongside its activity timeline. Use for observations, "
            "context, or reminders about the record (not a dated interaction — that's crm_log_activity)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "enum": ["deal", "contact"], "description": "'deal' or 'contact'"},
                "entity_id": {"type": "integer", "description": "ID of the deal or contact"},
                "message": {"type": "string", "description": "The note text"},
            },
            "required": ["entity_type", "entity_id", "message"],
        },
        "kind": "integration",
        "writes": True,
    },
    {
        "name": "crm_get_chatter",
        "description": "Read the notes thread for a deal or contact (newest first).",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "enum": ["deal", "contact"], "description": "'deal' or 'contact'"},
                "entity_id": {"type": "integer", "description": "ID of the deal or contact"},
                "limit": {"type": "integer", "description": "Max notes (default 50)", "default": 50, "minimum": 1, "maximum": 200},
                "include_archived": {"type": "boolean", "description": "Include archived notes", "default": False},
            },
            "required": ["entity_type", "entity_id"],
        },
        "kind": "integration",
        "writes": False,
    },
    # ── Companies (5 tools) ───────────────────────────────────────────────────
    {
        "name": "crm_search_companies",
        "writes": False,
        "description": (
            "Search CRM companies by name, domain, industry, or notes. "
            "Use when the user mentions a company/organization and you need to look it up."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (name, domain, industry, or keyword)"},
                "status": {"type": "string", "description": "Filter by status: active, archived"},
            },
            "required": ["query"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_get_company",
        "writes": False,
        "description": (
            "Get a company's full profile including its contacts, deals, open pipeline value, "
            "and recent activity. Use this to see everything about a specific organization."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "integer", "description": "Company ID"},
            },
            "required": ["company_id"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_list_companies",
        "writes": False,
        "description": "List companies with optional status filtering. Use to browse the organization list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter: active, archived"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
            "required": [],
        },
        "kind": "integration",
    },
    {
        "name": "crm_create_company",
        "writes": True,
        "description": (
            "Create a new company/organization in the CRM. Use when the user mentions a business "
            "they want to track, or to group contacts and deals under an organization."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Company name"},
                "domain": {"type": "string", "description": "Website domain", "default": ""},
                "industry": {"type": "string", "default": ""},
                "phone": {"type": "string", "default": ""},
                "address": {"type": "string", "default": ""},
                "notes": {"type": "string", "default": ""},
                "source": {"type": "string", "default": ""},
                "status": {"type": "string", "description": "active or archived", "default": "active"},
            },
            "required": ["name"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_update_company",
        "writes": True,
        "description": (
            "Update an existing company's details — name, domain, industry, phone, address, notes, "
            "or status. Archive a company by setting status to 'archived' (agent-initiated hard "
            "deletes are intentionally not exposed as a tool — archive instead)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "integer", "description": "Company ID to update"},
                "name": {"type": "string"},
                "domain": {"type": "string"},
                "industry": {"type": "string"},
                "phone": {"type": "string"},
                "address": {"type": "string"},
                "notes": {"type": "string"},
                "source": {"type": "string"},
                "status": {"type": "string", "description": "active or archived"},
            },
            "required": ["company_id"],
        },
        "kind": "integration",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Executor Functions
# ═══════════════════════════════════════════════════════════════════════════════

# ── Contacts ──────────────────────────────────────────────────────────────────

def crm_find_contact(query: str, status: str | None = None, tags: str | None = None) -> dict:
    contacts = crm.search_contacts(query, status=status, tags=tags)
    return {"contacts": contacts, "count": len(contacts)}


def crm_create_contact(name: str, **kwargs) -> dict:
    # An invalid company_id would raise a raw FK error; translate it (the HTTP
    # route returns 400 for the same case).
    try:
        return crm.create_contact(name=name, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced company does not exist"}


def crm_update_contact(contact_id: int, **kwargs) -> dict:
    try:
        result = crm.update_contact(contact_id, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced company does not exist"}
    if not result:
        return {"error": f"Contact {contact_id} not found"}
    return result


def crm_get_contact(contact_id: int) -> dict:
    result = crm.get_contact_detail(contact_id)
    if not result:
        return {"error": f"Contact {contact_id} not found"}
    return result


def crm_list_contacts(status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    return crm.list_contacts(offset=offset, limit=limit, status=status)


def crm_delete_contact(contact_id: int) -> dict:
    if crm.delete_contact(contact_id):
        return {"deleted": True, "contact_id": contact_id}
    return {"error": f"Contact {contact_id} not found"}


# ── Deals ─────────────────────────────────────────────────────────────────────

def crm_get_pipeline(stage: str | None = None) -> dict:
    return crm.get_pipeline(stage=stage)


def crm_create_deal(title: str, **kwargs) -> dict:
    try:
        return crm.create_deal(title=title, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced contact or company does not exist"}


def crm_update_deal(deal_id: int, **kwargs) -> dict:
    try:
        result = crm.update_deal(deal_id, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced contact or company does not exist"}
    if not result:
        return {"error": f"Deal {deal_id} not found or invalid stage"}
    return result


def crm_update_deal_stage(deal_id: int, stage: str) -> dict:
    deal = crm.update_deal_stage(deal_id, stage)
    if not deal:
        return {"error": f"Deal not found or invalid stage: {stage}"}
    return deal


def crm_get_deal(deal_id: int) -> dict:
    result = crm.get_deal_detail(deal_id)
    if not result:
        return {"error": f"Deal {deal_id} not found"}
    return result


# ── Activities ────────────────────────────────────────────────────────────────

def crm_log_activity(activity: str, note: str = "", contact_id: int | None = None, deal_id: int | None = None) -> dict:
    return crm.log_activity(activity=activity, note=note, contact_id=contact_id, deal_id=deal_id)


def crm_get_activity_log(contact_id: int | None = None, deal_id: int | None = None, limit: int = 20) -> dict:
    activities = crm.get_activity_log(contact_id=contact_id, deal_id=deal_id, limit=limit)
    return {"activities": activities, "count": len(activities)}


# ── Tasks ─────────────────────────────────────────────────────────────────────

def crm_create_task(title: str, **kwargs) -> dict:
    return crm.create_task(title=title, **kwargs)


def crm_list_tasks(
    contact_id: int | None = None, deal_id: int | None = None,
    completed: bool | None = None, due_before: str | None = None,
    priority: str | None = None, limit: int = 50,
) -> dict:
    tasks = crm.list_tasks(
        contact_id=contact_id, deal_id=deal_id, completed=completed,
        due_before=due_before, priority=priority, limit=limit,
    )
    return {"tasks": tasks, "count": len(tasks)}


def crm_complete_task(task_id: int) -> dict:
    result = crm.complete_task(task_id)
    if not result:
        return {"error": f"Task {task_id} not found"}
    return result


# ── Companies ─────────────────────────────────────────────────────────────────

def crm_search_companies(query: str, status: str | None = None) -> dict:
    companies = crm.search_companies(query, status=status)
    return {"companies": companies, "count": len(companies)}


def crm_get_company(company_id: int) -> dict:
    result = crm.get_company_detail(company_id)
    if not result:
        return {"error": f"Company {company_id} not found"}
    return result


def crm_list_companies(status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    return crm.list_companies(offset=offset, limit=limit, status=status)


def crm_create_company(name: str, **kwargs) -> dict:
    # Tools bypass the router's validation, so guard blank names and translate the
    # unique-name violation here (the assistant engine that consumes this is dormant).
    # There is deliberately no crm_delete_company tool — hard deletes are a human/UI
    # action; the assistant archives via crm_update_company(status="archived").
    if not name.strip():
        return {"error": "Name is required"}
    try:
        return crm.create_company(name=name, **kwargs)
    except psycopg2.errors.UniqueViolation:
        return {"error": "A company with that name already exists"}


def crm_update_company(company_id: int, **kwargs) -> dict:
    try:
        result = crm.update_company(company_id, **kwargs)
    except psycopg2.errors.UniqueViolation:
        return {"error": "A company with that name already exists"}
    if not result:
        return {"error": f"Company {company_id} not found"}
    return result


# ── Analytics ─────────────────────────────────────────────────────────────────

def crm_dashboard() -> dict:
    return crm.get_dashboard_stats()


# ── Chatter / notes ───────────────────────────────────────────────────────────

def crm_add_note(entity_type: str, entity_id: int, message: str) -> dict:
    try:
        note = chatter_service.add_note(entity_type, entity_id, message)
    except ValueError as e:
        return {"error": str(e)}
    return {"ok": True, "note": note}


def crm_get_chatter(
    entity_type: str, entity_id: int, limit: int = 50, include_archived: bool = False,
) -> dict:
    try:
        notes = chatter_service.get_chatter(
            entity_type, entity_id, limit=limit, include_archived=include_archived,
        )
    except ValueError as e:
        return {"error": str(e)}
    return {"notes": notes, "count": len(notes)}


# ═══════════════════════════════════════════════════════════════════════════════
# Executor Mapping (name -> callable(**kwargs) -> dict). 25 entries: the 24
# schema'd tools plus the crm_log_note back-compat alias (no schema def).
# ═══════════════════════════════════════════════════════════════════════════════

TOOL_EXECUTORS = {
    # Contacts
    "crm_find_contact": crm_find_contact,
    "crm_create_contact": crm_create_contact,
    "crm_update_contact": crm_update_contact,
    "crm_get_contact": crm_get_contact,
    "crm_list_contacts": crm_list_contacts,
    "crm_delete_contact": crm_delete_contact,
    # Deals
    "crm_get_pipeline": crm_get_pipeline,
    "crm_create_deal": crm_create_deal,
    "crm_update_deal": crm_update_deal,
    "crm_update_deal_stage": crm_update_deal_stage,
    "crm_get_deal": crm_get_deal,
    # Activities
    "crm_log_activity": crm_log_activity,
    "crm_get_activity_log": crm_get_activity_log,
    # Tasks
    "crm_create_task": crm_create_task,
    "crm_list_tasks": crm_list_tasks,
    "crm_complete_task": crm_complete_task,
    # Analytics
    "crm_dashboard": crm_dashboard,
    # Chatter / notes
    "crm_add_note": crm_add_note,
    "crm_get_chatter": crm_get_chatter,
    # Companies (kept last to match CRM_TOOL_DEFS' section order)
    "crm_search_companies": crm_search_companies,
    "crm_get_company": crm_get_company,
    "crm_list_companies": crm_list_companies,
    "crm_create_company": crm_create_company,
    "crm_update_company": crm_update_company,
    # Backwards compat alias — a legacy 'note' logs an activity (unchanged); the
    # editable notes thread is crm_add_note.
    "crm_log_note": crm_log_activity,
}


def get_crm_tools() -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """Return (tool definitions, executor map) for the CRM.

    CRM tools are first-class core: always collected, with NO per-integration
    enable gate (unlike their chatty origin). The assistant engine — a later
    issue — consumes this; nothing calls it yet.
    """
    return CRM_TOOL_DEFS, TOOL_EXECUTORS
