"""crm.setup_status_service — the assistant's one read of what is actually configured (#200).

Two things are pinned here and they are both about the SHAPE of the payload rather than
about any particular install:

1. **The key set and the type of every leaf.** This surface is advertised to the
   unattended background turn, and its safety argument is that it carries no free text
   for injected prose to ride in on. That argument is only true while the payload stays
   booleans, enums and counts — so the set is pinned, and a new key must arrive with a
   deliberate edit here rather than by accident.

2. **`None` means UNKNOWN, never "off".** Each fact is read in its own try/except; a
   failed read must not collapse to `False`, or Baker will tell a user their Gmail is
   disconnected and walk them through reconnecting it because the database hiccuped.

Fully hermetic: every source is a stub, nothing touches Postgres or a provider SDK.
"""

import pytest

from crm import setup_status_service as svc

EXPECTED_KEYS = {
    "ai_ready",
    "active_provider",
    "gmail_connected",
    "gmail_broken",
    "telegram_connected",
    "telegram_linked",
    "task_mode",
    "custom_field_counts",
}

# What a string leaf is allowed to be. Anything else is free text on a surface that
# claims to carry none.
ALLOWED_PROVIDERS = {"", "anthropic", "openai", "google", "ollama", "together"}
ALLOWED_TASK_MODES = {"gtd", "normal"}


class _Store:
    def __init__(self, active="anthropic", load_failed=False):
        self.data = {"active_provider": active, "active_model": "some-model-v2"}
        self.load_failed = load_failed


@pytest.fixture
def sources(monkeypatch):
    """Every source stubbed to a configured, healthy install. Tests override one at a
    time so a failure in one subsystem is never confused with a failure in another."""
    import crm.field_service
    import crm.service
    import gmail.store
    import providers
    import providers.credentials
    import telegram.store

    monkeypatch.setattr(providers, "get_ai_provider", lambda *a, **k: object())
    monkeypatch.setattr(providers.credentials, "CredentialStore", _Store)
    monkeypatch.setattr(gmail.store, "get_row", lambda: {"connection_status": "ok"})
    monkeypatch.setattr(gmail.store, "is_connected", lambda row=None: True)
    monkeypatch.setattr(telegram.store, "get_settings", lambda: {"connected": True})
    monkeypatch.setattr(telegram.store, "get_link",
                        lambda user_id: {"id": 1, "chat_id": "9", "link_code": ""})
    monkeypatch.setattr(crm.service, "get_crm_meta", lambda: {"id": 1, "task_mode": "gtd"})
    monkeypatch.setattr(crm.field_service, "list_field_definitions", lambda *a, **k: [
        {"entity_type": "contact"}, {"entity_type": "contact"}, {"entity_type": "deal"},
    ])
    return monkeypatch


# ── The contract ──────────────────────────────────────────────────────────────

def test_the_payload_key_set_is_exactly_this(sources):
    assert set(svc.get_setup_status(user_id=7)) == EXPECTED_KEYS
    assert set(svc.get_setup_status()) == EXPECTED_KEYS  # unattended: same shape


def test_a_healthy_install_reads_back_as_configured(sources):
    out = svc.get_setup_status(user_id=7)
    assert out == {
        "ai_ready": True,
        "active_provider": "anthropic",
        "gmail_connected": True,
        "gmail_broken": False,
        "telegram_connected": True,
        "telegram_linked": True,
        "task_mode": "gtd",
        "custom_field_counts": {"contact": 2, "company": 0, "deal": 1},
    }


def test_every_leaf_is_a_boolean_an_enum_a_count_or_unknown(sources):
    """The structural half of "no free text on this surface": a later field cannot
    quietly become a string the model reads as prose."""
    out = svc.get_setup_status(user_id=7)
    for key in ("ai_ready", "gmail_connected", "gmail_broken",
                "telegram_connected", "telegram_linked"):
        assert isinstance(out[key], bool) or out[key] is None, key
    assert out["active_provider"] in ALLOWED_PROVIDERS or out["active_provider"] is None
    assert out["task_mode"] in ALLOWED_TASK_MODES or out["task_mode"] is None
    counts = out["custom_field_counts"]
    assert counts is None or (
        isinstance(counts, dict)
        and all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                for k, v in counts.items())
    )


