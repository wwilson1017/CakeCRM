"""Context-file agent tools (issue #72 Phase 1).

A tool source alongside ``crm.tools`` / ``memory.tools``, same shape: a ``*_TOOL_DEFS``
list, a ``*_TOOL_EXECUTORS`` map, and ``get_context_file_tools()`` returning the
``(defs, executors)`` pair unconditionally — the store is core and needs no AI keys.

Ported from chatty's ``core/agents/tools/context_tools.py``. Chatty's ``_safe_filename``
lives at this layer too, and rejects ``/`` outright because its namespace is flat; ours
normalizes into ``topics/`` and ``daily/`` instead (see ``service.normalize_filename``).
Chatty's ``_index_context_file`` / ``_remove_context_index`` hooks maintain a separate
FTS5+vector index; here the generated ``search_tsv`` column does that automatically, so
they have no target.

## Security

Every write carries ``writes: True``, so the registry's confirmation gate covers them and
``background_allowlist()`` (which derives from ``writes_map``) excludes them from
unattended turns — a prompt injection reaching a background turn can never touch Baker's
identity.

``writes: True`` alone is NOT sufficient for the two protected files. The engine executes
writes immediately in power mode unless the turn is already tainted, and a poisoned
``soul.md`` is not one bad record — it is a permanent system instruction replayed on every
future turn, including background ones, surviving conversation deletion. So
``requires_confirmation()`` below marks writes to a protected file as always-confirm, in
every mode; the engine consults it exactly as it consults ``gmail.tools.binding_conflict``.
"""

import logging
from collections.abc import Callable

from context_files import service

logger = logging.getLogger(__name__)

# Tools whose confirmation requirement depends on their ARGUMENTS, not just their name.
# The engine's gate is name-keyed (writes_map), so this is the hook that lets one tool be
# "confirm sometimes" — kept here beside the rule it enforces.
_TARGETED_WRITE_TOOLS = frozenset({"write_context_file", "delete_context_file"})


def requires_confirmation(tool_name: str, args: dict | None) -> bool:
    """True when this specific call must confirm regardless of tool mode.

    Fails CLOSED: an unparseable or missing filename on a targeted write confirms. The
    cost of a needless confirmation is one click; the cost of a missed one is Baker's
    identity.
    """
    if tool_name not in _TARGETED_WRITE_TOOLS:
        return False
    filename = (args or {}).get("filename")
    if not isinstance(filename, str):
        return True
    try:
        return service.normalize_filename(filename) in service.PROTECTED_FILES
    except service.ContextFileError:
        return True


def _error(exc: service.ContextFileError) -> dict:
    return {"error": exc.message}


# ═══════════════════════════════════════════════════════════════════════════════
# Executors
# ═══════════════════════════════════════════════════════════════════════════════

def _list_context_files(kind: str | None = None) -> dict:
    if kind and kind not in ("soul", "memory", "topic", "daily"):
        return {"error": "kind must be one of: soul, memory, topic, daily"}
    files = service.list_files(kind=kind)
    return {
        "files": [
            {
                "filename": f["filename"],
                "kind": f["kind"],
                "headline": f["headline"],
                "size_chars": f["size_chars"],
                "updated_at": str(f["updated_at"]),
            }
            for f in files
        ],
        "count": len(files),
    }


def _read_context_file(filename: str) -> dict:
    try:
        row = service.read_file(filename)
    except service.ContextFileError as exc:
        return _error(exc)
    if not row:
        return {"error": f"No context file named '{filename}'."}
    content = row.get("content") or ""
    truncated = len(content) > service.MAX_READ_CHARS
    if truncated:
        content = content[: service.MAX_READ_CHARS]
    return {
        "filename": row["filename"],
        "kind": row["kind"],
        "content": content,
        "truncated": truncated,
        "updated_at": str(row["updated_at"]),
    }


def _write_context_file(filename: str, content: str) -> dict:
    try:
        row = service.write_file(filename, content, written_by="assistant")
    except service.ContextFileError as exc:
        return _error(exc)
    return {"filename": row.get("filename"), "ok": True, "updated_at": str(row.get("updated_at"))}


def _delete_context_file(filename: str) -> dict:
    try:
        deleted = service.delete_file(filename)
    except service.ContextFileError as exc:
        return _error(exc)
    if not deleted:
        return {"error": f"No context file named '{filename}'."}
    return {"filename": filename, "deleted": True}


def _append_daily_note(content: str, date: str | None = None) -> dict:
    try:
        return service.append_daily_note(content, day=date, written_by="assistant")
    except service.ContextFileError as exc:
        return _error(exc)


