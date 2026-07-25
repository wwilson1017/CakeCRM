"""The assistant's tool registry — a composition over the tool families.

Chatty's ``ToolRegistry`` dispatches many tool families by ``kind``; CakeCRM's
assistant composes a small ordered list of ``get_*_tools()`` sources — the CRM tools
(issue #4) and the long-term-memory tools (issue #5) — each a ``(defs, executors)``
pair. The registry is the SINGLE SOURCE OF TRUTH for which tools are writes: the
streaming loop's confirmation gate and the ``/confirm`` endpoint both read ``is_write``
from here, so they can never diverge on what needs approval.

Merging is fail-loud: a tool name duplicated across sources, or a def missing a boolean
``writes`` flag, RAISES at construction — a silently-misclassified tool would bypass the
confirmation gate. (Issue #6's heartbeat/reminder tools slot in the same way; when its
registry-composition rewrite lands, this merge collapses to appending its source to the
list — see the #5 team-coordination note.)

Executors are synchronous, blocking psycopg2 code. ``execute_tool_sync`` is the
real dispatch (used directly by sync endpoints); ``execute_tool`` offloads it to a
thread so a tool call never blocks the async SSE event loop.
"""

import asyncio
import logging

from crm.tools import get_crm_tools
from memory.tools import get_memory_tools

logger = logging.getLogger(__name__)

# Internal bookkeeping keys stripped before tool defs reach a provider (providers
# only understand name/description/input_schema).
_INTERNAL_KEYS = {"kind", "writes"}


class ToolRegistry:
    def __init__(self) -> None:
        # Ordered tool sources. Each is an always-on (defs, executors) family.
        sources = [get_crm_tools(), get_memory_tools()]

        tool_defs: list[dict] = []
        executors: dict = {}
        seen_defs: set[str] = set()
        for defs, execs in sources:
            for d in defs:
                name = d["name"]
                if name in seen_defs:
                    raise RuntimeError(f"Duplicate tool def name across sources: {name}")
                if not isinstance(d.get("writes"), bool):
                    raise RuntimeError(f"Tool def {name!r} is missing a boolean 'writes' flag")
                seen_defs.add(name)
                tool_defs.append(d)
            for ename, fn in execs.items():
                if ename in executors:
                    raise RuntimeError(f"Duplicate tool executor across sources: {ename}")
                executors[ename] = fn

        self.tool_defs = tool_defs
        self.executors = executors
        self.writes_map = {t["name"]: bool(t["writes"]) for t in self.tool_defs}
        self.descriptions = {t["name"]: t.get("description", "") for t in self.tool_defs}

    def is_write(self, name: str) -> bool:
        return self.writes_map.get(name, False)

    def provider_tools(self, tool_mode: str) -> list[dict]:
        """Tool defs to send the provider, internal keys stripped.

        In read-only mode, write tools are filtered out entirely so the model
        cannot even name them. (The engine ALSO refuses to execute a write in
        read-only mode — filtering is not the authorization boundary.)
        """
        out: list[dict] = []
        for t in self.tool_defs:
            if tool_mode == "read-only" and self.writes_map.get(t["name"], False):
                continue
            out.append({k: v for k, v in t.items() if k not in _INTERNAL_KEYS})
        return out

    def execute_tool_sync(self, name: str, args: dict | None) -> dict:
        # Fail closed: only DECLARED tools (those with a def, hence a writes flag)
        # are executable. This keeps executor-only aliases like crm_log_note — which
        # have no def and no writes classification — from ever running unconfirmed.
        if name not in self.writes_map:
            return {"error": f"Unknown tool: {name}"}
        fn = self.executors.get(name)
        if fn is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return fn(**(args or {}))
        except TypeError as e:  # LLM passed bad/unexpected arguments
            logger.warning("assistant tool %s bad args: %s", name, e)
            return {"error": f"The tool '{name}' could not run with those arguments."}
        except Exception:  # DB / service failure — log detail, return a generic message
            logger.exception("assistant tool %s failed", name)
            return {"error": f"The tool '{name}' failed. Please try again."}

    async def execute_tool(self, name: str, args: dict | None) -> dict:
        return await asyncio.to_thread(self.execute_tool_sync, name, args)
