"""CakeCRM — CRM agent tools (31 tools).

Contacts, deals, tasks, activities, chatter/notes, companies, custom fields, and
analytics — all accessible to the AI assistant for managing customer relationships
conversationally.
The CRM is first-class core, so these tools are collected UNCONDITIONALLY (no enable
gate); the assistant engine (backend/assistant/) consumes them via get_crm_tools().

Every def carries a boolean ``"writes"`` flag — the single source of truth for
the assistant's confirmation gate (``backend/assistant/registry.ToolRegistry``):
mutating tools (create/update/delete/log/complete) are ``True`` and prompt for
confirmation in normal mode; read tools are ``False``. Any def added here MUST
carry a ``"writes"`` flag — ``tests/test_crm_tools.py`` fails loudly otherwise.
"""

import logging
from collections.abc import Callable

import psycopg2

from crm import chatter_service, field_service, provenance_service, service as crm

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Tool Definitions (schema only — sent to the AI provider)
# ═══════════════════════════════════════════════════════════════════════════════

# Appended to every contact-READ tool description. The link is authoritative for
# display and search (issue #35), but the legacy free-text column deliberately
# stays and is never propagated to — so after a company rename it holds the old
# name forever. The assistant is the one contact surface that reads raw JSON
# rather than the UI's `company_name || company`, so it needs the precedence rule
# stated explicitly or it will answer with a stale company name.
_COMPANY_NAME_NOTE = (
    "Each contact carries both `company_name` (the linked company — authoritative) "
    "and `company` (legacy free text that can be stale, e.g. after the company was "
    "renamed). Prefer `company_name` whenever it is present."
)

