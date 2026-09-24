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
  normal    — a write is NOT executed unless its def declares ``confirm_tier``
              ROUTINE (issue #180); otherwise a ``confirm`` event is emitted and
              the pending call is persisted; the user approves it out-of-band via
              ``POST /confirm`` (server-authoritative, idempotent), then the
              client re-POSTs an empty-``messages`` continuation to resume.
  power     — writes execute immediately.

Untrusted content in play — an uploaded document, a Gmail read, this turn or an
earlier one — makes EVERY write confirm in EVERY mode, routine ones included.

All Postgres work is offloaded with ``asyncio.to_thread`` so a blocking pooled
connection never stalls the event loop / other concurrent SSE streams.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator

from assistant import assembly, compaction, confirm_tier, delimiters, history, identity
from assistant.write_budget import WRITE_BUDGET_PER_TURN, BudgetAction, BudgetState
from context_files import prompt as context_prompt, tools as context_file_tools
from memory import context as memory_context
from providers.base import AIProvider, _sse
from providers.windows import cache_inclusive_input_tokens, context_usage_event

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 20
_VALID_MODES = {"read-only", "normal", "power"}
_UNTRUSTED_MARKER = delimiters.UNTRUSTED_FILE_MARKER
# Tool results from untrusted EXTERNAL sources (e.g. Gmail — issue #8) are wrapped
# with this marker when recorded, so a later turn's untrusted-context confirmation fires on
# them exactly like uploaded-file content does. Both literals moved to `delimiters`
# with #72 Phase 3, which needs the same test one layer down.
_UNTRUSTED_EXTERNAL_MARKER = delimiters.UNTRUSTED_EXTERNAL_MARKER
_UNTRUSTED_MARKERS = delimiters.UNTRUSTED_MARKERS
# Baker's own recorded knowledge (issue #72), fenced when a context-file read is handed
# back to the model. Deliberately NOT in _UNTRUSTED_MARKERS: that tuple drives the
# untrusted-context confirmation and encodes THIRD-PARTY origin (email, uploads). Context files
# are written by the user, or by the assistant under a confirmation gate, so tainting
# them would kill power mode every time Baker reads its own notes — a cost with no
# matching risk, and the same call #5 already made for memory facts.
_RECORDED_CONTEXT_MARKER = "<recorded_context"
# ...but it IS excluded from "what did the user type", because a provider that stores a
# tool result as a plain string on a user message would otherwise let file content choose
# which memories surface. Same defence _usable already applies to Gmail content.
_NON_USER_MARKERS = _UNTRUSTED_MARKERS + (_RECORDED_CONTEXT_MARKER,)
# Both sets live in delimiters, which owns the fencing for BOTH loops (issue #72) and
# since #204 decides the taint too — `fence_tool_result` reads them, not this module. They
# are re-exported here because two coupling guards read them through the engine
# (test_gmail_guard pins every Gmail read into the first; test_context_files_security pins
# every context read into the second), and those guards are the reason a new
# attacker-controlled read cannot land un-fenced.
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
    def _typed(text):
        """The genuinely-typed remainder of a user message, or None.

        A compaction gist (#72 Phase 3) is folded ONTO a retained user turn, and when a
        thread is dominated by old content that turn is the CURRENT one — so the newest
        user message can arrive as "gist + what they actually typed". Rejecting it for
        carrying a marker would silently fall back to the conversation's FIRST message
        and match memory on the wrong words, so the gist block is removed and the rest
        kept. Everything else still disqualifies the whole message.
        """
        if not isinstance(text, str):
            return None
        text = delimiters.strip_conversation_summary(text)
        usable = (
            text.strip()
            and text != _CONTINUATION_ACK
            and not any(mark in text for mark in _NON_USER_MARKERS)
        )
        return text if usable else None

    def _usable(text) -> bool:
        return _typed(text) is not None

    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            typed = _typed(content)
            if typed is not None:
                return typed
        elif isinstance(content, list):
            # Provider block-list content: when assembly coalesces a freshly-typed user
            # message onto a trailing tool_result turn (abandoned confirmation / budget
            # terminate), the new text is a `{"type":"text"}` block here, not a str.
            for block in reversed(content):
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                typed = _typed(block.get("text"))
                if typed is not None:
                    return typed
    return None


def _context_has_untrusted_upload(messages: list[dict]) -> bool:
    """True if any message content carries untrusted wrapped text — an uploaded
    file OR a tool result from an untrusted external source (Gmail, issue #8).

    Tool-result history is reassembled into PROVIDER-SPECIFIC shapes: Anthropic
    stores the text under a block ``content`` key, Gemini nests it under
    ``response.result``, OpenAI keeps a top-level string. Keying off one field name
    (e.g. ``text``) would miss the marker for Anthropic/Gemini, so we stringify
    non-string content and substring-scan the whole structure — provider-agnostic,
    which is what keeps the untrusted-context confirmation firing on later turns."""
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
    page: dict | None = None,
    *,
    user: dict | None,
) -> AsyncGenerator[str, None]:
    """Stream one assistant turn as SSE.

    ``messages`` carries only the newest user message (history lives server-side).
    An EMPTY ``messages`` with a ``conversation_id`` is a *continuation* — used to
    resume after a write was approved out-of-band — and saves no new user row.

    ``context`` (the CRM record the user has open, e.g.
    ``{"record_type": "deal", "record_id": 3}``) is per-request/volatile: it is
    folded into the system prompt for this turn only and is NEVER persisted.

    ``page`` (the settings section the user has open, e.g.
    ``{"page": "settings", "section": "integrations"}``) is a SEPARATE input with the
    same lifetime and the same rules (#200) — validated as an enum pair at the router,
    rendered into English server-side, volatile, never persisted. Separate rather than
    folded into ``context`` so the record boundary stays exactly what it is, and because
    a turn can carry both.

    ``user`` is the seat this turn belongs to — keyword-only and REQUIRED (issue #191),
    because it decides which conversations the caller may resume and who a new one is
    owned by. A defaulted ``None`` would let a future route forget it and silently
    resume anyone's thread, so trusted seatless callers (the Telegram poller until B4)
    must pass ``user=None`` explicitly.

    Thin catch-all wrapper: the SSE response has already started (200 + bytes
    flushed), so any unexpected exception in the loop must still terminate with a
    proper ``error`` event rather than dropping the connection with no terminal
    event (which the frontend would otherwise render as a silent, successful-looking
    stop).
    """
    try:
        async for line in _chat_impl(provider, registry, messages, tool_mode, conversation_id, title_hint, context, page, user):
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
    page: dict | None = None,
    user: dict | None = None,
) -> AsyncGenerator[str, None]:
    if tool_mode not in _VALID_MODES:
        tool_mode = "normal"

    ident = await asyncio.to_thread(identity.get_identity)
    provider_tools = registry.provider_tools(tool_mode)
    budget = BudgetState(limit=WRITE_BUDGET_PER_TURN)

    is_continuation = not messages
    # The seat every conversation lookup below is scoped to. None = a trusted seatless
    # caller (the Telegram poller), which sees the conversation it was handed (#191).
    # Indexed, not ``.get``: a user row without an id is a bug, and answering it with
    # None would silently promote that seat to the unfiltered trusted path. The KeyError
    # reaches ``chat``'s catch-all and ends the turn, which is the safe direction.
    user_id = user["id"] if isinstance(user, dict) else None

    # ── Resolve / validate the conversation, persist the user row ──────────────
    try:
        new_conversation = False
        if conversation_id:
            # Scoped to the caller's seat, so ANOTHER seat's conversation is refused
            # with the identical response an unknown uuid gets — a resume is the third
            # door into a conversation and the REST filters would be decoration without
            # it (#191). Refusing here, before any write, is what keeps a rejected
            # resume from saving a message row or auto-titling someone else's thread.
            if not await asyncio.to_thread(
                history.conversation_exists, conversation_id, user_id=user_id
            ):
                yield _sse({"type": "error", "error": "Conversation not found."})
                return
        else:
            if is_continuation:
                yield _sse({"type": "error", "error": "Cannot continue without a conversation."})
                return
            conv = await asyncio.to_thread(history.create_conversation, user_id=user_id)
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
            # Durable half of the untrusted-content taint (#72 Phase 3). The scan below
            # reads the ASSEMBLED context, which compaction can empty of this row; the
            # flag is what keeps the untrusted-context confirmation firing afterwards. Written
            # here, unconditionally, rather than beside that scan — which only runs in
            # power mode, so a file uploaded during a normal-mode turn would otherwise
            # never be recorded and would go unnoticed after the thread compacts.
            if delimiters.UNTRUSTED_FILE_MARKER in user_text:
                await asyncio.to_thread(history.mark_untrusted_seen, conversation_id)
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

    # Bound the thread BEFORE assembling it: a long conversation used to grow until the
    # provider rejected the whole request. Never raises, and on any failure (no light
    # tier, a timeout, a summarizer that answers nothing) it returns False and the turn
    # assembles uncompacted, exactly as it did before #72 Phase 3.
    await compaction.maybe_compact(provider, conversation_id)

    # The compaction boundary this turn assembles against. Every usage reading reported
    # below is stamped with it, so a reading produced under an OLDER boundary — this
    # turn finishing after a CONCURRENT turn compacted the same thread — is rejected
    # rather than taken as the current fullness (history.save_message).
    #
    # Read BEFORE assembling, deliberately. A compaction landing in the gap then makes
    # our stamp older than what we actually assembled, and the reading is dropped: one
    # lost meter reading, and compaction falls back to the row estimate. Reading it
    # AFTER would make the stamp newer than the context and let exactly the stale
    # reading this exists to catch pass as current.
    comp_state = await asyncio.to_thread(history.get_compaction_state, conversation_id)
    context_boundary_seq = (comp_state or {}).get("first_kept_seq")

    current_messages = await asyncio.to_thread(assembly.assemble_messages, provider, conversation_id)
    if not current_messages:
        yield _sse({"type": "error", "error": "No conversation content to send."})
        return

    # Uploaded-document text is the untrusted-content channel, and it outlives the turn
    # it arrived on: the file text stays in context, so a LATER turn (a continuation
    # after approval, or the next message) could still auto-execute a write the model
    # proposed from injected instructions. So: if the assembled context carries
    # untrusted content, EVERY write this turn routes through confirmation, in every
    # mode and routine tier included (#180) — the client-selected mode does not matter.
    #
    # This used to be expressed by demoting power→normal, which was only correct while
    # "normal" meant "confirm everything". Since #180 it does not, so the signal is its
    # own boolean and reaches the gate directly instead of riding `tool_mode`. Demoting
    # into a mode that auto-approves would have switched the mitigation off silently.
    #
    # The second half is what keeps this true after compaction: the scan reads the
    # ASSEMBLED context, and compaction REMOVES rows, so a thread that aged out a Gmail
    # read or an uploaded file would otherwise quietly stop confirming. The flag is
    # monotone and fails closed, and the read only happens when the in-context scan
    # already came up clean. Read-only skips both: its writes are refused before the
    # gate, so it never needs the answer.
    context_is_untrusted = tool_mode != "read-only" and (
        _context_has_untrusted_upload(current_messages)
        or await asyncio.to_thread(history.is_conversation_tainted, conversation_id)
    )
    if context_is_untrusted:
        logger.info("assistant.chat: untrusted content in context — every write confirms this turn")

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
        user_note=identity.build_user_note(user),
        page=page,
    )

    # ── Main tool-execution loop ───────────────────────────────────────────────
    iteration = 0
    # Set once a read returns THIRD-PARTY text during THIS turn — an external read
    # (Gmail, #8) or any result carrying a public-capture row (#204). From then on EVERY
    # write routes through confirmation, in every mode and routine tier included (#180).
    # Prior-turn untrusted content is already covered by `context_is_untrusted` above.
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
        # Read independently of `ue`, which is None whenever the window is unknown:
        # compaction wants the NUMBER even when the meter cannot render a percentage.
        context_tokens = cache_inclusive_input_tokens(usage)

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
                    context_tokens=context_tokens,
                    context_boundary_seq=context_boundary_seq,
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

            # Confirmation gate (issues #4, #8, #72, #180). A write confirms when ANY of
            # these holds:
            #   * it is a write to a PROTECTED context file (soul.md / MEMORY.md) —
            #     every mode, power included. `writes: True` alone is not enough there:
            #     a poisoned soul is not one bad record, it is a permanent system
            #     instruction replayed on every later turn — including background ones —
            #     that survives deleting the conversation;
            #   * untrusted content is in the assembled context, or the conversation
            #     carries the durable taint;
            #   * a read returning THIRD-PARTY text already ran THIS turn — an external
            #     read (Gmail), or one that returned a public-capture row (#204) — so
            #     injected instructions in that content can't auto-execute a write;
            #   * we are in normal mode and the tool is not declared ROUTINE (#180).
            # The routine exemption appears exactly once, as a narrowing of the
            # normal-mode term, so it can never mask one of the terms above — that is
            # what makes "the stronger rule wins" structural rather than a fact about
            # today's tool defs. Power with a clean turn executes, unchanged; read-only
            # never reaches here.
            # Persist the pending placeholder BEFORE emitting confirm (so /confirm can
            # find it), then wait for approval.
            always_confirms = context_file_tools.requires_confirmation(name, args)
            # Routine by name, minus the call shapes that take a record out of view
            # (see confirm_tier.removes_from_view) — normal mode only; power is untouched.
            routine_exempt = registry.is_routine_write(name) and not confirm_tier.removes_from_view(name, args)
            if is_write and (
                always_confirms
                or context_is_untrusted
                or turn_has_untrusted_reads
                or (tool_mode == "normal" and not routine_exempt)
            ):
                placeholder = await _pending_placeholder(name, args)
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
            # ONE call decides what the model is shown and whether this result carried
            # THIRD-PARTY text — shared with the background runner so the two loops can
            # never disagree (delimiters.fence_tool_result). It covers three fences: a
            # live external read (Gmail) wrapped whole by tool name; a public-capture row
            # wrapped per ROW, because the same `todo_list`/`crm_list_todos` call returns
            # a stranger's inbox item beside the user's own (issue #204); and a
            # context-file read, wrapped but deliberately NOT tainting — see
            # _RECORDED_CONTEXT_MARKER.
            #
            # `result` itself is untouched and is what the browser gets as `tool_end`, so
            # the tool-call preview shows the record rather than a wall of nonce tags.
            content, tainted = delimiters.fence_tool_result(name, result)
            # Third-party text taints the rest of the turn and, via the nonce-fenced
            # marker persisted below, later turns too — so a prompt injection in an email
            # or in a stranger's capture can't silently drive an unconfirmed write. The
            # fence is forge-proof and the paired system-prompt instruction tells the
            # model to treat what is inside it as data.
            if tainted:
                turn_has_untrusted_reads = True
                # Recorded NOW, not when compaction later removes this row: the row is
                # saved with its tool_calls and its results merged afterwards, so a
                # compaction pass reading in between would find no marker and record no
                # taint. Best-effort — within this turn `turn_has_untrusted_reads`
                # already covers it, the next turn's in-context scan sees the fence
                # while the row is still assembled, and compaction's own scan is the
                # backstop if this write is the thing that failed.
                try:
                    await asyncio.to_thread(history.mark_untrusted_seen, conversation_id)
                except Exception as e:
                    logger.warning("assistant.chat: failed to record untrusted taint: %s", e)
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
            if not persisted and (is_write or tainted):
                # Fail closed when the result couldn't be recorded, for either of two
                # reasons: (a) a write executed but its result is unrecorded (a later
                # rebuild would show the stub and tempt the model to redo the
                # mutation), or (b) a read that returned third-party text (Gmail, or a
                # public-capture row) whose taint marker didn't persist — a later turn
                # would then miss the untrusted-context confirmation and could
                # auto-execute an injected write.
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
            wrap_context_tokens = None
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
                    # This turn's reading is the LARGEST of the whole exchange — it read
                    # every tool result — so dropping it would leave compaction sizing
                    # the thread from the pre-tool-call figure.
                    wrap_context_tokens = cache_inclusive_input_tokens(event.get("usage") or {})
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
                        context_tokens=wrap_context_tokens,
                        context_boundary_seq=context_boundary_seq,
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

