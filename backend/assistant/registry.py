"""The assistant's tool registry — a thin layer over the CRM tools.

Chatty's ``ToolRegistry`` dispatches many tool families by ``kind``; CakeCRM's
assistant has exactly one family (the CRM tools), so this is deliberately small.
It wraps ``crm.tools.get_crm_tools()`` — the ``(defs, executors)`` pair — and is
the SINGLE SOURCE OF TRUTH for which tools are writes: the streaming loop's
confirmation gate and the ``/confirm`` endpoint both read ``is_write`` from here,
so they can never diverge on what needs approval.

Executors are synchronous, blocking psycopg2 code. ``execute_tool_sync`` is the
real dispatch (used directly by sync endpoints); ``execute_tool`` offloads it to a
thread so a tool call never blocks the async SSE event loop.
"""

import asyncio
import logging

from crm.tools import get_crm_tools

logger = logging.getLogger(__name__)

# Internal bookkeeping keys stripped before tool defs reach a provider (providers
# only understand name/description/input_schema).
_INTERNAL_KEYS = {"kind", "writes"}


class ToolRegistry:
    def __init__(self) -> None:
        self.tool_defs, self.executors = get_crm_tools()
        self.writes_map = {t["name"]: bool(t.get("writes", False)) for t in self.tool_defs}
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
