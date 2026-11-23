from unittest.mock import Mock

import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backups import BackupError, router, service
from core.auth import get_current_user


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router.router, prefix="/api/backups")
    app.dependency_overrides[get_current_user] = fake_admin
    monkeypatch.setattr(service, "get_settings", lambda: {"passphrase_enc": None})
    return TestClient(app)


def test_download_streams_and_cleans(client, monkeypatch, tmp_path):
    path = tmp_path / "backup.dump"
    path.write_bytes(b"PGDMP-test")
    make = Mock(return_value=(path, "cakecrm-20261123T000000Z.dump"))
    cleaned = Mock()
    monkeypatch.setattr(router, "make_backup", make)
    monkeypatch.setattr(router, "cleanup", cleaned)
    response = client.get("/api/backups/download")
    assert response.status_code == 200 and response.content == b"PGDMP-test"
    assert "cakecrm-20261123T000000Z.dump" in response.headers["content-disposition"]
    make.assert_called_once_with(None)
    cleaned.assert_called_once_with(path)


def test_download_conflicts_with_shared_lock(client):
    with service.backup_lock:
        response = client.get("/api/backups/download")
    assert response.status_code == 409


def test_dump_failure_returns_error_and_releases_lock(client, monkeypatch):
    monkeypatch.setattr(router, "make_backup", Mock(side_effect=BackupError("verification failed")))
    assert client.get("/api/backups/download").json() == {"detail": "verification failed"}
    assert not service.backup_lock.locked()


def test_unreadable_passphrase_never_downloads_plain(client, monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: {"passphrase_enc": "enc:v1:invalid"})
    make = Mock()
    monkeypatch.setattr(router, "make_backup", make)
    response = client.get("/api/backups/download")
    assert response.status_code == 500 and "encryption key changed" in response.json()["detail"]
    make.assert_not_called()


def test_passphrase_length_and_null(client, monkeypatch):
    save = Mock()
    monkeypatch.setattr(service, "set_passphrase", save)
    monkeypatch.setattr(service, "public_status", lambda: {"has_passphrase": False})
    assert client.put("/api/backups/passphrase", json={}).status_code == 422
    assert client.put("/api/backups/passphrase", json={"passphrase": "short"}).status_code == 422
    assert client.put("/api/backups/passphrase", json={"passphrase": None}).status_code == 200
    save.assert_called_once_with(None)
