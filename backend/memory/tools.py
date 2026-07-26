"""Memory agent tools — the assistant's long-term-memory tool family.

A second tool source alongside ``crm.tools`` (issue #5). Structure mirrors
``crm/tools.py`` exactly: a ``MEMORY_TOOL_DEFS`` list (schema sent to the provider),
``MEMORY_TOOL_EXECUTORS`` (the callables), and ``get_memory_tools()`` returning the
``(defs, executors)`` pair unconditionally — no enable gate (memory is core; it simply
does nothing useful until the assistant records facts).

Every def carries a boolean ``"writes"`` flag — the single source of truth for the
assistant's confirmation gate (``assistant.registry.ToolRegistry``). Reads are
``False``; ``memory_add_fact`` / ``memory_invalidate_fact`` are ``True`` and prompt
for confirmation in normal mode. ``tests/test_memory_tools.py`` fails loudly if any
def lacks a boolean ``writes``. Names are ``memory_``-prefixed and ``kind: "memory"``,
so the family is self-describing in confirm dialogs and never collides with the
``crm_*`` family in the merged registry.

Ported from the *facts* tools of Chatty's ``core/agents/tool_definitions.py``: chatty's
``source_type`` filter (daily/topic/memory documents) is dropped — facts are the only
source here — and the bare names gain the ``memory_`` prefix.
"""

from collections.abc import Callable
from datetime import date

from memory import service
from memory.types import MEMORY_TYPES, validate_memory_type

# Sorted for a stable schema enum the model picks from (also keeps typos from silently
# stripping tier-1 archival protection — see memory/service.add_fact).
_MEMORY_TYPE_VALUES = sorted(MEMORY_TYPES)


def _filter_error(memory_type: str | None = None, dates: dict | None = None) -> dict | None:
    """Validate read-filter inputs at the tool boundary and return an ``{"error": ...}``
    dict for a bad value, else None. Symmetric with add_fact's write-path validation so
    an invalid memory_type or date gets a clear message instead of silently widening the
    result set (memory_type) or raising a raw psycopg2 cast error (dates)."""
    if memory_type and validate_memory_type(memory_type) is None:
        return {"error": "memory_type must be one of: " + ", ".join(_MEMORY_TYPE_VALUES)}
    for name, value in (dates or {}).items():
        if value:
            try:
                date.fromisoformat(str(value))
            except (TypeError, ValueError):
                return {"error": f"{name} must be a date in YYYY-MM-DD format"}
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# Tool Definitions (schema only — sent to the AI provider)
# ═══════════════════════════════════════════════════════════════════════════════