def test_no_secret_or_connected_account_address_can_appear(sources):
    """`active_model` is a provider-supplied string and the mailbox address is a person's
    email; neither belongs on a surface a background turn can read. The stub deliberately
    offers both, so a future re-write that starts echoing the store would fail here."""
    import gmail.store

    sources.setattr(gmail.store, "get_row", lambda: {
        "connection_status": "ok", "email": "someone@example.com",
        "client_secret_enc": "gAAAAAB-ciphertext", "refresh_token_enc": "gAAAAAB-more",
    })
    blob = repr(svc.get_setup_status(user_id=7))
    for leak in ("some-model-v2", "someone@example.com", "gAAAAAB"):
        assert leak not in blob, f"{leak!r} reached the setup-status payload"


# ── Unknown is not "off" ──────────────────────────────────────────────────────

def _boom(*a, **k):
    raise RuntimeError("database is having a day")


def test_an_unreadable_ai_state_is_unknown_not_unconfigured(sources):
    import providers

    sources.setattr(providers, "get_ai_provider", _boom)
    out = svc.get_setup_status()
    assert out["ai_ready"] is None and out["active_provider"] is None
    # One dead subsystem must not blank the rest.
    assert out["gmail_connected"] is True and out["task_mode"] == "gtd"


def test_an_unreadable_gmail_row_is_unknown_not_disconnected(sources):
    """`gmail.store.get_row` never raises and answers {} on failure. The singleton is
    seeded by its own migration and a serving process has always run migrations, so an
    empty row can only mean the read did not work."""
    import gmail.store

    sources.setattr(gmail.store, "get_row", dict)
    out = svc.get_setup_status()
    assert out["gmail_connected"] is None and out["gmail_broken"] is None
    assert out["telegram_connected"] is True


def test_a_broken_gmail_connection_is_reported_as_broken(sources):
    import gmail.store

    sources.setattr(gmail.store, "get_row", lambda: {"connection_status": "broken"})
    sources.setattr(gmail.store, "is_connected", lambda row=None: False)
    out = svc.get_setup_status()
    assert out["gmail_connected"] is False and out["gmail_broken"] is True


def test_the_telegram_link_is_read_for_THIS_seat(sources):
    """#193 split the two: the bot token is install-wide, a linked chat belongs to one
    person. A status read must answer the asking seat's question, not somebody else's."""
    import telegram.store

    seen = {}
    sources.setattr(telegram.store, "get_link",
                    lambda user_id: seen.setdefault("user_id", user_id) and None)
    out = svc.get_setup_status(user_id=7)
    assert seen == {"user_id": 7}
    assert out["telegram_connected"] is True and out["telegram_linked"] is False


def test_a_minted_code_with_no_device_on_it_is_not_linked(sources):
    """`mint_link_code` upserts the seat's row and CLEARS `chat_id`, and a bot swap clears
    it too — so a row is not a link. `chat_id` is the predicate /api/telegram/status uses,
    and the two must agree or the card says "not linked" while the assistant says linked."""
    import telegram.store

    sources.setattr(telegram.store, "get_link",
                    lambda user_id: {"id": 1, "link_code": "abc123", "chat_id": ""})
    assert svc.get_setup_status(user_id=7)["telegram_linked"] is False


def test_an_unattended_turn_cannot_answer_the_link_question(sources):
    """There is no seat, so "is your chat linked" has no answer. `False` would invite the
    turn to tell the install to link a chat that may already exist — unknown is honest.
    The install-wide half is still reported."""
    out = svc.get_setup_status()
    assert out["telegram_connected"] is True
    assert out["telegram_linked"] is None


def test_an_unreadable_link_does_not_blank_the_bot_config_beside_it(sources):
    import telegram.store

    sources.setattr(telegram.store, "get_link", _boom)
    out = svc.get_setup_status(user_id=7)
    assert out["telegram_connected"] is True and out["telegram_linked"] is None


def test_an_unreadable_bot_config_does_not_blank_the_seat_link_beside_it(sources):
    import telegram.store

    sources.setattr(telegram.store, "get_settings", _boom)
    out = svc.get_setup_status(user_id=7)
    assert out["telegram_connected"] is None and out["telegram_linked"] is True


def test_unreadable_custom_fields_are_unknown_not_zero(sources):
    """Zero definitions and "could not count them" are different answers: the first
    invites Baker to explain how to add one, the second does not."""
    import crm.field_service

    sources.setattr(crm.field_service, "list_field_definitions", _boom)
    assert svc.get_setup_status()["custom_field_counts"] is None


