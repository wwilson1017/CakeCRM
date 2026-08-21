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

from assistant import assembly, delimiters, history, identity
from assistant.write_budget import WRITE_BUDGET_PER_TURN, BudgetAction, BudgetState
from context_files import prompt as context_prompt, tools as context_file_tools
from memory import context as memory_context
from providers.base import AIProvider, _sse
from providers.windows import context_usage_event

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 20
_VALID_MODES = {"read-only", "normal", "power"}
_UNTRUSTED_MARKER = "<untrusted_file_content"
# Tool results from untrusted EXTERNAL sources (e.g. Gmail — issue #8) are wrapped
# with this marker when recorded, so a later turn's power→normal downgrade fires on
# them exactly like uploaded-file content does.
_UNTRUSTED_EXTERNAL_MARKER = "<untrusted_external_content"
_UNTRUSTED_MARKERS = (_UNTRUSTED_MARKER, _UNTRUSTED_EXTERNAL_MARKER)
# Baker's own recorded knowledge (issue #72), fenced when a context-file read is handed
# back to the model. Deliberately NOT in _UNTRUSTED_MARKERS: that tuple drives the
# power→normal downgrade and encodes THIRD-PARTY origin (email, uploads). Context files
# are written by the user, or by the assistant under a confirmation gate, so tainting
# them would kill power mode every time Baker reads its own notes — a cost with no
# matching risk, and the same call #5 already made for memory facts.
_RECORDED_CONTEXT_MARKER = "<recorded_context"
# ...but it IS excluded from "what did the user type", because a provider that stores a
# tool result as a plain string on a user message would otherwise let file content choose
# which memories surface. Same defence _usable already applies to Gmail content.
_NON_USER_MARKERS = _UNTRUSTED_MARKERS + (_RECORDED_CONTEXT_MARKER,)
# Defined in delimiters so the background runner fences identically (issue #72).
_CONTEXT_READ_TOOLS = delimiters.CONTEXT_READ_TOOLS
# Read tools whose output is untrusted external content. Reading it must not let a
# prompt injection inside that content drive an unconfirmed write in power mode.
_UNTRUSTED_SOURCE_TOOLS = delimiters.UNTRUSTED_SOURCE_TOOLS

# Transient user turn appended on resume to keep a trailing-assistant sequence valid.
_CONTINUATION_ACK = "Please continue based on the results shown above."


def _last_user_text(messages: list[dict]) -> str | None:
    """The most recent genuine user-typed text in the assembled context, or None.

    Used to FTS-match long-term memory for this turn. Scans from the end so a normal
    turn picks the just-saved user row and a continuation picks the last real user
    message. Skips the synthetic resume ack, and — on an upload turn — the wrapped
    untrusted file blob AND any external (Gmail, #8) content, so attacker-controlled
    text never chooses which facts surface; memory matching only ever uses
    genuinely-typed text.
    """
    def _usable(text) -> bool:
        return (
            isinstance(text, str)
            and text.strip()
            and text != _CONTINUATION_ACK
            and not any(mark in text for mark in _NON_USER_MARKERS)
        )

    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            if _usable(content):
                return content
        elif isinstance(content, list):
            # Provider block-list content: when assembly coalesces a freshly-typed user
            # message onto a trailing tool_result turn (abandoned confirmation / budget
            # terminate), the new text is a `{"type":"text"}` block here, not a str.
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text" and _usable(block.get("text")):
                    return block["text"]
    return None


def _context_has_untrusted_upload(messages: list[dict]) -> bool:
    """True if any message content carries untrusted wrapped text — an uploaded
    file OR a tool result from an untrusted external source (Gmail, issue #8).

    Tool-result history is reassembled into PROVIDER-SPECIFIC shapes: Anthropic
    stores the text under a block ``content`` key, Gemini nests it under
    ``response.result``, OpenAI keeps a top-level string. Keying off one field name
    (e.g. ``text``) would miss the marker for Anthropic/Gemini, so we stringify
    non-string content and substring-scan the whole structure — provider-agnostic,
    which is what keeps the power→normal downgrade firing on later turns."""
    def _has_marker(text: str) -> bool:
        return any(marker in text for marker in _UNTRUSTED_MARKERS)

    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            if _has_marker(content):
                return True
        elif content is not None and _has_marker(str(content)):
            return True
    return False


