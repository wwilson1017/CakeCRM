"""Security pins for the two no-login todo surfaces (#70; the todo app's browser
sessions since #293).

These are the tests that matter most in this feature: everything else is behind a
login, and these two endpoints are not. Each assertion below encodes a decision that
would be easy to regress silently — a 403 instead of a 404 tells a scanner the
surface exists; parsing the body before the size check hands an unauthenticated
caller free work; a non-ASCII token turning into a 500 is an oracle.
"""

from datetime import datetime, timezone
from pathlib import Path

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
    state = {"todo_capture_token": "", "todo_web_enabled": False}

    def _set(**kwargs):
        state.update(kwargs)
        return state

    monkeypatch.setattr(todo_capture.service, "get_todo_public_settings", lambda: dict(state))
    monkeypatch.setattr(todo_web.service, "get_todo_public_settings", lambda: dict(state))
    return _set


class FakeSessions:
    """In-memory stand-in for crm/todo_sessions.py's database half, recording calls.

    `codes` maps a sign-in code to its seat (spent on first use); `live` maps a cookie
    value to its session. The lifecycle SQL itself is pinned against a real Postgres in
    test_todo_web_sessions_integration.py.
    """

    def __init__(self):
        self.codes: dict[str, int] = {}
        self.live: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self._n = 0

    def resolve_session(self, token, *, now=None):
        self.calls.append(("resolve", token))
        return self.live.get(token) if token else None

    def redeem_signin_token(self, token, *, replaces=None, now=None):
        self.calls.append(("redeem", token, replaces))
        user_id = self.codes.pop(token, None)
        if user_id is None:
            return None
        if replaces:
            self.live.pop(replaces, None)
        self._n += 1
        session_token = f"session-{self._n}"
        self.live[session_token] = {
            "token_hash": f"h{self._n}", "user_id": user_id,
            "last_used_at": datetime.now(timezone.utc),
        }
        return {"user_id": user_id, "session_token": session_token}

    def touch_session(self, token_hash, last_used_at, *, now=None):
        self.calls.append(("touch", token_hash))
        return False

    def revoke_session(self, token):
        self.calls.append(("revoke", token))
        self.live.pop(token, None)


@pytest.fixture
def sessions(monkeypatch):
    fake = FakeSessions()
    for name in ("resolve_session", "redeem_signin_token", "touch_session", "revoke_session"):
        monkeypatch.setattr(todo_web.todo_sessions, name, getattr(fake, name))
    return fake


@pytest.fixture
def client(surfaces, sessions, monkeypatch):
    # An install nobody configured: the Origin fallback compares with the request's own
    # scheme and Host (http://testserver here).
    monkeypatch.setattr(todo_web.settings, "frontend_url", "http://localhost:5173")
    monkeypatch.setattr(todo_web.settings, "frontend_url_is_default", True)
    monkeypatch.setattr(todo_web, "_index_html", lambda: "<html><head></head><body>SHELL</body></html>")
    app = FastAPI()
    app.include_router(todo_capture.router)
    app.include_router(todo_web.session_api_router, prefix="/api/todo-web")
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


def test_capture_page_keeps_autofocus_under_the_focus_retry_script(client):
    """#233: `autofocus` is the first focus attempt and the script's retry ladder is layered on
    top of it. The ladder's behaviour (standalone-only resume, never stealing focus, the
    touch-only keyboard nudge) is exercised in jsdom by
    `frontend/src/crm/capturePageFocus.test.ts`, which runs this page's real script."""
    body = client.get("/capture").text
    assert '<textarea id="t" autofocus' in body
    assert "function onResume()" in body


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


# ── Web app: switched off ─────────────────────────────────────────────────────

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
COOKIE = todo_web.todo_sessions._COOKIE_BASE  # http install: no __Host- prefix