MEMORY_TOOL_DEFS: list[dict] = [
    {
        "name": "memory_search",
        "writes": False,
        "description": (
            "Full-text search across your long-term memory — facts you previously "
            "recorded about people, companies, deals, decisions, and preferences. "
            "The most relevant facts are already injected into your context each turn; "
            "use this to look up OLDER or MORE SPECIFIC facts that aren't shown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (natural language or keywords)"},
                "memory_type": {"type": "string", "enum": _MEMORY_TYPE_VALUES, "description": "Optional filter by memory type"},
                "date_from": {"type": "string", "description": "Optional start date (YYYY-MM-DD), filters by valid_from"},
                "date_to": {"type": "string", "description": "Optional end date (YYYY-MM-DD)"},
                "limit": {"type": "integer", "description": "Max results (default 20, max 100)"},
            },
            "required": ["query"],
        },
        "kind": "memory",
    },
    {
        "name": "memory_add_fact",
        "writes": True,
        "description": (
            "Record a durable fact (an entity-relationship triple) in your long-term "
            "memory so you remember it in future conversations. Use for stable knowledge "
            "worth keeping: who someone is, a customer preference, a decision, a key date. "
            "Facts have validity windows and can be queried or invalidated later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The entity (e.g. 'Dana Chen')"},
                "predicate": {"type": "string", "description": "The relationship (e.g. 'works at')"},
                "object": {"type": "string", "description": "The value (e.g. 'Acme Corp')"},
                "memory_type": {"type": "string", "enum": _MEMORY_TYPE_VALUES,
                                "description": "Optional durability type (e.g. decision/preference are never auto-archived)"},
                "confidence": {"type": "number", "description": "Confidence 0.0-1.0 (default 1.0)"},
                "valid_from": {"type": "string", "description": "Date the fact became true (YYYY-MM-DD); defaults to today. Set this when recording a fact you learned about the past."},
            },
            "required": ["subject", "predicate", "object"],
        },
        "kind": "memory",
    },
    {
        "name": "memory_query_facts",
        "writes": False,
        "description": (
            "Query your recorded facts by subject, predicate, memory type, or "
            "point-in-time. Returns live facts by default; set include_expired or "
            "include_archived to see invalidated or auto-archived ones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Filter by subject (partial match)"},
                "predicate": {"type": "string", "description": "Filter by predicate (partial match)"},
                "as_of": {"type": "string", "description": "Point-in-time view (YYYY-MM-DD)"},
                "memory_type": {"type": "string", "enum": _MEMORY_TYPE_VALUES, "description": "Filter by memory type"},
                "include_expired": {"type": "boolean", "description": "Include invalidated facts (default false)"},
                "include_archived": {"type": "boolean", "description": "Include auto-archived dormant facts (default false)"},
                "limit": {"type": "integer", "description": "Max results (default 50)"},
            },
            "required": [],
        },
        "kind": "memory",
    },
    {
        "name": "memory_invalidate_fact",
        "writes": True,
        "description": (
            "Mark a fact as no longer valid by setting its valid_to date (e.g. when a "
            "person changes jobs or a decision is reversed). The fact stops surfacing in "
            "context and search but stays on record."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer", "description": "The fact ID to invalidate"},
                "valid_to": {"type": "string", "description": "End date (YYYY-MM-DD, default: today)"},
            },
            "required": ["fact_id"],
        },
        "kind": "memory",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Executor Functions
# ═══════════════════════════════════════════════════════════════════════════════

def memory_search(
    query: str,
    memory_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> dict:
    err = _filter_error(memory_type, {"date_from": date_from, "date_to": date_to})
    if err:
        return err
    results = service.search_facts(
        query, memory_type=memory_type, date_from=date_from, date_to=date_to, limit=limit
    )
    return {"query": query, "results": results, "total": len(results)}


def memory_add_fact(
    subject: str,
    predicate: str,
    object: str,  # provider-facing name must match the schema property (registry calls fn(**args))
    memory_type: str | None = None,
    confidence: float = 1.0,
    valid_from: str | None = None,
) -> dict:
    return service.add_fact(
        subject=subject, predicate=predicate, object_=object,
        memory_type=memory_type, confidence=confidence, valid_from=valid_from,
    )


def memory_query_facts(
    subject: str | None = None,
    predicate: str | None = None,
    as_of: str | None = None,
    memory_type: str | None = None,
    include_expired: bool = False,
    include_archived: bool = False,
    limit: int = 50,
) -> dict:
    err = _filter_error(memory_type, {"as_of": as_of})
    if err:
        return err
    facts = service.query_facts(
        subject=subject, predicate=predicate, as_of=as_of, memory_type=memory_type,
        include_expired=include_expired, include_archived=include_archived, limit=limit,
    )
    return {"facts": facts, "total": len(facts)}


def memory_invalidate_fact(fact_id: int, valid_to: str | None = None) -> dict:
    return service.invalidate_fact(fact_id, valid_to=valid_to)


MEMORY_TOOL_EXECUTORS: dict[str, Callable[..., dict]] = {
    "memory_search": memory_search,
    "memory_add_fact": memory_add_fact,
    "memory_query_facts": memory_query_facts,
    "memory_invalidate_fact": memory_invalidate_fact,
}


def get_memory_tools() -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """Return (tool definitions, executor map) for the assistant's memory family.

    Collected unconditionally alongside the CRM tools (no enable gate); the
    assistant registry merges both sources.
    """
    return MEMORY_TOOL_DEFS, MEMORY_TOOL_EXECUTORS
