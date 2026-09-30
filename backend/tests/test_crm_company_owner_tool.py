"""The assistant's company tools take an owner WORD (issue #237, port of cake_os #3184).

Hermetic: the service layer is patched, so these assert what the tool layer PASSES.
The rules, each pinned below:

  * ``owner`` is the #190 word — 'me', 'unassigned', or an email — resolved by the same
    ``_resolve_owner`` the reads use, never a raw id.
  * Create defaults to the asking seat (an unattended turn stamps nobody); update leaves
    the owner untouched unless one is named, and 'unassigned' CLEARS it.
  * A model-supplied ``owner_id`` is dropped on both tools, attended or not.
  * An assignment refuses a deactivated teammate and the read-only word 'everyone'.
"""

from unittest.mock import patch

import pytest

from crm import tools

USER = {"id": 7, "email": "rep@example.com", "name": "Rep", "role": "member"}
CREATE = "crm.tools.crm.create_company"
UPDATE = "crm.tools.crm.update_company"
LOOKUP = "crm.tools.users_service.get_user_by_email"


def _ex(user):
    return tools.get_crm_tools(user=user)[1]


def test_both_company_defs_advertise_owner():
    defs = {d["name"]: d for d in tools.CRM_TOOL_DEFS}
    for name in ("crm_create_company", "crm_update_company"):
        assert defs[name]["input_schema"]["properties"]["owner"]["type"] == "string"


@pytest.mark.parametrize("user,expected", [(USER, 7), (None, None)])
@pytest.mark.parametrize("owner", [None, "", "  "])
def test_create_defaults_to_the_asking_seat(user, expected, owner):
    with patch(CREATE, return_value={"id": 1}) as m:
        _ex(user)["crm_create_company"](name="Acme", owner=owner)
    assert m.call_args.kwargs["owner_id"] == expected


@pytest.mark.parametrize("tool,target,args", [
    ("crm_create_company", CREATE, {"name": "Acme"}),
    ("crm_update_company", UPDATE, {"company_id": 3}),
])
@pytest.mark.parametrize("owner,expected", [
    ("me", 7), ("Me", 7), ("unassigned", None), ("UNASSIGNED", None),
])
def test_owner_words_assign(tool, target, args, owner, expected):
    with patch(target, return_value={"id": 3}) as m:
        _ex(USER)[tool](**args, owner=owner)
    assert m.call_args.kwargs["owner_id"] == expected


@pytest.mark.parametrize("tool,target,args", [
    ("crm_create_company", CREATE, {"name": "Acme"}),
    ("crm_update_company", UPDATE, {"company_id": 3}),
])
def test_owner_email_resolves_to_an_active_teammate(tool, target, args):
    with patch(LOOKUP, return_value={"id": 12, "is_active": True}) as g, \
         patch(target, return_value={"id": 3}) as m:
        _ex(USER)[tool](**args, owner="Boss@Example.com")
    assert g.call_args.args[0] == "Boss@Example.com"
    assert m.call_args.kwargs["owner_id"] == 12


def test_update_without_owner_leaves_it_alone():
    # update_company reads KEY PRESENCE as "set this", and None there means unassigned —
    # so an omitted owner must send no owner_id key at all, or every edit would clear it.
    with patch(UPDATE, return_value={"id": 3}) as m:
        _ex(USER)["crm_update_company"](company_id=3, industry="Retail")
    assert "owner_id" not in m.call_args.kwargs
    assert m.call_args.kwargs["industry"] == "Retail"


@pytest.mark.parametrize("user", [USER, None])
@pytest.mark.parametrize("tool,target,args,expected", [
    ("crm_create_company", CREATE, {"name": "Acme"}, "seat"),
    ("crm_update_company", UPDATE, {"company_id": 3}, "absent"),
])
def test_model_supplied_owner_id_is_dropped(user, tool, target, args, expected):
    with patch(target, return_value={"id": 3}) as m:
        _ex(user)[tool](**args, owner_id=999)
    if expected == "seat":
        assert m.call_args.kwargs["owner_id"] == (user or {}).get("id")
    else:
        assert "owner_id" not in m.call_args.kwargs


@pytest.mark.parametrize("tool,target,args", [
    ("crm_create_company", CREATE, {"name": "Acme"}),
    ("crm_update_company", UPDATE, {"company_id": 3}),
])
@pytest.mark.parametrize("user,owner,lookup", [
    (USER, "ghost@example.com", None),                                  # nobody
    (USER, "gone@example.com", {"id": 12, "is_active": False}),         # deactivated
    (USER, "everyone", None),                                           # a filter word
    (None, "me", None),                                                 # unattended 'me'
])
def test_bad_owner_errors_without_writing(tool, target, args, user, owner, lookup):
    with patch(LOOKUP, return_value=lookup), patch(target) as m:
        result = _ex(user)[tool](**args, owner=owner)
    assert "error" in result and not m.called


def test_binding_does_not_move_the_tool_surface():
    # The background allowlist is derived from the writes map; binding a user must not
    # add, drop or reflag either company tool.
    bound, unbound = tools.get_crm_tools(user=USER), tools.get_crm_tools(user=None)
    assert [d["name"] for d in bound[0]] == [d["name"] for d in unbound[0]]
    assert set(bound[1]) == set(unbound[1])
