"""Attachment routes — the serving contract (issue #57).

These bytes come back from the app's OWN origin, so the response headers are not
decoration: they are what stops a stored file executing in the session's context, and
what stops a reused attachment id serving a previous attachment's bytes out of the
browser cache. Each is pinned here.
"""

import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import attachment_service as svc
from crm.router import _etag_matches, router as crm_router

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
SHA = "a" * 64
META = {
    "id": 3, "note_id": 7, "filename": "photo.png", "mime_type": "image/png",
    "byte_size": 16, "sha256": SHA, "thumb_mime": "image/webp", "has_thumb": True,
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


# ── Upload ────────────────────────────────────────────────────────────────────

def test_upload_passes_the_bytes_the_filename_and_the_uploader(client, monkeypatch):
    seen = {}

    def fake(note_id, *, data, filename, uploaded_by):
        seen.update(note_id=note_id, data=data, filename=filename, uploaded_by=uploaded_by)
        return {"id": 1, "note_id": note_id}

    monkeypatch.setattr(svc, "create_attachment", fake)
    r = client.post("/api/crm/chatter/note/7/attachments",
                    files={"file": ("photo.png", PNG, "image/png")})
    assert r.status_code == 200
    assert seen["note_id"] == 7 and seen["data"] == PNG and seen["filename"] == "photo.png"
    assert seen["uploaded_by"] == 1                      # the authenticated user's id


def test_upload_accepts_a_body_at_the_cap_and_413s_one_byte_over(client, monkeypatch):
    """Bounded read: take cap+1 bytes and reject on what actually arrived, rather than
    trusting Content-Length — the repo-wide idiom (assistant/router.py, the CSV import).

    The boundary is tested from both sides so the assertion cannot pass by accident: at
    the cap the service sees every byte, one over it is never called at all.
    """
    monkeypatch.setattr(svc, "MAX_ATTACHMENT_BYTES", 10)
    monkeypatch.setattr(svc, "create_attachment",
                        lambda note_id, *, data, filename, uploaded_by:
                            {"id": 1, "received": len(data)})
    r = client.post("/api/crm/chatter/note/7/attachments",
                    files={"file": ("f.bin", b"x" * 10, "application/octet-stream")})
    assert r.status_code == 200 and r.json()["received"] == 10

    monkeypatch.setattr(svc, "create_attachment",
                        lambda *a, **k: pytest.fail("service called for an over-cap body"))
    r = client.post("/api/crm/chatter/note/7/attachments",
                    files={"file": ("f.bin", b"x" * 11, "application/octet-stream")})
    assert r.status_code == 413


@pytest.mark.parametrize("code,status", [
    ("note_not_found", 404),
    ("note_archived", 404),
    ("limit_exceeded", 400),
    ("file_too_large", 413),
    ("file_empty", 400),
])
def test_every_error_code_maps_to_its_status(client, monkeypatch, code, status):
    def boom(*a, **k):
        raise svc.AttachmentError(code, "nope")

    monkeypatch.setattr(svc, "create_attachment", boom)
    r = client.post("/api/crm/chatter/note/7/attachments",
                    files={"file": ("f.png", PNG, "image/png")})
    assert r.status_code == status


def test_an_unmapped_error_code_would_escape_as_a_500(client, monkeypatch):
    """Proves the failure mode the service-side code-coverage test guards against is real.

    Without this, "every raised code is mapped" is an assertion about a consequence nobody
    has demonstrated — and a guard whose failure mode is hypothetical tends to get relaxed.
    """
    def boom(*a, **k):
        raise svc.AttachmentError("a_code_nobody_mapped", "nope")

    monkeypatch.setattr(svc, "create_attachment", boom)
    with pytest.raises(KeyError):
        client.post("/api/crm/chatter/note/7/attachments",
                    files={"file": ("f.png", PNG, "image/png")})


# ── Serving headers ───────────────────────────────────────────────────────────

def _serve(monkeypatch, *, meta=META):
    monkeypatch.setattr(svc, "get_meta", lambda i: meta)
    monkeypatch.setattr(svc, "get_file", lambda i: {
        "data": PNG, "mime_type": meta["mime_type"], "filename": meta["filename"],
        "sha256": meta["sha256"],
    })
    monkeypatch.setattr(svc, "get_thumb", lambda i: {
        "thumb_data": b"thumb", "thumb_mime": meta["thumb_mime"], "sha256": meta["sha256"],
    })


@pytest.mark.parametrize("path", ["file", "thumb"])
def test_served_bytes_carry_the_hardening_headers(client, monkeypatch, path):
    _serve(monkeypatch)
    r = client.get(f"/api/crm/chatter/attachments/3/{path}")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["vary"] == "Authorization"


@pytest.mark.parametrize("path", ["file", "thumb"])
def test_cache_control_is_revalidated_never_immutable(client, monkeypatch, path):
    """`immutable` would be wrong twice over: TRUNCATE ... RESTART IDENTITY reuses
    attachment ids, so a cached /attachments/1/file could be served for a DIFFERENT
    attachment after a CRM reset; and a fresh immutable response is never revalidated, so
    a deleted attachment would stay viewable in that browser."""
    _serve(monkeypatch)
    cache = client.get(f"/api/crm/chatter/attachments/3/{path}").headers["cache-control"]
    assert "immutable" not in cache
    assert "no-cache" in cache and "private" in cache


def test_file_and_thumb_etags_cannot_cross_satisfy(client, monkeypatch):
    _serve(monkeypatch)
    file_etag = client.get("/api/crm/chatter/attachments/3/file").headers["etag"]
    thumb_etag = client.get("/api/crm/chatter/attachments/3/thumb").headers["etag"]
    assert file_etag != thumb_etag
    # The thumb's ETag must not validate the original.
    r = client.get("/api/crm/chatter/attachments/3/file", headers={"If-None-Match": thumb_etag})
    assert r.status_code == 200


def test_a_non_ascii_filename_round_trips_through_rfc_5987(client, monkeypatch):
    meta = {**META, "filename": "réçu—2026.pdf", "mime_type": "application/pdf"}
    _serve(monkeypatch, meta=meta)
    disp = client.get("/api/crm/chatter/attachments/3/file").headers["content-disposition"]
    assert "filename*=UTF-8''" in disp
    # quote() must be called with safe="" — its default leaves `/` unescaped, which is not
    # the RFC 5987 encoding.
    from urllib.parse import quote
    assert quote(meta["filename"], safe="") in disp
    # The ASCII fallback is still a valid quoted-string.
    assert disp.startswith('attachment; filename="')


def test_an_all_non_ascii_filename_still_produces_an_ascii_fallback(client, monkeypatch):
    _serve(monkeypatch, meta={**META, "filename": "写真.png"})
    disp = client.get("/api/crm/chatter/attachments/3/file").headers["content-disposition"]
    # Not a bare ".png" — a legacy client would save that as a hidden dotfile.
    assert 'filename="attachment.png"' in disp


# ── The 304 fast path (this is the memory bound, not a nicety) ────────────────

def test_a_matching_etag_returns_304_without_reading_the_blob(client, monkeypatch):
    _serve(monkeypatch)
    monkeypatch.setattr(svc, "get_file", lambda i: pytest.fail("read the blob on a 304"))
    r = client.get("/api/crm/chatter/attachments/3/file",
                   headers={"If-None-Match": f'"{SHA}"'})
    assert r.status_code == 304
    assert not r.content


def test_the_thumb_route_has_the_same_fast_path(client, monkeypatch):
    """The thumb is the image a list view fetches per tile, so if either route needs the
    cheap revalidation it is this one."""
    _serve(monkeypatch)
    monkeypatch.setattr(svc, "get_thumb", lambda i: pytest.fail("read the blob on a 304"))
    r = client.get("/api/crm/chatter/attachments/3/thumb",
                   headers={"If-None-Match": f'"{SHA}-thumb"'})
    assert r.status_code == 304


@pytest.mark.parametrize("header,expected", [
    ('"abc"', True),
    ('W/"abc"', True),                       # weak validator
    ('*', True),                             # wildcard
    ('"zzz", "abc"', True),                  # comma-separated list
    ('"zzz", W/"abc"', True),
    ('  "abc"  ', True),
    ('"zzz"', False),
    ('', False),
    (None, False),
    ('"ab"', False),
])
def test_if_none_match_parsing(header, expected):
    """Raw string equality would miss every form but the first and silently disable the
    fast path — a dead optimization looks exactly like a working one."""
    assert _etag_matches(header, '"abc"') is expected


# ── Missing rows ──────────────────────────────────────────────────────────────

def test_missing_attachment_is_404_on_both_routes(client, monkeypatch):
    monkeypatch.setattr(svc, "get_meta", lambda i: None)
    assert client.get("/api/crm/chatter/attachments/9/file").status_code == 404
    assert client.get("/api/crm/chatter/attachments/9/thumb").status_code == 404


def test_an_attachment_with_no_thumbnail_is_404_on_the_thumb_route(client, monkeypatch):
    """NULL thumb is a legal state (a non-image, or one the bomb ceilings refused). The UI
    keys off `has_thumb` and never asks, so reaching here is a race, not a flow."""
    _serve(monkeypatch, meta={**META, "has_thumb": False})
    monkeypatch.setattr(svc, "get_thumb", lambda i: pytest.fail("read a thumb that isn't there"))
    assert client.get("/api/crm/chatter/attachments/3/thumb").status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_returns_ok_then_404(client, monkeypatch):
    monkeypatch.setattr(svc, "delete_attachment", lambda i: True)
    assert client.delete("/api/crm/chatter/attachments/3").json() == {"ok": True}
    monkeypatch.setattr(svc, "delete_attachment", lambda i: False)
    assert client.delete("/api/crm/chatter/attachments/3").status_code == 404


def test_delete_is_not_shadowed_by_the_entity_scoped_chatter_route(client, monkeypatch):
    """DELETE /chatter/attachments/{id} has the same three-segment shape as
    GET /chatter/{entity_type}/{entity_id}. Registration order is what keeps the literal
    prefix reachable."""
    seen = []
    monkeypatch.setattr(svc, "delete_attachment", lambda i: seen.append(i) or True)
    assert client.delete("/api/crm/chatter/attachments/12").status_code == 200
    assert seen == [12]


# ── Handler shape ─────────────────────────────────────────────────────────────

def test_the_attachment_handlers_are_sync_defs(client):
    """They do blocking psycopg2 work and (upload) run Pillow. FastAPI runs a sync handler
    in its threadpool; an `async def` would put a multi-megabyte read and an image decode
    on the event loop and stall every SSE stream — the same reason get_current_user and
    the login handlers are sync."""
    import inspect

    from crm import router as r

    for fn in (r.add_note_attachment, r.get_note_attachment_thumb,
               r.get_note_attachment_file, r.delete_note_attachment):
        assert not inspect.iscoroutinefunction(fn), fn.__name__
