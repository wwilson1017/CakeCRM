"""The SPA shell is served no-cache so a changed logo/favicon shows on the next load (#306)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from main import _mount_spa


def test_shell_is_no_cache_but_hashed_assets_are_not(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("x")
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    app = FastAPI()
    _mount_spa(app, tmp_path)
    client = TestClient(app)

    for path in ("/", "/contacts/42", "/favicon.svg"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path

    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "no-cache" not in r.headers.get("cache-control", "")
