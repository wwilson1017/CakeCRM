"""Identity in the assistant's tool layer (issue #190, Multi-user Phase B / B1).

Hermetic: the service layer is patched, so these assert what the tool layer PASSES
rather than what the database stores (the integration suite covers the storing).

Three properties, and they are the whole point of the change:

  1. **Attribution.** An attended turn credits the seat that is talking; an unattended
     one credits nobody, exactly as it did before Phase B.
  2. **The model cannot say who.** Every identity argument is bound server-side and any
     same-named key in the model's arguments is dropped — including on the unattended
     path, where "no user" must mean NULL and not "whatever the model produced".
  3. **Identity does not move the tool surface.** The background allowlist is derived
     from ``writes_map``, so a user-bound registry must expose the identical tool names
     and the identical flags as an unbound one.
"""

from unittest.mock import patch

import pytest

from assistant.registry import ToolRegistry
from crm import service, tools

USER = {"id": 7, "email": "rep@example.com", "name": "Rep", "role": "member"}
OTHER = {"id": 12, "email": "boss@example.com", "name": "Boss", "role": "admin"}


@pytest.fixture
def crm_executors(task_mode):
    """Executor maps for an attended and an unattended turn, in normal task mode.

    Normal mode so ``crm_list_tasks`` is present: GTD mode swaps the task tools out, and
    the no-database fail-safe answers 'gtd' (#102).
    """
    task_mode("normal")

    def _for(user):
        return tools.get_crm_tools(user=user)[1]

    return _for


# ── 1. Attribution ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("user,expected", [(USER, 7), (None, None)])
def test_log_activity_credits_the_talking_seat(crm_executors, user, expected):
    ex = crm_executors(user)
    with patch("crm.tools.crm.log_activity", return_value={"id": 1}) as m:
        ex["crm_log_activity"](activity="call", note="rang them")
    assert m.call_args.kwargs["actor_id"] == expected


@pytest.mark.parametrize("user,expected", [(USER, 7), (None, None)])
def test_log_note_alias_credits_the_same_seat(crm_executors, user, expected):
    # crm_log_note is an executor-only alias onto crm_log_activity. It has no def of its
    # own, which is exactly how an alias gets forgotten when a wrapper is added.
    ex = crm_executors(user)
    with patch("crm.tools.crm.log_activity", return_value={"id": 1}) as m:
        ex["crm_log_note"](activity="note", note="left a voicemail")
    assert m.call_args.kwargs["actor_id"] == expected


@pytest.mark.parametrize("user,expected", [(USER, 7), (None, None)])
def test_add_note_records_its_author(crm_executors, user, expected):
    ex = crm_executors(user)
    with patch("crm.tools.chatter_service.add_note", return_value={"id": 1}) as m:
        ex["crm_add_note"](entity_type="deal", entity_id=3, message="hi")
    assert m.call_args.kwargs["author_id"] == expected


@pytest.mark.parametrize("tool,target", [
    ("crm_create_contact", "crm.tools.crm.create_contact"),
    ("crm_create_company", "crm.tools.crm.create_company"),
    ("crm_create_deal", "crm.tools.crm.create_deal"),
    ("crm_create_task", "crm.tools.crm.create_task"),
])
@pytest.mark.parametrize("user,expected", [(USER, 7), (None, None)])
def test_interactive_creates_stamp_the_requesting_seat(crm_executors, tool, target, user, expected):
    ex = crm_executors(user)
    with patch(target, return_value={"id": 5, "name": "x", "title": "x"}) as m:
        ex[tool](name="Acme", title="Acme")
    assert m.call_args.kwargs["owner_id"] == expected


# ── 2. The model cannot say who ────────────────────────────────────────────────

@pytest.mark.parametrize("tool,target,key,spoof", [
    ("crm_log_activity", "crm.tools.crm.log_activity", "actor_id", {"activity": "call"}),
    ("crm_log_note", "crm.tools.crm.log_activity", "actor_id", {"activity": "note"}),
    ("crm_create_contact", "crm.tools.crm.create_contact", "owner_id", {"name": "Acme"}),
    ("crm_create_company", "crm.tools.crm.create_company", "owner_id", {"name": "Acme"}),
    ("crm_create_deal", "crm.tools.crm.create_deal", "owner_id", {"title": "Acme"}),
    ("crm_create_task", "crm.tools.crm.create_task", "owner_id", {"title": "Call"}),
])
def test_model_supplied_identity_is_dropped_not_honored(crm_executors, tool, target, key, spoof):
    ex = crm_executors(USER)
    with patch(target, return_value={"id": 5}) as m:
        ex[tool](**spoof, **{key: 999})
    assert m.call_args.kwargs[key] == 7, "the model set the identity argument"


