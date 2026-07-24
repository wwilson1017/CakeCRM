"""Assistant router contract — degradation gate, streaming, uploads, CRUD, identity.

Minimal FastAPI app (no lifespan / DATABASE_URL). Auth is overridden; the provider
factory, engine, and history are monkeypatched, so nothing touches a DB or SDK.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant import router as router_mod
from assistant.router import router as assistant_router
from core.auth import get_current_user


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(assistant_router, prefix="/api/assistant")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


async def _fake_stream(*a, **k):
    yield 'data: {"type": "done", "model": "m"}\n\n'


@pytest.fixture
def with_provider(monkeypatch):
    """A configured provider + a capturing engine.chat stub."""
    monkeypatch.setattr(router_mod, "get_ai_provider", lambda: object())
    captured = {}

    def _chat(provider, registry, messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return _fake_stream()

    monkeypatch.setattr(router_mod.engine, "chat", _chat)
    return captured


# ── Degradation gate ──────────────────────────────────────────────────────────

def test_chat_without_provider_returns_clean_400(client, monkeypatch):
    monkeypatch.setattr(router_mod, "get_ai_provider", lambda: None)
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400
    assert "AI Setup" in r.json()["detail"]  # friendly, points to setup — never a 500


def test_chat_requires_messages_or_conversation(client, with_provider):
    r = client.post("/api/assistant/chat", json={"messages": []})
    assert r.status_code == 400


def test_chat_streams_when_provider_present(client, with_provider):
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert '"type": "done"' in r.text
    assert with_provider["messages"] == [{"role": "user", "content": "hi"}]


# ── Uploads ───────────────────────────────────────────────────────────────────

def test_upload_prepends_extracted_text(client, with_provider):
    payload = json.dumps({"messages": [{"role": "user", "content": "what is this?"}]})
    r = client.post(
        "/api/assistant/chat/upload",
        data={"payload": payload},
        files=[("files", ("notes.txt", b"secret plans", "text/plain"))],
    )
    assert r.status_code == 200
    content = with_provider["messages"][-1]["content"]
    assert "secret plans" in content and content.rstrip().endswith("what is this?")
    # title_hint is the user's typed text, not the file content
    assert with_provider["kwargs"]["title_hint"] == "what is this?"


def test_upload_rejects_bad_json(client, with_provider):
    r = client.post("/api/assistant/chat/upload", data={"payload": "not-json"},
                    files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 400


def test_upload_rejects_disallowed_extension(client, with_provider):
    payload = json.dumps({"messages": [{"role": "user", "content": "hi"}]})
    r = client.post("/api/assistant/chat/upload", data={"payload": payload},
                    files=[("files", ("evil.exe", b"x", "application/octet-stream"))])
    assert r.status_code == 400


def test_upload_rejects_too_many_files(client, with_provider):
    payload = json.dumps({"messages": [{"role": "user", "content": "hi"}]})
    files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(6)]
    r = client.post("/api/assistant/chat/upload", data={"payload": payload}, files=files)
    assert r.status_code == 400


def test_upload_rejects_oversize_file(client, with_provider):
    from assistant import uploads
    payload = json.dumps({"messages": [{"role": "user", "content": "hi"}]})
    big = b"a" * (uploads.MAX_FILE_SIZE + 10)
    r = client.post("/api/assistant/chat/upload", data={"payload": payload},
                    files=[("files", ("big.txt", big, "text/plain"))])
    assert r.status_code == 400


# ── Confirm ───────────────────────────────────────────────────────────────────

def test_confirm_invalid_decision_400(client):
    r = client.post("/api/assistant/confirm",
                    json={"conversation_id": "c1", "tool_use_id": "t1", "decision": "maybe"})
    assert r.status_code == 400


def test_confirm_delegates_to_resolver(client, monkeypatch):
    monkeypatch.setattr(router_mod.engine, "resolve_confirmation",
                        lambda reg, cid, tuid, decision: {"tool": "crm_create_contact", "decision": decision})
    r = client.post("/api/assistant/confirm",
                    json={"conversation_id": "c1", "tool_use_id": "t1", "decision": "approve"})
    assert r.status_code == 200 and r.json()["decision"] == "approve"


# ── Conversations ─────────────────────────────────────────────────────────────

def test_list_conversations(client, monkeypatch):
    monkeypatch.setattr(router_mod.history, "list_conversations", lambda limit, offset: [{"id": "c1"}])
    r = client.get("/api/assistant/conversations")
    assert r.json()["conversations"] == [{"id": "c1"}]


def test_get_conversation_merges_previews_and_strips_results(client, monkeypatch):
    conv = {
        "id": "c1",
        "messages": [{
            "id": "m1", "role": "assistant", "content": "",
            "tool_calls": [{"tool": "crm_dashboard", "tool_use_id": "t1", "args": {}}],
            "tool_results": [{"tool_use_id": "t1", "tool_name": "crm_dashboard", "content": '{"n": 3}'}],
        }],
    }
    monkeypatch.setattr(router_mod.history, "get_conversation", lambda cid: conv)
    r = client.get("/api/assistant/conversations/c1")
    msg = r.json()["messages"][0]
    assert "tool_results" not in msg
    assert msg["tool_calls"][0]["result"] == {"n": 3}  # preview folded in, parsed


def test_get_conversation_404(client, monkeypatch):
    monkeypatch.setattr(router_mod.history, "get_conversation", lambda cid: None)
    assert client.get("/api/assistant/conversations/nope").status_code == 404


def test_delete_conversation_404(client, monkeypatch):
    monkeypatch.setattr(router_mod.history, "delete_conversation", lambda cid: False)
    assert client.delete("/api/assistant/conversations/nope").status_code == 404


def test_rename_conversation(client, monkeypatch):
    monkeypatch.setattr(router_mod.history, "rename_conversation", lambda cid, title: "New Name")
    r = client.patch("/api/assistant/conversations/c1/title", json={"title": "New Name"})
    assert r.json()["title"] == "New Name"


# ── Identity ──────────────────────────────────────────────────────────────────

def test_get_identity(client, monkeypatch):
    monkeypatch.setattr(router_mod.identity, "get_identity",
                        lambda: {"name": "Baker", "personality": "p", "using_default": True})
    assert client.get("/api/assistant/identity").json()["name"] == "Baker"


def test_put_identity_blank_name_400(client):
    assert client.put("/api/assistant/identity", json={"name": "   "}).status_code == 400


def test_put_identity_updates(client, monkeypatch):
    monkeypatch.setattr(router_mod.identity, "update_identity",
                        lambda name, personality: {"name": name, "personality": personality or "", "using_default": not personality})
    r = client.put("/api/assistant/identity", json={"name": "Ace", "personality": "friendly"})
    assert r.json()["name"] == "Ace"


# ── Auth audit ────────────────────────────────────────────────────────────────

def test_every_route_requires_auth():
    for route in assistant_router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        dep_names = [d.call.__name__ for d in dependant.dependencies]
        assert "get_current_user" in dep_names, f"{route.path} missing auth"
