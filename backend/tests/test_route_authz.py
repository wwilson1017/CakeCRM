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
}

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
    ("/api/telegram/connect", "POST"),
    ("/api/telegram/disconnect", "POST"),
    ("/api/telegram/link-code/regenerate", "POST"),
    ("/api/gmail/app", "POST"),
    ("/api/gmail/oauth/start", "POST"),
    ("/api/gmail/connection", "DELETE"),
    # Assistant identity is install-wide (one assistant per install)
    ("/api/assistant/identity", "PUT"),
    # Heartbeat control
    ("/api/heartbeat/run-now", "POST"),
    ("/api/heartbeat/proactive", "POST"),
    # Destructive / global CRM operations
    ("/api/crm/load-sample-data", "POST"),
    ("/api/crm/demo-clear", "POST"),
    ("/api/crm/clear-all", "POST"),
    ("/api/crm/deals/touch-count/backfill", "POST"),
    ("/api/crm/scores/backfill", "POST"),
    # Custom-field SCHEMA (the install's data model). Field VALUES stay member-writable
    # at /api/crm/{entity_type}/{entity_id}/fields — deliberately NOT in this set.
    ("/api/crm/fields", "POST"),
    ("/api/crm/fields/{field_id}", "PUT"),
    ("/api/crm/fields/{field_id}", "DELETE"),
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


def test_every_route_authenticates():
    unguarded = [
        (path, method)
        for path, method, names in _app_routes()
        if not ({"get_current_user", "require_admin"} & names)
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
