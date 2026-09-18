"""Server-side conversation assembler.

Rebuilds the provider ``messages`` array for a turn from Postgres by
``conversation_id``, so context BUILDS and PERSISTS server-side instead of
trusting a thin client transcript (which drops tool results between turns). Each
DB row maps independently because persistence is per-iteration faithful:

    user row                        -> {"role": "user", "content": text}
    assistant row, no tool_calls    -> {"role": "assistant", "content": text}
    assistant row, with tool_calls  -> provider.build_tool_turn(text, calls, results)

``build_tool_turn`` keeps the assembler provider-neutral (Anthropic blocks /
OpenAI tool_calls+role:tool / Gemini parts) and re-injects the assistant text
that ``add_tool_results`` drops.

Ported from Chatty's ``context_assembly.py``, dropping all ``json.loads`` (psycopg2 returns JSONB already parsed as Python lists). The
oversized-row guard bounds any single row that would blow the live context;
storage stays full. User-content truncation is delimiter-safe: it re-closes any
``<untrusted_file_content>`` block a cut would leave open, so an uploaded
document's text can never escape its fence.
"""

import json
import logging
import re

from assistant import delimiters, history

logger = logging.getLogger(__name__)

# Per-row live-context cap as a fraction of the budget (storage is never capped).
_OVERSIZED_ROW_FRACTION = 0.25
# PUBLIC because `assistant.compaction` imports them: the compaction trigger, the
# boundary it picks and this module's oversized-row guard must all measure a thread
# the same way, and two copies of "4 chars a token" would drift silently.
CHARS_PER_TOKEN = 4
DEFAULT_BUDGET_TOKENS = 128_000

# The opening exchange, kept verbatim across every compaction. It lives HERE rather
# than in `compaction` so the two modules agree on what "the middle" is, and because
# the dependency has to run this way round: compaction imports the assembler's
# measurements, never the reverse.
HEAD_ROWS = 2

_TRUNCATION_MARKER = "\n…[truncated]"
# Each pattern captures TWO groups: whether the tag's quotes are backslash-escaped, and
# the nonce. The escape group exists because since #204 a fence can sit INSIDE a JSON
# string value — `delimiters.fence_public_rows` wraps one field of a public-capture row
# and `fence_tool_result` then serializes the whole row, so the persisted text reads
# `id=\"<nonce>\"`. A pattern spelling the quote bare matches none of those, and
# `_reclose_untrusted` then silently appends nothing: exactly the open fence that
# function exists to close. The `\1` backreference ties the two quotes together, so a
# half-escaped lookalike matches neither spelling, and the group is empty for every
# pre-#204 fence (uploads and Gmail whole-payload), where behaviour is byte-identical.
_ESCAPED_QUOTE = r'(\\?)'
_UPLOAD_OPEN_RE = re.compile(rf'<untrusted_file_content id={_ESCAPED_QUOTE}"([0-9a-f]+)\1"')
_EXTERNAL_OPEN_RE = re.compile(rf'<untrusted_external_content id={_ESCAPED_QUOTE}"([0-9a-f]+)\1"')
# Built from the tag `delimiters` owns rather than a fourth copy of the literal — that
# module's own comment calls a second copy a silent bug, because the test keeps passing
# and just stops matching. (The two untrusted tags above predate this and still spell
# themselves out; they are left alone rather than widened into this diff.)
_SUMMARY_TAG = delimiters.CONVERSATION_SUMMARY_TAG
# A gist is never JSON-nested, so its escape group is always empty; it carries one only
# so the three patterns can share `_reclose_untrusted`'s single loop.
_SUMMARY_OPEN_RE = re.compile(rf'<{_SUMMARY_TAG} id={_ESCAPED_QUOTE}"([0-9a-f]+)\1"')
# A complete gist sitting at the very START of a row — the shape _apply_compaction
# writes. The backreference makes it a matched pair rather than two lookalike tags.
_SUMMARY_BLOCK_AT_START_RE = re.compile(
    rf'^<{_SUMMARY_TAG} id="([0-9a-f]+)"[^>]*>.*?</{_SUMMARY_TAG} id="\1">',
    re.DOTALL,
)
_GIST_SEPARATOR = "\n\n"


def _reclose_untrusted(cut: str) -> str:
    """Close any untrusted-content fence (uploaded file OR external tool result,
    e.g. Gmail) that a truncation cut left open, so text after the cut can't escape
    the fence. Returns the close tags to append (newline-joined), or ''."""
    reopened = []
    for open_re, tag in (
        (_UPLOAD_OPEN_RE, "untrusted_file_content"),
        (_EXTERNAL_OPEN_RE, "untrusted_external_content"),
        # A compaction gist (#72 Phase 3) is prepended to a retained user row, so an
        # oversized row can cut it open exactly like an upload block.
        (_SUMMARY_OPEN_RE, _SUMMARY_TAG),
    ):
        for esc, nonce in open_re.findall(cut):
            # The close tag has to be spelled the way the OPEN tag was: appending a bare
            # `</… id="n">` after an escaped `id=\"n\"` opener closes nothing the model
            # can pair up, and the "already closed?" test below would miss a real close.
            close = f'</{tag} id={esc}"{nonce}{esc}">'
            if close not in cut:
                reopened.append(close)
    return "\n".join(reopened)