def test_add_note_author_cannot_be_spoofed(crm_executors):
    ex = crm_executors(USER)
    with patch("crm.tools.chatter_service.add_note", return_value={"id": 1}) as m:
        ex["crm_add_note"](entity_type="deal", entity_id=3, message="hi", author_id=999)
    assert m.call_args.kwargs["author_id"] == 7


@pytest.mark.parametrize("tool,target,key,spoof", [
    ("crm_log_activity", "crm.tools.crm.log_activity", "actor_id", {"activity": "call"}),
    ("crm_create_deal", "crm.tools.crm.create_deal", "owner_id", {"title": "Acme"}),
])
def test_unattended_turn_stamps_nobody_even_when_asked(crm_executors, tool, target, key, spoof):
    # The binding is unconditional on purpose: "no user" has to mean NULL, not "take the
    # model's word for it". A prompt injection in a deal note is the realistic source.
    ex = crm_executors(None)
    with patch(target, return_value={"id": 5}) as m:
        ex[tool](**spoof, **{key: 999})
    assert m.call_args.kwargs[key] is None


def test_raw_owner_id_on_a_read_is_ignored(crm_executors):
    # The model names people; ids are ours. Honoring a model-supplied owner_id would let
    # it read another seat's slice by guessing a number.
    ex = crm_executors(USER)
    with patch("crm.tools.crm.search_contacts", return_value=[]) as m:
        ex["crm_find_contact"](query="a", owner_id=999)
    assert m.call_args.kwargs["owner_id"] is None


def test_confirmed_write_is_credited_to_the_approver():
    # /confirm builds its registry with the APPROVER, so the executor the stored call
    # resolves to already carries their seat — which is why resolve_confirmation needs no
    # signature change. Pinned here because that reasoning is invisible at the call site.
    registry = ToolRegistry(user=OTHER)
    with patch("crm.tools.crm.log_activity", return_value={"id": 1}) as m:
        registry.execute_tool_sync("crm_log_activity", {"activity": "call", "actor_id": 7})
    assert m.call_args.kwargs["actor_id"] == 12


# ── 3. Owner resolution ────────────────────────────────────────────────────────

@pytest.mark.parametrize("owner,expected", [
    (None, None), ("", None), ("all", None), ("everyone", None),
    ("me", 7), ("Me", 7), ("mine", 7),
    ("unassigned", service.UNASSIGNED), ("UNASSIGNED", service.UNASSIGNED),
])
def test_owner_words_resolve(crm_executors, owner, expected):
    ex = crm_executors(USER)
    with patch("crm.tools.crm.search_contacts", return_value=[]) as m:
        ex["crm_find_contact"](query="a", owner=owner)
    assert m.call_args.kwargs["owner_id"] == expected


def test_owner_email_resolves_through_users(crm_executors):
    ex = crm_executors(USER)
    with patch("crm.tools.users_service.get_user_by_email", return_value={"id": 12}) as g, \
         patch("crm.tools.crm.search_companies", return_value=[]) as m:
        ex["crm_search_companies"](query="a", owner="Boss@Example.com")
    assert g.call_args.args[0] == "Boss@Example.com"
    assert m.call_args.kwargs["owner_id"] == 12


def test_a_deactivated_teammate_still_resolves(crm_executors):
    # Deliberate: records outlive the seat, and "what was Ana working on before she left"
    # is the question this filter exists for. Pinned because it is a decision, not an
    # accident — get_user_by_email does not filter on is_active, and the next reader
    # should be able to tell which of those two it is.
    ex = crm_executors(USER)
    departed = {"id": 12, "email": "gone@example.com", "is_active": False}
    with patch("crm.tools.users_service.get_user_by_email", return_value=departed), \
         patch("crm.tools.crm.search_contacts", return_value=[]) as m:
        ex["crm_find_contact"](query="a", owner="gone@example.com")
    assert m.call_args.kwargs["owner_id"] == 12


def test_unknown_owner_errors_without_querying(crm_executors):
    ex = crm_executors(USER)
    with patch("crm.tools.users_service.get_user_by_email", return_value=None), \
         patch("crm.tools.crm.search_companies") as m:
        result = ex["crm_search_companies"](query="a", owner="ghost@example.com")
    assert "error" in result and not m.called


def test_owner_me_on_an_unattended_turn_errors_without_querying(crm_executors):
    ex = crm_executors(None)
    with patch("crm.tools.analytics_service.get_stale_deals") as m:
        result = ex["crm_get_stale_deals"](owner="me")
    assert "error" in result and not m.called


