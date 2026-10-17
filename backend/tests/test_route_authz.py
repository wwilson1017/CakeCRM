"""App-wide authorization audit (issue #60 Phase A).

Two properties, checked across every route the app actually mounts rather than
per-router, because the failure mode this guards against is a route added later that
nobody remembers to gate:

1. Every route authenticates, except a short, explicit public allowlist.
2. The set of admin-only routes is EXACTLY the enumerated set below.

Property 2 fails in both directions on purpose. Adding an admin-gated route without
listing it here fails CI (so the decision gets recorded), and — more importantly —
*removing* a gate from a listed route fails too, which is the direction that silently
hands install configuration to every member.
"""

import pytest

from main import app

# Routes that must stay reachable without a token, with the reason.
PUBLIC_ROUTES = {
    ("/api/login", "POST"),               # the login endpoint itself
    ("/api/login/verify-2fa", "POST"),    # second step of that same login
    ("/api/gmail/oauth/callback", "GET"), # Google redirects the browser here
    ("/api/branding/logo", "GET"),        # referenced from <img>/CSS with no header
    ("/api/health", "GET"),
    ("/api/health/live", "GET"),
    # The SPA fallback — it serves index.html and the built assets, including the
    # login page itself, so it cannot require a token. Path traversal is guarded in
    # the handler (is_relative_to). Note it is mounted ONLY when frontend/dist
    # exists, so this entry is inert in a backend-only checkout and present after a
    # frontend build; listing it keeps the audit's result the same either way.
    ("/{path:path}", "GET"),
    # The no-login todo surfaces (#70). These are the FEATURE: quick-capture and the
    # todo web app are meant to be openable on a phone with no login. Each handler
    # 404s the surface unless the install has explicitly configured it, and rate-limits
    # by IP; capture matches the URL token, the todo app a browser session (#293) —
    # tests/test_todo_public.py pins all of that. Nothing here reads or writes a CRM
    # record other than a todo.
    ("/capture", "GET"),                                # write-only capture page
    ("/capture/{token}", "GET"),
    ("/capture/manifest.webmanifest", "GET"),           # PWA manifests, static
    ("/capture/{token}/manifest.webmanifest", "GET"),
    ("/api/capture", "POST"),                           # the capture write itself
    ("/api/capture/{token}", "POST"),
    ("/todo", "GET"),                                   # the todo app's HTML shell
    ("/todo/{rest:path}", "GET"),
    ("/todo/manifest.webmanifest", "GET"),
    # #293: the sign-in page (redeems nothing) and its redemption (same-origin, single
    # use, strict guess limiter) are how a browser GETS a session, so they cannot need one.
    ("/todo/signin/{token}", "GET"),
    ("/api/todo-web/signin/{token}", "POST"),
    # The retired tokened API: a JSON 404 for every method, so an old client's GET does
    # not fall through to the SPA shell.
    ("/api/todo-web/{token}/{rest:path}", "GET"),
    ("/api/todo-web/{token}/{rest:path}", "POST"),
    ("/api/todo-web/{token}/{rest:path}", "PUT"),
    ("/api/todo-web/{token}/{rest:path}", "DELETE"),
}

# The todo web app's JSON API (#70) is gtd_router.build_router's route set, so listing
# paths here would mean entries that silently rot as the route set grows. Recognize its
# guard by name instead: a router-level dependency (also on /session and /signout) that
# 404s unless the surface is enabled, rate-limits, and 401s without a live browser
# session (#293). The property this audit
# protects — no route reaches a handler with NO guard at all — is preserved, and
# build_router's own docstring carries the warning that everything added there is
# public.
PUBLIC_SURFACE_GUARDS = {"_session_api_guard"}