def assemble_messages(provider, conversation_id: str) -> list[dict]:
    """Rebuild the provider ``messages`` array for ``conversation_id`` from the DB.

    Returns [] when the conversation has no rows. Callers persist the new user row
    BEFORE assembling, so the result already ends with the latest user turn.
    """
    conv = history.get_conversation(conversation_id)
    if not conv:
        return []
    rows = _apply_compaction(
        conv.get("messages") or [],
        conv.get("compaction_summary"),
        conv.get("compaction_first_kept_seq"),
    )

    budget = getattr(provider, "context_window", None) or DEFAULT_BUDGET_TOKENS
    max_row_chars = int(_OVERSIZED_ROW_FRACTION * budget * CHARS_PER_TOKEN)

    messages: list[dict] = []
    for row in rows:
        messages.extend(_row_to_messages(provider, row, max_row_chars))
    return _coalesce_consecutive(messages)



def _apply_compaction(rows, summary, first_kept_seq):
    """Replace the aged middle with a stored gist: HEAD verbatim, gist folded onto the
    first retained user turn, TAIL verbatim (issue #72 Phase 3).

    Folding onto a REAL user row rather than inserting a synthetic turn is what keeps
    role alternation valid on every provider — ``assistant.compaction`` snaps its
    boundary forward to a user row for exactly that reason. ``summary`` arrives already
    nonce-fenced: the wrapper is minted once at write time, because this function runs
    every turn and a fresh nonce here would re-key the provider's conversation-prefix
    cache each time.

    A no-op whenever there is nothing valid to compact, so a conversation with no
    stored boundary assembles exactly as it always did.
    """
    if not summary or first_kept_seq is None or len(rows) <= HEAD_ROWS:
        return rows
    head = rows[:HEAD_ROWS]
    # Guard a boundary that would overlap or precede the head — then there is no real
    # middle to drop, and folding the gist on would duplicate context we still hold.
    if first_kept_seq <= head[-1]["seq"]:
        return rows
    tail = [r for r in rows if r["seq"] >= first_kept_seq]
    if not tail:
        return rows

    first = tail[0]
    if first.get("role") == "user":
        merged = {**first, "content": summary + _GIST_SEPARATOR + (first.get("content") or "")}
        return head + [merged] + tail[1:]
    # Defensive: the boundary did not land on a user row (compaction snaps to one, so
    # this needs a hand-edited or migrated boundary). A standalone gist turn keeps the
    # summary in context; `_coalesce_consecutive` merges it with any neighbour that
    # would otherwise break alternation.
    synthetic = {
        "role": "user", "content": summary,
        "tool_calls": None, "tool_results": None, "seq": first_kept_seq,
    }
    return head + [synthetic] + tail

def _row_to_messages(provider, row, max_row_chars):
    """Convert one DB row to provider-native message(s), applying the oversized guard."""
    role = row.get("role")
    content = row.get("content") or ""

    if role == "user":
        return [{"role": "user", "content": _truncate_user_content(content, max_row_chars)}]

    tool_calls = row.get("tool_calls")  # JSONB → list | None (never a JSON string)
    if not tool_calls:
        if not content:
            return []  # nothing to say and no tools — skip empty row
        return [{"role": "assistant", "content": _truncate_text(content, max_row_chars)}]

    # Assistant iteration that used tools — reconstruct natively.
    try:
        tool_results = list(row.get("tool_results") or [])
        tool_calls = [_truncate_call_args(tc, max_row_chars) for tc in tool_calls]
        tool_results = [_truncate_result(r, max_row_chars) for r in tool_results]
        return provider.build_tool_turn(_truncate_text(content, max_row_chars), tool_calls, tool_results)
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("Assembler: malformed tool row %s, text fallback: %s", row.get("id"), e)
        return [{"role": "assistant", "content": _truncate_text(content, max_row_chars)}] if content else []


