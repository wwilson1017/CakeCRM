"""Hermetic tests for gmail.oauth — auth URL construction and code exchange
(httpx mocked). No network."""

from __future__ import annotations

import urllib.parse

import pytest

from gmail import oauth


def test_redirect_uri_derives_from_backend_url(monkeypatch):
    monkeypatch.setattr(oauth.settings, "backend_url", "https://crm.example.com/")
    assert oauth.redirect_uri() == "https://crm.example.com/api/gmail/oauth/callback"


def test_build_auth_url_has_offline_consent_scopes_state(monkeypatch):
    monkeypatch.setattr(oauth.settings, "backend_url", "http://localhost:8000")
    url = oauth.build_auth_url("client-123", "state-xyz")
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "https" and parsed.netloc == "accounts.google.com"
    q = urllib.parse.parse_qs(parsed.query)
    assert q["client_id"] == ["client-123"]
    assert q["redirect_uri"] == ["http://localhost:8000/api/gmail/oauth/callback"]
    assert q["response_type"] == ["code"]
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert q["state"] == ["state-xyz"]
    scopes = q["scope"][0].split()
    assert set(scopes) == {oauth.GMAIL_READONLY_SCOPE, oauth.GMAIL_COMPOSE_SCOPE}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_exchange_code_posts_expected_body(monkeypatch):
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return _FakeResp({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

    monkeypatch.setattr("httpx.post", fake_post)
    result = oauth.exchange_code("the-code", "cid", "secret")
    assert result["access_token"] == "at"
    assert captured["url"] == oauth.TOKEN_ENDPOINT
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "the-code"
    assert captured["data"]["client_id"] == "cid"
    assert captured["data"]["client_secret"] == "secret"
    assert captured["data"]["redirect_uri"] == oauth.redirect_uri()


def test_exchange_code_raises_on_http_error(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResp({}, status=400))
    with pytest.raises(RuntimeError):
        oauth.exchange_code("bad", "cid", "secret")


def test_revoke_token_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.post", boom)
    # Must not raise.
    oauth.revoke_token("some-token")
    oauth.revoke_token("")  # empty short-circuits
