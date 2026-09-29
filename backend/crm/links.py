"""CRM deep-link shapes — the ONE server-side definition of a deal's URL (issue #145).

A deal is reached at ``/crm/pipeline?deal={id}``: the real react-router route for the
board, plus a query parameter ``PipelinePage`` parses on load. It opens the deal's
detail sheet, and shows a notice when the id resolves to nothing.

There is deliberately no ``/crm/deals/{id}`` route. The board is where a deal is
worked, the query-param form already had a sibling (``?stage=``) with a parser and a
consume-then-strip convention, and a second shape would mean two things to keep in
step with one parser.

**Why a module rather than an f-string at each call site.** Every agent tool that returns
a deal record attaches this link — well over a dozen call sites. Issue #145 was filed
because *nothing* produced one, so the format was discoverable nowhere and the assistant's
only option was to invent a path, and an invented path is a broken link. A duplicated
literal is how that many call sites drift apart the first time the frontend parser
changes. (No tally is written down anywhere here on purpose: `CRM_DEAL_URL_TOOLS` is the
count, and a number in prose is a second source of truth that goes stale silently — this
one did, within a single review round.)

**The frontend keeps its own copy** — ``dealDeepLink`` in
``frontend/src/crm/dealDeepLink.ts``, for the in-app link handoff, because a browser
cannot call Python. That copy is not deduplicated away; it is *pinned*:
``test_the_frontend_producer_agrees_with_the_backend_one`` in
``backend/tests/test_crm_deal_links.py`` **reads the TypeScript source** and compares
the template it emits against ``DEAL_PATH_TEMPLATE``. Two suites each asserting their
own hardcoded copy of the string would prove nothing — editing the template and its
Python expectation in one commit is a natural, self-consistent change that leaves both
suites green while the two producers emit different shapes. Reading the other
language's source is what makes the pin real.

There are no contact or company templates here. Upstream has them because its global
search page builds those paths server-side; nothing in CakeCRM does, so adding them
would ship two untested constants with no producer and no parser. They belong with
whatever first needs them.

Leaf module: stdlib plus a lazy ``core.config``, so ``crm.tools`` imports *downward*
into it and nothing here imports back up.
"""

from __future__ import annotations

import re

#: The canonical deal deep-link shape. A module constant rather than an inline literal
#: so the cross-language pin has exactly one thing to assert against.
DEAL_PATH_TEMPLATE = "/crm/pipeline?deal={id}"


def deal_path(deal_id: int) -> str:
    """Relative in-app path that opens the pipeline board on ``deal_id``."""
    return DEAL_PATH_TEMPLATE.format(id=deal_id)


def deal_url(deal_id: int) -> str:
    """Shareable link for a deal — what the assistant hands the user.

    Absolute when this install's public address is actually known, relative otherwise.

    ``settings.frontend_url`` always holds a string, but its last fallback is the
    ``http://localhost:5173`` dev default — a value nobody configured. Pasting that into
    a Telegram message or a notification asserts a hostname that is wrong for every
    reader who is not sitting at the machine, and a wrong absolute URL is worse than a
    relative one: the browser resolves ``/crm/pipeline?deal=42`` correctly from wherever
    the app is actually being served. So the absolute form is used only when
    ``FRONTEND_URL`` was set or Railway injected a public domain, which is what
    ``settings.frontend_url_is_default`` records.

    Known limit, and a deployment note rather than a defect: a relative path is correct
    in-app but is not clickable in Telegram or a push notification, which have no document
    origin to resolve it against. There is no better answer from here — an absolute
    ``http://localhost:5173/...`` is wrong for every reader who is not sitting at that
    machine, and omitting the link entirely would take the feature away from the in-app
    surfaces too. Any install whose assistant messages leave the app should set
    ``FRONTEND_URL``.

    ``rstrip('/')`` because a configured base may or may not carry a trailing slash and
    ``deal_path`` already supplies the leading one.

    ``core.config`` is imported lazily so this module stays cheap for callers that only
    ever want the relative path.
    """
    from core.config import settings

    if settings.frontend_url_is_default:
        return deal_path(deal_id)
    return f"{settings.frontend_url.rstrip('/')}{deal_path(deal_id)}"