# Writes bound to the VERSION of the row they were proposed against, rather than to a
# connection. A context-file overwrite is composed against what the assistant just read,
# but a protected file always waits for approval — and the user can edit that same file
# in the Memory page while it waits, so approving must not silently discard their edit
# (#72). Hand-maintained by name, same shape as the set above.
_VERSION_BOUND_WRITE_TOOLS = frozenset({"write_context_file"})


async def _pending_placeholder(tool_name: str, args: dict | None) -> str:
    """The pending-approval result to persist for a gated write.

    Plain PENDING_RESULT_JSON, except for a bound write, which also carries what it was
    proposed against — the live connection for Gmail, the row version for a context
    file. Extra keys are safe: history's status helpers read only "status". Both binding
    reads touch Postgres, so they are offloaded — this runs on the SSE event loop.
    """
    if tool_name in _CONNECTION_BOUND_WRITE_TOOLS:
        from gmail import tools as gmail_tools  # lazy: keeps gmail out of engine import

        binding = await asyncio.to_thread(gmail_tools.pending_binding)
    elif tool_name in _VERSION_BOUND_WRITE_TOOLS:
        binding = await asyncio.to_thread(context_file_tools.pending_binding, args)
    else:
        return history.PENDING_RESULT_JSON
    if not binding:
        return history.PENDING_RESULT_JSON
    return json.dumps({"status": history.PENDING_STATUS, **binding})


