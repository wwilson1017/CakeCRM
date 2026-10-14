"""Baker's reminder tools (issue #283): identity binding and people resolution."""

import pytest

from reminders import service, tools

SEAT = {"id": 3, "email": "ada@example.test", "name": "Ada"}


@pytest.fixture
def created(monkeypatch):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return {"id": 1, "note": kw["note"], "owner_id": kw["owner_id"]}

    monkeypatch.setattr(service, "create_reminder", create)
    monkeypatch.setattr(tools.users_service, "get_user_by_email", lambda e: {
        "bo@example.test": {"id": 4, "is_active": True},
        "gone@example.test": {"id": 5, "is_active": False}}.get(e))
    names = {"cy": [{"id": 6, "email": "cy@example.test"}],
             "sam": [{"id": 7, "email": "sam1@example.test"}, {"id": 8, "email": "sam2@example.test"}]}
    monkeypatch.setattr(tools, "pg_fetchall", lambda sql, params: names.get(params[0].lower(), []))
    return seen


def _create(user=SEAT, **args):
    return tools.get_reminder_tools(user)[1]["reminder_create"](**args)


def test_the_four_tools_and_their_write_flags():
    defs = {d["name"]: d["writes"] for d in tools.REMINDER_TOOL_DEFS}
    assert defs == {"reminder_create": True, "reminder_list": False,
                    "reminder_update": True, "reminder_void": True}
    assert not any("confirm_tier" in d for d in tools.REMINDER_TOOL_DEFS)


def test_owner_and_author_come_from_the_seat_not_the_model(created):
    _create(note="x", due_date="2030-01-01", owner_id=99, created_by=99)
    assert created["owner_id"] == 3 and created["created_by"] == 3


def test_the_model_cannot_pass_the_clock(created):
    with pytest.raises(TypeError):
        _create(note="x", due_date="2030-01-01", now="2000-01-01")


def test_people_resolve_from_me_email_and_exact_name(created):
    _create(note="x", due_date="2030-01-01", people=["me", "bo@example.test", "Cy", "me"])
    assert created["people"] == [3, 4, 6]


@pytest.mark.parametrize("word, message", [
    ("sam", "matches several people: sam1@example.test, sam2@example.test"),
    ("nobody", "No active user matches 'nobody'"),
    ("gone@example.test", "No active user matches 'gone@example.test'"),
])
def test_people_resolution_errors_are_tool_results(created, word, message):
    out = _create(note="x", due_date="2030-01-01", people=[word])
    assert message in out["error"]
    assert not created


def test_me_in_an_unattended_run_is_an_error(created):
    out = _create(user=None, note="x", due_date="2030-01-01", people=["me"])
    assert "unattended" in out["error"]


def test_list_scope_binds_the_seat_and_restates_the_untrusted_note(monkeypatch):
    seen = []
    monkeypatch.setattr(service, "list_reminders", lambda **kw: seen.append(kw) or [])
    ex = tools.get_reminder_tools(SEAT)[1]["reminder_list"]
    out = ex(mine_user_id=99)
    ex(scope="all")
    tools.get_reminder_tools(None)[1]["reminder_list"]()
    assert [s["mine_user_id"] for s in seen] == [3, None, None]
    assert out["note"] == tools._UNTRUSTED_NOTE


def test_update_resolves_people_and_drops_the_clock(monkeypatch, created):
    seen = {}
    monkeypatch.setattr(service, "update_reminder",
                        lambda rid, fields: seen.update(fields) or {"id": rid, "note": "n", "owner_id": 3})
    out = tools.get_reminder_tools(SEAT)[1]["reminder_update"](
        reminder_id=1, people=["me", "Cy"], now="2000-01-01")
    assert seen == {"people": [3, 6]}
    assert out["target"]["entity_type"] == "reminder"


def test_update_and_void_on_a_missing_reminder_are_tool_errors(monkeypatch):
    from crm.gtd_common import NotFoundError

    def missing(*a, **k):
        raise NotFoundError("x")

    monkeypatch.setattr(service, "update_reminder", missing)
    monkeypatch.setattr(service, "void_reminder", missing)
    ex = tools.get_reminder_tools(SEAT)[1]
    assert ex["reminder_update"](reminder_id=9, note="x") == {"error": "Not found"}
    assert ex["reminder_void"](reminder_id=9, reason="x") == {"error": "Not found"}