@pytest.mark.parametrize("tool,target,kwargs", [
    ("crm_find_contact", "crm.tools.crm.search_contacts", {"query": "a"}),
    ("crm_search_companies", "crm.tools.crm.search_companies", {"query": "a"}),
    ("crm_list_tasks", "crm.tools.crm.list_tasks", {}),
    ("crm_get_stale_deals", "crm.tools.analytics_service.get_stale_deals", {}),
    ("crm_get_contact_staleness", "crm.tools.analytics_service.get_contact_staleness", {}),
])
def test_every_owner_filterable_read_threads_the_filter(crm_executors, tool, target, kwargs):
    ex = crm_executors(USER)
    with patch(target, return_value={"deals": [], "contacts": []}) as m:
        ex[tool](owner="me", **kwargs)
    assert m.call_args.kwargs["owner_id"] == 7


def test_todo_tools_carry_the_same_identity(task_mode):
    from crm import gtd_tools
    task_mode("gtd")
    _, ex = gtd_tools.get_gtd_tools(user=USER)
    with patch("crm.gtd_tools.gtd_service.create_todo", return_value={"id": 1}) as m:
        ex["todo_create"](title="Call Acme", owner_id=999)
    assert m.call_args.kwargs["owner_id"] == 7
    with patch("crm.gtd_tools.gtd_service.list_todos", return_value=[]) as m:
        ex["todo_list"](owner="unassigned")
    assert m.call_args.kwargs["owner_id"] == service.UNASSIGNED


# ── 4. Identity does not move the tool surface ─────────────────────────────────

def test_user_binding_changes_no_tool_name_and_no_writes_flag(task_mode):
    # background_allowlist() is derived from writes_map. If identity could shift either,
    # binding a user would silently widen or narrow what an unattended turn may call.
    task_mode("normal")
    anonymous = ToolRegistry()
    attended = ToolRegistry(user=USER)
    assert anonymous.writes_map == attended.writes_map
    assert set(anonymous.executors) == set(attended.executors)


def test_owner_property_is_advertised_on_every_owner_filterable_read(task_mode):
    task_mode("normal")
    reg = ToolRegistry(user=USER)
    advertised = {
        d["name"] for d in reg.tool_defs if "owner" in d["input_schema"]["properties"]
    }
    assert advertised == {
        "crm_find_contact", "crm_search_companies", "crm_list_tasks",
        "crm_get_stale_deals", "crm_get_contact_staleness",
    }
    for d in reg.tool_defs:
        if "owner" in d["input_schema"]["properties"]:
            assert d["writes"] is False, d["name"]
            assert "owner" not in d["input_schema"].get("required", [])


def test_owner_is_never_required_and_never_an_id(task_mode):
    # The description is what the model reads. It must offer words, not ids — a def that
    # invited an id would undo the executor's guard by making the id look legitimate.
    task_mode("normal")
    reg = ToolRegistry(user=USER)
    for d in reg.tool_defs:
        prop = d["input_schema"]["properties"].get("owner")
        if prop is not None:
            assert prop["type"] == "string"
            assert "'me'" in prop["description"] and "'unassigned'" in prop["description"]


def test_registry_exposes_its_seat():
    assert ToolRegistry(user=USER).user == USER
    assert ToolRegistry().user is None
    assert ToolRegistry(background=True).user is None


# ── 5. The owner-filter sentinel in the shared WHERE builders ──────────────────

@pytest.mark.parametrize("owner_id,expected_sql,expected_params", [
    (None, None, []),
    (service.UNASSIGNED, "t.owner_id IS NULL", []),
    (7, "t.owner_id = %s", [7]),
])
def test_owner_condition(owner_id, expected_sql, expected_params):
    params: list = []
    assert service.owner_condition("t.owner_id", owner_id, params) == expected_sql
    assert params == expected_params


@pytest.mark.parametrize("builder,args", [
    (service._contact_search_where, ("bo", None, None)),
    (service._company_search_where, ("bo", None)),
])
def test_shared_where_builders_understand_the_sentinel(builder, args):
    # The builders exist so a filter can never reach a page query without also reaching
    # its COUNT. Both call sites of each go through here, so testing the builder tests both.
    everyone, _ = builder(*args, None)
    unowned, unowned_params = builder(*args, service.UNASSIGNED)
    mine, mine_params = builder(*args, 7)
    assert "owner_id" not in everyone
    assert "owner_id IS NULL" in unowned and 7 not in unowned_params
    assert "owner_id = %s" in mine and mine_params[-1] == 7