CRM_TOOL_DEFS = [
    # ── Contacts (6 tools) ────────────────────────────────────────────────────
    {
        "name": "crm_find_contact",
        "writes": False,
        "description": (
            "Search CRM contacts by name, email, company, or notes. "
            "Use this when the user mentions a person or company and you need to look them up. "
            "Matches the linked company's name as well as the legacy text, so a contact linked "
            "to a company is found by that company's name. "
            + _COMPANY_NAME_NOTE
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
        "writes": True,
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
                "company": {"type": "string", "description": "Company name (free text). It is automatically linked to the matching company, or a new company is created if the name is new — no need to call crm_create_company first. Pass company_id instead to link an exact existing company.", "default": ""},
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
        "writes": True,
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
                "company": {"type": "string", "description": "Company name (free text). Setting it re-links the contact to that company, creating it if the name is new; setting it to an empty string unlinks. Ignored for linking if company_id is passed in the same call."},
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
        "writes": False,
        "description": (
            "Get a contact's full profile including their deals, tasks, and recent activity. "
            "Use this to see everything about a specific customer. "
            + _COMPANY_NAME_NOTE
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
        "writes": False,
        "description": (
            "List contacts with optional filtering. Use to browse the customer list or "
            "see contacts by status. "
            + _COMPANY_NAME_NOTE
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
        "writes": True,
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
        "writes": False,
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
        "writes": True,
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
        "writes": True,
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
        "writes": True,
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
        "writes": False,
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
        "writes": True,
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
        "writes": False,
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
        "writes": True,
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
        "writes": False,
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
        "writes": True,
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

    # ── Analytics (2 tools) ───────────────────────────────────────────────────
    {
        "name": "crm_dashboard",
        "writes": False,
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
    {
        "name": "crm_analytics",
        "writes": False,
        "description": (
            "Sales analytics summary: win/loss rate, average won deal size, average days to "
            "close, total open pipeline value, activity volume by type, and deal aging (age "
            "buckets plus the stalest open deals). Use for questions about performance, win "
            "rate, stale or neglected deals, or how active the pipeline has been. For a simple "
            "record-counts overview use crm_dashboard instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stale_days": {
                    "type": "integer",
                    "description": "Days without a touch before an open deal counts as stale (default 14)",
                },
            },
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
    # ── Custom fields (6 tools) ───────────────────────────────────────────────
    # User-defined fields on contacts/companies/deals. Get tools discover the field
    # schema (types/options/keys); with an id they also read that record's values.
    # Set tools write by field_key. Values are stored as text (booleans as "1"/"0").
    {
        "name": "crm_get_contact_fields",
        "writes": False,
        "description": (
            "List the custom-field definitions for contacts (name, key, type, options). "
            "Optionally pass a contact_id to also get that contact's current values. "
            "Call this before crm_set_contact_fields to learn the valid field keys."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {
                    "type": "integer",
                    "description": "Optional. Omit to list definitions; provide to also get this contact's values.",
                },
            },
            "required": [],
        },
        "kind": "integration",
    },
    {
        "name": "crm_set_contact_fields",
        "writes": True,
        "description": (
            "Set custom-field values on a contact. Pass a map of field_key → value. "
            "Send an empty string to clear a field; booleans as true/false or \"1\"/\"0\". "
            "Discover valid keys/types first with crm_get_contact_fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Contact ID"},
                "fields": {
                    "type": "object",
                    "description": "Map of field_key → value.",
                    "additionalProperties": {"type": ["string", "number", "boolean"]},
                },
            },
            "required": ["contact_id", "fields"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_get_company_fields",
        "writes": False,
        "description": (
            "List the custom-field definitions for companies (name, key, type, options). "
            "Optionally pass a company_id to also get that company's current values. "
            "Call this before crm_set_company_fields to learn the valid field keys."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "integer",
                    "description": "Optional. Omit to list definitions; provide to also get this company's values.",
                },
            },
            "required": [],
        },
        "kind": "integration",
    },
    {
        "name": "crm_set_company_fields",
        "writes": True,
        "description": (
            "Set custom-field values on a company. Pass a map of field_key → value. "
            "Send an empty string to clear a field; booleans as true/false or \"1\"/\"0\". "
            "Discover valid keys/types first with crm_get_company_fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "integer", "description": "Company ID"},
                "fields": {
                    "type": "object",
                    "description": "Map of field_key → value.",
                    "additionalProperties": {"type": ["string", "number", "boolean"]},
                },
            },
            "required": ["company_id", "fields"],
        },
        "kind": "integration",
    },
    {
        "name": "crm_get_deal_fields",
        "writes": False,
        "description": (
            "List the custom-field definitions for deals (name, key, type, options). "
            "Optionally pass a deal_id to also get that deal's current values. "
            "Call this before crm_set_deal_fields to learn the valid field keys."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {
                    "type": "integer",
                    "description": "Optional. Omit to list definitions; provide to also get this deal's values.",
                },
            },
            "required": [],
        },
        "kind": "integration",
    },
    {
        "name": "crm_set_deal_fields",
        "writes": True,
        "description": (
            "Set custom-field values on a deal. Pass a map of field_key → value. "
            "Send an empty string to clear a field; booleans as true/false or \"1\"/\"0\". "
            "Discover valid keys/types first with crm_get_deal_fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "integer", "description": "Deal ID"},
                "fields": {
                    "type": "object",
                    "description": "Map of field_key → value.",
                    "additionalProperties": {"type": ["string", "number", "boolean"]},
                },
            },
            "required": ["deal_id", "fields"],
        },
        "kind": "integration",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Executor Functions
# ═══════════════════════════════════════════════════════════════════════════════

# ── Field provenance (issue #16) ──────────────────────────────────────────────
# These executors are reached ONLY through the assistant (assistant.registry →
# get_crm_tools()); human edits go through crm/router.py, which never calls them. So a
# successful write here IS an "AI wrote this field" event — record provenance so the UI
# can badge it until a human confirms or overwrites it.

def _record_provenance(entity_type: str, entity_id: int, provided: dict, result: dict) -> None:
    """Best-effort: record 'assistant' provenance for the fields this tool call set.

    Snapshots come from the POST-write entity dict (``result``) so normalization can't mint
    an instantly-stale badge; record_fields skips empty values (no badge on a field the UI
    won't render). Never raises — a provenance failure must not fail the write it describes."""
    try:
        if not entity_id:
            return
        fields = {
            k: result.get(k)
            for k in provided
            if k in provenance_service.PROVENANCE_FIELDS.get(entity_type, ())
        }
        if fields:
            provenance_service.record_fields(entity_type, entity_id, fields)
    except Exception:
        logger.warning("provenance recording failed for %s %s", entity_type, entity_id,
                       exc_info=True)


# ── Contacts ──────────────────────────────────────────────────────────────────

def crm_find_contact(query: str, status: str | None = None, tags: str | None = None) -> dict:
    contacts = crm.search_contacts(query, status=status, tags=tags)
    return {"contacts": contacts, "count": len(contacts)}


def crm_create_contact(name: str, **kwargs) -> dict:
    # An invalid company_id would raise a raw FK error; translate it (the HTTP
    # route returns 400 for the same case).
    try:
        result = crm.create_contact(name=name, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced company does not exist"}
    if not result:
        return {"error": "Contact could not be created"}
    _record_provenance("contact", result.get("id"), {"name": name, **kwargs}, result)
    return result


def crm_update_contact(contact_id: int, **kwargs) -> dict:
    try:
        result = crm.update_contact(contact_id, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced company does not exist"}
    if not result:
        return {"error": f"Contact {contact_id} not found"}
    _record_provenance("contact", contact_id, kwargs, result)
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
        result = crm.create_deal(title=title, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced contact or company does not exist"}
    if not result:
        return {"error": "Deal could not be created"}
    _record_provenance("deal", result.get("id"), {"title": title, **kwargs}, result)
    return result


def crm_update_deal(deal_id: int, **kwargs) -> dict:
    try:
        result = crm.update_deal(deal_id, **kwargs)
    except psycopg2.errors.ForeignKeyViolation:
        return {"error": "Referenced contact or company does not exist"}
    if not result:
        return {"error": f"Deal {deal_id} not found or invalid stage"}
    _record_provenance("deal", deal_id, kwargs, result)
    return result


def crm_update_deal_stage(deal_id: int, stage: str) -> dict:
    deal = crm.update_deal_stage(deal_id, stage)
    if not deal:
        return {"error": f"Deal not found or invalid stage: {stage}"}
    _record_provenance("deal", deal_id, {"stage": stage}, deal)
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


def crm_analytics(stale_days: int = 14) -> dict:
    # get_analytics clamps stale_days server-side, so an absurd LLM value is bounded.
    return crm.summarize_analytics(crm.get_analytics(stale_days=stale_days))


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


# ── Custom fields ─────────────────────────────────────────────────────────────
# Get tools discover the field schema (and optionally a record's values); set tools
# write by field_key. Attribution is hardcoded "assistant" — the set tools expose no
# user_email param, so the model can't spoof who edited a value.

def _normalize_field_value(value) -> str:
    """Coerce an LLM-supplied value to the text form the value table stores."""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _normalize_field(r: dict) -> dict:
    """One field's schema in the shape shown to the model. Used for BOTH get-tool
    modes so a schema learned without an id matches the keys/types read with one."""
    out = {
        "field_id": r.get("field_id", r.get("id")),
        "field_key": r["field_key"],
        "name": r["name"],
        "field_type": r["field_type"],
        "is_required": bool(r["is_required"]),
    }
    if r.get("dropdown_options"):
        out["options"] = r["dropdown_options"]
    return out


def _get_entity_fields(entity_type: str, entity_id: int | None) -> dict:
    """No id → list the definitions (schema discovery). With an id → that entity's
    values (every definition, unset ones with value=None)."""
    if entity_id is None:
        try:
            defs = field_service.list_field_definitions(entity_type)
        except ValueError as e:
            return {"error": str(e)}
        fields = [_normalize_field(d) for d in defs]
        return {"fields": fields, "total": len(fields)}
    if not field_service.entity_exists(entity_type, entity_id):
        return {"error": f"{entity_type} {entity_id} not found"}
    try:
        rows = field_service.get_field_values(entity_type, entity_id)
    except ValueError as e:
        return {"error": str(e)}
    # Same normalized schema shape as the no-id path, plus the per-entity value fields.
    fields = [
        {**_normalize_field(r), "value": r["value"],
         "value_updated_at": r.get("value_updated_at"), "updated_by_email": r.get("updated_by_email")}
        for r in rows
    ]
    return {"fields": fields, "total": len(fields)}


def _set_entity_fields(entity_type: str, entity_id: int, fields: dict) -> dict:
    """Resolve field_key → id (scoped to entity_type), normalize values, and upsert.
    None values are rejected (send "" to clear); unknown keys are reported, not set."""
    if not isinstance(fields, dict) or not fields:
        return {"error": "No fields provided"}
    if not field_service.entity_exists(entity_type, entity_id):
        return {"error": f"{entity_type} {entity_id} not found"}
    try:
        defs = field_service.list_field_definitions(entity_type)
    except ValueError as e:
        return {"error": str(e)}
    key_to_id = {d["field_key"]: d["id"] for d in defs}

    values_by_id: dict[str, str] = {}
    unknown: list[str] = []
    rejected: list[str] = []
    for key, value in fields.items():
        if value is None:
            rejected.append(key)
            continue
        field_id = key_to_id.get(key)
        if field_id is None:
            unknown.append(key)
            continue
        values_by_id[str(field_id)] = _normalize_field_value(value)

    if not values_by_id:
        detail = []
        if unknown:
            detail.append(f"unknown keys: {unknown}")
        if rejected:
            detail.append(f"null values (send an empty string to clear): {rejected}")
        return {"error": "No valid fields to set" + (f" ({'; '.join(detail)})" if detail else "")}

    try:
        result = field_service.set_field_values(entity_type, entity_id, values_by_id, "assistant")
    except ValueError as e:
        return {"error": str(e)}
    if unknown:
        result["unknown_keys"] = unknown
    if rejected:
        result["rejected"] = rejected
    return result


def crm_get_contact_fields(contact_id: int | None = None) -> dict:
    return _get_entity_fields("contact", contact_id)


def crm_set_contact_fields(contact_id: int, fields: dict) -> dict:
    return _set_entity_fields("contact", contact_id, fields)


def crm_get_company_fields(company_id: int | None = None) -> dict:
    return _get_entity_fields("company", company_id)


def crm_set_company_fields(company_id: int, fields: dict) -> dict:
    return _set_entity_fields("company", company_id, fields)


def crm_get_deal_fields(deal_id: int | None = None) -> dict:
    return _get_entity_fields("deal", deal_id)


def crm_set_deal_fields(deal_id: int, fields: dict) -> dict:
    return _set_entity_fields("deal", deal_id, fields)


# ═══════════════════════════════════════════════════════════════════════════════
# Executor Mapping (name -> callable(**kwargs) -> dict). 32 entries: the 31
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
    "crm_analytics": crm_analytics,
    # Chatter / notes
    "crm_add_note": crm_add_note,
    "crm_get_chatter": crm_get_chatter,
    # Companies (kept last to match CRM_TOOL_DEFS' section order)
    "crm_search_companies": crm_search_companies,
    "crm_get_company": crm_get_company,
    "crm_list_companies": crm_list_companies,
    "crm_create_company": crm_create_company,
    "crm_update_company": crm_update_company,
    # Custom fields
    "crm_get_contact_fields": crm_get_contact_fields,
    "crm_set_contact_fields": crm_set_contact_fields,
    "crm_get_company_fields": crm_get_company_fields,
    "crm_set_company_fields": crm_set_company_fields,
    "crm_get_deal_fields": crm_get_deal_fields,
    "crm_set_deal_fields": crm_set_deal_fields,
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