def _coalesce_consecutive(messages):
    """Merge consecutive same-role user/assistant messages so providers that require
    strict turn alternation (Anthropic, Gemini) never see two in a row. Role 'tool'
    (OpenAI) is left untouched — those stay one-per-tool_call.

    NEVER merge a message that carries an OpenAI-style top-level ``tool_calls`` key:
    ``_merge_content`` keeps only ``content``, so merging would silently drop the
    tool_calls and orphan the following ``role:'tool'`` result messages (OpenAI /
    Ollama / Together then 400 on the whole conversation). OpenAI does not require
    strict alternation, so leaving two consecutive assistant messages is valid
    there. Anthropic/Gemini embed tool calls inside ``content`` (no top-level
    ``tool_calls`` key), so they still coalesce and stay alternation-valid."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        can_merge = (
            out and role in ("user", "assistant") and out[-1].get("role") == role
            and "tool_calls" not in m and "tool_calls" not in out[-1]
        )
        if can_merge:
            out[-1] = {**out[-1], "content": _merge_content(out[-1].get("content"), m.get("content"))}
            continue
        out.append(dict(m))
    return out


def _merge_content(a, b):
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    if isinstance(a, str) and isinstance(b, str):
        return f"{a}\n\n{b}" if a and b else (a or b)
    return _as_blocks(a) + _as_blocks(b)


def _as_blocks(content):
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": content}] if content else []


# ── Oversized-row guard (live context only; stored rows stay full) ────────────

def _truncate_text(text, limit):
    if not text or len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATION_MARKER


def _truncate_user_content(text, limit):
    """Delimiter-safe truncation of a user row (which may carry uploaded-file blocks).

    If a cut would leave an ``<untrusted_file_content id="X">`` (or external) block
    open, append its matching close tag so injected text can't escape the fence.

    A compaction gist is PREPENDED to a retained user row, and when the boundary
    reaches the current turn that row is the message the user just typed. A plain cut
    from the end spends the budget on the summary first, so the gist survives and the
    actual request is what gets truncated — and once the gist alone fills the row,
    ``engine._last_user_text`` strips it and finds nothing but a truncation marker.

    So the two are budgeted separately, with the request taking priority: the gist may
    occupy at most half the row, and past that it is dropped WHOLE rather than sliced,
    leaving the user's text the entire budget. Dropping is right at that point — the
    gist is a convenience, the request is the turn — and dropping it whole avoids
    leaving a half-summary that reads as a complete one.
    """
    if not text or len(text) <= limit:
        return text
    gist, rest = _split_leading_summary(text)
    # `rest` empty means the row IS the gist — the standalone turn `_apply_compaction`
    # falls back to. There is nothing to preserve it in favour of, so that drops to the
    # plain cut below, which truncates and RE-CLOSES the fence rather than emptying the
    # row. Dropping is a trade against the user's text, never a way to lose the gist.
    if gist and rest:
        if len(gist) + len(_GIST_SEPARATOR) <= limit // 2:
            return gist + _GIST_SEPARATOR + _truncate_user_content(
                rest, limit - len(gist) - len(_GIST_SEPARATOR)
            )
        return _truncate_user_content(rest, limit)
    cut = text[:limit]
    result = cut + _TRUNCATION_MARKER
    reclosed = _reclose_untrusted(cut)
    if reclosed:
        result += "\n" + reclosed
    return result


def _split_leading_summary(text):
    """Split a leading complete compaction gist off the front. ('', text) when absent.

    Only a matched nonce PAIR at the very start counts, so nothing a message merely
    quotes can claim the protected slot."""
    m = _SUMMARY_BLOCK_AT_START_RE.match(text)
    if not m:
        return "", text
    return m.group(0), text[m.end():].lstrip("\n")


def _truncate_result(result, limit):
    """Bound one persisted tool result. CRM results are plain JSON, but Gmail
    (untrusted-external) results ARE nonce-fenced (the engine wraps them), and they
    flow through here on reassembly — so re-close any fence a cut leaves open, the
    same treatment _truncate_user_content gives uploads, so a cut can't drop the
    closing tag and let email content escape the fence."""
    content = result.get("content") or ""
    if len(content) <= limit:
        return result
    cut = content[:limit]
    new_content = cut + _TRUNCATION_MARKER
    reclosed = _reclose_untrusted(cut)
    if reclosed:
        new_content += "\n" + reclosed
    return {**result, "content": new_content}


def _truncate_call_args(tool_call, limit):
    """Bound a tool call's argument payload, truncating the largest string fields
    rather than dropping args wholesale, so the call stays legible to the model."""
    args = tool_call.get("args")
    if not isinstance(args, dict) or not args:
        return tool_call
    try:
        if len(json.dumps(args, default=str)) <= limit:
            return tool_call
    except (TypeError, ValueError):
        return tool_call
    per_field = max(500, limit // max(1, len(args)))
    bounded = {}
    for k, v in args.items():
        bounded[k] = v[:per_field] + _TRUNCATION_MARKER if isinstance(v, str) and len(v) > per_field else v
    return {**tool_call, "args": bounded}