def test_an_install_with_nothing_configured_reports_false_not_unknown(sources):
    import crm.field_service
    import gmail.store
    import providers
    import providers.credentials
    import telegram.store

    sources.setattr(providers, "get_ai_provider", lambda *a, **k: None)
    sources.setattr(providers.credentials, "CredentialStore", lambda: _Store(active=""))
    sources.setattr(gmail.store, "is_connected", lambda row=None: False)
    sources.setattr(gmail.store, "get_row", lambda: {"connection_status": "disconnected"})
    sources.setattr(telegram.store, "get_settings", lambda: {"connected": False})
    sources.setattr(telegram.store, "get_link", lambda user_id: None)
    sources.setattr(crm.field_service, "list_field_definitions", lambda *a, **k: [])
    out = svc.get_setup_status(user_id=7)
    assert out["ai_ready"] is False and out["active_provider"] == ""
    assert out["gmail_connected"] is False and out["gmail_broken"] is False
    assert out["telegram_connected"] is False and out["telegram_linked"] is False
    assert out["custom_field_counts"] == {"contact": 0, "company": 0, "deal": 0}


# ── The provider name is an enum, not whatever the column holds ───────────────

@pytest.mark.parametrize("stored", ["azure", "Anthropic", "anthropic ", "'; DROP TABLE", None])
def test_an_unrecognized_active_provider_is_reported_as_none_configured(sources, stored):
    import providers.credentials

    sources.setattr(providers.credentials, "CredentialStore", lambda: _Store(active=stored))
    assert svc.get_setup_status()["active_provider"] == ""


def test_the_provider_enum_matches_the_one_the_routes_validate_against():
    """Two lists of provider names would drift; this reads the canonical one."""
    from providers.router import ALL_PROVIDERS

    assert ALLOWED_PROVIDERS == {"", *ALL_PROVIDERS}


def test_the_counted_entities_match_the_ones_that_can_carry_custom_fields():
    from crm.field_service import VALID_ENTITY_TYPES

    assert set(svc._FIELD_ENTITIES) == VALID_ENTITY_TYPES


def test_an_unknown_entity_type_is_ignored_rather_than_counted(sources):
    import crm.field_service

    sources.setattr(crm.field_service, "list_field_definitions", lambda *a, **k: [
        {"entity_type": "contact"}, {"entity_type": "invoice"}, {},
    ])
    assert svc.get_setup_status()["custom_field_counts"] == {
        "contact": 1, "company": 0, "deal": 0}


def test_get_setup_status_never_raises(sources):
    """It is dispatched by the tool registry, which turns an exception into a tool error;
    this read has no reason to spend one — every fact it cannot get is simply unknown."""
    import crm.field_service
    import crm.service
    import gmail.store
    import providers
    import telegram.store

    for module, name in (
        (providers, "get_ai_provider"), (gmail.store, "get_row"),
        (telegram.store, "get_settings"), (telegram.store, "get_link"),
        (crm.service, "get_crm_meta"), (crm.field_service, "list_field_definitions"),
    ):
        sources.setattr(module, name, _boom)
    out = svc.get_setup_status(user_id=7)
    assert set(out) == EXPECTED_KEYS
    assert all(v is None for v in out.values())


# ── The two readers that cannot report their own failure ─────────────────────
#
# `CredentialStore._load` and `crm.service.get_task_mode` both catch every database error
# and return a plausible value — no provider configured, and the product-default task
# mode. Nothing raises, so the surrounding try/except cannot see it, and without a probe
# this surface would report a dead database as a deliberate configuration. The first of
# those is the worst case the nullable payload exists for: it invites Baker to walk
# someone through an AI Setup they already completed.

def test_a_failed_credential_load_is_unknown_not_unconfigured(sources):
    """Exactly what a database outage produces: the store hands back the empty shape and
    the factory hands back None, with NO exception anywhere. `load_failed` is the store
    reporting which of the two it was, off the same load that produced the shape."""
    import providers.credentials

    sources.setattr(providers.credentials, "CredentialStore",
                    lambda: _Store(active="", load_failed=True))
    sources.setattr(providers, "get_ai_provider", lambda *a, **k: None)
    out = svc.get_setup_status(user_id=7)
    assert out["ai_ready"] is None and out["active_provider"] is None
    # One dead source does not blank the ones that CAN answer.
    assert out["gmail_connected"] is True and out["task_mode"] == "gtd"