def _parse_pending(pending_content: str | dict | None) -> dict:
    """The persisted pending placeholder as a mapping ({} when there is nothing to read).

    Accepts an already-decoded mapping as well as the JSON string the history layer
    stores, so a future change in how the placeholder is deserialized can't make a
    binding check silently fail open (it would parse to nothing and find no binding).
    """
    if isinstance(pending_content, dict):
        return pending_content
    if isinstance(pending_content, str) and pending_content:
        try:
            parsed = json.loads(pending_content)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _with_version_binding(tool: str, args: dict | None, pending_content: str | None) -> dict:
    """``args`` plus the precondition enforcing a stamped version binding.

    Unlike the connection binding this is NOT a pre-check: the kwarg rides the
    executor into ``service.write_file``'s in-UPDATE comparison, so there is no
    check-then-write window for an edit to slip through.
    """
    if tool not in _VERSION_BOUND_WRITE_TOOLS:
        return args or {}
    extra = context_file_tools.binding_kwargs(_parse_pending(pending_content))
    return {**(args or {}), **extra}


def _binding_conflict(tool: str, pending_content: str | None) -> dict | None:
    """An error result when a connection-bound write's target changed since it was
    proposed, else None. Never raises — the call is already claimed and marked
    executing, so raising here would strand the confirmation."""
    if tool not in _CONNECTION_BOUND_WRITE_TOOLS:
        return None
    from gmail import tools as gmail_tools  # lazy: see _pending_placeholder

    return gmail_tools.binding_conflict(_parse_pending(pending_content))


