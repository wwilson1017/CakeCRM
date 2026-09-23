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

Always-confirm is not a ROLE, though, and issue #213 is the hole that leaves: the person
who approves the card is whoever is in the conversation, so a member could ask for a
``soul.md`` rewrite and then approve their own request. Since #213 the seat decides too —
``get_context_file_tools(user=…)`` wraps ``write_context_file`` so a non-admin seat is
refused a PROTECTED filename, the same rule ``context_files.router.put_context_file``
applies to the REST door (#194, Decision 1d). The two gates are independent and both
hold: the card still fires for an admin, and the role check still refuses a member who
somehow got past the card.
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
    # isinstance, not `args or {}`: a provider can decode malformed tool JSON to a list,
    # string or number, and `.get` on that raises INSIDE the engine's gate — before the
    # registry's bad-argument handling — killing the turn instead of failing closed.
    if not isinstance(args, dict):
        return True
    filename = args.get("filename")
    if not isinstance(filename, str):
        return True
    try:
        return service.normalize_filename(filename) in service.PROTECTED_FILES
    except service.ContextFileError:
        return True


def _error(exc: service.ContextFileError) -> dict:
    return {"error": exc.message}


# ── Version binding for gated writes ─────────────────────────────────────────────
# A gated write is composed against the file the assistant just read, but it EXECUTES
# only once the user approves it — and for a protected file that gate fires in EVERY
# mode, so the gap is as long as the human takes to decide. The user can edit that same
# file in the Memory page inside the gap, and an unconditional upsert on approval would
# discard their edit with no trace. So the version is stamped into the pending
# placeholder when the write is PROPOSED (the server reads it; the model is never asked
# to echo it back, because a model that forgets the token would silently opt out of the
# guard) and enforced when the write finally runs. Same shape as gmail.tools' connection
# binding — see engine._VERSION_BOUND_WRITE_TOOLS.

_BINDING_VERSION = "context_updated_at"


def pending_binding(args: dict | None) -> dict | None:
    """Placeholder keys binding a pending write to the version it was proposed against.

    ``None`` when there is nothing trustworthy to bind, and the write then proceeds
    unconditionally exactly as it did before this guard existed. Failing OPEN is
    deliberate: a bogus binding would refuse a legitimate write forever, which is worse
    than the race it closes — the same call ``gmail.tools._live_generation`` makes.

    simplification: a file that does not exist yet binds to nothing, so a file CREATED
    inside the gap is still overwritten silently. Closing that needs an
    expect-absent insert (``ON CONFLICT DO NOTHING`` + rowcount) which the service has
    no primitive for; the protected files this gate exists for are seeded by the
    migration and always exist.
    """
    if not isinstance(args, dict):
        return None
    filename = args.get("filename")
    if not isinstance(filename, str):
        return None
    try:
        row = service.read_file(filename)
    except Exception:
        # Unparseable name, or the read failed. Bind nothing — but say so: a guard that
        # disables itself in silence is indistinguishable from one that is working.
        logger.warning("context_files: no version to bind for %r; write will be unconditional",
                       filename, exc_info=True)
        return None
    updated_at = (row or {}).get("updated_at")
    return {_BINDING_VERSION: str(updated_at)} if updated_at is not None else None


def binding_kwargs(parsed: dict) -> dict:
    """Executor kwargs that enforce a stamped binding; ``{}`` when unbound.

    Merged into the canonical args at approval time rather than checked beforehand, so
    the precondition rides ``service.write_file``'s in-UPDATE comparison and leaves no
    check-then-write window at all (see that docstring).
    """
    token = parsed.get(_BINDING_VERSION) if isinstance(parsed, dict) else None
    return {"expected_updated_at": token} if isinstance(token, str) and token else {}


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
    # An on-demand read the assistant CHOSE to make — the only kind file-dreaming counts.
    service.track_read_for([row["filename"]])
    return {
        "filename": row["filename"],
        "kind": row["kind"],
        "content": content,
        "truncated": truncated,
        "updated_at": str(row["updated_at"]),
    }


def _write_context_file(filename: str, content: str,
                        expected_updated_at: str | None = None) -> dict:
    """Create or overwrite a file.

    ``expected_updated_at`` is NOT in the tool schema — the engine injects it from the
    pending placeholder when a confirmed write had a version bound to it (see
    ``pending_binding``). An immediate power-mode write passes None: read and write land
    in one turn with no human pause between them, so there is no gap to guard.
    """
    try:
        row = service.write_file(
            filename, content, written_by="assistant",
            expected_updated_at=expected_updated_at,
        )
    except service.ContextFileError as exc:
        if exc.code == "conflict":
            # The service's message is written for the browser editor ("reload"). Tell
            # the model what IT has to do instead, or it will just retry the same
            # now-stale overwrite and clobber the edit on the second pass. Deliberately
            # does NOT blame the user: an earlier pending write of Baker's own, approved
            # first, lands here too, and a wrong cause sends it off to ask about an edit
            # nobody made.
            return {"error": (
                f"'{filename}' changed after you proposed this write, so it was NOT "
                "saved — the user may have edited it, or an earlier write of yours "
                "landed first. Read the file again and re-apply your change on top of "
                "what is there now."
            )}
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
    # Daily notes are not SCORED (Decision C — they are a dated log reached by name), but
    # recording the signal costs one line and means flipping that decision later needs no
    # change here. `read_daily_note` returns a body, not a row, so derive the key the same
    # way it did.
    service.track_read_for([service.daily_filename(date)])
    return {"date": day, "content": text[: service.MAX_READ_CHARS], "exists": True}


def _search_context_files(query: str, limit: int = 20) -> dict:
    results = service.search_files(query, limit=limit)
    # The surfaced subset ONLY — what the search actually handed back, never the whole
    # corpus it scanned. Same rule memory/context.py applies to FTS matches vs backfill.
    service.track_read_for([r["filename"] for r in results])
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


# ── Seat gate for the two protected files (issue #213) ───────────────────────────
# Shape B of the two the issue named: the tools stay advertised to EVERY seat, and the
# executor refuses a protected filename for a non-admin. The whole-tool gate Gmail uses
# would also take `topics/` and `daily/` writes away from members — real product loss for
# the case Decision 1d never meant to restrict — unless `write_context_file` were split
# in two. Refusing inside the executor keeps one tool and puts the check on the same
# normalized name the REST handler checks, so the two doors cannot drift apart.
#
# `delete_context_file` needs no wrapper: `service.delete_file` already raises `forbidden`
# for a protected file, for every seat including an admin.


def _admin_only_protected_writes(
    fn: Callable[..., dict], user: dict | None,
) -> Callable[..., dict]:
    """``fn`` with writes to a PROTECTED file refused unless ``user`` is an admin seat.

    Wraps at collection time rather than inside the executor, the shape
    ``crm.tools.bind_server_args`` established: the module-level executor map keeps its
    plain, seat-free functions and only the registry's copy carries the gate.

    Resolving the role ONCE is safe because a registry is built fresh for every turn —
    per SSE request, per ``/confirm`` (``assistant.router.confirm``), per Telegram button
    (``telegram.service``) — each from a row the server just loaded. A protected write
    proposed while the seat was an admin and approved after a demotion is therefore
    refused: the approving registry is a new one built from the new row.

    Fails CLOSED in every direction. ``user=None`` is an unattended turn and is not an
    admin, so a background registry can never write a protected file — a second lock in
    front of ``assistant.background``'s read-only allowlist, which already excludes every
    ``writes: True`` tool; neither relies on the other. A non-dict user, a row with no
    role, and an unrecognised role are all "not admin". A filename that will not normalize
    returns the service's own error and never reaches ``service.write_file`` — and
    normalization is load-bearing, because gating the raw string would be bypassed by
    asking for ``SOUL.MD``.
    """
    if isinstance(user, dict) and user.get("role") == "admin":
        return fn

    def _run(**kwargs) -> dict:
        try:
            name = service.normalize_filename(kwargs.get("filename"))
        except service.ContextFileError as exc:
            return _error(exc)
        if name in service.PROTECTED_FILES:
            # The REST handler's sentence verbatim (router.put_context_file), so a member
            # is told the same thing whichever door they came through.
            return {"error": f"Only an admin can edit {name}."}
        return fn(**kwargs)

    return _run


def get_context_file_tools(
    user: dict | None = None,
) -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """The ``(defs, executors)`` pair for ``assistant.registry.ToolRegistry``.

    ``user`` is the seat this registry serves (#190) — the ``get_current_user`` row, or
    None for an unattended turn. Collection stays UNCONDITIONAL: the store is core and
    keyless, and the defs handed back are identical for every seat, so the model always
    sees the same seven tools and the registry's derived ``writes_map`` — and therefore
    the background allowlist — cannot move. The seat changes exactly one thing: whether
    ``write_context_file`` will accept a protected filename (#213).

    MUST never raise; it touches no database at all.
    """
    executors = dict(CONTEXT_FILE_TOOL_EXECUTORS)
    executors["write_context_file"] = _admin_only_protected_writes(
        executors["write_context_file"], user,
    )
    return list(CONTEXT_FILE_TOOL_DEFS), executors
