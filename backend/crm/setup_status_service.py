"""CakeCRM — install setup status, assembled for the assistant (issue #200).

ONE structured read that answers "what is actually live on this install?" — the third
question the help manual phases split apart. Phase 1's library answers *how* something
works and phase 2's page note answers *where* the user is; neither can say whether the
thing is switched on right now, and a confident how-to for a feature that was never
configured is exactly the kind of wrong answer the manual was built to stop.

**No REST route, deliberately.** Every fact here is already reachable through a route
that has its own authz (`/api/setup/status`, `/api/gmail/status`, `/api/telegram/*`,
`/api/crm/fields`); this module only re-reads them for the tool layer. Adding an
endpoint would mean adding a route-authz decision — for a payload the frontend has no
use for — so there is none, and `tests/test_route_authz.py` has nothing new to pin.

## The payload contract

Booleans, enums and integers. No free text, no secrets, no ciphertext, and no
connected-account address: the Gmail mailbox address and the Telegram bot username are
omitted on purpose, and so is `active_model` (a provider-supplied string that would put
free text on this surface the first time a model id got descriptive). A read tool is on
the unattended on-ramp — the background turn advertises every read — and an
all-structured payload is the ideal case there, because there is no channel for injected
prose to ride in on. `tests/test_crm_setup_status.py` pins the key set and the type of
every leaf so a later field cannot quietly widen it.

## `None` means UNKNOWN, never "off"

Each fact is read in its OWN try/except, so one dead subsystem cannot blank the rest,
and a read that fails yields ``None`` rather than ``False``. This distinction is the
whole reason the fields are nullable: collapsing a failed read to ``False`` would have
Baker tell a user their Gmail is disconnected and walk them through reconnecting it,
when in fact the database hiccuped. The tool description states the same contract to the
model in one line.

Only one fact here is per-SEAT rather than per-install — whether the person Baker is
talking to has a linked Telegram chat (#193) — and it is the only reason
``get_setup_status`` takes a ``user_id`` at all. An unattended turn has no seat, so that
field is unknown there rather than false.

Three readers cannot express "unknown" on their own, because they are documented
never-raising, fail-safe-to-a-default reads used on hot paths (`gmail.store.get_row`,
`providers.credentials.CredentialStore._load`, `crm.service.get_todo_mode`). That is
correct for them and wrong for here, so each is answered with a tell **carried by the same
query as the value** — never a separate preflight probe, which can succeed in the instant
before the real read fails and so proves nothing about it. Gmail brings its own: the
`gmail_connection` singleton is seeded by its migration, so an EMPTY row can only mean the
read failed. `CredentialStore` now reports `load_failed` off its own load. Todo mode is
read through `get_crm_meta`, the failure-aware reader of that singleton, which lets the
error propagate.
"""

import logging

logger = logging.getLogger(__name__)

# Entity types that can carry user-defined custom fields (#19). Mirrors
# crm.field_service.VALID_ENTITY_TYPES; ordered so the payload is stable.
_FIELD_ENTITIES = ("contact", "company", "deal")

def _ai() -> tuple[bool | None, str | None]:
    """(ai_ready, active_provider). ``active_provider`` is '' when none is configured.

    ``CredentialStore._load`` catches every database error and returns the EMPTY
    credential shape — right for its hot paths, since chat must not 500 because a read
    hiccuped, but it means no exception reaches the ``except`` below and a dead database
    would otherwise be reported here as "no AI provider configured", inviting Baker to
    walk someone through an AI Setup they already did. ``load_failed`` is the store
    telling us which of the two it was, read off the SAME load rather than inferred from
    a separate preflight query that could succeed just before this one fails — and that
    one load is handed to the factory too, so both fields describe one snapshot.

    The provider name is sanitized against the canonical list rather than echoed: the
    value reaches Postgres only through validated routes today, but this payload's claim
    is that it carries enums, and an enum that is "whatever the column happens to hold"
    is not one.
    """
    try:
        from providers import get_ai_provider
        from providers.credentials import CredentialStore
        from providers.router import ALL_PROVIDERS

        store = CredentialStore()
        if store.load_failed:
            return None, None
        # ONE load, and the factory resolves against it. Calling `get_ai_provider()` bare
        # would load a SECOND store, whose own failure `load_failed` cannot see — so a
        # read that failed there came back as `ai_ready: false` beside an
        # `active_provider` from the first snapshot. Sharing the store also means the two
        # fields cannot straddle a concurrent connect or disconnect.
        ready = get_ai_provider(store=store) is not None
        active = store.data.get("active_provider", "") or ""
        return ready, (active if active in ALL_PROVIDERS else "")
    except Exception:
        logger.debug("setup status: AI readiness unreadable", exc_info=True)
        return None, None


def _gmail() -> tuple[bool | None, bool | None]:
    """(gmail_connected, gmail_broken).

    ``gmail.store.get_row`` never raises and returns ``{}`` on failure. The
    `gmail_connection` singleton is seeded by its migration and a serving process has
    always run migrations, so an empty row is not "no connection yet" — it is a read
    that did not work, and it maps to unknown.
    """
    try:
        from gmail.store import get_row, is_connected

        row = get_row()
        if not row:
            return None, None
        return is_connected(row), row.get("connection_status") == "broken"
    except Exception:
        logger.debug("setup status: Gmail state unreadable", exc_info=True)
        return None, None


