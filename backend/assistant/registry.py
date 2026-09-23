"""The assistant's tool registry — a composition over the feature tool sources.

Each feature exposes a ``get_*_tools() -> (defs, executors)`` pair (same shape as
``crm.tools.get_crm_tools()``); the registry concatenates an ORDERED list of them
and is the SINGLE SOURCE OF TRUTH for which tools are writes — and, since
issue #180, for which of those writes are ROUTINE (``is_routine_write``) — the streaming
loop's confirmation gate, the ``/confirm`` endpoint, and the background runner all
read ``is_write`` from here, so they can never diverge.

Sources (issue #6 turned this from a single-source wrapper into a composition so
features append cleanly):
  * always: ``crm.tools.get_crm_tools()`` + ``crm.gtd_tools.get_gtd_tools()``
  * background only (``ToolRegistry(background=True)``): ``notify_user`` — Chatty
    gates it behind background mode; interactive chat never needs it.
  * always: ``memory.tools.get_memory_tools()`` (issue #5 — long-term facts).
  * always: ``context_files.tools.get_context_file_tools(user=…)`` (issue #72 — soul.md,
    MEMORY.md, topic files and daily notes). Core, keyless, no enable gate: the SAME
    seven defs reach every registry. The seat is passed only so the write executor can
    refuse a PROTECTED filename for a non-admin (#213); it never removes a tool.
  * always: ``help.tools.get_help_tools()`` (issue #143 — the product manual's three
    read tools). Core, keyless, no enable gate; the defs are constants, so composing
    the registry never touches the disk.
  * conditional: ``gmail.tools.get_gmail_tools(user=…)`` (issue #8, seat-gated by
    #194) — defs ONLY when Gmail is connected AND the caller is an admin seat (or the
    install turned on ``share_with_all_seats``), so a disconnected/keyless instance —
    and, now, a member seat — never shows the model those tools; it returns ``([], {})``
    otherwise and never raises. It reads the connection state from Postgres, so
    constructing a registry does one DB read — the async chat endpoints build it via
    ``asyncio.to_thread``. An unattended registry (``user=None``) gets nothing at all.

Construction FAILS LOUD on a malformed composition — a duplicate tool name, a def
with no executor, an executor with no def, or a non-boolean ``writes`` — so a new
source can never silently clobber another feature's tool (issue #6 R13).

Executors are synchronous, blocking psycopg2 code. ``execute_tool_sync`` is the
real dispatch; ``execute_tool`` offloads it to a thread so a call never blocks the
async SSE event loop.

**Identity (issue #190).** ``ToolRegistry(user=…)`` is where the caller's seat enters the
tool layer. A registry is built fresh for every turn — per SSE request, per confirmation,
per Telegram message, per background run — so handing the ``get_current_user`` row to the
sources that accept it curries identity into the handful of executors that need it with
NO change to dispatch, which stays ``fn(**args)`` over the model's arguments alone. The
sources bind those arguments server-side and strip any same-named key the model produced,
so who a record is credited to is never something the model can say. ``user=None`` means
an unattended turn: every identity-bearing executor then records nobody, exactly as it did
before.

Identity changes which tools EXIST in exactly one place — the Gmail seat gate above
(#194) — and never changes a ``writes`` flag. The background allowlist is derived from
the writes map, so this can only make that allowlist SMALLER (an unattended registry
carries no Gmail tools to admit), never larger; ``assistant.background`` subtracts the
Gmail reads independently anyway, so neither lock depends on the other.

Since #213 identity also changes what ONE executor will accept — ``write_context_file``
refuses ``soul.md``/``MEMORY.md`` for a non-admin seat — which is a different thing from
changing the tool surface: the def, the name and the ``writes`` flag are identical for
every seat, so the writes map and the allowlist derived from it still cannot move.
"""

import asyncio
import logging
from collections.abc import Callable

from assistant.confirm_tier import ROUTINE
from context_files.tools import get_context_file_tools
from crm.gtd_tools import get_gtd_tools
from crm.tools import get_crm_tools
from gmail.tools import get_gmail_tools
from help.tools import get_help_tools
from memory.tools import get_memory_tools

logger = logging.getLogger(__name__)

# Internal bookkeeping keys stripped before tool defs reach a provider (providers
# only understand name/description/input_schema).
_INTERNAL_KEYS = {"kind", "writes", "confirm_tier"}


class ToolRegistry:
    def __init__(self, *, background: bool = False, user: dict | None = None) -> None:
        # Per-run flag for the one-notification-per-run guard (see notify_user).
        self._notify_user_called = False

        # The seat this registry serves (the get_current_user row), or None for an
        # unattended turn. Read by the sources below; kept on the instance so a later
        # source (per-user Telegram, the Gmail seat gate) can reach it without another
        # constructor change.
        self.user = user

        # Ordered tool sources — the extension point. Each is a (defs, executors)
        # pair. Features append here; second-to-land resolves keep-both.
        sources: list[tuple[list[dict], dict[str, Callable[..., dict]]]] = [
            get_crm_tools(user=user),
            get_gtd_tools(user=user),
            get_memory_tools(),
            get_context_file_tools(user=user),
            get_gmail_tools(user=user),
            get_help_tools(),
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
                if "confirm_tier" in d:
                    # Fail loud on a typo instead of silently reading it as "not routine".
                    # Absence already denies, so a mistyped tier can only cost a needless
                    # Approve card — but it would be an invisible cost, and the same
                    # sloppiness applied to a value that DOES exempt is how a hole lands.
                    if d["confirm_tier"] != ROUTINE:
                        raise ValueError(
                            f"tool {name!r}: confirm_tier must be {ROUTINE!r} or absent, "
                            f"got {d['confirm_tier']!r}"
                        )
                    if d["writes"] is not True:
                        raise ValueError(
                            f"tool {name!r}: confirm_tier is only valid on a write (writes: True)"
                        )
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
        # Derived once from the VALIDATED defs, so membership is the whole predicate.
        self.routine_writes: frozenset[str] = frozenset(
            t["name"] for t in self.tool_defs if t.get("confirm_tier") == ROUTINE
        )

    def is_write(self, name: str) -> bool:
        return self.writes_map.get(name, False)

    def is_routine_write(self, name: str) -> bool:
        """True only for a DECLARED write whose def carries ``confirm_tier`` ROUTINE (#180).

        Absence is the deny state: an unknown name, a read, and every write nobody
        classified all answer False — the same fail-closed shape as ``is_write``.
        """
        return name in self.routine_writes

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
