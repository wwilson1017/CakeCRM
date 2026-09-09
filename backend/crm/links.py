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