def _read_daily_note(date: str | None = None) -> dict:
    try:
        text = service.read_daily_note(date)
    except service.ContextFileError as exc:
        return _error(exc)
    day = date or service.today_str()
    if not text:
        return {"date": day, "content": "", "exists": False}
    return {"date": day, "content": text[: service.MAX_READ_CHARS], "exists": True}


def _search_context_files(query: str, limit: int = 20) -> dict:
    results = service.search_files(query, limit=limit)
    return {
        "results": [
            {
                "filename": r["filename"],
                "kind": r["kind"],
                "headline": r["headline"],
                "updated_at": str(r["updated_at"]),
            }
            for r in results
        ],
        "count": len(results),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Definitions (schema only — sent to the AI provider)
# ═══════════════════════════════════════════════════════════════════════════════

_FILENAME_DESC = (
    "File name: 'soul.md', 'MEMORY.md', 'topics/<name>.md', or 'daily/YYYY-MM-DD.md'. "
    "A bare '<name>.md' is treated as a topic file."
)

CONTEXT_FILE_TOOL_DEFS: list[dict] = [
    {
        "name": "list_context_files",
        "writes": False,
        "description": (
            "List your knowledge files with their one-line headlines. Use this to see "
            "what you have written down before answering from memory alone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["soul", "memory", "topic", "daily"],
                    "description": "Optional filter by file kind",
                },
            },
        },
        "kind": "context",
    },
    {
        "name": "read_context_file",
        "writes": False,
        "description": (
            "Read one of your knowledge files in full. Your soul and your MEMORY snapshot "
            "are already in your context each turn; use this for topic files listed in "
            "your 'Your other knowledge' manifest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"filename": {"type": "string", "description": _FILENAME_DESC}},
            "required": ["filename"],
        },
        "kind": "context",
    },
    {
        "name": "write_context_file",
        "writes": True,
        "description": (
            "Create or OVERWRITE one of your knowledge files. This replaces the whole "
            "file, so include everything that should remain plus your additions — read it "
            "first if you are unsure what is there. Use 'soul.md' for what you learn about "
            "yourself, 'MEMORY.md' for your durable snapshot of key people and decisions, "
            "and 'topics/<name>.md' for subject-scoped knowledge. Keep topic files focused; "
            "split one that grows past roughly 50 lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": _FILENAME_DESC},
                "content": {"type": "string", "description": "The COMPLETE new file content"},
            },
            "required": ["filename", "content"],
        },
        "kind": "context",
    },
    {
        "name": "delete_context_file",
        "writes": True,
        "description": (
            "Delete a topic file or a past daily note. Your soul and MEMORY files are "
            "protected and cannot be deleted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"filename": {"type": "string", "description": _FILENAME_DESC}},
            "required": ["filename"],
        },
        "kind": "context",
    },
    {
        "name": "append_daily_note",
        "writes": True,
        "description": (
            "Append a short, factual, timestamped entry to today's running log. One call "
            "per event — a decision made, a commitment given, something the user told you. "
            "This appends; it never overwrites."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "A short factual entry"},
                "date": {
                    "type": "string",
                    "description": "Optional day (YYYY-MM-DD); defaults to today",
                },
            },
            "required": ["content"],
        },
        "kind": "context",
    },
    {
        "name": "read_daily_note",
        "writes": False,
        "description": (
            "Read a past day's running log. Today's note is already in your context; use "
            "this for a date listed in your 'Recent daily notes' manifest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Day to read (YYYY-MM-DD)"},
            },
            "required": ["date"],
        },
        "kind": "context",
    },
    {
        "name": "search_context_files",
        "writes": False,
        "description": (
            "Full-text search across all of your knowledge files. Use this when you are "
            "not sure which file covers a topic. Searches file names and contents; to "
            "search recorded facts instead, use memory_search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (keywords)"},
                "limit": {"type": "integer", "description": "Max results (default 20, max 50)"},
            },
            "required": ["query"],
        },
        "kind": "context",
    },
]

CONTEXT_FILE_TOOL_EXECUTORS: dict[str, Callable[..., dict]] = {
    "list_context_files": _list_context_files,
    "read_context_file": _read_context_file,
    "write_context_file": _write_context_file,
    "delete_context_file": _delete_context_file,
    "append_daily_note": _append_daily_note,
    "read_daily_note": _read_daily_note,
    "search_context_files": _search_context_files,
}


def get_context_file_tools() -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """The ``(defs, executors)`` pair for ``assistant.registry.ToolRegistry``."""
    return list(CONTEXT_FILE_TOOL_DEFS), dict(CONTEXT_FILE_TOOL_EXECUTORS)
