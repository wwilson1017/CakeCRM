from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from core.auth import get_current_user
from core.ratelimit import IPRateLimiter
from voice import router as mod, service


class FakeTranscriber:
    can_transcribe = True
    text = "hello"

    def __init__(self):
        self.calls = []

    async def transcribe(self, *args):
        self.calls.append(args)
        return self.text


@pytest.fixture
def harness(monkeypatch):
    fake = FakeTranscriber()
    monkeypatch.setattr(mod, "get_transcriber", lambda: fake)
    monkeypatch.setattr(mod, "get_ai_provider", lambda **kw: None)
    mod._limiter.clear()
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/voice")
    app.dependency_overrides[get_current_user] = lambda: {"id": 7, "role": "member"}
    return SimpleNamespace(client=TestClient(app), fake=fake, app=app)


def post(h, data=b"audio", mime="audio/webm", tidy=False):
    return h.client.post('/api/voice/transcribe' + ('?tidy=true' if tidy else ''), content=data,
                         headers={"Content-Type": mime})


@pytest.mark.parametrize("case, status", [("no-provider", 400), ("throttled", 429), ("mime", 400)])
def test_refusals_happen_before_the_body_is_read(harness, monkeypatch, case, status):
    reads = []

    async def stream(self):
        reads.append(True)
        yield b"audio"

    monkeypatch.setattr(Request, "stream", stream)
    if case == "no-provider":
        monkeypatch.setattr(mod, "get_transcriber", lambda: None)
    if case == "throttled":
        monkeypatch.setattr(mod, "_limiter", IPRateLimiter(60, 0))
    response = post(harness, mime="text/plain" if case == "mime" else "audio/webm")
    assert response.status_code == status
    assert reads == []
    assert harness.fake.calls == []
    if case == "no-provider":
        assert "provider" in response.json()["detail"]


def test_codec_parameter_is_stripped(harness):
    assert post(harness, mime="audio/webm;codecs=opus").status_code == 200
    assert harness.fake.calls == [(b"audio", "audio/webm", "clip.webm")]


def test_oversize_is_413(harness):
    assert post(harness, data=b"x" * (service.MAX_AUDIO_BYTES + 1)).status_code == 413
    assert harness.fake.calls == []


def test_oversize_stops_reading_at_the_cap(harness, monkeypatch):
    monkeypatch.setattr(service, "MAX_AUDIO_BYTES", 8)
    reads = []

    async def stream(self):
        for i in range(10):
            reads.append(i)
            yield b"1234"

    monkeypatch.setattr(Request, "stream", stream)
    assert post(harness).status_code == 413
    assert reads == [0, 1, 2]
    assert harness.fake.calls == []


def test_empty_is_400(harness):
    assert post(harness, data=b"").status_code == 400
    assert harness.fake.calls == []


def test_happy_path_returns_text_untidied(harness):
    response = post(harness)
    assert response.status_code == 200
    assert response.json() == {"text": "hello", "tidied": False}
    assert harness.fake.calls == [(b"audio", "audio/webm", "clip.webm")]


def test_provider_failure_is_a_generic_502(harness, monkeypatch, caplog):
    async def fail(*args):
        raise RuntimeError("private-request-content")

    monkeypatch.setattr(harness.fake, "transcribe", fail)
    response = post(harness)
    assert response.status_code == 502
    assert "private-request-content" not in response.text + caplog.text


@pytest.mark.parametrize("tidy_text, error, expected", [
    ("Call Dana at 3 pm.", False, True), ("Call Dana.", False, False), ("", True, False),
])
def test_tidy_uses_light_tier_and_falls_back_on_errors(harness, monkeypatch, tidy_text, error, expected):
    raw = harness.fake.text = "um so call Dana at 3 pm"

    class Provider:
        async def stream_turn(self, *args):
            yield {"type": "error"} if error else {"type": "text", "text": tidy_text}
            yield {"type": "_turn_complete", "stop_reason": "stop"}

    def resolve(**kw):
        assert kw == {"agent_model_tier": "light"}
        return Provider()

    monkeypatch.setattr(mod, "get_ai_provider", resolve)
    assert post(harness, tidy=True).json() == {"text": tidy_text if expected else raw, "tidied": expected}


@pytest.mark.parametrize("raises", [True, False])
def test_tidy_without_chat_provider_falls_back_to_raw(harness, monkeypatch, raises):
    def resolve(**kw):
        if raises:
            raise RuntimeError("unavailable")
        return None

    monkeypatch.setattr(mod, "get_ai_provider", resolve)
    assert post(harness, tidy=True).json() == {"text": "hello", "tidied": False}


def test_tidy_is_skipped_for_baker_chat(harness, monkeypatch):
    def resolve(**kw):
        pytest.fail("chat must not resolve the tidy provider")

    monkeypatch.setattr(mod, "get_ai_provider", resolve)
    assert post(harness).json()["tidied"] is False


def test_per_seat_throttle_returns_429(harness, monkeypatch):
    monkeypatch.setattr(mod, "_limiter", IPRateLimiter(60, 2))
    assert [post(harness).status_code for _ in range(3)] == [200, 200, 429]
    harness.app.dependency_overrides[get_current_user] = lambda: {"id": 8}
    assert post(harness).status_code == 200