# Install configuration + destructive/global operations. Members may do everything
# else, including all record CRUD — ownership is not access control.
ADMIN_ONLY = {
    # Accounts
    ("/api/users", "POST"),
    ("/api/users/{user_id}", "PATCH"),
    ("/api/users/{user_id}/password", "POST"),
    # AI provider configuration
    ("/api/providers/tiers", "PUT"),
    ("/api/providers/{provider}/connect-key", "POST"),
    ("/api/providers/{provider}/disconnect", "POST"),
    ("/api/providers/ollama/connect", "POST"),
    ("/api/providers/active", "PUT"),
    # Branding
    ("/api/branding", "PUT"),
    ("/api/branding/logo", "POST"),
    ("/api/branding/logo", "DELETE"),
    # Integrations
    # Bot config is install-wide (one token, one poller), so connecting and
    # disconnecting stay admin. The per-seat link routes (POST /api/telegram/link-code,
    # POST /api/telegram/unlink) are deliberately NOT here: since #193 a link code claims
    # the caller's own row, so there is nothing an admin gate would protect. That is why
    # /api/telegram/link-code/regenerate left this set.
    ("/api/telegram/connect", "POST"),
    ("/api/telegram/disconnect", "POST"),
    ("/api/gmail/app", "POST"),
    ("/api/gmail/oauth/start", "POST"),
    ("/api/gmail/connection", "DELETE"),
    # One mailbox per install, so "share it with all seats" (#194) is install policy.
    # The protected-context-file gate from that same issue is deliberately NOT here: it
    # lives in put_context_file because the route also serves member-writable topic and
    # daily notes, so the gate has to see the filename.
    ("/api/gmail/sharing", "PUT"),
    # Assistant identity is install-wide (one assistant per install)
    ("/api/assistant/identity", "PUT"),
    # Heartbeat control
    ("/api/heartbeat/run-now", "POST"),
    ("/api/heartbeat/proactive", "POST"),
    # Destructive / global CRM operations
    ("/api/crm/load-sample-data", "POST"),
    ("/api/crm/demo-clear", "POST"),
    ("/api/crm/clear-all", "POST"),
    # #302: the whole database as CSV
    ("/api/crm/export", "GET"),
    ("/api/crm/deals/touch-count/backfill", "POST"),
    ("/api/crm/scores/backfill", "POST"),
    # Todo mode is install-wide (it lives on the crm_meta singleton, so one member
    # flipping it changes everyone's todo experience) — same rule as assistant identity.
    # The no-login surfaces are install-wide too: GET returns the capture token itself,
    # and POST switches the todo app on for every seat. Both admin-only since #102.
    # (Signing a device in, #293, is per seat: /api/crm/gtd/web-* are member routes.)
    ("/api/crm/todo-mode", "POST"),
    ("/api/crm/todo-surfaces", "GET"),
    ("/api/crm/todo-surfaces", "POST"),
    # Custom-field SCHEMA (the install's data model). Field VALUES stay member-writable
    # at /api/crm/{entity_type}/{entity_id}/fields — deliberately NOT in this set.
    ("/api/crm/fields", "POST"),
    ("/api/crm/fields/{field_id}", "PUT"),
    ("/api/crm/fields/{field_id}", "DELETE"),
    # Pipeline stage criteria (#289): replacing or resetting a stage's checklist changes it
    # for every seat, so it is install configuration. Reading it stays member-visible.
    ("/api/crm/stage-criteria/{stage}", "PUT"),
    ("/api/crm/stage-criteria/{stage}", "DELETE"),
    # Connected AI tools (#322): the members switch is an install-wide egress control, and
    # an admin sees and revokes every seat's connections. Members manage their own under
    # /api/mcp/connections, deliberately NOT in this set.
    ("/api/mcp/admin/connections", "GET"),
    ("/api/mcp/admin/settings", "PUT"),
    ("/api/mcp/admin/connections/{connection_id}", "DELETE"),
    ("/api/mcp/admin/connections/{connection_id}/calls", "GET"),
}


