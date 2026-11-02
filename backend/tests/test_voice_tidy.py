import asyncio

import pytest

from voice import service


@pytest.mark.parametrize("raw, tidy, expected", [
    ("um so call Dana at 3 pm", "Call Dana at 3 pm.", True),
    ("call Dana at 3 pm", "Call Dana.", False),
    ("call Ana", "Call Anna.", False),
    ("Um so call tomorrow", "Call tomorrow.", True),
    ("I call tomorrow", "Call tomorrow.", True),
    ("hi", "hello " * 20, False),
    ("hello", "", False),
    ("Pues llama a Ana a las 3", "Llama a Ana a las 3.", True),
    ("12, no, 14", "14", False),
    ("Dana met Dana", "Dana met someone", False),
])
def test_facts_preserved(raw, tidy, expected):
    assert service.facts_preserved(raw, tidy) is expected


async def test_tidy_request_shape():
    raw = "um call Dana at 3"

    class Provider:
        async def stream_turn(self, messages, tools, system):
            assert system == service.TIDY_SYSTEM_PROMPT
            assert tools == []
            assert '<untrusted_external_content' in messages[0]["content"]
            assert 'source="dictation"' in messages[0]["content"]
            assert raw in messages[0]["content"]
            yield {"type": "text", "text": "Call Dana at 3."}
            yield {"type": "_turn_complete", "stop_reason": "stop"}

    assert await service.tidy_transcript(raw, Provider()) == ("Call Dana at 3.", True)


async def test_timeout_falls_back_to_raw(monkeypatch):
    monkeypatch.setattr(service, "TIDY_TIMEOUT_SECONDS", 0.01)

    class Provider:
        async def stream_turn(self, *args):
            await asyncio.Event().wait()
            yield {}

    assert await service.tidy_transcript("hello", Provider()) == ("hello", False)


@pytest.mark.parametrize("events", [
    [{"type": "text", "text": "hello"}],
    [{"type": "error"}, {"type": "_turn_complete"}],
    [{"type": "text", "text": "hello"}, {"type": "_turn_complete", "stop_reason": "length"}],
    [{"type": "text", "text": "hello"}, {"type": "_turn_complete", "stop_reason": "max_tokens"}],
    [{"type": "text", "text": "hello"}, {"type": "_turn_complete", "stop_reason": "error"}],
    [{"type": "text", "text": "x" * 500}],
])
async def test_incomplete_or_runaway_tidy_returns_raw(events):
    class Provider:
        async def stream_turn(self, *args):
            for event in events:
                yield event

    assert await service.tidy_transcript("hello", Provider()) == ("hello", False)