def test_the_web_app_is_completely_dark_until_enabled(client, sessions):
    """Every route on the surface is a 404 while the switch is off — signed in or not."""
    sessions.live["c"] = {"token_hash": "h", "user_id": 1, "last_used_at": datetime.now(timezone.utc)}
    client.cookies.set(COOKIE, "c")
    for path in ("/todo", "/todo/inbox", "/todo/manifest.webmanifest", "/todo/signin/x",
                 "/todo/oldtoken", "/api/todo-web/todos", "/api/todo-web/session"):
        assert client.get(path).status_code == 404, path
    for path in ("/api/todo-web/signin/x", "/api/todo-web/signout", "/api/todo-web/todos"):
        assert client.post(path, headers=SAME_ORIGIN, json={"title": "x"}).status_code == 404, path


# ── Web app: pages ────────────────────────────────────────────────────────────

@pytest.fixture
def on(surfaces):
    surfaces(todo_web_enabled=True)


def _sign_in(client, sessions, code="code-1", user_id=7) -> str:
    sessions.codes[code] = user_id
    r = client.post(f"/api/todo-web/signin/{code}", headers=SAME_ORIGIN)
    assert r.status_code == 200, r.text
    return r.cookies[COOKIE]


def test_page_without_a_session_is_the_signed_out_page_not_a_redirect(client, on):
    r = client.get("/todo", follow_redirects=False)
    assert r.status_code == 200
    assert "Sign in to your Todos" in r.text
    assert "location" not in r.headers
    assert "SHELL" not in r.text
    assert r.headers["cache-control"] == "no-store"
    assert "noindex" in r.headers["x-robots-tag"]


def test_page_with_a_session_serves_the_shell_with_no_token_in_it(client, on, sessions):
    _sign_in(client, sessions)
    r = client.get("/todo/inbox")
    assert r.status_code == 200
    assert "SHELL" in r.text
    assert 'href="/todo/manifest.webmanifest"' in r.text
    assert '<meta name="referrer" content="no-referrer">' in r.text
    assert "__CAKECRM_TODO_BASE__" not in r.text


def test_signin_page_resolves_and_redeems_nothing(client, on, sessions):
    sessions.codes["code-1"] = 7
    r = client.get("/todo/signin/code-1")
    assert r.status_code == 200 and "SHELL" in r.text
    assert sessions.calls == []
    assert sessions.codes == {"code-1": 7}


@pytest.mark.parametrize("old", ["s3cret", "webs3cret/inbox", "today", "anything-at-all"])
def test_an_old_address_is_the_retired_page_and_looks_nothing_up(client, on, sessions, old):
    r = client.get(f"/todo/{old}")
    assert r.status_code == 404
    assert "This address is no longer used" in r.text
    assert sessions.calls == []
    # Byte-identical whatever the segment: it cannot tell a once-valid token from a guess.
    assert r.text == client.get("/todo/zzz").text


def test_an_old_tokened_api_call_is_a_json_404(client, on):
    for method in ("get", "post", "put", "delete"):
        r = getattr(client, method)("/api/todo-web/s3cret/todos")
        assert r.status_code == 404, method
        assert r.json() == {"detail": "Not found"}


def test_one_manifest_for_everyone(client, on):
    body = client.get("/todo/manifest.webmanifest").json()
    assert body["start_url"].startswith("/todo") and body["scope"].startswith("/todo")


def test_the_page_opts_into_the_cover_viewport(monkeypatch):
    """#266: the bottom bar's safe-area padding is 0 on iOS without `viewport-fit=cover`.
    Read against the REAL built-shell source, so a reworded meta in index.html fails here
    rather than silently leaving the inset at 0 on someone's phone."""
    index = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text()
    monkeypatch.setattr(todo_web, "_index_html", lambda: index)
    body = todo_web._page().body.decode()
    assert '<meta name="viewport" content="viewport-fit=cover, width=device-width' in body
    # The CRM's own shell is untouched — it pads no insets, so it must not take cover.
    assert "viewport-fit" not in index


