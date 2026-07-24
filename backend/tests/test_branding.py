"""Hermetic branding API contract tests.

Issue #9 wires the frontend (settings page + shell wordmark/accent) to these
endpoints, which previously had zero coverage. The branding backend is the
sanctioned file-based carve-out; we point its storage paths at ``tmp_path`` so
the suite stays hermetic and never touches ``backend/data/branding``.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from branding import storage
from branding.router import router as branding_router
from core.auth import get_current_user

# Declared content-type is what the endpoint gates on; the stored bytes are
# opaque, so a short marker is enough for a contract test.
_PNG = b"\x89PNG\r\n\x1a\nfake-logo-bytes"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "BRANDING_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(storage, "LOGO_FILE", tmp_path / "logo.png")
    app = FastAPI()
    app.include_router(branding_router, prefix="/api/branding")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


def test_get_returns_defaults(client):
    assert client.get("/api/branding").json() == {
        "company_name": "CakeCRM", "accent_color": "#B03A52", "has_logo": False,
    }


def test_put_partial_update_roundtrips(client):
    out = client.put(
        "/api/branding", json={"company_name": "Acme", "accent_color": "#2563EB"}
    ).json()
    assert out["company_name"] == "Acme" and out["accent_color"] == "#2563EB"
    # persisted across a fresh read
    assert client.get("/api/branding").json()["company_name"] == "Acme"


def test_put_single_field_preserves_other(client):
    # The partial-update branch: updating only accent must not clobber company_name.
    client.put("/api/branding", json={"company_name": "Acme", "accent_color": "#123456"})
    out = client.put("/api/branding", json={"accent_color": "#654321"}).json()
    assert out["company_name"] == "Acme"
    assert out["accent_color"] == "#654321"


def test_put_rejects_non_hex_accent(client):
    assert client.put("/api/branding", json={"accent_color": "blue"}).status_code == 400


def test_get_derives_has_logo_when_config_corrupt(client, tmp_path):
    # A corrupt config.json must not error, and has_logo still derives from the
    # logo file (the except branch of load_config touched by this fix).
    (tmp_path / "config.json").write_text("}{ not json", encoding="utf-8")
    body = client.get("/api/branding").json()
    assert body["company_name"] == "CakeCRM" and body["has_logo"] is False
    client.post("/api/branding/logo", files={"file": ("logo.png", _PNG, "image/png")})
    (tmp_path / "config.json").write_text("}{ still bad", encoding="utf-8")
    assert client.get("/api/branding").json()["has_logo"] is True


def test_logo_upload_rejects_bad_type(client):
    r = client.post("/api/branding/logo", files={"file": ("x.txt", b"nope", "text/plain")})
    assert r.status_code == 400


def test_logo_upload_rejects_oversize(client):
    big = b"x" * (2 * 1024 * 1024 + 1)  # just over the 2 MB cap
    r = client.post("/api/branding/logo", files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 400 and "2 MB" in r.json()["detail"]


def test_logo_delete_when_absent_is_noop(client):
    # Idempotent: DELETE with no logo present still returns 200 / has_logo false.
    r = client.delete("/api/branding/logo")
    assert r.status_code == 200 and r.json()["has_logo"] is False


def test_logo_upload_then_get_reports_has_logo(client):
    # POST → GET has_logo must be TRUE even with NO prior PUT (config.json absent):
    # the fresh-install regression this fix closes.
    r = client.post("/api/branding/logo", files={"file": ("logo.png", _PNG, "image/png")})
    assert r.status_code == 200 and r.json()["has_logo"] is True
    assert client.get("/api/branding").json()["has_logo"] is True
    assert client.get("/api/branding/logo").status_code == 200


def test_logo_delete_then_get_reports_no_logo(client):
    client.post("/api/branding/logo", files={"file": ("logo.png", _PNG, "image/png")})
    assert client.delete("/api/branding/logo").json()["has_logo"] is False
    assert client.get("/api/branding").json()["has_logo"] is False
    assert client.get("/api/branding/logo").status_code == 404
