"""The assistant's SECOND execution mode: a non-SSE background turn (issue #6).

``engine.chat`` is an SSE generator welded to conversation persistence and a
human-in-the-loop confirmation state machine — unusable for autonomous work (its
``normal`` mode parks every write behind a ``/confirm`` a background run can never
provide). ``run_background_turn`` is the counterpart: it drives ``provider.stream_turn``
directly, executes tools through the same ``ToolRegistry`` (so ``writes``/budget
semantics live in one place), and returns a ``BackgroundResult`` — no SSE, no
conversation rows (outcomes land on ``reminders.result`` / ``heartbeat_state`` /
the ``notifications`` log).

Two safety rules make the autonomous turn acceptable (the confirmation gate is
never involved here):
  * a SERVER-ENFORCED ALLOWLIST (``allowed_tools``) checked at BOTH advertisement
    and execution — background turns get READ tools + ``notify_user`` ONLY, no CRM
    write tools at all (so a prompt injection can at most send one notification),
    and MINUS ``BACKGROUND_EXCLUDED_TOOLS``, the live external-source reads that
    would turn that one notification into an exfiltration channel (issue #114); and
  * a dedicated ``WRITE_BUDGET_BACKGROUND`` (bounds ``notify_user``) + a
    ``max_iterations`` cap.

The whole thing is wrapped in ``asyncio.wait_for`` + a catch-all so a crashed or
slow turn can never hang the scheduler thread. Sync→async bridge is a plain
``asyncio.run`` — every caller (APScheduler thread; run-now via
``asyncio.to_thread``) runs with NO event loop, which is asserted.
"""

import asyncio
import concurrent.futures
import json
import logging
import time
from dataclasses import dataclass, field

from assistant import delimiters
from assistant.write_budget import WRITE_BUDGET_BACKGROUND, BudgetAction, BudgetState
from providers import get_ai_provider

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 5
DEFAULT_TIMEOUT_SECONDS = 120

