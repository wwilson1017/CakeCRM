"""The assistant's streaming chat loop.

An async generator that drives a provider-agnostic tool-execution loop over the
``AIProvider`` ABC and yields SSE-formatted strings. Ported and slimmed from
Chatty's ``core/agents/ai_service.chat()`` — single assistant, one tool family,
no training/plan/roster/Telegram machinery.

Per turn: call ``provider.stream_turn`` → forward ``text``/``tool_start``/
``tool_args`` to the browser → intercept the internal ``_turn_complete`` →
persist the iteration → either finish (``done``) or run the requested tools and
loop with ``build_tool_turn`` (which, unlike ``add_tool_results``, keeps the
assistant text so the in-request history matches what the DB assembler rebuilds).

Confirmation modes:
  read-only — write tools are hidden from the provider AND refused if named.
  normal    — a write is NOT executed; a ``confirm`` event is emitted and the
              pending call is persisted; the user approves it out-of-band via
              ``POST /confirm`` (server-authoritative, idempotent), then the
              client re-POSTs an empty-``messages`` continuation to resume.
  power     — writes execute immediately.

All Postgres work is offloaded with ``asyncio.to_thread`` so a blocking pooled
connection never stalls the event loop / other concurrent SSE streams.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator

from assistant import assembly, history, identity
from assistant.write_budget import WRITE_BUDGET_PER_TURN, BudgetAction, BudgetState
from providers.base import AIProvider, _sse
from providers.windows import context_usage_event

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 20
_VALID_MODES = {"read-only", "normal", "power"}


async def chat(
    provider: AIProvider,
    registry,
    messages: list[dict],
    tool_mode: str = "normal",
    conversation_id: str | None = None,
    title_hint: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream one assistant turn as SSE.

    ``messages`` carries only the newest user message (history lives server-side).
    An EMPTY ``messages`` with a ``conversation_id`` is a *continuation* — used to
    resume after a write was approved out-of-band — and saves no new user row.
    """
    if tool_mode not in _VALID_MODES:
        tool_mode = "normal"

    ident = await asyncio.to_thread(identity.get_identity)
    system_prompt = identity.build_system_prompt(ident)
    provider_tools = registry.provider_tools(tool_mode)
    budget = BudgetState(limit=WRITE_BUDGET_PER_TURN)

    is_continuation = not messages

    # ── Resolve / validate the conversation, persist the user row ──────────────
    try:
        if conversation_id:
            if not await asyncio.to_thread(history.conversation_exists, conversation_id):
                yield _sse({"type": "error", "error": "Conversation not found."})
                return
        else:
            if is_continuation:
                yield _sse({"type": "error", "error": "Cannot continue without a conversation."})
                return
            conv = await asyncio.to_thread(history.create_conversation)
            conversation_id = conv["id"]

        if not is_continuation:
            last = messages[-1]
            user_text = last.get("content") or ""
            user_msg_id = str(uuid.uuid4())
            # Fail-closed: if we can't record the user's message we can't assemble
            # reliable history, so don't proceed.
            await asyncio.to_thread(
                history.save_message, conversation_id, user_msg_id, "user", user_text
            )
            await asyncio.to_thread(
                history.auto_title, conversation_id, (title_hint or user_text)
            )
    except Exception as e:  # DB down / unknown conversation — fail loud, never half-run
        logger.warning("assistant.chat setup failed: %s", e)
        yield _sse({"type": "error", "error": "Could not start the assistant turn."})
        return

    yield _sse({"type": "conversation_id", "id": conversation_id})

    current_messages = await asyncio.to_thread(assembly.assemble_messages, provider, conversation_id)
    if not current_messages:
        yield _sse({"type": "error", "error": "No conversation content to send."})
        return

    # ── Main tool-execution loop ───────────────────────────────────────────────
    iteration = 0
    while iteration < MAX_ITERATIONS:
        iteration += 1
        turn_text = ""
        tool_calls: list[dict] = []
        stop_reason: str | None = None
        usage: dict = {}

        async for event in provider.stream_turn(current_messages, provider_tools, system_prompt):
            etype = event.get("type")
            if etype == "text":
                turn_text += event.get("text", "")
                yield _sse({"type": "text", "text": event.get("text", "")})
            elif etype == "tool_start":
                yield _sse({"type": "tool_start", "tool": event.get("tool"), "tool_use_id": event.get("tool_use_id")})
            elif etype == "tool_args":
                yield _sse({
                    "type": "tool_args",
                    "tool": event.get("tool"),
                    "tool_use_id": event.get("tool_use_id"),
                    "args": event.get("args", {}),
                    "description": registry.descriptions.get(event.get("tool"), ""),
                })
            elif etype == "error":
                yield _sse({"type": "error", "error": event.get("error", "Provider error")})
                return
            elif etype == "_turn_complete":
                tool_calls = event.get("tool_calls", []) or []
                stop_reason = event.get("stop_reason")
                usage = event.get("usage") or {}
                break

        ue = context_usage_event(usage, getattr(provider, "context_window", None))
        if ue:
            yield _sse(ue)

        # Persist this iteration. Fail CLOSED when it carries tool calls — never
        # execute or confirm a tool we couldn't record (the confirm flow keys off
        # this row's id).
        iter_msg_id = str(uuid.uuid4())
        persisted_calls = [
            {"tool": tc.get("name"), "tool_use_id": tc.get("id"), "args": tc.get("args", {})}
            for tc in tool_calls
        ] or None
        if turn_text or persisted_calls:
            try:
                await asyncio.to_thread(
                    history.save_message, conversation_id, iter_msg_id, "assistant",
                    turn_text, persisted_calls, provider.model,
                )
            except Exception as e:
                if persisted_calls:
                    logger.warning("assistant.chat: failed to persist tool iteration: %s", e)
                    yield _sse({"type": "error", "error": "Failed to save the assistant turn."})
                    return
                logger.warning("assistant.chat: failed to persist text response: %s", e)  # best-effort

        if stop_reason != "tool_use" or not tool_calls:
            yield _sse({"type": "done", "model": provider.model})
            return

        # ── Execute / gate each tool call ──────────────────────────────────────
        results: list[dict] = []
        has_pending = False
        terminated = False

        for tc in tool_calls:
            name = tc.get("name")
            tool_use_id = tc.get("id")
            args = tc.get("args", {}) or {}
            is_write = registry.is_write(name)

            # Read-only is an authorization boundary, not just tool-hiding.
            if tool_mode == "read-only" and is_write:
                async for line in _record_result(
                    conversation_id, iter_msg_id, name, tool_use_id, results,
                    {"error": "Write tools are disabled in read-only mode."}, 0,
                ):
                    yield line
                continue

            # Per-turn write budget backstop (writes only, all modes).
            if is_write:
                action = budget.check_write(name)
                if action == BudgetAction.REJECT:
                    async for line in _record_result(
                        conversation_id, iter_msg_id, name, tool_use_id, results,
                        {"error": "Write budget exceeded for this turn; action not executed."}, 0,
                    ):
                        yield line
                    continue
                if action == BudgetAction.TERMINATE:
                    async for line in _record_result(
                        conversation_id, iter_msg_id, name, tool_use_id, results,
                        {"error": "Write budget exceeded; stopping this turn."}, 0,
                    ):
                        yield line
                    terminated = True
                    break

            # Normal-mode confirmation gate: persist the pending placeholder BEFORE
            # emitting confirm (so /confirm can find it), then wait for approval.
            if tool_mode == "normal" and is_write:
                try:
                    await asyncio.to_thread(
                        history.merge_tool_result, iter_msg_id, tool_use_id, name,
                        history.PENDING_RESULT_JSON,
                    )
                except Exception as e:
                    logger.warning("assistant.chat: failed to persist pending action: %s", e)
                    yield _sse({"type": "error", "error": "Failed to save the pending action."})
                    return
                results.append({"tool_use_id": tool_use_id, "tool_name": name, "content": history.PENDING_RESULT_JSON})
                has_pending = True
                yield _sse({
                    "type": "confirm", "tool": name, "args": args,
                    "tool_use_id": tool_use_id, "msg_id": iter_msg_id,
                    "description": registry.descriptions.get(name, name),
                })
                continue

            # Execute (reads in any mode; writes in power mode).
            t0 = time.monotonic()
            result = await registry.execute_tool(name, args)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            async for line in _record_result(
                conversation_id, iter_msg_id, name, tool_use_id, results, result, elapsed_ms,
            ):
                yield line

        # Rebuild history for the next turn using build_tool_turn (keeps the
        # assistant text; add_tool_results would drop it).
        current_messages = current_messages + provider.build_tool_turn(turn_text, tool_calls, results)

        if terminated:
            yield _sse({"type": "done", "model": provider.model})
            return

        if has_pending:
            # One narration-only wrap-up turn (tools=[]) so the model can say what
            # it's about to do; it cannot emit new tool calls here.
            wrap_text = ""
            async for event in provider.stream_turn(current_messages, [], system_prompt):
                etype = event.get("type")
                if etype == "text":
                    wrap_text += event.get("text", "")
                    yield _sse({"type": "text", "text": event.get("text", "")})
                elif etype == "error":
                    yield _sse({"type": "error", "error": event.get("error", "Provider error")})
                    return
                elif etype == "_turn_complete":
                    wu = context_usage_event(event.get("usage") or {}, getattr(provider, "context_window", None), meter_only=True)
                    if wu:
                        yield _sse(wu)
                    break
                # stray tool_start/tool_args ignored — tools=[] means none expected
            if wrap_text.strip():
                try:
                    await asyncio.to_thread(
                        history.save_message, conversation_id, str(uuid.uuid4()),
                        "assistant", wrap_text, None, provider.model,
                    )
                except Exception as e:
                    logger.warning("assistant.chat: failed to persist wrap-up text: %s", e)  # best-effort
            yield _sse({"type": "done", "model": provider.model})
            return

        # else: loop again with the tool results in context.

    yield _sse({"type": "error", "error": "Tool loop exceeded maximum iterations."})