def resolve_confirmation(registry, conversation_id: str, tool_use_id: str, decision: str,
                         msg_id: str | None = None, *, user: dict | None) -> dict:
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

    ``user`` is keyword-only and REQUIRED for the same reason ``chat``'s is (#191): a
    confirmation is the fourth door into a conversation — it reaches the stored tool
    call by ``(conversation_id, tool_use_id, msg_id)`` and would otherwise execute
    another seat's pending write. The ownership check runs BEFORE the claim, so a
    refused call leaves the pending result untouched and returns ``not_found``, which
    the route renders as the same 404 an unknown conversation gets. Trusted seatless
    callers (the Telegram poller) pass ``user=None`` explicitly.
    """
    if user is not None and not history.conversation_exists(
        conversation_id, user_id=user["id"]  # indexed: see chat()'s note on failing closed
    ):
        return {"status": "not_found"}
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
        if conflict is not None:
            result = conflict
        else:
            # A version-bound write carries its precondition INTO the executor, so the
            # window above does not apply to it.
            result = registry.execute_tool_sync(
                tool, _with_version_binding(tool, args, claimed.get("content")),
            )
    else:  # deny
        result = {"status": history.DENIED_STATUS}
    # An approved write ECHOES the row it wrote, and that row can be a public-capture one:
    # `todo_update` on a stranger's inbox item answers with the stranger's title, which
    # the continuation turn then reads back out of history (issue #204). So this path
    # TAINTS but deliberately does NOT fence.
    #
    # The taint is what carries the security property: from here on `context_is_untrusted`
    # is true for the rest of the conversation, so every write — routine tier included —
    # routes through the confirmation gate, and an injection riding that echo can at worst
    # raise an Approve card the human sees. Fencing the persisted content as well was
    # tried and reverted: `history.get_tool_result` is what the `already_resolved` branch
    # above returns to the /confirm caller VERBATIM, so a double-click on Approve would
    # have shown the human nonce markup where the record's real text belongs. The model
    # loses only the prose framing on one already-approved write, and the static
    # untrusted-content instruction still tells it what a fence means everywhere else.
    _fenced, tainted = delimiters.fence_tool_result(tool, result)
    if tainted:
        # Best-effort, exactly as in the main loop — losing this must not strand an
        # approved write. The next turn's in-context scan is not a backstop here (nothing
        # was fenced), so this write IS the record; a failure degrades to the pre-#204
        # behaviour rather than to something worse.
        try:
            history.mark_untrusted_seen(conversation_id)
        except Exception as e:
            logger.warning("assistant.confirm: failed to record untrusted taint: %s", e)
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
