"""#260: Baker's two todo-create tools stamp `source='agent'` through the server-side
binding, and a model-supplied `source` never reaches the store — the GTD UI's "added by"
label is only honest if provenance is the server's to write."""
import pytest

from crm import gtd_common, gtd_tools, tools


def _capture(monkeypatch, module, attr):
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return {"id": 1, "title": "Call Acme"}

    monkeypatch.setattr(module, attr, fake)
    return calls


@pytest.mark.parametrize("user", [{"id": 3, "role": "member"}, None])
def test_crm_create_todo_stamps_agent_and_drops_a_model_source(monkeypatch, user):
    monkeypatch.setattr(tools.crm, "get_todo_mode", lambda: "normal")
    calls = _capture(monkeypatch, tools.crm, "create_todo")
    _, executors = tools.get_crm_tools(user=user)
    executors["crm_create_todo"](title="Call Acme", source="capture_web")
    assert calls[0]["source"] == "agent"


@pytest.mark.parametrize("user", [{"id": 3, "role": "member"}, None])
def test_gtd_todo_create_stamps_agent_and_drops_a_model_source(monkeypatch, user):
    monkeypatch.setattr(gtd_tools.service, "get_todo_mode", lambda: "gtd")
    calls = _capture(monkeypatch, gtd_tools.gtd_service, "create_todo")
    _, executors = gtd_tools.get_gtd_tools(user=user)
    executors["todo_create"](title="Call Acme", source="capture_web")
    assert calls[0]["source"] == "agent"


def test_observer_is_a_known_source():
    assert "observer" in gtd_common.TODO_SOURCES