def test_the_unbuilt_frontend_503_is_still_uncacheable(client, on, sessions, monkeypatch):
    monkeypatch.setattr(todo_web, "_index_html", lambda: "")
    _sign_in(client, sessions)
    r = client.get("/todo")
    assert r.status_code == 503
    assert r.headers["cache-control"] == "no-store"
    assert "noindex" in r.headers["x-robots-tag"]


def test_page_slugs_match_the_spa_routes():
    """The page route calls anything outside `_PAGE_SLUGS` an old tokened address, so a
    new SPA page left out of it would answer "no longer used"."""
    import re

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "crm" / "gtd"
           / "PublicTodoApp.tsx").read_text()
    firsts = {p.split("/")[0] for p in re.findall(r'path="([^"]+)"', src)}
    firsts -= {"*", todo_web._SIGNIN_SEGMENT}
    assert firsts == set(todo_web._PAGE_SLUGS)


# ── Web app: sign-in ──────────────────────────────────────────────────────────

def test_redeem_sets_the_cookie_and_only_the_fresh_token(client, on, sessions):
    sessions.codes["code-1"] = 7
    r = client.post("/api/todo-web/signin/code-1", headers=SAME_ORIGIN)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=session-1;")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age=31536000" in cookie
    assert "Secure" not in cookie  # an http install would never store a Secure cookie
    assert r.headers["cache-control"] == "no-store"
    # The code is spent.
    assert client.post("/api/todo-web/signin/code-1", headers=SAME_ORIGIN).status_code == 404


def test_an_https_install_sets_a_secure_host_prefixed_cookie(client, on, sessions, monkeypatch):
    monkeypatch.setattr(todo_web.todo_sessions.settings, "frontend_url", "https://crm.example.test")
    sessions.codes["code-1"] = 7
    r = client.post("/api/todo-web/signin/code-1", headers=SAME_ORIGIN)
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-cakecrm_todo_session=") and "Secure" in cookie


def test_signing_in_again_replaces_this_browsers_old_session(client, on, sessions):
    first = _sign_in(client, sessions, code="a")
    client.cookies.set(COOKIE, first)
    _sign_in(client, sessions, code="b")
    assert ("redeem", "b", first) in sessions.calls


@pytest.mark.parametrize("code", ["made-up", "ü", "x" * 300])
def test_a_wrong_spent_or_expired_code_is_the_same_404(client, on, code):
    r = client.post(f"/api/todo-web/signin/{code}", headers=SAME_ORIGIN)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}
    assert "set-cookie" not in r.headers
    assert r.headers["cache-control"] == "no-store"


def test_wrong_codes_burn_the_strict_guess_limiter(client, on):
    for _ in range(30):
        assert client.post("/api/todo-web/signin/nope", headers=SAME_ORIGIN).status_code == 404
    r = client.post("/api/todo-web/signin/nope", headers=SAME_ORIGIN)
    assert r.status_code == 429
    assert r.headers["cache-control"] == "no-store"


def test_a_good_code_never_charges_the_guess_limiter(client, on, sessions):
    for i in range(35):
        _sign_in(client, sessions, code=f"c{i}")


# ── Web app: the session guard ────────────────────────────────────────────────

