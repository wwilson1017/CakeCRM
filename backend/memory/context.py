"""Assistant context builder — surfaces relevant facts into each turn's prompt.

This is acceptance clause 1 of issue #5 ("facts persist across conversations and
surface in context"): the engine calls ``build_memory_context`` right before the
provider loop and appends the result to the *volatile* half of the system prompt.

Selection is FTS-first: facts matching the user's latest message, backfilled with the
top live facts by confidence/recency. Only the FTS *matches* are counted as retrievals
(the dreaming usage signal) — confidence backfill is filler and must never keep a fact
alive, or every fact would become permanently un-archivable. Retrieval tracking is
throttled to once per hour per fact in the service layer.

Never raises: any failure returns ``""`` so a memory outage degrades to a memory-less
turn, never a broken chat.
"""

import logging

from assistant.delimiters import wrap_untrusted_memory
from memory import service

logger = logging.getLogger(__name__)

MEMORY_CONTEXT_FACT_LIMIT = 10
_USER_TEXT_CAP = 2000       # never feed a whole upload into the search tokenizer

_HEADER = "Facts you previously recorded with your memory tools, most relevant first:"


def _render_fact(fact: dict) -> str:
    mt = fact.get("memory_type")
    prefix = f"[{mt}] " if mt else ""
    since = fact.get("valid_from")
    tail = f" (since {since})" if since else ""
    return f"- {prefix}{fact.get('subject')} — {fact.get('predicate')} — {fact.get('object')}{tail}"


def build_memory_context(user_text: str | None) -> str:
    """Return a rendered 'Long-term memory' block for the system prompt, or ``""``.

    Facts matching *user_text* come first (and are counted as retrievals); the top
    live facts backfill to the limit (and are NOT counted). ``user_text=None`` (a
    continuation turn) uses the backfill path only.
    """
    try:
        matches: list[dict] = []
        if user_text and user_text.strip():
            # search_facts tokenizes into an OR-of-keywords internally; just bound the
            # length so a huge upload turn doesn't feed the tokenizer a novel.
            matches = service.search_facts(
                user_text[:_USER_TEXT_CAP], limit=MEMORY_CONTEXT_FACT_LIMIT, track_retrieval=False
            )

        surfaced: list[dict] = []
        seen_ids: set = set()
        for fact in matches:
            if fact["id"] not in seen_ids:
                seen_ids.add(fact["id"])
                surfaced.append(fact)

        if len(surfaced) < MEMORY_CONTEXT_FACT_LIMIT:
            backfill = service.query_facts(
                limit=MEMORY_CONTEXT_FACT_LIMIT, track_retrieval=False
            )
            for fact in backfill:
                if fact["id"] not in seen_ids:
                    seen_ids.add(fact["id"])
                    surfaced.append(fact)
                    if len(surfaced) >= MEMORY_CONTEXT_FACT_LIMIT:
                        break

        surfaced = surfaced[:MEMORY_CONTEXT_FACT_LIMIT]
        if not surfaced:
            return ""

        # Count ONLY the FTS matches that actually surfaced as retrievals — never
        # the confidence backfill (which is filler, not a genuine "use").
        surfaced_ids = {f["id"] for f in surfaced}
        matched_ids = [f["id"] for f in matches if f["id"] in surfaced_ids]
        service.track_retrieval_for(matched_ids)

        body = "\n".join([_HEADER] + [_render_fact(f) for f in surfaced])
        # Nonce-fence the facts (same pattern as untrusted uploads): fact text may carry
        # content captured from documents/messages, so it must never impersonate system
        # instructions. The static MEMORY_NOTE tells the model to treat it as data.
        return wrap_untrusted_memory(body)
    except Exception:
        logger.warning("build_memory_context failed — proceeding without memory", exc_info=True)
        return ""