async def chat(
    provider: AIProvider,
    registry,
    messages: list[dict],
    tool_mode: str = "normal",
    conversation_id: str | None = None,
    title_hint: str | None = None,
    context: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Stream one assistant turn as SSE.

    ``messages`` carries only the newest user message (history lives server-side).
    An EMPTY ``messages`` with a ``conversation_id`` is a *continuation* — used to
    resume after a write was approved out-of-band — and saves no new user row.

    ``context`` (the CRM record the user has open, e.g.
    ``{"record_type": "deal", "record_id": 3}``) is per-request/volatile: it is
    folded into the system prompt for this turn only and is NEVER persisted.

    Thin catch-all wrapper: the SSE response has already started (200 + bytes
    flushed), so any unexpected exception in the loop must still terminate with a
    proper ``error`` event rather than dropping the connection with no terminal
    event (which the frontend would otherwise render as a silent, successful-looking
    stop).
    """
    try:
        async for line in _chat_impl(provider, registry, messages, tool_mode, conversation_id, title_hint, context):
            yield line
    except Exception:
        logger.exception("assistant.chat crashed mid-stream")
        yield _sse({"type": "error", "error": "The assistant hit an unexpected error and stopped."})


async def _chat_impl(
    provider: AIProvider,
    registry,
    messages: list[dict],
    tool_mode: str = "normal",
    conversation_id: str | None = None,
    title_hint: str | None = None,
    context: dict | None = None,
) -> AsyncGenerator[str, None]:
    if tool_mode not in _VALID_MODES:
        tool_mode = "normal"

    ident = await asyncio.to_thread(identity.get_identity)
    provider_tools = registry.provider_tools(tool_mode)
    budget = BudgetState(limit=WRITE_BUDGET_PER_TURN)

    is_continuation = not messages

    # ── Resolve / validate the conversation, persist the user row ──────────────
    try:
        new_conversation = False
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
            new_conversation = True

        if not is_continuation:
            last = messages[-1]
            user_text = last.get("content") or ""
            user_msg_id = str(uuid.uuid4())
            # Fail-closed: if we can't record the user's message we can't assemble
            # reliable history, so don't proceed.
            await asyncio.to_thread(
                history.save_message, conversation_id, user_msg_id, "user", user_text
            )
            # Title only from the FIRST message of a brand-new conversation — never
            # rewrite an already-titled thread on every subsequent message.
            if new_conversation:
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

    # Uploaded-document text is the untrusted-content channel. The upload endpoint
    # downgrades power→normal for the turn a file is attached, but the file text
    # stays in context, so a LATER turn (a continuation after approval, or the next
    # message) in power mode could still auto-execute a write the model proposed
    # from injected instructions. Enforce it here for EVERY turn: if the assembled
    # context carries untrusted upload content, writes route through confirmation
    # regardless of the client-selected mode.
    if tool_mode == "power" and _context_has_untrusted_upload(current_messages):
        logger.info("assistant.chat: untrusted upload content present — forcing normal mode")
        tool_mode = "normal"

    # A resumed (continuation) turn can end on an assistant row — the persisted
    # pending-confirmation wrap-up narration ("Shall I create X?") is saved as its
    # own assistant message, so after an out-of-band approval the rebuilt sequence
    # is [… assistant(tool_use), tool_result, assistant(narration)]. Providers
    # reject / mis-prefill a trailing assistant turn (Anthropic would continue the
    # narration; Gemini rejects it), so append a transient user ack to keep the
    # sequence valid. A normal turn always ends on the just-saved user row, so this
    # only fires on resume.
    if current_messages[-1].get("role") == "assistant":
        current_messages = current_messages + [{
            "role": "user",
            "content": _CONTINUATION_ACK,
        }]

    # Surface long-term memory into THIS turn's prompt (issue #5 acceptance clause 1).
    # Done AFTER validation + assembly so a rejected/failed turn never accrues retrieval
    # usage, and matched against the user's genuine text. The facts ride the volatile
    # half of the prompt (never the cached static block); build_memory_context never
    # raises, so a memory outage degrades to a memory-less turn.
    memory_block = await asyncio.to_thread(
        memory_context.build_memory_context, _last_user_text(current_messages)
    )
    # Baker's context files (issue #72), loaded the same way. Split by trust, not by
    # file: the soul is a stable unfenced document and rides the cacheable STATIC half,
    # while MEMORY.md and the manifests are freshly nonce-fenced every turn and so MUST
    # ride the volatile half — a fresh nonce in the static block would re-key Anthropic's
    # prompt cache on every single turn. Neither builder raises.
    soul_block, knowledge_block = await asyncio.gather(
        asyncio.to_thread(context_prompt.build_soul_block),
        asyncio.to_thread(context_prompt.build_knowledge_block),
    )
    # ONE unified pre-loop build: the record context (#14), the memory block (#5) and the
    # context-file blocks (#72) are all per-turn injections — never assembled/persisted
    # messages. ALL kwargs must be passed here; dropping any silently loses that
    # feature's injection.
    # This same system_prompt feeds the main loop and the confirmation wrap-up turn.
    system_prompt = identity.build_system_prompt(
        ident, context=context, memory_context=memory_block,
        soul=soul_block, knowledge_context=knowledge_block,
    )

    # ── Main tool-execution loop ───────────────────────────────────────────────
    iteration = 0
    # Set once an untrusted-external read (e.g. Gmail) runs during THIS turn; from
    # then on, power-mode writes route through confirmation (issue #8). Prior-turn
    # untrusted content already downgraded tool_mode above.
    turn_has_untrusted_reads = False
    while iteration < MAX_ITERATIONS:
        iteration += 1
        turn_text = ""
        tool_calls: list[dict] = []
        stop_reason: str | None = None
        usage: dict = {}
        completed = False

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
                yield _sse({"type": "error", "error": str(event.get("error") or "Provider error")[:500]})
                return
            elif etype == "_turn_complete":
                tool_calls = event.get("tool_calls", []) or []
                stop_reason = event.get("stop_reason")
                usage = event.get("usage") or {}
                completed = True
                break

        # A stream that ended without _turn_complete died mid-turn — never report it
        # as a clean `done`.
        if not completed:
            yield _sse({"type": "error", "error": "The model response ended unexpectedly."})
            return

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

            # Confirmation gate: normal mode always confirms writes; power mode
            # confirms them too once an untrusted external read (Gmail) has run this
            # turn, so injected instructions in that content can't auto-execute a
            # write (issue #8). Persist the pending placeholder BEFORE emitting
            # confirm (so /confirm can find it), then wait for approval.
            # A write to a PROTECTED context file (soul.md / MEMORY.md) confirms in every
            # mode, power included (issue #72). `writes: True` alone is not enough there:
            # a poisoned soul is not one bad record, it is a permanent system instruction
            # replayed on every later turn — including background ones — that survives
            # deleting the conversation. Same shape as the Gmail binding check below.
            always_confirms = context_file_tools.requires_confirmation(name, args)
            if is_write and (
                always_confirms
                or tool_mode == "normal"
                or (tool_mode == "power" and turn_has_untrusted_reads)
            ):
                placeholder = await _pending_placeholder(name)
                try:
                    await asyncio.to_thread(
                        history.merge_tool_result, iter_msg_id, tool_use_id, name,
                        placeholder,
                    )
                except Exception as e:
                    logger.warning("assistant.chat: failed to persist pending action: %s", e)
                    yield _sse({"type": "error", "error": "Failed to save the pending action."})
                    return
                results.append({"tool_use_id": tool_use_id, "tool_name": name, "content": placeholder})
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
            content = json.dumps(result, default=str)
            # An untrusted external read (Gmail) taints the rest of the turn and, via
            # the nonce-fenced marker persisted below, later turns too — so a prompt
            # injection in the email can't silently drive a power-mode write. The
            # nonce fence (delimiters.wrap_untrusted_external) is forge-proof and the
            # paired system-prompt instruction tells the model to treat it as data.
            if name in _UNTRUSTED_SOURCE_TOOLS:
                turn_has_untrusted_reads = True
                content = delimiters.wrap_untrusted_external(name, content)
            # A context-file read hands back a whole document Baker (or the user) wrote
            # earlier, which may quote an email or an upload. Fence it as DATA for the
            # same reason the prompt-injected copy is fenced (issue #72) — but do NOT
            # taint the turn: see _RECORDED_CONTEXT_MARKER.
            elif name in _CONTEXT_READ_TOOLS:
                content = delimiters.wrap_recorded_context(content)
            results.append({"tool_use_id": tool_use_id, "tool_name": name, "content": content})
            persisted = True
            try:
                await asyncio.to_thread(history.merge_tool_result, iter_msg_id, tool_use_id, name, content)
            except Exception as e:
                persisted = False
                logger.warning("assistant.chat: failed to persist tool result for %s: %s", name, e)
            yield _sse({
                "type": "tool_end", "tool": name, "tool_use_id": tool_use_id,
                "result": result, "elapsed_ms": elapsed_ms,
            })
            if not persisted and (is_write or name in _UNTRUSTED_SOURCE_TOOLS):
                # Fail closed when the result couldn't be recorded, for either of two
                # reasons: (a) a write executed but its result is unrecorded (a later
                # rebuild would show the stub and tempt the model to redo the
                # mutation), or (b) an untrusted external read (Gmail) whose taint
                # marker didn't persist — a later turn would then miss the
                # power→normal downgrade and could auto-execute an injected write.
                yield _sse({"type": "error", "error": "The result could not be fully saved — please reload the conversation."})
                return

        # Rebuild history for the next turn using build_tool_turn (keeps the
        # assistant text; add_tool_results would drop it).
        current_messages = current_messages + provider.build_tool_turn(turn_text, tool_calls, results)

        if terminated:
            yield _sse({"type": "done", "model": provider.model})
            return

        if has_pending:
            # One narration wrap-up turn so the model can say what it's about to do.
            # Pass provider_tools (NOT []): current_messages carries the just-added
            # tool_use/tool_result blocks, and Anthropic REJECTS tool_use/tool_result
            # history when no `tools` param is defined (empty list → NOT_GIVEN → 400).
            # We still ignore any tool_start/tool_args and discard tool_calls below,
            # so the turn stays narration-only.
            wrap_text = ""
            wrap_completed = False
            async for event in provider.stream_turn(current_messages, provider_tools, system_prompt):
                etype = event.get("type")
                if etype == "text":
                    wrap_text += event.get("text", "")
                    yield _sse({"type": "text", "text": event.get("text", "")})
                elif etype == "error":
                    yield _sse({"type": "error", "error": str(event.get("error") or "Provider error")[:500]})
                    return
                elif etype == "_turn_complete":
                    wu = context_usage_event(event.get("usage") or {}, getattr(provider, "context_window", None), meter_only=True)
                    if wu:
                        yield _sse(wu)
                    wrap_completed = True
                    break
                # stray tool_start/tool_args ignored — the wrap-up is narration-only
                # even though the toolset is passed (needed for Anthropic; see above)
            if not wrap_completed:
                yield _sse({"type": "error", "error": "The model response ended unexpectedly."})
                return
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


# Writes whose target can change between propose and approve, so the pending
# confirmation must be bound to what was live when it was proposed. Only Gmail
# qualifies today: a draft approved minutes later would otherwise land in whatever
# Google account happens to be connected then (#43). Hand-maintained by name, the
# same shape as _UNTRUSTED_SOURCE_TOOLS above.
_CONNECTION_BOUND_WRITE_TOOLS = frozenset({"gmail_create_draft"})


async def _pending_placeholder(tool_name: str) -> str:
    """The pending-approval result to persist for a gated write.

    Plain PENDING_RESULT_JSON, except for a connection-bound write, which also
    carries the identity of the connection it was proposed against. Extra keys are
    safe: history's status helpers read only "status". The binding read touches
    Postgres, so it is offloaded — this runs on the SSE event loop.
    """
    if tool_name not in _CONNECTION_BOUND_WRITE_TOOLS:
        return history.PENDING_RESULT_JSON
    from gmail import tools as gmail_tools  # lazy: keeps gmail out of engine import

    binding = await asyncio.to_thread(gmail_tools.pending_binding)
    if not binding:
        return history.PENDING_RESULT_JSON
    return json.dumps({"status": history.PENDING_STATUS, **binding})


def _binding_conflict(tool: str, pending_content: str | None) -> dict | None:
    """An error result when a connection-bound write's target changed since it was
    proposed, else None. Never raises — the call is already claimed and marked
    executing, so raising here would strand the confirmation."""
    if tool not in _CONNECTION_BOUND_WRITE_TOOLS:
        return None
    from gmail import tools as gmail_tools  # lazy: see _pending_placeholder

    # Accept an already-decoded mapping as well as the JSON string the history layer
    # stores, so a future change in how the placeholder is deserialized can't make
    # this check silently fail open (it would parse to nothing and find no binding).
    if isinstance(pending_content, dict):
        parsed = pending_content
    elif isinstance(pending_content, str) and pending_content:
        try:
            parsed = json.loads(pending_content)
        except ValueError:
            return None
    else:
        return None
    return gmail_tools.binding_conflict(parsed if isinstance(parsed, dict) else {})


def resolve_confirmation(registry, conversation_id: str, tool_use_id: str, decision: str,
                         msg_id: str | None = None) -> dict:
    """Approve or deny a pending write — server-authoritative and idempotent.

    Atomically claims the pending call (loading its canonical tool + args from the
    DB, NOT from the client — ``msg_id`` disambiguates positional-id providers like
    Gemini), executes it (approve) or records a denial (deny), and merges the result
    onto the persisted iteration. A second call for an already-resolved call is a
    no-op (``already_resolved``), so a double-click or replay can never execute a
    write twice. Synchronous — the router runs it in a worker thread.

    Durability note (deliberate): the claim marks the result ``executing`` and
    commits BEFORE the tool runs, so a crash between claim and result-merge leaves
    a recoverable ``executing`` marker rather than re-running the write. This trades
    a rare, inspectable stuck state for a guarantee of NO double-execution — the
    safer failure mode for non-idempotent CRM mutations (create/delete). Full
    exactly-once recovery would need idempotency keys on the CRM ops (future work).
    """
    claimed = history.claim_pending_tool(conversation_id, tool_use_id, msg_id=msg_id)
    if claimed is None:
        # Already resolved (or executing) by a prior call — return the CANONICAL
        # persisted outcome so the client reflects reality instead of assuming its
        # own click won an approve/deny race.
        existing = history.get_tool_result(conversation_id, tool_use_id, msg_id=msg_id)
        return {"status": "already_resolved", "result": existing}
    claimed_msg_id, tool, args = claimed["msg_id"], claimed["tool"], claimed["args"]
    # Defense-in-depth: only a write should ever have been marked pending. If a
    # non-write somehow got here (a future bug in the gate), refuse rather than
    # execute an unconfirmed action — is_write is the single source of truth.
    if decision == "approve" and not registry.is_write(tool):
        result = {"error": "Not a confirmable write action."}
    elif decision == "approve":
        # A connection-bound write must still be aimed at what it was proposed
        # against. simplification: checked here rather than inside the executor, so
        # a millisecond-scale check→execute window remains — acceptable for
        # single-user v1, where the admin is the only actor; closing it fully would
        # thread the binding through every executor signature.
        conflict = _binding_conflict(tool, claimed.get("content"))
        result = conflict if conflict is not None else registry.execute_tool_sync(tool, args)
    else:  # deny
        result = {"status": history.DENIED_STATUS}
    history.merge_tool_result(claimed_msg_id, tool_use_id, tool, json.dumps(result, default=str))
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