# The provider async clients (e.g. anthropic.AsyncAnthropic → httpx.AsyncClient) are
# module-cached and BOUND to the event loop they were first used on — uvicorn's main
# loop. A background turn driven from the scheduler thread must therefore run its
# coroutine ON that same main loop (via run_coroutine_threadsafe), NOT on a throwaway
# asyncio.run loop (which would reuse a client bound to an already-closed loop and
# fail on the 2nd turn). main.py captures the loop here at startup.
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the app's main event loop (called once from main.py's lifespan)."""
    global _main_loop
    _main_loop = loop


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

# Reads that fetch third-party content LIVE from a connected external account (today
# the two Gmail reads). The interactive engine answers these with the power→normal
# taint downgrade: a human is watching, sees the nonce fence, and an unconfirmed write
# is taken off the table. An unattended turn has no analogue — nobody reads the fence,
# and its one permitted external action (notify_user → web push + Telegram) would carry
# whatever the read returned. So the documented "worst case is one notification" ceiling
# held mechanically while still being an email-exfiltration channel: hostile text in a
# reminder or CRM record could steer the turn gmail_search → gmail_read_thread → private
# mail in the notification body (issue #114).
#
# Sourced from delimiters.UNTRUSTED_SOURCE_TOOLS rather than re-listed, so the rule is
# written down ONCE and cannot drift: test_gmail_guard already pins every Gmail read
# into that set, which means a future Gmail reader is excluded here the moment it
# satisfies that existing guard.
#
# Scope: this removes LIVE mailbox access only. Sender and subject that #17's
# deterministic gmail_scan already logged into activity_log remain visible through the
# ordinary CRM reads — that is CRM data by design, and _heartbeat_prompt already tells
# the model everything a CRM tool returns is third-party text, never instructions.
BACKGROUND_EXCLUDED_TOOLS = delimiters.UNTRUSTED_SOURCE_TOOLS


def read_tool_names(registry) -> set[str]:
    """Names of all non-write tools on ``registry``.

    Not a safety verdict on its own — ``writes: False`` means "changes nothing", not
    "safe unattended". ``background_allowlist`` subtracts BACKGROUND_EXCLUDED_TOOLS
    from this before any background turn sees it.
    """
    return {name for name, is_write in registry.writes_map.items() if not is_write}


def background_allowlist(registry) -> set[str]:
    """Tools a background (autonomous) turn may use: READ tools + notify_user ONLY,
    minus the live external-source reads in ``BACKGROUND_EXCLUDED_TOOLS``.

    No CRM write tools at all. The turn observes the user's data and, if warranted,
    calls notify_user once (its only externally-visible action, budgeted). This is
    a hard boundary against prompt injection via reminder/CRM text: even if the
    model were steered by injected content, the worst it can do is send one
    notification — it can never create/log/update/delete CRM records, and (since
    #114) that notification can no longer be filled with the connected mailbox.
    """
    return (read_tool_names(registry) - BACKGROUND_EXCLUDED_TOOLS) | {"notify_user"}


# Heartbeat and reminder turns share the same (read + notify_user) boundary.
heartbeat_allowlist = background_allowlist
reminder_allowlist = background_allowlist


def _short(value, limit: int) -> str:
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


async def _run_turn(provider, registry, system_prompt, user_message: str,
                   allowed_tools: set[str], max_iterations: int,
                   write_budget_limit: int) -> BackgroundResult:
    # Clamp the CALLER-SUPPLIED set: an untrusted-source read must not run in this mode
    # even if a caller assembles its own allowlist instead of using the builder above.
    # One subtraction here covers BOTH enforcement points below, because each reads
    # `allowed_tools` — advertisement (registry.provider_tools(allow=...)) and execution
    # (the `name not in allowed_tools` check). Narrow on purpose: this pins the
    # untrusted-source exclusion only. The wider "reads + notify_user" ceiling is still
    # the caller's allowlist to declare, which is why a caller-supplied write still runs.
    allowed_tools = set(allowed_tools) - BACKGROUND_EXCLUDED_TOOLS
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

            # Fence exactly as the interactive loop does. An unattended turn has no human
            # to notice a planted instruction, and its read allowlist reaches Baker's
            # context files (Gmail is excluded outright — BACKGROUND_EXCLUDED_TOOLS) —
            # a stored `Headline:` line is attacker-authored text that must arrive as
            # DATA, not as raw JSON (issue #72).
            content = delimiters.fence_tool_result(name, json.dumps(result, default=str))
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


def _with_fence_safety(system_prompt):
    """Append the untrusted-content contract to a background turn's STATIC prompt.

    Fencing the results (see ``_run_turn``) only helps if the model has been told what a
    fence means. Each caller's prompt frames its own input — the reminder prompt covers
    reminder text, the heartbeat prompt covers CRM record text — but the background
    allowlist also reaches Baker's context files, and nothing explained those tags.
    Applied HERE rather than in each caller so a future background job cannot ship
    without it. (It still describes the external-content fence too: Gmail tools are
    excluded from background turns since #114, but CRM records can quote email, and the
    instruction is cheap insurance against a future external read being admitted.)

    Accepts either a ``(static, volatile)`` pair or a plain string, matching what
    providers take.
    """
    note = delimiters.UNTRUSTED_CONTENT_SAFETY_INSTRUCTION
    if isinstance(system_prompt, tuple) and len(system_prompt) == 2:
        static, volatile = system_prompt
        return (f"{static}\n\n{note}" if static else note), volatile
    if isinstance(system_prompt, str):
        return f"{system_prompt}\n\n{note}" if system_prompt else note
    return system_prompt


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

    loop = _main_loop
    use_main_loop = loop is not None and loop.is_running()
    if not use_main_loop:
        # Fallback path is asyncio.run — which cannot run inside an already-running
        # loop. Check BEFORE creating the coroutine so the refuse path leaves no
        # un-awaited coroutine.
        try:
            asyncio.get_running_loop()
            logger.error("run_background_turn invoked inside a running loop with no main loop set; refusing")
            return BackgroundResult(text="background turn cannot run inside an event loop", error=True)
        except RuntimeError:
            pass  # no running loop — safe to asyncio.run

    coro = asyncio.wait_for(
        _run_turn(provider, registry, _with_fence_safety(system_prompt), user_message,
                  allowed_tools, max_iterations, write_budget_limit),
        timeout=timeout,
    )
    started = time.monotonic()
    future: concurrent.futures.Future | None = None
    try:
        if use_main_loop:
            # Normal path: submit onto the main loop (where the provider client is
            # bound) from this scheduler thread and block for the result.
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            result = future.result(timeout=timeout + 30)   # backstop if the loop wedges
        else:
            # No main loop captured (tests / standalone) — run on a throwaway loop.
            # Only safe because such callers use non-loop-bound (fake) providers.
            result = asyncio.run(coro)
    except (asyncio.TimeoutError, concurrent.futures.TimeoutError):
        # Cancel the abandoned coroutine so a recovered loop can't keep running its
        # tools (e.g. a late notify_user) and overlap the next turn.
        if future is not None:
            future.cancel()
        logger.warning("background turn timed out after %ss", timeout)
        return BackgroundResult(text=f"background turn timed out after {timeout}s",
                                error=True, model_used=getattr(provider, "model", ""))
    except Exception as e:
        logger.warning("background turn crashed: %s", e, exc_info=True)
        return BackgroundResult(text=str(e)[:500], error=True)
    logger.info("background turn done in %dms (tools=%d, error=%s)",
                int((time.monotonic() - started) * 1000), len(result.tool_log), result.error)
    return result