def resolve_confirmation(registry, conversation_id: str, tool_use_id: str, decision: str) -> dict:
    """Approve or deny a pending write — server-authoritative and idempotent.

    Atomically claims the pending call (loading its canonical tool + args from the
    DB, NOT from the client), executes it (approve) or records a denial (deny), and
    merges the result onto the persisted iteration. A second call for an
    already-resolved tool_use_id is a no-op (``already_resolved``), so a
    double-click or replay can never execute a write twice. Synchronous — the
    router runs it in a worker thread.
    """
    claimed = history.claim_pending_tool(conversation_id, tool_use_id)
    if claimed is None:
        return {"status": "already_resolved"}
    msg_id, tool, args = claimed["msg_id"], claimed["tool"], claimed["args"]
    if decision == "approve":
        result = registry.execute_tool_sync(tool, args)
    else:  # deny
        result = {"status": history.DENIED_STATUS}
    history.merge_tool_result(msg_id, tool_use_id, tool, json.dumps(result, default=str))
    return {"tool": tool, "decision": decision, "result": result}


async def _record_result(
    conversation_id: str, iter_msg_id: str, name: str, tool_use_id: str,
    results: list[dict], result: dict, elapsed_ms: int,
) -> AsyncGenerator[str, None]:
    """Append a tool result in-memory, persist it (per-entry merge), and emit tool_end."""
    content = json.dumps(result, default=str)
    results.append({"tool_use_id": tool_use_id, "tool_name": name, "content": content})
    try:
        await asyncio.to_thread(history.merge_tool_result, iter_msg_id, tool_use_id, name, content)
    except Exception as e:
        logger.warning("assistant.chat: failed to persist tool result for %s: %s", name, e)
    yield _sse({
        "type": "tool_end", "tool": name, "tool_use_id": tool_use_id,
        "result": result, "elapsed_ms": elapsed_ms,
    })
