"""#260: Baker's two todo-create tools stamp `source='agent'` themselves, and a
model-supplied `source` never reaches the store — the GTD UI's "added by" label is only
honest if provenance is the server's to write."""
from crm import gtd_common, gtd_tools, tools


def _capture(monkeypatch, module, attr):
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return {"id": 1, "title": kwargs.get("title", args[0] if args else "")}

    monkeypatch.setattr(module, attr, fake)
    return calls


def test_crm_create_todo_stamps_agent_and_drops_a_model_source(monkeypatch):
    calls = _capture(monkeypatch, tools.crm, "create_todo")
    tools.crm_create_todo("Call Acme", source="capture_web")
    assert calls[0]["source"] == "agent"


def test_gtd_todo_create_stamps_agent_and_drops_a_model_source(monkeypatch):
    calls = _capture(monkeypatch, gtd_tools.gtd_service, "create_todo")
    gtd_tools._todo_create("Call Acme", source="capture_web")
    assert calls[0]["source"] == "agent"


def test_observer_is_a_known_source():
    assert "observer" in gtd_common.TODO_SOURCES
