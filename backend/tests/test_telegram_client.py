"""Telegram Bot API client — response parsing, send payloads, and safe fallbacks.

Hermetic: the network is never touched. ``_post`` is monkeypatched to capture the
payloads the send helpers build (so we assert parse_mode, keyboard placement, and the
parse-error → plain-text fallback), and ``_parse`` is exercised directly with fake
responses (error classification, retry_after, parse-error detection).
"""

import pytest

from telegram import client


class FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_parse_ok_returns_result():
    assert client._parse("getMe", FakeResp(200, {"ok": True, "result": {"username": "b"}})) == {"username": "b"}


def test_parse_error_raises_with_status():
    with pytest.raises(client.TelegramError) as ei:
        client._parse("getUpdates", FakeResp(409, {"ok": False, "description": "Conflict"}))
    assert ei.value.status == 409


def test_parse_429_carries_retry_after():
    resp = FakeResp(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 12}})
    with pytest.raises(client.TelegramError) as ei:
        client._parse("sendMessage", resp)
    assert ei.value.retry_after == 12


def test_parse_400_parse_error_flagged():
    resp = FakeResp(400, {"ok": False, "description": "Bad Request: can't parse entities"})
    with pytest.raises(client.TelegramError) as ei:
        client._parse("sendMessage", resp)
    assert ei.value.is_parse_error is True


def test_parse_400_non_parse_not_flagged():
    resp = FakeResp(400, {"ok": False, "description": "Bad Request: chat not found"})
    with pytest.raises(client.TelegramError) as ei:
        client._parse("sendMessage", resp)
    assert ei.value.is_parse_error is False


def _capture_post(monkeypatch):
    calls = []

    def fake_post(token, method, payload=None, *, read_timeout=30.0):
        calls.append({"method": method, "payload": payload})
        return {}

    monkeypatch.setattr(client, "_post", fake_post)
    return calls


def test_send_html_uses_html_parse_mode(monkeypatch):
    calls = _capture_post(monkeypatch)
    client.send_html("42", "**hi**", "TOKEN")
    assert calls[0]["payload"]["parse_mode"] == "HTML"
    assert "<b>hi</b>" in calls[0]["payload"]["text"]


def test_send_text_has_no_parse_mode(monkeypatch):
    calls = _capture_post(monkeypatch)
    client.send_text("42", "a < b & c", "TOKEN")
    assert "parse_mode" not in calls[0]["payload"]
    assert calls[0]["payload"]["text"] == "a < b & c"  # literal, not escaped


def test_keyboard_only_on_last_chunk(monkeypatch):
    calls = _capture_post(monkeypatch)
    markup = {"inline_keyboard": [[{"text": "ok", "callback_data": "a:1"}]]}
    client.send_text("42", "x" * 9000, "TOKEN", reply_markup=markup)  # forces >1 chunk
    assert len(calls) > 1
    assert "reply_markup" not in calls[0]["payload"]
    assert calls[-1]["payload"]["reply_markup"] == markup


def test_send_html_falls_back_to_plain_on_parse_error(monkeypatch):
    calls = []

    def fake_post(token, method, payload=None, *, read_timeout=30.0):
        calls.append(payload)
        # First (HTML) attempt fails with a parse error; the plain retry succeeds.
        if len(calls) == 1 and payload.get("parse_mode") == "HTML":
            raise client.TelegramError("parse", status=400, is_parse_error=True)
        return {}

    monkeypatch.setattr(client, "_post", fake_post)
    client.send_html("42", "weird *markup", "TOKEN")
    assert len(calls) == 2
    assert calls[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1]  # plain-text retry of the same chunk


def test_send_html_does_not_fall_back_on_non_parse_error(monkeypatch):
    def fake_post(token, method, payload=None, *, read_timeout=30.0):
        raise client.TelegramError("rate", status=429, retry_after=5)

    monkeypatch.setattr(client, "_post", fake_post)
    with pytest.raises(client.TelegramError):
        client.send_html("42", "hi", "TOKEN")


def test_get_updates_requests_message_and_callback(monkeypatch):
    captured = {}

    def fake_post(token, method, payload=None, *, read_timeout=30.0):
        captured["payload"] = payload
        return [{"update_id": 1}]

    monkeypatch.setattr(client, "_post", fake_post)
    out = client.get_updates("TOKEN", offset=7, timeout=25)
    assert out == [{"update_id": 1}]
    assert captured["payload"]["allowed_updates"] == ["message", "callback_query"]
    assert captured["payload"]["offset"] == 7


def test_validate_token_none_on_error(monkeypatch):
    def fake_post(token, method, payload=None, *, read_timeout=30.0):
        raise client.TelegramError("bad", status=401)

    monkeypatch.setattr(client, "_post", fake_post)
    assert client.validate_token("bad") is None
