"""Render the context-file store into the system prompt (issue #72 Phase 1).

Two blocks, split by TRUST rather than by file — this is the approved Decision 1
(Option 4) plus the correction the plan review forced:

* ``build_soul_block()`` → the **static** half, UNFENCED. soul.md is genuinely Baker's
  identity; fencing it as untrusted data would defeat the feature outright.
* ``build_knowledge_block()`` → the **volatile** half, nonce-FENCED as
  ``<recorded_context>``. MEMORY.md, the topic manifest and the daily manifest are the
  large, frequently-written, (from Phase 4) extractor-fed surface, so they stay DATA.

Why the split is by half and not "all context files are static, as the issue says":
``delimiters._wrap`` mints fresh entropy on every call, so a fenced block placed in the
static half would change the cached prefix EVERY turn — the precise opposite of the
prompt-cache discipline the issue asks for. The invariant that actually matters is *no
per-turn entropy in static*, not *static never changes*: the static half already changes
legitimately when the user edits their personality. So the stable, unfenced document is
cached and the freshly-fenced ones ride along with the facts in volatile, which is where
``memory_context`` already lives.

Chatty loads every context file in full plus a BM25 relevance prefetch. We deliberately
load only soul + MEMORY + the two manifests; topic bodies and past daily notes are
reached on demand with ``read_context_file``. That is strictly cheaper and keeps the
cacheable prefix small, and the manifest is what makes an un-loaded file discoverable.

Never raises: any failure returns ``""`` so a store outage degrades to a context-less
turn, never a broken chat — same contract as ``memory/context.py``.
"""

import logging

from assistant.delimiters import wrap_recorded_context
from context_files import service

logger = logging.getLogger(__name__)

# Chatty's MAX_CONTEXT_CHARS is 200_000. That is a personal assistant's whole disk; for a
# prompt we want cached and cheap, a fifth of that is already generous.
MAX_SOUL_CHARS = 20_000
MAX_KNOWLEDGE_CHARS = 20_000
# PER-SECTION budgets, not just an overall one. With a single shared cap the first
# section can eat the whole thing: a 20k MEMORY.md would truncate today's note and BOTH
# manifests to nothing — silently disabling the very mechanism that makes un-loaded files
# discoverable. Bodies are bounded here so the (small, row-bounded) manifests always fit.
MAX_MEMORY_CHARS = 8_000
MAX_TODAY_CHARS = 6_000
MAX_TOPIC_MANIFEST_ENTRIES = 40
MAX_DAILY_MANIFEST_ENTRIES = 30
_TRUNCATED = "(Truncated — context size limit reached)"

_MEMORY_HEADER = "## MEMORY\n\nYour living snapshot (MEMORY.md):"
_TOPIC_HEADER = (
    "## Your other knowledge\n\n"
    "Topic files you are NOT currently shown in full. Read one with "
    "`read_context_file(filename)` when the conversation touches it:"
)
_DAILY_HEADER = (
    "## Recent daily notes\n\n"
    "Past days you are NOT currently shown. Read one with `read_daily_note(date)`:"
)
_TODAY_HEADER = "## Today's note\n\nYour running log for today (append with `append_daily_note`):"


def _cap(text: str, limit: int) -> str:
    """Bound a body BEFORE it is fenced.

    Order matters: truncating the assembled block *after* wrapping could cut off a
    closing nonce tag, which would leave the fence open and hand injected text the very
    boundary the nonce exists to protect.
    """
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return f"{body[:limit].rstrip()}\n\n{_TRUNCATED}"


def build_soul_block() -> str:
    """soul.md for the STATIC half — unfenced.

    Blank stored content resolves to ``identity.DEFAULT_SOUL``: the migration seeds the
    row EMPTY so a later boot can never overwrite a soul the user or the assistant
    rewrote, which means the built-in text has to be applied at read time. Same
    blank-means-default contract as ``assistant_identity.personality``.

    Imported inside the function to keep the module-level dependency one-way
    (``assistant.engine`` imports this module).
    """
    from assistant.identity import DEFAULT_SOUL

    try:
        row = service.read_file(service.SOUL_FILE)
        stored = ((row or {}).get("content") or "").strip()
    except Exception:
        logger.warning("build_soul_block failed — falling back to the built-in soul", exc_info=True)
        stored = ""
    content = _cap(stored or DEFAULT_SOUL, MAX_SOUL_CHARS)
    return f"## Your soul\n\n{content}" if content else ""


def build_knowledge_block() -> str:
    """MEMORY.md + today's note + the two manifests, nonce-fenced, for the VOLATILE half.

    Deterministic section order so the same data renders identically turn to turn (only
    the nonce differs, which is the point).
    """
    try:
        sections: list[str] = []

        memory = service.read_file(service.MEMORY_FILE)
        memory_text = _cap((memory or {}).get("content") or "", MAX_MEMORY_CHARS)
        if memory_text:
            sections.append(f"{_MEMORY_HEADER}\n\n{memory_text}")

        today = _cap(service.read_daily_note(), MAX_TODAY_CHARS)
        if today:
            sections.append(f"{_TODAY_HEADER}\n\n{today}")

        topics = service.topic_manifest()[:MAX_TOPIC_MANIFEST_ENTRIES]
        if topics:
            lines = [f"- {t['filename']} · {t.get('headline') or '(no summary yet)'}" for t in topics]
            sections.append(f"{_TOPIC_HEADER}\n\n" + "\n".join(lines))

        dailies = service.daily_manifest()[:MAX_DAILY_MANIFEST_ENTRIES]
        if dailies:
            lines = [
                f"- {d['filename'][len('daily/'):-len('.md')]} · {d.get('headline') or '(no summary yet)'}"
                for d in dailies
            ]
            sections.append(f"{_DAILY_HEADER}\n\n" + "\n".join(lines))

        if not sections:
            return ""
        # Cap once more across the assembled sections, still BEFORE fencing.
        return wrap_recorded_context(_cap("\n\n---\n\n".join(sections), MAX_KNOWLEDGE_CHARS))
    except Exception:
        logger.warning("build_knowledge_block failed — proceeding without it", exc_info=True)
        return ""