def test_api_without_a_live_session_is_401_with_no_www_authenticate(client, on, sessions):
    for path in ("/api/todo-web/todos", "/api/todo-web/session"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert "www-authenticate" not in r.headers
        assert r.headers["cache-control"] == "no-store"
    client.cookies.set(COOKIE, "revoked-or-made-up")
    assert client.get("/api/todo-web/session").status_code == 401


def test_a_signed_out_browser_never_charges_the_guess_limiter(client, on):
    for _ in range(40):
        assert client.get("/api/todo-web/session").status_code == 401


def test_the_session_bootstrap_answers_ok_with_a_cookie(client, on, sessions):
    _sign_in(client, sessions)
    r = client.get("/api/todo-web/session")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ("touch", "h1") in sessions.calls


@pytest.fixture
def created(monkeypatch):
    calls = []
    monkeypatch.setattr("crm.gtd_service.create_todo", lambda *a, **kw: calls.append((a, kw)) or {"id": 1})
    return calls


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": "http://evil.example"},
    {"Origin": "https://testserver"},       # wrong scheme
    {"Origin": "http://testserver:8443"},   # wrong port
    {"Origin": "null"},
    {},                                     # neither header: a script or curl
])
def test_a_cross_site_write_is_403_and_writes_nothing(client, on, sessions, created, headers):
    _sign_in(client, sessions)
    r = client.post("/api/todo-web/todos", json={"title": "x"}, headers=headers)
    assert r.status_code == 403
    assert r.headers["cache-control"] == "no-store"
    assert created == []


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "same-origin"}, {"Origin": "http://testserver"}])
def test_a_same_origin_write_passes(client, on, sessions, created, headers):
    _sign_in(client, sessions)
    r = client.post("/api/todo-web/todos", json={"title": "x"}, headers=headers)
    assert r.status_code == 200, r.text
    assert len(created) == 1


def test_a_configured_install_compares_origin_with_its_own_address(client, on, sessions, created, monkeypatch):
    monkeypatch.setattr(todo_web.settings, "frontend_url", "https://crm.example.test/")
    monkeypatch.setattr(todo_web.settings, "frontend_url_is_default", False)
    sessions.live["c"] = {"token_hash": "h", "user_id": 1, "last_used_at": datetime.now(timezone.utc)}
    client.cookies.set("__Host-cakecrm_todo_session", "c")  # https install: the prefixed name
    ok = client.post("/api/todo-web/todos", json={"title": "x"}, headers={"Origin": "https://crm.example.test"})
    assert ok.status_code == 200
    # The Host the request reached no longer counts.
    bad = client.post("/api/todo-web/todos", json={"title": "x"}, headers={"Origin": "http://testserver"})
    assert bad.status_code == 403