def _app_routes():
    """(path, method, dependency names) for every mounted API route."""
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        methods = getattr(route, "methods", None) or set()
        if dependant is None:
            continue
        names = {d.call.__name__ for d in dependant.dependencies}
        for method in methods - {"HEAD", "OPTIONS"}:
            yield route.path, method, names


# Starlette routes carry no `.dependant`, so `_app_routes` above cannot see them: a route
# mounted this way would escape every check in this file. #322 mounts the MCP SDK's routes
# like this, so the exact set is pinned here. Each one authenticates itself: /mcp takes a
# bearer token (mcp_endpoint.server), the OAuth endpoints are the public half of the
# authorization server, and FastAPI's own docs routes need no auth.
_FASTAPI_DOC_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_non_fastapi_routes_are_exactly_pinned():
    from starlette.routing import Route

    from mcp_endpoint.oauth import OAUTH_PATHS, build_routes, oauth_base_url

    bare = {r.path for r in app.routes
            if isinstance(r, Route) and getattr(r, "dependant", None) is None}
    expected = _FASTAPI_DOC_ROUTES | {"/mcp"} | (OAUTH_PATHS if oauth_base_url() else set())
    assert bare == expected
    # The configured shape, pinned too: CI has no public address, so the line above
    # only ever sees /mcp.
    assert {r.path for r in build_routes("https://crm.example")} == {"/mcp"} | OAUTH_PATHS
    assert {r.path for r in build_routes(None)} == {"/mcp"}


def test_every_route_authenticates():
    unguarded = [
        (path, method)
        for path, method, names in _app_routes()
        if not ({"get_current_user", "require_admin"} & names)
        and not (PUBLIC_SURFACE_GUARDS & names)
        and (path, method) not in PUBLIC_ROUTES
    ]
    assert unguarded == [], f"routes reachable without authentication: {unguarded}"


def test_admin_only_route_set_is_exactly_pinned():
    actual = {
        (path, method)
        for path, method, names in _app_routes()
        if "require_admin" in names
    }
    missing_gate = ADMIN_ONLY - actual
    unexpected_gate = actual - ADMIN_ONLY
    assert not missing_gate, (
        "these routes are listed as admin-only but are NOT gated — every member can "
        f"call them: {sorted(missing_gate)}"
    )
    assert not unexpected_gate, (
        "these routes are admin-gated but not listed. If that is intended, add them to "
        f"ADMIN_ONLY: {sorted(unexpected_gate)}"
    )


@pytest.mark.parametrize("path,method", sorted(ADMIN_ONLY))
def test_admin_routes_do_not_also_take_the_plain_dependency(path, method):
    """require_admin already returns the user, so a route needs only that one.

    Depending on both is harmless at runtime (FastAPI caches the sub-dependency) but
    it reads as though the gate were optional, and it is the shape a careless edit
    turns into "authenticated but ungated".
    """
    for p, m, names in _app_routes():
        if (p, m) == (path, method):
            assert "get_current_user" not in names, (
                f"{method} {p} depends on both require_admin and get_current_user"
            )


# ── Blocking work must not run on the event loop ─────────────────────────────

def test_credential_handlers_are_sync_defs():
    """These handlers await nothing and do blocking psycopg2 I/O plus deliberately
    expensive bcrypt work. As `async def`, FastAPI runs that ON the event loop, so a
    burst of logins stalls every other request and every SSE stream. As sync `def`
    they land in the threadpool, which is where blocking work belongs — the same
    reason get_current_user is a sync def."""
    import inspect

    from core import auth
    from mcp_endpoint import router as mcp_router
    from users import router as users_router

    for fn in (auth.login, auth.get_me, auth.change_password,
               users_router.list_users, users_router.create_user,
               users_router.update_user, users_router.reset_password,
               mcp_router.create_token, mcp_router.get_consent, mcp_router.answer_consent):
        assert not inspect.iscoroutinefunction(fn), (
            f"{fn.__name__} is async but does blocking work with no await"
        )
