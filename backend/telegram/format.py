"""Markdown-to-Telegram-HTML converter.

Converts standard Markdown (as produced by AI models) into Telegram's supported HTML
subset. Ported verbatim from Chatty's ``integrations/telegram/format.py`` — pure, no
I/O. Callers must catch exceptions and fall back to plain text if conversion fails.

Also exposes ``escape_html`` and ``chunk_markdown`` so the client can split a long
reply into <=4096-char pieces on the SOURCE markdown BEFORE converting each chunk —
that keeps HTML tags/entities from ever being split across a message boundary.
"""

import re

_PLACEHOLDER_PREFIX = "\x00TGFMT"

# Telegram's per-message limit. Chunk the source markdown to this so each converted
# HTML chunk stays comfortably within the limit (HTML is only ever longer than its
# markdown, so we leave headroom rather than chunk the HTML itself and risk tag splits).
MAX_CHUNK_LENGTH = 3800


def escape_html(text: str) -> str:
    """Escape the three HTML-significant characters for Telegram's HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def markdown_to_telegram_html(text: str) -> str:
    """Convert standard Markdown to Telegram-compatible HTML.

    Handles bold, italic, strikethrough, inline code, fenced code blocks, links,
    headers, and blockquotes. Returns HTML suitable for ``parse_mode="HTML"``.
    """
    if not text:
        return text

    placeholders: list[str] = []

    def _placeholder(html: str) -> str:
        idx = len(placeholders)
        placeholders.append(html)
        return f"{_PLACEHOLDER_PREFIX}{idx}\x00"

    # 1. Extract fenced code blocks before anything else
    def _fenced_code(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = escape_html(m.group(2))
        if lang:
            return _placeholder(f'<pre><code class="language-{escape_html(lang)}">{code}</code></pre>')
        return _placeholder(f"<pre><code>{code}</code></pre>")

    result = re.sub(r"```(\w*)\n(.*?)```", _fenced_code, text, flags=re.DOTALL)

    # 2. Extract inline code
    def _inline_code(m: re.Match) -> str:
        return _placeholder(f"<code>{escape_html(m.group(1))}</code>")

    result = re.sub(r"`([^`]+)`", _inline_code, result)

    # 3. Extract links (before HTML-escaping to avoid double-escaping URLs)
    def _link(m: re.Match) -> str:
        label = escape_html(m.group(1))
        # Escape the double-quote too: href is interpolated inside a quoted HTML
        # attribute, and assistant output can echo untrusted CRM field values, so an
        # unescaped " would break out of the attribute.
        href = escape_html(m.group(2)).replace('"', "&quot;")
        return _placeholder(f'<a href="{href}">{label}</a>')

    result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, result)

    # 4. HTML-escape remaining text
    result = escape_html(result)

    # 5. Block elements — headers
    result = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)

    # 6. Block elements — blockquotes (merge consecutive lines)
    def _blockquote(m: re.Match) -> str:
        lines = m.group(0).split("\n")
        inner = "\n".join(re.sub(r"^&gt;\s?", "", line) for line in lines)
        return f"<blockquote>{inner}</blockquote>"

    result = re.sub(r"^&gt;\s?.+(?:\n&gt;\s?.+)*", _blockquote, result, flags=re.MULTILINE)

    # 7. Inline elements — bold before italic
    result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)
    result = re.sub(r"__(.+?)__", r"<b>\1</b>", result)

    # Italic — single * only (skip _ to avoid false positives in URLs)
    result = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", result)

    # Strikethrough
    result = re.sub(r"~~(.+?)~~", r"<s>\1</s>", result)

    # 8. Restore placeholders
    for idx, html in enumerate(placeholders):
        result = result.replace(f"{_PLACEHOLDER_PREFIX}{idx}\x00", html)

    return result


def chunk_text(text: str, max_length: int = MAX_CHUNK_LENGTH) -> list[str]:
    """Split text into <=max_length chunks on paragraph/line/word boundaries.

    Generic (not markdown-specific) — the single splitter for both the HTML send path
    (which chunks the SOURCE markdown before converting, so an HTML tag/entity is never
    split across two Telegram messages) and the plain-text send path.

    ``split_at <= 0`` (a boundary only at index 0, e.g. leading blank line before an
    unbroken run longer than max_length) falls through to a hard split, and empty
    chunks are filtered out — otherwise an empty chunk would be sent as an empty
    message, which Telegram rejects with a non-parse 400 that aborts the send loop and
    silently truncates the reply.
    """
    if len(text) <= max_length:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 0, max_length)
        if split_at <= 0:
            split_at = remaining.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, max_length)
        if split_at <= 0:
            split_at = max_length

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return [c for c in chunks if c]
