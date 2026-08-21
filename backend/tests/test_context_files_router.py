"""Context-files + memory REST contracts (issue #72 Phase 2).

Minimal FastAPI app, auth overridden, service layer monkeypatched — nothing touches a
DB. Pins the error-code → HTTP-status mapping, the auth requirement, and the two routing
hazards: a filename containing '/', and '/search' never being parsed as a filename.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from context_files import router as cf_router_mod, service
from context_files.router import router as context_files_router
from core.auth import get_current_user
from memory import router as mem_router_mod
from memory.router import router as memory_router


def _app(*, authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(context_files_router, prefix="/api/context-files")
    app.include_router(memory_router, prefix="/api/memory")
    if authed:
        app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return app


@pytest.fixture
def client():
    return TestClient(_app())


def _svc(monkeypatch, mod, name, result):
    def fake(*a, **k):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(mod.service, name, fake)


# ── auth ──────────────────────────────────────────────────────────────────────────

def test_every_endpoint_requires_auth():
    """The Memory page exposes Baker's identity; an unauthenticated read would leak it
    and an unauthenticated write would BE the injection vector this issue worries about."""
    anon = TestClient(_app(authed=False))
    for method, path in [
        ("get", "/api/context-files"),
        ("get", "/api/context-files/search?q=x"),
        ("get", "/api/context-files/file/soul.md"),
        ("put", "/api/context-files/file/soul.md"),
        ("delete", "/api/context-files/file/topics/x.md"),
        ("get", "/api/memory/facts"),
        ("get", "/api/memory/facts/search?q=x"),
        ("delete", "/api/memory/facts/1"),
        ("get", "/api/memory/dreaming/runs"),
    ]:
        kwargs = {"json": {"content": "x"}} if method == "put" else {}
        r = getattr(anon, method)(path, **kwargs)
        assert r.status_code in (401, 403), f"{method} {path} answered {r.status_code}"


# ── routing hazards ───────────────────────────────────────────────────────────────

def test_a_slashed_filename_routes(client, monkeypatch):
    """'topics/pricing.md' contains a '/', which a plain {filename} param cannot match."""
    _svc(monkeypatch, cf_router_mod, "read_file", {"filename": "topics/pricing.md", "content": "x"})
    r = client.get("/api/context-files/file/topics/pricing.md")
    assert r.status_code == 200
    assert r.json()["filename"] == "topics/pricing.md"


def test_search_is_not_swallowed_as_a_filename(client, monkeypatch):
    _svc(monkeypatch, cf_router_mod, "search_files", [{"filename": "topics/a.md"}])
    r = client.get("/api/context-files/search?q=pricing")
    assert r.status_code == 200 and r.json()["count"] == 1


# ── status mapping ────────────────────────────────────────────────────────────────

def test_missing_file_is_404(client, monkeypatch):
    _svc(monkeypatch, cf_router_mod, "read_file", None)
    assert client.get("/api/context-files/file/topics/nope.md").status_code == 404


def test_invalid_filename_is_400(client, monkeypatch):
    _svc(monkeypatch, cf_router_mod, "read_file",
         service.ContextFileError("bad name", code="bad_request"))
    assert client.get("/api/context-files/file/topics/x.md").status_code == 400


def test_stale_edit_is_409(client, monkeypatch):
    """Optimistic concurrency: without this a save silently discards whatever the
    assistant wrote while the editor was open."""
    _svc(monkeypatch, cf_router_mod, "write_file",
         service.ContextFileError("changed since you opened it", code="conflict"))
    r = client.put("/api/context-files/file/soul.md",
                   json={"content": "x", "expected_updated_at": "2026-01-01T00:00:00Z"})
    assert r.status_code == 409


def test_oversize_write_is_413(client, monkeypatch):
    _svc(monkeypatch, cf_router_mod, "write_file",
         service.ContextFileError("too big", code="too_large"))
    assert client.put("/api/context-files/file/topics/x.md", json={"content": "x"}).status_code == 413


def test_deleting_a_protected_file_is_403(client, monkeypatch):
    _svc(monkeypatch, cf_router_mod, "delete_file",
         service.ContextFileError("protected", code="forbidden"))
    assert client.delete("/api/context-files/file/soul.md").status_code == 403


def test_put_records_the_human_as_the_writer(client, monkeypatch):
    """'Who last changed soul.md' is only answerable if the REST path marks itself."""
    captured = {}

    def fake_write(filename, content, written_by="assistant", expected_updated_at=None):
        captured.update(written_by=written_by)
        return {"filename": filename}

    monkeypatch.setattr(cf_router_mod.service, "write_file", fake_write)
    assert client.put("/api/context-files/file/soul.md", json={"content": "x"}).status_code == 200
    assert captured["written_by"] == "user"


# ── memory facts ──────────────────────────────────────────────────────────────────

def test_facts_list_does_not_count_as_retrieval(client, monkeypatch):
    """Browsing the UI is not the assistant 'using' a fact; counting it would keep
    dormant facts permanently un-archivable (issue #5's rule)."""
    captured = {}

    def fake_query(*args, **kwargs):
        captured["track"] = args[-1] if args else kwargs.get("track_retrieval")
        return []

    monkeypatch.setattr(mem_router_mod.service, "query_facts", fake_query)
    assert client.get("/api/memory/facts").status_code == 200
    assert captured["track"] is False


def test_missing_fact_invalidate_is_404(client, monkeypatch):
    _svc(monkeypatch, mem_router_mod, "invalidate_fact",
         {"error": "Fact 9 not found", "not_found": True})
    assert client.post("/api/memory/facts/9/invalidate", json={}).status_code == 404


def test_bad_invalidate_input_is_400(client, monkeypatch):
    _svc(monkeypatch, mem_router_mod, "invalidate_fact", {"error": "valid_to must be a date"})
    assert client.post("/api/memory/facts/1/invalidate", json={"valid_to": "nope"}).status_code == 400


def test_delete_fact_404_then_200(client, monkeypatch):
    _svc(monkeypatch, mem_router_mod, "delete_fact", False)
    assert client.delete("/api/memory/facts/9").status_code == 404
    _svc(monkeypatch, mem_router_mod, "delete_fact", True)
    assert client.delete("/api/memory/facts/1").status_code == 200