def with_deal_url(deal: dict | None) -> dict | None:
    """Attach a shareable ``url`` to a deal record, in place (issue #145).

    The rule this repo follows, so nobody has to guess which tools qualify: **every tool
    that returns a deal record returns its ``url``.** Purely additive — no existing key
    changes — and it returns the same object it was handed, so nested deal lists can be
    mapped without rebuilding their containers.

    Inert on anything that is not a dict carrying an integer ``id``. A tool can return
    an error dict, ``None``, or a projection that dropped the id, and a link built from a
    missing or string id points at a deal that does not exist — worse than no link.

    ``type(...) is int``, not ``isinstance``: ``bool`` is an ``int`` subclass, so an
    ``id`` of ``True`` would otherwise emit ``?deal=True``. That is the same trap
    ``crm_bulk_move_deals`` spells out for its ``deal_ids`` guard.

    **There are no exclusions by tool name.** Trace what a tool's service actually
    returns — ``crm_get_contact`` embeds full deal rows, ``crm_dashboard`` returns five
    of them under ``top_deals``, and ``crm_analytics`` returns a ``stale_deals`` list.
    None of those three read like deal tools; all three hand the user specific deals.
    """
    if isinstance(deal, dict) and type(deal.get("id")) is int:
        deal["url"] = deal_url(deal["id"])
    return deal


# ── Bare deal ids in outbound text (issue #238, port of the blueprint's #3175) ──────
#
# Baker's tool results carry a `url` for every deal (#145), but its prose still says
# "deal #14" — fine in the drawer, useless in a Telegram message or a push notification
# whose reader cannot click an id. So the outbound seams rewrite a bare id to
# ``Title (url)``, from the title and url a tool returned for that deal THIS turn.
#
# Only ids the turn actually read are touched. An id the model made up (or a PO number
# that happens to collide with a deal id) is left exactly as written: rewriting it would
# lend a hallucinated reference a real deal's title.

# "deal #14", "deals 14", "Deal#14" or a bare "#14". The bare form must not follow a word
# character or a URL/anchor character, so "PO#14" and "?deal=14" are never references.
_DEAL_REF_RE = re.compile(r"\bdeals?\s*#?\s*(\d+)\b|(?<![\w#/=&])#(\d+)\b", re.IGNORECASE)

# A bare "#14" right after one of these labels names that thing, not a deal
# ("PO #14", "invoice #14"), even when the number collides with a deal id.
# simplification: a fixed label list; widen it when a new collision shows up.
_OTHER_REF_LABEL_RE = re.compile(
    r"\b(?:po|purchase order|so|sales order|order|invoice|bill|issue|pr|ticket|todo"
    r"|contact|company|item|case|check|step)\s*$",
    re.IGNORECASE,
)


def remember_deal_refs(result, deal_refs: dict[int, tuple[str, str]] | None) -> None:
    """Record every deal record in a tool ``result`` into ``deal_refs`` (``{id: (title, url)}``).

    A deal record is any dict whose ``url`` is exactly ``deal_url`` of its own ``id``
    (or ``deal_id``, the ``crm_get_deal_health`` shape) and that carries a non-blank
    ``title`` — so a contact row (which has an ``id`` and sometimes a job ``title``) or a
    todo never counts, because only ``with_deal_url`` ever produces that url. Nested
    dicts and lists are walked, since deal rows ride inside contact/company rollups,
    ``top_deals`` and the like.
    """
    if deal_refs is None:
        return

    def _walk(node) -> None:
        if isinstance(node, dict):
            deal_id = node.get("id", node.get("deal_id"))
            title, url = node.get("title"), node.get("url")
            if (
                type(deal_id) is int
                and isinstance(title, str)
                and title.strip()
                and isinstance(url, str)
                and url == deal_url(deal_id)
            ):
                deal_refs[deal_id] = (title.strip(), url)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(result)


def link_deal_refs(text: str, deal_refs: dict[int, tuple[str, str]] | None) -> str:
    """Rewrite bare deal ids in outbound ``text`` to ``Title (url)``.

    Only ids present in ``deal_refs`` — deals a tool returned this turn — are rewritten;
    every other number is left exactly as written. When the deal's url is already in the
    text, or the deal was already linked earlier in the same text, the id becomes the
    title alone so the link is never doubled. A relative ``deal_url`` stays relative:
    this never invents a host.
    """
    if not text or not deal_refs:
        return text

    linked: set[int] = set()

    def _sub(m: re.Match) -> str:
        if m.group(2) and _OTHER_REF_LABEL_RE.search(text, 0, m.start()):
            return m.group(0)
        deal_id = int(m.group(1) or m.group(2))
        hit = deal_refs.get(deal_id)
        if not hit:
            return m.group(0)
        title, url = hit
        # Digit-bounded, not a bare `in`: deal 1's url is a prefix of deal 14's.
        if deal_id in linked or re.search(re.escape(url) + r"(?!\d)", text):
            return title
        linked.add(deal_id)
        return f"{title} ({url})"

    return _DEAL_REF_RE.sub(_sub, text)