def test_readiness_is_resolved_against_the_SAME_load_the_provider_name_came_from(sources):
    """`get_ai_provider()` builds its own CredentialStore, which `load_failed` cannot see.
    Bare, a failure in that second load came back as `ai_ready: false` beside an
    `active_provider` from the first snapshot — and the two could also straddle a
    concurrent connect or disconnect. One store, handed in, removes both."""
    import providers.credentials

    mine = _Store()
    seen = {}
    sources.setattr(providers.credentials, "CredentialStore", lambda: mine)
    sources.setattr(providers, "get_ai_provider",
                    lambda *a, **k: seen.setdefault("store", k.get("store")) and object())
    out = svc.get_setup_status(user_id=7)
    assert seen["store"] is mine, "the factory loaded a second store"
    assert out["ai_ready"] is True and out["active_provider"] == "anthropic"


def test_the_factory_really_accepts_a_store_and_does_not_reload(monkeypatch):
    """Pinned against the real factory, not the fixture's stand-in: a keyword it quietly
    ignored would leave the window open while every mock-based test stayed green."""
    import providers
    import providers.credentials

    def _explode():
        raise AssertionError("the factory loaded its own store")

    monkeypatch.setattr(providers, "CredentialStore", _explode)
    mine = providers.credentials.CredentialStore.__new__(providers.credentials.CredentialStore)
    mine.load_failed = False
    mine.data = {"active_provider": "", "active_model": "", "profiles": {}}
    # Nothing configured, so it resolves to None without constructing an SDK client —
    # what matters is that it got there without loading a store of its own.
    assert providers.get_ai_provider(store=mine) is None


def test_the_store_really_sets_that_flag_when_its_load_fails(monkeypatch):
    """Pinned against the real CredentialStore, not the fixture's stand-in — the fixture
    could agree with a flag nothing ever sets."""
    import providers.credentials as creds

    monkeypatch.setattr(creds, "get_connection", _boom)
    store = creds.CredentialStore()
    assert store.load_failed is True
    assert store.data == {"active_provider": "", "active_model": "", "profiles": {}}


def test_an_unreadable_crm_meta_row_makes_the_task_mode_unknown(sources):
    """`get_task_mode` fail-safes to the product default, which is right for the four hot
    paths that call it and wrong here — a guess must not be reported as a fact. The value
    is therefore read through `get_crm_meta`, which lets the error propagate, so failure
    and value come from ONE query."""
    import crm.service

    sources.setattr(crm.service, "get_crm_meta", _boom)
    out = svc.get_setup_status(user_id=7)
    assert out["task_mode"] is None
    assert out["ai_ready"] is True


def test_an_absent_crm_meta_row_is_unknown_too(sources):
    """`get_crm_meta` answers a missing row with a default dict carrying no task_mode.
    The singleton is seeded by its migration and swept by nothing, so that means the row
    did not come back."""
    import crm.service

    sources.setattr(crm.service, "get_crm_meta",
                    lambda: {"id": 1, "sample_data_loaded": False})
    assert svc.get_setup_status(user_id=7)["task_mode"] is None


@pytest.mark.parametrize("stored,expected", [
    ("gtd", "gtd"), ("normal", "normal"), (None, "gtd"), ("", "gtd"), ("bogus", "gtd"),
])
def test_the_task_mode_rule_matches_the_one_every_other_reader_uses(
    sources, monkeypatch, stored, expected
):
    """This module restates `get_task_mode`'s normalization instead of calling it, so the
    two are pinned together against the same stored value — four readers already agree on
    this default and a fifth must not drift."""
    import crm.service

    sources.setattr(crm.service, "get_crm_meta", lambda: {"id": 1, "task_mode": stored})
    assert svc.get_setup_status(user_id=7)["task_mode"] == expected

    monkeypatch.setattr(crm.service, "pg_fetchone", lambda *a, **k: {"task_mode": stored})
    assert crm.service.get_task_mode() == expected


def test_a_readable_singleton_with_nothing_configured_still_reports_false(sources):
    """The probe must not turn "nobody has set this up" into "unknown" — that is the
    answer the tool exists to give."""
    import providers.credentials

    sources.setattr(providers.credentials, "CredentialStore", lambda: _Store(active=""))
    sources.setattr(providers, "get_ai_provider", lambda *a, **k: None)
    out = svc.get_setup_status(user_id=7)
    assert out["ai_ready"] is False and out["active_provider"] == ""


def test_no_source_is_answered_by_a_separate_preflight_query():
    """A probe can succeed in the instant before the read it was meant to vouch for
    fails, so every tell here has to ride the SAME query as the value. Read off the
    module source, because the shape is the guarantee."""
    import inspect

    source = inspect.getsource(svc)
    assert "_readable" not in source
    assert "SELECT 1" not in source.upper()
