"""The request-size ceiling (#57) — the only layer that runs before a body is spooled.

This exists because the obvious place to enforce an upload cap does not work for multipart:
FastAPI parses the form — spooling the entire body, to /tmp past 1 MB — BEFORE it solves a
route's dependencies, so neither the handler's bounded read nor a `Depends` guard can stop
an oversized body from being written to disk first. The first test below proves that
ordering rather than asserting it, so the middleware's reason to exist is checked, not
remembered.
"""

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
