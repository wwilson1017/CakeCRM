"""Security pins for the two no-login todo surfaces (#70).

These are the tests that matter most in this feature: everything else is behind a
login, and these two endpoints are not. Each assertion below encodes a decision that
would be easy to regress silently — a 403 instead of a 404 tells a scanner the
surface exists; parsing the body before the size check hands an unauthenticated
caller free work; a non-ASCII token turning into a 500 is an oracle.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crm import todo_capture, todo_tokens, todo_web


@pytest.fixture(autouse=True)
def _reset_limiters():
    """Limiter state is module-level and would leak across tests."""
    todo_capture.capture_limiter.clear()
    todo_web.web_limiter.clear()
    todo_web.guess_limiter.clear()
    yield


@pytest.fixture
def surfaces(monkeypatch):
    """Install the crm_meta-backed settings both surfaces read."""
    state = {"todo_capture_token": "", "todo_web_enabled": False, "todo_web_token": ""}

    def _set(**kwargs):
        state.update(kwargs)
        return state

    monkeypatch.setattr(todo_capture.service, "get_todo_public_settings", lambda: dict(state))
    monkeypatch.setattr(todo_web.service, "get_todo_public_settings", lambda: dict(state))
    return _set


@pytest.fixture
def client(surfaces):
    app = FastAPI()
    app.include_router(todo_capture.router)
    app.include_router(todo_web.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def captured(monkeypatch):
    """Record captures instead of touching the database."""
    calls = []

    def _capture(text, source="capture_web"):
        calls.append((text, source))
        return {"id": len(calls), "title": text}

    monkeypatch.setattr(todo_capture.gtd_service, "capture", _capture)
    return calls


# ── Capture: tokenless mode ───────────────────────────────────────────────────

def test_capture_is_public_while_no_token_is_configured(client, captured):
    r = client.post("/api/capture", json={"text": "buy vanilla"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": 1}
    assert captured == [("buy vanilla", "capture_web")]


def test_capture_response_leaks_nothing_but_the_new_id(client, captured):
    """This surface is WRITE-ONLY. Echoing the stored row — or any other row — would
    quietly turn it into a read endpoint."""
    body = client.post("/api/capture", json={"text": "secret plan"}).json()
    assert set(body) == {"ok", "id"}


def test_capture_page_renders_and_is_never_cached_or_indexed(client):
    r = client.get("/capture")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert "noindex" in r.headers["x-robots-tag"]


# ── Capture: token mode ───────────────────────────────────────────────────────

def test_setting_a_token_takes_the_bare_paths_dark(client, surfaces, captured):
    surfaces(todo_capture_token="s3cret")
    assert client.get("/capture").status_code == 404
    assert client.post("/api/capture", json={"text": "x"}).status_code == 404
    assert captured == []


def test_the_configured_token_works(client, surfaces, captured):
    surfaces(todo_capture_token="s3cret")
    assert client.get("/capture/s3cret").status_code == 200
    r = client.post("/api/capture/s3cret", json={"text": "buy vanilla"})
    assert r.status_code == 200
    assert captured == [("buy vanilla", "capture_web")]


def test_a_wrong_token_is_404_not_403(client, surfaces):
    """404, never 403: an unauthorized caller must not learn that a capture surface
    exists at all."""
    surfaces(todo_capture_token="s3cret")
    assert client.get("/capture/wrong").status_code == 404
    assert client.post("/api/capture/wrong", json={"text": "x"}).status_code == 404


def test_a_non_ascii_token_guess_is_404_not_500(client, surfaces):
    """compare_digest raises TypeError on non-ASCII str, so the comparison runs on
    BYTES — otherwise a scanner probing /capture/ü gets a 500 and learns something."""
    surfaces(todo_capture_token="s3cret")
    assert client.get("/capture/ü").status_code == 404


# ── Capture: body limits ──────────────────────────────────────────────────────

def test_an_oversized_declared_body_is_rejected_before_it_is_read(client, captured):
    r = client.post(
        "/api/capture",
        content=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(64 * 1024 + 1)},
    )
    assert r.status_code == 413
    assert captured == []


def test_an_oversized_actual_body_is_rejected(client, captured):
    r = client.post("/api/capture", content=b'{"text":"' + b"x" * (64 * 1024) + b'"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert captured == []


def test_malformed_json_is_a_400(client):
    r = client.post("/api/capture", content=b"not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_a_non_string_text_is_a_400(client):
    assert client.post("/api/capture", json={"text": 42}).status_code == 400


def test_capture_rate_limits_per_ip(client, captured):
    for _ in range(30):
        assert client.post("/api/capture", json={"text": "x"}).status_code == 200
    assert client.post("/api/capture", json={"text": "x"}).status_code == 429


def test_wrong_token_guesses_burn_the_same_budget_as_captures(client, surfaces):
    """Rate-checking BEFORE the token comparison is what stops the secret being
    brute-forced at line speed."""
    surfaces(todo_capture_token="s3cret")
    for _ in range(30):
        assert client.get("/capture/wrong").status_code == 404
    assert client.get("/capture/wrong").status_code == 429


# ── Web app ───────────────────────────────────────────────────────────────────

def test_the_web_app_is_completely_dark_until_enabled(client):
    assert client.get("/todo").status_code == 404
    assert client.get("/todo/manifest.webmanifest").status_code == 404
    assert client.get("/api/todo-web/todos").status_code == 404


def test_enabling_the_web_app_tokenless_serves_it(client, surfaces, monkeypatch):
    monkeypatch.setattr(todo_web, "_index_html", lambda: "<html><head></head></html>")
    surfaces(todo_web_enabled=True)
    r = client.get("/todo")
    assert r.status_code == 200
    assert "__CAKECRM_TODO_BASE__" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_a_token_takes_the_bare_web_paths_dark(client, surfaces, monkeypatch):
    monkeypatch.setattr(todo_web, "_index_html", lambda: "<html><head></head></html>")
    surfaces(todo_web_enabled=True, todo_web_token="webs3cret")
    assert client.get("/api/todo-web/todos").status_code == 404
    r = client.get("/todo/webs3cret")
    assert r.status_code == 200
    assert '"/todo/webs3cret"' in r.text


def test_a_wrong_web_token_is_404(client, surfaces, monkeypatch):
    monkeypatch.setattr(todo_web, "_index_html", lambda: "<html><head></head></html>")
    surfaces(todo_web_enabled=True, todo_web_token="webs3cret")
    assert client.get("/todo/nope").status_code == 404


def test_the_page_escapes_its_basename_into_both_contexts(monkeypatch):
    """`_page` is tested directly rather than through a route: a hostile token can
    never reach it via the URL (the clamp rejects it on write, and a `/` or `"` would
    not survive routing anyway). That is precisely why the escaping is worth pinning
    on its own — it is the layer that holds if the value ever arrives from elsewhere,
    and neither layer is load-bearing alone."""
    monkeypatch.setattr(todo_web, "_index_html", lambda: "<html><head></head></html>")
    body = todo_web._page('/todo/a";alert(1)//<script>').body.decode()
    # JS string literal: the quote is escaped, so the statement never closes.
    assert 'window.__CAKECRM_TODO_BASE__ = "/todo/a\\";alert(1)//' in body
    # HTML attribute: the quote and angle brackets are entities, not markup.
    assert '&quot;;alert(1)//&lt;script&gt;/manifest.webmanifest' in body
    assert "<script>alert" not in body


def test_the_unbuilt_frontend_503_is_still_uncacheable(client, surfaces, monkeypatch):
    """This fires AFTER a successful token match, so it must not become a cacheable
    or indexable oracle for "the token was right"."""
    monkeypatch.setattr(todo_web, "_index_html", lambda: "")
    surfaces(todo_web_enabled=True)
    r = client.get("/todo")
    assert r.status_code == 503
    assert r.headers["cache-control"] == "no-store"
    assert "noindex" in r.headers["x-robots-tag"]


# ── Tokens ────────────────────────────────────────────────────────────────────

def test_clamp_strips_path_breaking_characters():
    assert todo_tokens.clamp_token("ab/cd.ef?gh") == "abcdefgh"
    assert todo_tokens.clamp_token("  spaced  ") == "spaced"


@pytest.mark.parametrize("slug", ["todos", "inbox", "next", "TODOS"])
def test_a_reserved_slug_can_never_become_a_token(slug):
    """A token equal to a page slug would make /todo/<slug> ambiguous, and 'todos'
    would shadow the API mount entirely."""
    assert todo_tokens.clamp_token(slug) == ""


def test_a_token_can_never_literally_be_the_manifest_path():
    """Why the manifest routes are safe to register ahead of the token routes: the
    clamp strips dots, so no configured token can collide with them."""
    assert "." not in todo_tokens.clamp_token("manifest.webmanifest")


def test_minted_tokens_survive_their_own_clamp():
    for _ in range(20):
        token = todo_tokens.mint_token()
        assert token and todo_tokens.clamp_token(token) == token
