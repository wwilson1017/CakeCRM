"""The assistant's tool registry — a composition over the feature tool sources.

Each feature exposes a ``get_*_tools() -> (defs, executors)`` pair (same shape as
``crm.tools.get_crm_tools()``); the registry concatenates an ORDERED list of them
and is the SINGLE SOURCE OF TRUTH for which tools are writes — the streaming
loop's confirmation gate, the ``/confirm`` endpoint, and the background runner all
read ``is_write`` from here, so they can never diverge.

Sources (issue #6 turned this from a single-source wrapper into a composition so
features append cleanly):
  * always: ``crm.tools.get_crm_tools()`` + ``reminders.tools.get_reminder_tools()``
  * background only (``ToolRegistry(background=True)``): ``notify_user`` — Chatty
    gates it behind background mode; interactive chat never needs it.
  * a future feature (e.g. #5 memory) appends its ``get_memory_tools()`` here.

Construction FAILS LOUD on a malformed composition — a duplicate tool name, a def
with no executor, an executor with no def, or a non-boolean ``writes`` — so a new
source can never silently clobber another feature's tool (issue #6 R13).

Executors are synchronous, blocking psycopg2 code. ``execute_tool_sync`` is the
real dispatch; ``execute_tool`` offloads it to a thread so a call never blocks the
async SSE event loop.
"""

import asyncio
import logging
from collections.abc import Callable

from crm.tools import get_crm_tools
from reminders.tools import get_reminder_tools

logger = logging.getLogger(__name__)

# Internal bookkeeping keys stripped before tool defs reach a provider (providers
# only understand name/description/input_schema).
_INTERNAL_KEYS = {"kind", "writes"}


class ToolRegistry:
    def __init__(self, *, background: bool = False) -> None:
        # Per-run flag for the one-notification-per-run guard (see notify_user).
        self._notify_user_called = False

        # Ordered tool sources — the extension point. Each is a (defs, executors)
        # pair. Features append here; second-to-land resolves keep-both.
        sources: list[tuple[list[dict], dict[str, Callable[..., dict]]]] = [
            get_crm_tools(),
            get_reminder_tools(),
        ]
        if background:
            from notifications.tools import get_notification_tools
            sources.append(get_notification_tools(self))

        self.tool_defs: list[dict] = []
        self.executors: dict[str, Callable[..., dict]] = {}
        for defs, executors in sources:
            for d in defs:
                name = d.get("name")
                if not name:
                    raise ValueError("tool def missing a name")
                if name in self.executors or any(t["name"] == name for t in self.tool_defs):
                    raise ValueError(f"duplicate tool name across sources: {name!r}")
                if not isinstance(d.get("writes"), bool):
                    raise ValueError(f"tool {name!r} must carry a boolean 'writes' flag")
                if name not in executors:
                    raise ValueError(f"tool {name!r} has a def but no executor")
                self.tool_defs.append(d)
            # Merge executors (a source may carry executor-only aliases with no def,
            # e.g. crm_log_note — legitimate; execute_tool_sync fail-closes on any
            # name not in writes_map, so an alias can never run unconfirmed).
            for name, fn in executors.items():
                if name in self.executors:
                    raise ValueError(f"duplicate executor across sources: {name!r}")
                self.executors[name] = fn

        self.writes_map = {t["name"]: bool(t.get("writes", False)) for t in self.tool_defs}
        self.descriptions = {t["name"]: t.get("description", "") for t in self.tool_defs}

    def is_write(self, name: str) -> bool:
        return self.writes_map.get(name, False)

    def provider_tools(self, tool_mode: str, allow: set[str] | None = None) -> list[dict]:
        """Tool defs to send the provider, internal keys stripped.

        In read-only mode, write tools are filtered out so the model cannot even
        name them (the engine ALSO refuses to execute a write in read-only mode —
        filtering is not the authorization boundary). When ``allow`` is given
        (background turns), only allowlisted tools are advertised — advertisement
        AND execution are both gated so an off-list tool is never even offered.
        """
        out: list[dict] = []
        for t in self.tool_defs:
            name = t["name"]
            if allow is not None and name not in allow:
                continue
            if tool_mode == "read-only" and self.writes_map.get(name, False):
                continue
            out.append({k: v for k, v in t.items() if k not in _INTERNAL_KEYS})
        return out

    def execute_tool_sync(self, name: str, args: dict | None) -> dict:
        # Fail closed: only DECLARED tools (those with a def, hence a writes flag)
        # are executable.
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
