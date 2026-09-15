"""Help-library agent tools (issue #143 phase 1).

A tool source alongside ``crm.tools`` / ``memory.tools`` / ``context_files.tools``, same
shape: a ``*_TOOL_DEFS`` list, a ``*_TOOL_EXECUTORS`` map, and ``get_help_tools()``
returning the ``(defs, executors)`` pair UNCONDITIONALLY — the manual is core product
content, needs no AI keys of its own and has no enable flag.

Three tools, not one and not two:

* ``help_search`` is the entry point. Search-first is the whole design — the library is
  consulted on demand, never front-loaded into the prompt.
* ``help_read_topic`` fetches one topic in full. Folding this into search (returning the
  best topic's whole body) was considered and rejected: it would return up to 8,000
  characters on every search whether or not they were wanted, and conflate "find" with
  "read" to save one cheap tool call.
* ``help_list_topics`` browses the tree. It is what makes the folder structure real to
  the model rather than an implementation detail, and it is the discoverability surface
  that lets ``identity.HELP_NOTE`` stay a few lines instead of a per-topic manifest that
  grows with the library.

## Why these defs hold no disk reads

``get_help_tools()`` runs inside ``ToolRegistry.__init__``, on every chat turn. A def
built from the corpus would put file I/O — and a possible exception — on the path that
constructs the registry, so a single malformed topic file would break chat rather than
break one tool call. The defs are constants; only the executors touch disk, where the
registry's own handler turns any failure into one tool error.

## Why help results are NOT fenced as untrusted

``delimiters.UNTRUSTED_SOURCE_TOOLS`` encodes THIRD-PARTY origin: a Gmail body is text a
stranger wrote. Help content is our own committed prose, reviewed in a pull request and
scanned by ``tests/test_prompt_genericization.py``. It is the one payload class that
cannot carry hostile user text, so — exactly like a context-file read — a help read must
not taint the turn. Pinned in both directions by ``tests/test_help_library.py`` so a
later "fence everything" refactor fails loudly instead of silently downgrading every
power-mode turn that ever consulted the manual.
"""

import logging
from collections.abc import Callable

from help import library as lib, search as help_search_mod

logger = logging.getLogger(__name__)

# Fixed error strings. No model-supplied string is ever echoed back into a tool result:
# reflecting arbitrary model text into an unfenced payload is exactly the hole this
# module's "nothing user-typed reaches the payload" claim would otherwise have.
_ERR_UNKNOWN_TOPIC = "No help topic by that name."
_ERR_NO_TOPIC = "help_read_topic needs a topic name. Use help_list_topics to see them."
_ERR_UNKNOWN_FOLDER = "No help section by that name."
_ERR_BAD_ARG = "That argument is not a string."
_ERR_UNAVAILABLE = "The help library could not be read."

_MAX_ARG_CHARS = 200


def _load() -> lib.Library | None:
    try:
        return lib.get_library()
    except Exception:
        logger.exception("help library unavailable")
        return None


def _clean(value, *, default: str = "") -> str | None:
    """A model-supplied string, trimmed and length-capped; ``None`` if it is not one."""
    if value is None:
        return default
    if not isinstance(value, str):
        return None
    return value.strip()[:_MAX_ARG_CHARS]


def _brief(topic: lib.Topic) -> dict:
    return {
        "topic": topic.slug,
        "title": topic.title,
        "description": topic.description,
        "admin_only": topic.admin_only,
    }


def _help_search(query=None, limit: int = help_search_mod.DEFAULT_RESULTS) -> dict:
    text = _clean(query)
    if text is None:
        return {"error": _ERR_BAD_ARG}
    library = _load()
    if library is None:
        return {"error": _ERR_UNAVAILABLE}
    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = help_search_mod.DEFAULT_RESULTS
    hits = help_search_mod.search(library, text, count)
    if not hits:
        # Fail closed the same way an unknown slug does: hand back the shape of the
        # library rather than an empty result the model has to guess its way out of.
        return {
            "results": [],
            "message": "Nothing in the help library matched. Browse the sections with "
                       "help_list_topics, or tell the user the manual does not cover it.",
            "sections": list(library.folders),
        }
    return {
        "results": [{**_brief(h.topic), "snippet": h.snippet} for h in hits],
        "count": len(hits),
    }


def _help_read_topic(topic=None) -> dict:
    slug = _clean(topic)
    if slug is None:
        return {"error": _ERR_BAD_ARG}
    slug = slug.strip("/")
    if not slug:
        return {"error": _ERR_NO_TOPIC}
    library = _load()
    if library is None:
        return {"error": _ERR_UNAVAILABLE}
    found = library.get(slug)
    if found is None:
        # The search index answering its own error: a miss returns corpus-drawn nearest
        # matches, never a bare "not found" and never the string that missed.
        return {
            "error": _ERR_UNKNOWN_TOPIC,
            "closest": [_brief(t) for t in help_search_mod.nearest(library, slug)],
        }
    return {**_brief(found), "content": found.body}


def _help_list_topics(folder=None) -> dict:
    name = _clean(folder)
    if name is None:
        return {"error": _ERR_BAD_ARG}
    library = _load()
    if library is None:
        return {"error": _ERR_UNAVAILABLE}
    name = name.strip("/")
    if not name:
        return {
            "sections": [
                {"section": f, "topic_count": len(library.in_folder(f))}
                for f in library.folders
            ],
            "topics": [_brief(t) for t in library.root_topics],
        }
    if name not in library.folders:
        return {"error": _ERR_UNKNOWN_FOLDER, "sections": list(library.folders)}
    return {"section": name, "topics": [_brief(t) for t in library.in_folder(name)]}


HELP_TOOL_DEFS: list[dict] = [
    {
        "name": "help_search",
        "writes": False,
        "description": (
            "Search this CRM's built-in product manual. Use this FIRST whenever the user "
            "asks how the product itself works — how to connect a mailbox, what a setting "
            "does, where a number on screen comes from, what happens when they click "
            "something. Returns matching topics with a short extract; read the full topic "
            "with help_read_topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What the user wants to know, in their own words",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max topics to return (default 5, max 8)",
                },
            },
            "required": ["query"],
        },
        "kind": "help",
    },
    {
        "name": "help_read_topic",
        "writes": False,
        "description": (
            "Read one manual topic in full, by the topic name a help_search result or "
            "help_list_topics gave you (for example 'settings/gmail'). Answer product "
            "how-to questions from what the topic says rather than from general CRM "
            "knowledge, which is usually wrong about this product's specifics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic name, e.g. 'pipeline/stages' or 'getting-started'",
                },
            },
            "required": ["topic"],
        },
        "kind": "help",
    },
    {
        "name": "help_list_topics",
        "writes": False,
        "description": (
            "Browse the product manual. With no argument it lists the manual's sections "
            "and any top-level topics; with a section name it lists that section's "
            "topics. Use it to see what the manual covers before telling the user "
            "something is not documented."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Section to list, e.g. 'settings'. Omit for the top level.",
                },
            },
            "required": [],
        },
        "kind": "help",
    },
]

HELP_TOOL_EXECUTORS: dict[str, Callable[..., dict]] = {
    "help_search": _help_search,
    "help_read_topic": _help_read_topic,
    "help_list_topics": _help_list_topics,
}


def get_help_tools() -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """The ``(defs, executors)`` pair for ``assistant.registry.ToolRegistry``."""
    return list(HELP_TOOL_DEFS), dict(HELP_TOOL_EXECUTORS)