def test_a_cross_site_redemption_is_refused_before_any_lookup(client, on, sessions):
    sessions.codes["code-1"] = 7
    r = client.post("/api/todo-web/signin/code-1", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert "set-cookie" not in r.headers
    assert sessions.calls == [] and sessions.codes == {"code-1": 7}


def test_sign_out_ends_only_this_browser_and_leaves_its_cookie(client, on, sessions):
    other = _sign_in(client, sessions, code="phone")
    client.cookies.clear()  # a second device: it holds no cookie of its own yet
    mine = _sign_in(client, sessions, code="laptop")
    client.cookies.set(COOKIE, mine)
    r = client.post("/api/todo-web/signout", headers=SAME_ORIGIN)
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert client.get("/api/todo-web/session").status_code == 401
    client.cookies.set(COOKIE, other)
    assert client.get("/api/todo-web/session").status_code == 200


def test_sign_out_is_same_origin_only(client, on, sessions):
    mine = _sign_in(client, sessions)
    client.cookies.set(COOKIE, mine)
    assert client.post("/api/todo-web/signout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/todo-web/session").status_code == 200


def test_the_cookie_opens_nothing_else_in_the_crm(monkeypatch, sessions, surfaces):
    """The real app: a live todo session reaches no contacts, deals, Settings or
    assistant — every other route authenticates with the CRM's own Bearer token."""
    import main

    surfaces(todo_web_enabled=True)
    sessions.live["c"] = {"token_hash": "h", "user_id": 1, "last_used_at": datetime.now(timezone.utc)}
    c = TestClient(main.app, raise_server_exceptions=False)
    c.cookies.set(COOKIE, "c")
    assert c.get("/api/todo-web/session").status_code == 200
    for path in ("/api/crm/contacts", "/api/crm/deals", "/api/crm/todo-surfaces",
                 "/api/crm/gtd/web-sessions", "/api/assistant/conversations", "/api/users"):
        assert c.get(path).status_code == 401, path


# ── Tokens ────────────────────────────────────────────────────────────────────

# ── The event loop must never block on these surfaces ─────────────────────────

# The two POSTs legitimately stay `async def` — they must `await request.body()` —
# and push their blocking work through asyncio.to_thread instead.
_ASYNC_BY_DESIGN = {"capture_post", "capture_post_token"}


def _handlers(router):
    import inspect
    return [(r.name, r.endpoint) for r in router.routes
            if getattr(r, "endpoint", None) and inspect.isfunction(r.endpoint)]


@pytest.mark.parametrize("router", [todo_capture.router, todo_web.router, todo_web.session_api_router])
def test_public_handlers_are_sync_so_blocking_db_calls_leave_the_event_loop(router):
    """The single most damaging thing that could regress here.

    Every one of these handlers reaches Postgres, and psycopg2 blocks. FastAPI
    offloads a plain `def` to its threadpool; a blocking call inside an `async def`
    runs ON the event loop and never yields. On an UNAUTHENTICATED surface, with the
    deploy pinned to one worker, that turns an anonymous request burst into a stall of
    every request the app is serving — login, the CRM API, SSE, health.
    """
    import inspect

    offenders = [
        name for name, fn in _handlers(router)
        if inspect.iscoroutinefunction(fn) and name not in _ASYNC_BY_DESIGN
    ]
    assert not offenders, (
        f"these public handlers are async and would block the "
        f"event loop on their DB calls: {offenders}. Make them plain `def`, or "
        f"offload the blocking work with asyncio.to_thread."
    )


def test_the_two_async_capture_posts_offload_their_blocking_work():
    """They cannot be plain `def` (they await the request body), so prove they push
    the DB half off the loop rather than doing it inline."""
    import inspect

    for name in _ASYNC_BY_DESIGN:
        src = inspect.getsource(getattr(todo_capture, name))
        # The insert and the token/settings read are the two blocking calls; both
        # must go through to_thread, never be awaited inline.
        assert "to_thread(_do_capture" in src, (
            f"{name} must run _do_capture via asyncio.to_thread, not inline"
        )
        assert "to_thread(_tokenless_or_404" in src or "to_thread(_require_token" in src, (
            f"{name} must run its settings/token read via asyncio.to_thread"
        )


def test_the_web_api_guard_is_sync():
    """A FastAPI dependency, not a route, so the route scan above misses it — and it
    gates every public CRUD call."""
    import inspect

    for guard in (todo_web._session_api_guard,):
        assert not inspect.iscoroutinefunction(guard), (
            f"{guard.__name__} must be a plain def — it reads settings from Postgres "
            f"on every public request."
        )


def test_every_tokenless_public_route_is_rate_limited():
    """SECURITY.md promises a per-IP limit on every public endpoint; the two tokenless
    GETs originally had none."""
    import inspect

    for name in ("capture_page", "capture_manifest"):
        src = inspect.getsource(getattr(todo_capture, name))
        assert "_rate_or_429" in src, f"{name} is a public route with no rate limit"


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


def test_no_todo_delete_route_on_any_mount():
    """#299: a todo is never deleted. `build_router` is mounted signed-in AND on the
    no-login app (behind a browser session since #293); neither may offer DELETE on a todo."""
    from crm import gtd_router, todo_web
    from crm.router import router as crm_router

    mounts = [gtd_router.router, todo_web.session_api_router, crm_router]
    for mount in mounts:
        todo_paths = [r for r in mount.routes if r.path.endswith("/todos/{todo_id}")]
        assert todo_paths, mount  # vacuity: the todo routes are on this mount
        assert not any("DELETE" in r.methods for r in todo_paths), mount