def _telegram(user_id) -> tuple[bool | None, bool | None]:
    """(telegram_connected, telegram_linked).

    The two halves answer different questions and #193 split them apart: the bot token is
    install-wide configuration, while a linked chat belongs to ONE seat. So
    ``telegram_connected`` reads the singleton and ``telegram_linked`` means "the seat
    Baker is talking to has a Telegram chat that can actually be reached", read through
    ``store.get_link`` and keyed on ``chat_id`` rather than on the row existing.

    An unattended turn has no seat, so the link question has no answer and the field is
    ``None`` — unknown — rather than ``False``. Reporting "not linked" to a background
    turn would invite it to tell the install to go and link a chat that may well exist.
    They are read in SEPARATE try/excepts for the same reason every other source is:
    a per-seat read that fails must not also blank the install-wide fact beside it.
    """
    connected: bool | None
    try:
        from telegram.store import get_settings

        connected = bool(get_settings()["connected"])
    except Exception:
        logger.debug("setup status: Telegram bot config unreadable", exc_info=True)
        connected = None
    if user_id is None:
        return connected, None
    try:
        from telegram.store import get_link

        # A ROW is not a link. `mint_link_code` upserts the seat's row and clears
        # `chat_id`, and re-connecting or disconnecting the workspace bot clears it too,
        # so a seat that pressed "Get my link code" and stopped there has a row and no
        # reachable phone. `chat_id` is the predicate `/api/telegram/status` uses, and the
        # two must agree — the card says "not linked" while this said "linked" otherwise.
        return connected, bool((get_link(user_id) or {}).get("chat_id"))
    except Exception:
        logger.debug("setup status: Telegram link state unreadable", exc_info=True)
        return connected, None


# The product default, and the rule `crm.service.get_todo_mode` applies to the stored
# column: anything that is not one of the two known modes reads as GTD. Restated here
# rather than reached through that function, because that function ALSO swallows a failed
# read into this same default — which is right for the four hot paths that call it and
# wrong for a surface that promised not to present a guess as a fact.
# `test_the_todo_mode_rule_matches_the_one_every_other_reader_uses` pins the two together.
_TODO_MODES = ("normal", "gtd")
_TODO_MODE_DEFAULT = "gtd"


def _todo_mode() -> str | None:
    """'gtd' or 'normal', or None when the row could not be read.

    Reads the value through ``get_crm_meta``, which is the singleton's failure-AWARE
    reader — it lets a database error propagate — so the failure and the value come from
    ONE query. A preflight probe would not do: it can succeed in the moment before the
    real read fails, which is the window this shape closes.
    """
    try:
        from crm.service import get_crm_meta

        meta = get_crm_meta()
    except Exception:
        logger.debug("setup status: crm_meta unreadable — todo mode is unknown", exc_info=True)
        return None
    if "todo_mode" not in meta:
        # `get_crm_meta` answers an ABSENT row with a literal default dict that has no
        # todo_mode key. The singleton is seeded by its migration and deleted by nothing
        # (both CRM-reset TRUNCATE sweeps exclude it), so that can only mean the row did
        # not come back.
        return None
    mode = meta.get("todo_mode")
    return mode if mode in _TODO_MODES else _TODO_MODE_DEFAULT


def _custom_field_counts() -> dict[str, int] | None:
    """How many field DEFINITIONS exist per entity type — counts, never names.

    Definition names are user-authored free text, which is precisely what this payload
    does not carry. The count is what answers "is anyone using custom fields here?", and
    `crm_get_contact_fields` and friends already exist for the rest.
    """
    try:
        from crm.field_service import list_field_definitions

        defs = list_field_definitions()
        counts = dict.fromkeys(_FIELD_ENTITIES, 0)
        for d in defs:
            entity = d.get("entity_type")
            if entity in counts:
                counts[entity] += 1
        return counts
    except Exception:
        logger.debug("setup status: custom field definitions unreadable", exc_info=True)
        return None


def get_setup_status(user_id=None) -> dict:
    """The install's configuration state, as structured values. Never raises.

    ``user_id`` is the seat this turn belongs to, bound server-side by the tool layer
    (#190) and never supplied by the model. It is read by exactly ONE field — whether
    THIS seat has a linked Telegram chat — because that is the only fact here that is
    per-person rather than per-install. ``None`` is an unattended turn.
    """
    ai_ready, active_provider = _ai()
    gmail_connected, gmail_broken = _gmail()
    telegram_connected, telegram_linked = _telegram(user_id)
    return {
        "ai_ready": ai_ready,
        "active_provider": active_provider,
        "gmail_connected": gmail_connected,
        "gmail_broken": gmail_broken,
        "telegram_connected": telegram_connected,
        "telegram_linked": telegram_linked,
        "todo_mode": _todo_mode(),
        "custom_field_counts": _custom_field_counts(),
    }
