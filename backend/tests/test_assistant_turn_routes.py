"""Detached turns (#282) over the wire — the route contract, DB-free.

The runner's store is swapped for the in-memory twin from `test_assistant_turns`, the
engine for a scripted stream. Pins: every frame of a started turn carries a `seq` and
opens with `turn_start`; the turn id travels in the JSON body and as an upload form field;
a re-sent id replays for its sender and is a 404 for anyone else; the two turn routes are
404 (never 403) unless the caller started the turn; the conversation read reports a live
turn; and chat still answers when the turn log is down.
"""

import json
import uuid

import pytest
from conftest import fake_admin, fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_assistant_turns import STARTED_AT, TID, MemoryTurnStore, seed

from assistant import router as router_mod, turns
from assistant.router import router as assistant_router
from core.auth import get_current_user
from providers.base import _sse


@pytest.fixture
def who():
    return {"user": fake_admin()}


@pytest.fixture
def client(who):
    app = FastAPI()
    app.include_router(assistant_router, prefix="/api/assistant")
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    return TestClient(app)


@pytest.fixture
def store(monkeypatch):
    store = MemoryTurnStore()
    monkeypatch.setattr(turns.runner, "store", store)
    monkeypatch.setattr(turns.runner, "_runs", {})  # no run leaks between tests
    monkeypatch.setattr(turns, "DB_FLUSH_S", 0.005)
    monkeypatch.setattr(turns, "REMOTE_POLL_S", 0.005)
    return store


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(router_mod, "get_ai_provider", lambda: object())
    seen = {"calls": 0}

    def _chat(provider, registry, messages, **kwargs):
        seen["calls"] += 1
        seen["kwargs"] = kwargs

        async def gen():
            yield _sse({"type": "conversation_id", "id": "conv-1"})
            yield _sse({"type": "text", "text": "Hello"})
            yield _sse({"type": "done", "model": "m"})
        return gen()

    monkeypatch.setattr(router_mod.engine, "chat", _chat)
    return seen


def _frames(r) -> list[dict]:
    return [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]


def _chat(client, turn_id=TID):
    return client.post("/api/assistant/chat",
                       json={"messages": [{"role": "user", "content": "hi"}], "turn_id": turn_id})


def test_chat_stream_opens_with_turn_start_and_every_frame_carries_seq(client, store, engine):
    r = _chat(client)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    got = _frames(r)
    assert [f["type"] for f in got] == ["turn_start", "conversation_id", "text", "done"]
    assert [f["seq"] for f in got] == [0, 1, 2, 3]
    assert got[0]["turn_id"] == TID
    assert store.turns[TID]["status"] == "finished"
    assert store.turns[TID]["conversation_id"] == "conv-1"
    assert store.turns[TID]["user_id"] == fake_admin()["id"]


@pytest.mark.parametrize("bad", [None, "nope", 123])
def test_a_missing_or_invalid_turn_id_is_minted_by_the_server(client, store, engine, bad):
    body = {"messages": [{"role": "user", "content": "hi"}]}
    if bad is not None:
        body["turn_id"] = bad
    r = client.post("/api/assistant/chat", json=body)
    if isinstance(bad, int):
        assert r.status_code == 422  # pydantic: a str field
        return
    minted = _frames(r)[0]["turn_id"]
    assert str(uuid.UUID(minted)) == minted and minted in store.turns


def test_upload_takes_the_turn_id_as_a_form_field(client, store, engine):
    payload = json.dumps({"messages": [{"role": "user", "content": "typed"}]})
    r = client.post("/api/assistant/chat/upload", data={"payload": payload, "turn_id": TID})
    assert r.status_code == 200
    assert _frames(r)[0] == {"type": "turn_start", "turn_id": TID, "started_at": STARTED_AT, "seq": 0}
    assert engine["kwargs"]["title_hint"] == "typed"  # the existing upload contract holds


