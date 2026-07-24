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

Ported from Chatty's ``context_assembly.py``, dropping compaction (deferred) and
all ``json.loads`` (psycopg2 returns JSONB already parsed as Python lists). The
oversized-row guard bounds any single row that would blow the live context;
storage stays full. User-content truncation is delimiter-safe: it re-closes any
``<untrusted_file_content>`` block a cut would leave open, so an uploaded
document's text can never escape its fence.
"""

import json
import logging
import re

from assistant import history

logger = logging.getLogger(__name__)

# Per-row live-context cap as a fraction of the budget (storage is never capped).
_OVERSIZED_ROW_FRACTION = 0.25
_CHARS_PER_TOKEN = 4
_DEFAULT_BUDGET_TOKENS = 128_000

_TRUNCATION_MARKER = "\n…[truncated]"
_UPLOAD_OPEN_RE = re.compile(r'<untrusted_file_content id="([0-9a-f]+)"')


def assemble_messages(provider, conversation_id: str) -> list[dict]:
    """Rebuild the provider ``messages`` array for ``conversation_id`` from the DB.

    Returns [] when the conversation has no rows. Callers persist the new user row
    BEFORE assembling, so the result already ends with the latest user turn.
    """
    conv = history.get_conversation(conversation_id)
    if not conv:
        return []
    rows = conv.get("messages") or []

    budget = getattr(provider, "context_window", None) or _DEFAULT_BUDGET_TOKENS
    max_row_chars = int(_OVERSIZED_ROW_FRACTION * budget * _CHARS_PER_TOKEN)

    messages: list[dict] = []
    for row in rows:
        messages.extend(_row_to_messages(provider, row, max_row_chars))
    return _coalesce_consecutive(messages)


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
    (OpenAI) is left untouched — those stay one-per-tool_call."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if out and role in ("user", "assistant") and out[-1].get("role") == role:
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

    If a cut would leave an ``<untrusted_file_content id="X">`` block open, append
    its matching close tag so injected text can't escape the fence.
    """
    if not text or len(text) <= limit:
        return text
    cut = text[:limit]
    reopened = [
        f'</untrusted_file_content id="{nonce}">'
        for nonce in _UPLOAD_OPEN_RE.findall(cut)
        if f'</untrusted_file_content id="{nonce}">' not in cut
    ]
    result = cut + _TRUNCATION_MARKER
    if reopened:
        result += "\n" + "\n".join(reopened)
    return result


def _truncate_result(result, limit):
    """Bound one persisted tool result. CRM tool results are plain JSON (never
    untrusted-wrapped), so a plain cut is safe here."""
    content = result.get("content") or ""
    if len(content) <= limit:
        return result
    return {**result, "content": content[:limit] + _TRUNCATION_MARKER}


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
