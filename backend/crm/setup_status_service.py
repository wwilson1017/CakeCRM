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

Two readers cannot express "unknown" because they are documented never-raising,
fail-safe-to-a-default reads used on hot paths (`gmail.store.get_row`,
`crm.service.get_task_mode`). For Gmail the degenerate case is still detectable — the
`gmail_connection` singleton is seeded by its own migration, so an EMPTY row can only
mean the read failed — and that is mapped back to ``None`` here. Task mode has no such
tell, and its fail-safe default is the product default, so it is reported as read.
"""

import logging

logger = logging.getLogger(__name__)

# Entity types that can carry user-defined custom fields (#19). Mirrors
# crm.field_service.VALID_ENTITY_TYPES; ordered so the payload is stable.
_FIELD_ENTITIES = ("contact", "company", "deal")


def _ai() -> tuple[bool | None, str | None]:
    """(ai_ready, active_provider). ``active_provider`` is '' when none is configured.

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
        ready = get_ai_provider() is not None
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
    Baker is talking to has a linked Telegram chat", read through ``store.get_link``.

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

        return connected, get_link(user_id) is not None
    except Exception:
        logger.debug("setup status: Telegram link state unreadable", exc_info=True)
        return connected, None


def _task_mode() -> str | None:
    """'gtd' or 'normal'. ``get_task_mode`` is documented never to raise and to
    fail-safe to the product default, so there is no unknown to report; the except is
    the belt-and-braces half (an import failure in an embedding with no CRM)."""
    try:
        from crm.service import get_task_mode

        return get_task_mode()
    except Exception:
        logger.debug("setup status: task mode unreadable", exc_info=True)
        return None


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
        "task_mode": _task_mode(),
        "custom_field_counts": _custom_field_counts(),
    }