def test_a_resent_turn_id_replays_for_its_sender_and_404s_for_anyone_else(client, store, engine, who):
    first = _frames(_chat(client))
    again = _frames(_chat(client))
    assert again == first and engine["calls"] == 1
    who["user"] = fake_member()
    r = _chat(client)
    assert r.status_code == 404 and r.json() == {"detail": "Turn not found."}


def test_chat_still_answers_when_the_turn_log_is_down(client, store, engine):
    store.fail = {"insert"}
    r = _chat(client)
    assert r.status_code == 200
    got = _frames(r)
    assert [f["type"] for f in got] == ["conversation_id", "text", "done"]
    assert all("seq" not in f for f in got) and store.turns == {}


def test_chat_with_no_database_at_all_still_answers(client, engine):
    # The real TurnStore with no pool — the path every DB-free router test rides.
    r = _chat(client)
    assert r.status_code == 200 and _frames(r)[-1]["type"] == "done"


def test_events_replays_from_after_and_ends_on_the_terminal_frame(client, store):
    seed(store, events=("turn_start", "text", "done"), status="finished")
    r = client.get(f"/api/assistant/turns/{TID}/events?after=0")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert [f["seq"] for f in _frames(r)] == [1, 2]
    assert [f["seq"] for f in _frames(client.get(f"/api/assistant/turns/{TID}/events"))] == [0, 1, 2]


def test_turn_routes_404_unless_the_caller_started_the_turn(client, store, who):
    seed(store, user_id=fake_admin()["id"])
    who["user"] = fake_member()
    mine_elsewhere = [
        client.get(f"/api/assistant/turns/{TID}/events"),
        client.post(f"/api/assistant/turns/{TID}/cancel"),
    ]
    unknown = client.get("/api/assistant/turns/22222222-2222-4222-8222-222222222222/events")
    for r in [*mine_elsewhere, unknown]:
        assert r.status_code == 404 and r.content == b'{"detail":"Turn not found."}'
    assert store.turns[TID]["cancel_requested_at"] is None


def test_an_unreachable_turn_log_is_a_503_not_a_404(client, store):
    seed(store)
    store.fail = {"get"}
    assert client.get(f"/api/assistant/turns/{TID}/events").status_code == 503
    assert client.post(f"/api/assistant/turns/{TID}/cancel").status_code == 503


def test_cancel_returns_whether_it_was_taken(client, store):
    seed(store)
    assert client.post(f"/api/assistant/turns/{TID}/cancel").json() == {"cancelled": True}
    assert store.turns[TID]["cancel_reason"] == turns.STOP_REASON
    seed(store, "done-one", events=("turn_start", "done"), status="finished")
    assert client.post("/api/assistant/turns/done-one/cancel").json() == {"cancelled": False}


def _owned_conversation(monkeypatch):
    monkeypatch.setattr(router_mod.history, "get_conversation",
                        lambda conv_id, user_id: {"id": conv_id, "messages": []})


def test_conversation_read_reports_the_running_turn(client, store, monkeypatch):
    _owned_conversation(monkeypatch)
    assert client.get("/api/assistant/conversations/c1").json()["running_turn"] is None
    seed(store, conversation_id="c1")
    assert client.get("/api/assistant/conversations/c1").json()["running_turn"] == {
        "turn_id": TID, "started_at": STARTED_AT, "last_seq": 1,
    }


def test_the_read_that_judges_a_stale_turn_reports_no_running_turn(client, store, monkeypatch):
    _owned_conversation(monkeypatch)
    seed(store, conversation_id="c1")
    store.now += turns.STALE_AFTER_S + 1
    assert client.get("/api/assistant/conversations/c1").json()["running_turn"] is None
    assert store.turns[TID]["status"] == "dead"
    assert store.events[TID][-1]["dead_turn"] is True


def test_turn_routes_require_auth():
    paths = {"/turns/{turn_id}/events", "/turns/{turn_id}/cancel"}
    for route in assistant_router.routes:
        if route.path in paths:
            assert get_current_user in [d.call for d in route.dependant.dependencies]
            paths.discard(route.path)
    assert paths == set()
