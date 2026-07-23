"""Smoke test: the FastAPI app builds and registers routes without a database.

This is intentionally minimal — it exists so the "tests on PR" CI gate runs a
real (green) test instead of skipping an empty suite, and to give contributors
a `tests/` package to extend. The Postgres pool initializes in the app's
lifespan handler (which is not triggered here), so importing and inspecting the
route table needs no DATABASE_URL.
"""


def test_app_imports_without_database():
    import main

    assert main.app is not None
    assert main.VERSION


def test_liveness_route_is_registered():
    import main

    paths = {getattr(route, "path", None) for route in main.app.routes}
    assert "/api/health/live" in paths
