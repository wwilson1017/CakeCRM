"""The request-size ceiling (#57) — the only layer that runs before a body is spooled.

This exists because the obvious place to enforce an upload cap does not work for multipart:
FastAPI parses the form — spooling the entire body, to /tmp past 1 MB — BEFORE it solves a
route's dependencies, so neither the handler's bounded read nor a `Depends` guard can stop
an oversized body from being written to disk first. The first test below proves that
ordering rather than asserting it, so the middleware's reason to exist is checked, not
remembered.
"""

import re

import pytest
import starlette.formparsers as formparsers
from conftest import fake_admin
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.testclient import TestClient

import main
from core.auth import get_current_user


def test_fastapi_spools_a_multipart_body_before_dependencies_run(monkeypatch):
    """The premise. If this ever stops being true, the middleware can move to a Depends."""
    parsed = []
    real_parse = formparsers.MultiPartParser.parse

    async def counting_parse(self, *a, **k):
        parsed.append(1)
        return await real_parse(self, *a, **k)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", counting_parse)

    def guard(request: Request):
        raise HTTPException(status_code=413, detail="too big")

    probe = FastAPI()

    @probe.post("/probe")
    def _probe(file: UploadFile = File(...), _=Depends(guard)):
        return {"ok": True}

    r = TestClient(probe).post("/probe", files={"file": ("f.bin", b"x" * 5000)})
    assert r.status_code == 413
    # The dependency rejected the request — and the body was spooled anyway.
    assert parsed == [1]


@pytest.fixture
def client():
    app = FastAPI()
    app.middleware("http")(main.request_size_limit_middleware)

    @app.post("/echo")
    def echo(file: UploadFile = File(...), user=Depends(get_current_user)):
        return {"n": len(file.file.read())}

    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


def test_a_body_over_the_ceiling_is_refused(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BYTES", 500)
    r = client.post("/echo", files={"file": ("f.bin", b"x" * 5000)})
    assert r.status_code == 413
    assert r.json()["detail"] == "Request too large."


def test_a_body_under_the_ceiling_passes_through(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BYTES", 100_000)
    r = client.post("/echo", files={"file": ("f.bin", b"x" * 5000)})
    assert r.status_code == 200
    assert r.json()["n"] == 5000


def test_an_unparseable_content_length_is_not_a_500(client, monkeypatch):
    """A malformed header is the ASGI server's problem, not ours — but it must not become
    a ValueError inside the middleware."""
    monkeypatch.setattr(main, "MAX_REQUEST_BYTES", 100_000)
    r = client.post("/echo", files={"file": ("f.bin", b"x" * 10)},
                    headers={"Content-Length": "not-a-number"})
    assert r.status_code != 500


def test_the_ceiling_clears_the_largest_legitimate_request():
    """It is a disk backstop, not a feature limit: if it were ever tightened below what a
    real route accepts, that route would start 413ing correct requests."""
    from assistant import uploads
    from crm import attachment_service

    assert main.MAX_REQUEST_BYTES > uploads.MAX_FILES * uploads.MAX_FILE_SIZE
    assert main.MAX_REQUEST_BYTES > attachment_service.MAX_ATTACHMENT_BYTES


# ── Per-route ceilings (issue #127) ──────────────────────────────────────────
#
# The global ceiling is a disk backstop at 64 MB, which is 6.4x what a chatter attachment
# may be. Everything between the two was admitted, spooled and parsed before the route's
# bounded read rejected it — so the route needs its own, tighter admission number.

ATTACHMENT_PATH = "/api/crm/chatter/note/7/attachments"


@pytest.fixture
def attachment_client():
    """A stand-in for the real attachment route: same path, same multipart shape."""
    app = FastAPI()
    app.middleware("http")(main.request_size_limit_middleware)

    @app.post("/api/crm/chatter/note/{note_id}/attachments")
    def upload(note_id: int, file: UploadFile = File(...), user=Depends(get_current_user)):
        return {"n": len(file.file.read())}

    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


def test_an_oversized_attachment_is_refused_before_the_body_is_parsed(
    attachment_client, monkeypatch
):
    """The whole point of the issue: not merely that it 413s — the route already did that —
    but that the multipart parser never runs, so nothing is spooled to disk."""
    parsed = []
    real_parse = formparsers.MultiPartParser.parse

    async def counting_parse(self, *a, **k):
        parsed.append(1)
        return await real_parse(self, *a, **k)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", counting_parse)

    limit = main._request_limit_for(ATTACHMENT_PATH)
    r = attachment_client.post(ATTACHMENT_PATH, files={"file": ("f.bin", b"x" * (limit + 1))})

    assert r.status_code == 413
    assert parsed == []


def test_a_legitimate_attachment_upload_still_passes(attachment_client):
    """The ceiling must clear a real max-size upload plus its envelope, or this hardening
    would 413 correct requests — the failure mode the global ceiling's own test guards."""
    from crm import attachment_service

    body = b"x" * attachment_service.MAX_ATTACHMENT_BYTES
    r = attachment_client.post(ATTACHMENT_PATH, files={"file": ("f.bin", body)})

    assert r.status_code == 200
    assert r.json()["n"] == attachment_service.MAX_ATTACHMENT_BYTES


def test_the_attachment_ceiling_clears_the_feature_limit():
    """The same rule as the global ceiling, applied to the per-route one."""
    from crm import attachment_service

    limit = main._request_limit_for(ATTACHMENT_PATH)
    assert limit > attachment_service.MAX_ATTACHMENT_BYTES
    # ...and is genuinely tighter than the backstop it replaces, or it buys nothing.
    assert limit < main.MAX_REQUEST_BYTES


@pytest.mark.parametrize(
    "path",
    [
        "/api/crm/chatter/note/abc/attachments",  # non-numeric id: not this route
        "/api/crm/chatter/note/7/attachments/extra",  # deeper path
        "/api/assistant/chat",  # a route with a legitimately larger body
        "/",
    ],
)
def test_other_paths_keep_the_global_ceiling(path):
    """A near-miss must fall back to the backstop, never inherit a limit meant for
    something else."""
    assert main._request_limit_for(path) == main.MAX_REQUEST_BYTES


def test_every_route_limit_matches_a_real_mounted_route():
    """The one failure this table can suffer silently: a pattern that matches nothing.

    Every other test here drives a stand-in app at a hand-written path, so renaming or
    re-prefixing the real route would turn the ceiling off while leaving them all green —
    a permanent pass that reads as coverage. This asserts the patterns against the app's
    OWN mounted paths, so the table cannot quietly stop applying to anything.
    """
    mounted = [
        getattr(route, "path", "")
        for route in main.app.routes
        if "POST" in getattr(route, "methods", set())
    ]
    # Substitute a concrete id for each `{param}` so a real path can be matched literally.
    concrete = [re.sub(r"\{[^}]+\}", "1", path) for path in mounted]

    for pattern, _limit in main._ROUTE_REQUEST_LIMITS:
        assert any(pattern.match(path) for path in concrete), (
            f"{pattern.pattern} matches no mounted POST route — the per-route ceiling it "
            f"declares is silently inactive. Update it to the route's current path."
        )
