"""The assistant's SECOND execution mode: a non-SSE background turn (issue #6).

``engine.chat`` is an SSE generator welded to conversation persistence and a
human-in-the-loop confirmation state machine — unusable for autonomous work (its
``normal`` mode parks every write behind a ``/confirm`` a background run can never
provide). ``run_background_turn`` is the counterpart: it drives ``provider.stream_turn``
directly, executes tools through the same ``ToolRegistry`` (so ``writes``/budget
semantics live in one place), and returns a ``BackgroundResult`` — no SSE, no
conversation rows (outcomes land on ``reminders.result`` / ``heartbeat_state`` /
the ``notifications`` log).

Two safety rules make auto-approved writes acceptable (the confirmation gate is
never involved here):
  * a SERVER-ENFORCED ALLOWLIST (``allowed_tools``) checked at BOTH advertisement
    and execution — background turns get read tools + ``notify_user`` (+ a tiny,
    additive-only write subset for reminder enhancement); never delete/update; and
  * a dedicated ``WRITE_BUDGET_BACKGROUND`` + a ``max_iterations`` cap.

The whole thing is wrapped in ``asyncio.wait_for`` + a catch-all so a crashed or
slow turn can never hang the scheduler thread. Sync→async bridge is a plain
``asyncio.run`` — every caller (APScheduler thread; run-now via
``asyncio.to_thread``) runs with NO event loop, which is asserted.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from assistant.write_budget import WRITE_BUDGET_BACKGROUND, BudgetAction, BudgetState
from providers import get_ai_provider

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 5
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass
class BackgroundResult:
    text: str
    error: bool = False
    tool_log: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_used: str = ""

    @property
    def no_provider(self) -> bool:
        """True when the turn was skipped for lack of an AI provider (keyless)."""
        return self.error and self.text == NO_PROVIDER_TEXT


NO_PROVIDER_TEXT = "No AI provider configured"


# ── Allowlist builders (R1) ─────────────────────────────────────────────────

def read_tool_names(registry) -> set[str]:
    """Names of all non-write tools on ``registry`` (safe in any background turn)."""
    return {name for name, is_write in registry.writes_map.items() if not is_write}


def background_allowlist(registry) -> set[str]:
    """Tools a background (autonomous) turn may use: READ tools + notify_user ONLY.

    No CRM write tools at all. The turn observes the user's data and, if warranted,
    calls notify_user once (its only externally-visible action, budgeted). This is
    a hard boundary against prompt injection via reminder/CRM text: even if the
    model were steered by injected content, the worst it can do is send one
    notification — it can never create/log/update/delete CRM records.
    """
    return read_tool_names(registry) | {"notify_user"}


# Heartbeat and reminder turns share the same (read + notify_user) boundary.
heartbeat_allowlist = background_allowlist
reminder_allowlist = background_allowlist


def _short(value, limit: int) -> str:
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


async def _run_turn(provider, registry, system_prompt, user_message: str,
                   allowed_tools: set[str], max_iterations: int,
                   write_budget_limit: int) -> BackgroundResult:
    messages = [{"role": "user", "content": user_message}]
    provider_tools = registry.provider_tools("power", allow=allowed_tools)
    budget = BudgetState(limit=write_budget_limit)
    text_out = ""
    tool_log: list[dict] = []
    in_tok = out_tok = 0

    for _ in range(max_iterations):
        turn_text = ""
        tool_calls: list[dict] = []
        stop_reason = None
        usage: dict = {}
        completed = False

        async for event in provider.stream_turn(messages, provider_tools, system_prompt):
            etype = event.get("type")
            if etype == "text":
                turn_text += event.get("text", "")
            elif etype == "error":
                return BackgroundResult(
                    text=str(event.get("error") or "provider error")[:500], error=True,
                    tool_log=tool_log, model_used=provider.model)
            elif etype == "_turn_complete":
                tool_calls = event.get("tool_calls", []) or []
                stop_reason = event.get("stop_reason")
                usage = event.get("usage") or {}
                completed = True
                break

        if not completed:
            return BackgroundResult(text="the model response ended unexpectedly", error=True,
                                    tool_log=tool_log, model_used=provider.model)

        text_out += turn_text
        in_tok += int(usage.get("input_tokens", 0) or 0)
        out_tok += int(usage.get("output_tokens", 0) or 0)

        if stop_reason != "tool_use" or not tool_calls:
            return BackgroundResult(text=text_out.strip(), tool_log=tool_log,
                                    input_tokens=in_tok, output_tokens=out_tok,
                                    model_used=provider.model)

        results: list[dict] = []
        terminated = False
        for tc in tool_calls:
            name = tc.get("name")
            tool_use_id = tc.get("id")
            args = tc.get("args", {}) or {}

            # Allowlist is the authorization boundary — fail closed.
            if name not in allowed_tools:
                result = {"error": f"Tool '{name}' is not permitted in a background run."}
            elif registry.is_write(name):
                action = budget.check_write(name)
                if action == BudgetAction.REJECT:
                    result = {"error": "Write budget exceeded; action not executed."}
                elif action == BudgetAction.TERMINATE:
                    result = {"error": "Write budget exceeded; stopping this run."}
                    terminated = True
                else:
                    result = await registry.execute_tool(name, args)
            else:
                result = await registry.execute_tool(name, args)

            content = json.dumps(result, default=str)
            results.append({"tool_use_id": tool_use_id, "tool_name": name, "content": content})
            tool_log.append({"tool": name, "args": _short(args, 200), "result": _short(result, 500)})
            if terminated:
                break

        messages = messages + provider.build_tool_turn(turn_text, tool_calls, results)
        if terminated:
            return BackgroundResult(text=text_out.strip(), error=True, tool_log=tool_log,
                                    input_tokens=in_tok, output_tokens=out_tok,
                                    model_used=provider.model)

    return BackgroundResult(text=(text_out.strip() or "(max iterations reached)"), error=True,
                            tool_log=tool_log, input_tokens=in_tok, output_tokens=out_tok,
                            model_used=provider.model)


def run_background_turn(system_prompt, user_message: str, *, allowed_tools: set[str],
                       registry=None, model_tier: str = "light",
                       max_iterations: int = DEFAULT_MAX_ITERATIONS,
                       write_budget_limit: int = WRITE_BUDGET_BACKGROUND,
                       timeout: int = DEFAULT_TIMEOUT_SECONDS) -> BackgroundResult:
    """Run one background assistant turn synchronously. NEVER raises.

    Returns ``BackgroundResult(no_provider=True)`` (text == NO_PROVIDER_TEXT) when
    no AI provider is configured — callers treat that as "skip", not failure.
    """
    try:
        provider = get_ai_provider(agent_model_tier=model_tier)
    except Exception:
        logger.warning("get_ai_provider failed in background turn", exc_info=True)
        provider = None
    if provider is None:
        return BackgroundResult(text=NO_PROVIDER_TEXT, error=True)

    if registry is None:
        from assistant.registry import ToolRegistry
        registry = ToolRegistry(background=True)

    # Sync context: all real callers run with NO event loop (APScheduler thread /
    # asyncio.to_thread). Refuse rather than deadlock if that invariant breaks.
    try:
        asyncio.get_running_loop()
        logger.error("run_background_turn invoked inside a running event loop; refusing")
        return BackgroundResult(text="background turn cannot run inside an event loop", error=True)
    except RuntimeError:
        pass  # good — no running loop

    started = time.monotonic()
    try:
        result = asyncio.run(asyncio.wait_for(
            _run_turn(provider, registry, system_prompt, user_message,
                      allowed_tools, max_iterations, write_budget_limit),
            timeout=timeout,
        ))
    except asyncio.TimeoutError:
        logger.warning("background turn timed out after %ss", timeout)
        return BackgroundResult(text=f"background turn timed out after {timeout}s",
                                error=True, model_used=getattr(provider, "model", ""))
    except Exception as e:
        logger.warning("background turn crashed: %s", e, exc_info=True)
        return BackgroundResult(text=str(e)[:500], error=True)
    logger.info("background turn done in %dms (tools=%d, error=%s)",
                int((time.monotonic() - started) * 1000), len(result.tool_log), result.error)
    return result
