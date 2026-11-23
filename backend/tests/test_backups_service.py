import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from backups import BackupError, service
from core.encryption import EncryptionKeyManager, decrypt_value, encrypt_value


def settings(**kw):
    return dict(endpoint="https://storage.example.com", bucket="bucket", region="us-east-1", prefix="own/",
                access_key_id="key", secret_key_enc=service._encrypt_secret("secret"), passphrase_enc=None,
                retention=30, config_version=1, **kw)


@pytest.mark.parametrize("success,attempt,configured,due", [
    (None, None, False, False), (2, 2, True, False), (25, 0.5, True, False),
    (25, 2, True, True), (None, None, True, True),
])
def test_due_check(monkeypatch, success, attempt, configured, due):
    now = datetime.now(timezone.utc)
    row = settings(last_success_at=(now - timedelta(hours=success)).isoformat() if success else None,
                   last_attempt_at=(now - timedelta(hours=attempt)).isoformat() if attempt else None)
    if not configured:
        row["endpoint"] = None
    monkeypatch.setattr(service, "get_settings", lambda: row)
    run = Mock()
    monkeypatch.setattr(service, "run_backup", run)
    service.run_if_due()
    assert run.call_count == int(due)


@pytest.mark.parametrize("age,success,expected", [(47, False, False), (49, False, True), (49, True, True), (1, True, False)])
def test_status_stale_and_no_secrets(monkeypatch, age, success, expected):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=age)).isoformat()
    row = settings(configured_at=stamp, last_success_at=stamp if success else None)
    monkeypatch.setattr(service, "get_settings", lambda: row)
    status = service.public_status()
    assert status["stale"] is expected
    assert status["has_secret"] is True
    assert not {"secret_key_enc", "passphrase_enc", "secret_key", "passphrase"} & status.keys()


@pytest.mark.parametrize("column", ["passphrase_enc", "secret_key_enc"])
def test_wrong_key_fails_closed(monkeypatch, column):
    row = settings()
    row[column] = encrypt_value("stored test secret")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    EncryptionKeyManager.reset_cache()
    with pytest.raises(BackupError, match="encryption key changed"):
        service.read_secret(row, column, "backup secret")


def test_no_passphrase_is_allowed():
    assert service.passphrase(settings()) is None


def test_settings_lock_encrypt_and_preserve_history(monkeypatch, fake_conn):
    row = settings()
    conn = fake_conn(monkeypatch, service, description=list(row), fetchone_results=[tuple(row.values())])
    service.save_destination(endpoint=row["endpoint"], bucket=row["bucket"], region=row["region"],
                             prefix="own", access_key_id="new-key", secret_key="new-secret", retention=12)
    assert "FOR UPDATE" in conn.executed[0][0]
    sql, params = conn.executed[1]
    assert "last_success_at = NULL" not in sql
    assert "config_version = config_version + 1" in sql
    assert json.loads(decrypt_value(params[5])) == "new-secret"
    assert params[5] != "new-secret"
    assert conn.entries == 1 and len(set(conn.executed_by)) == 1


def test_destination_change_resets_health(monkeypatch, fake_conn):
    row = settings()
    conn = fake_conn(monkeypatch, service, description=list(row), fetchone_results=[tuple(row.values())])
    service.save_destination(endpoint=row["endpoint"], bucket="different", region=row["region"],
                             prefix="own", access_key_id="key", secret_key="", retention=30)
    sql, params = conn.executed[1]
    assert "last_success_at = NULL" in sql and "configured_at = now()" in sql
    assert params[5] == row["secret_key_enc"]


@pytest.mark.parametrize("prune_failure", [False, True])
def test_upload_success_precedes_prune_and_is_version_guarded(monkeypatch, tmp_path, prune_failure):
    row = settings()
    monkeypatch.setattr(service, "get_settings", lambda: dict(row))
    path = tmp_path / "dump"
    path.write_bytes(b"data")
    monkeypatch.setattr(service, "make_backup", lambda phrase: (path, "cakecrm-20261123T000000Z.dump"))
    cleaned = Mock()
    monkeypatch.setattr(service, "cleanup", cleaned)
    writes = []
    monkeypatch.setattr(service, "pg_execute", lambda sql, params: writes.append((sql, params)))
    target = Mock(prefix="own/")
    # Simulate destination being edited during the network upload.
    target.put_file.side_effect = lambda *args: row.update(config_version=2)
    monkeypatch.setattr(service, "destination", lambda row: target)

    def prune(*args):
        assert "last_success_at = now()" in writes[-1][0]
        if prune_failure:
            raise BackupError("permission denied")
    monkeypatch.setattr(service, "prune", prune)
    service.run_backup()
    assert all("AND config_version = %s" in sql and params[-1] == 1 for sql, params in writes)
    assert target.put_file.call_count == 1 and cleaned.call_count == 1
    if prune_failure:
        assert writes[-1][1][0].startswith("Uploaded, but removing old backups failed:")
    else:
        assert "last_error = NULL" in writes[-1][0]


def test_lock_contention_is_not_an_attempt(monkeypatch):
    get = Mock()
    monkeypatch.setattr(service, "get_settings", get)
    with service.backup_lock:
        service.run_backup()
    get.assert_not_called()
    assert not service.backup_lock.locked()


@pytest.mark.parametrize("fail", [False, True])
def test_connection_always_removes_unique_object(monkeypatch, fail):
    target = Mock(prefix="own/")
    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service, "destination", lambda row: target)
    target.list_keys.side_effect = lambda prefix: [] if fail else [target.put_bytes.call_args.args[0]]
    if fail:
        with pytest.raises(BackupError, match="could not list"):
            service.test_connection()
    else:
        service.test_connection()
    key = target.put_bytes.call_args.args[0]
    assert key.startswith("own/cakecrm-connection-test-")
    target.delete.assert_called_once_with(key)


def test_scheduler_seam_survives_failure(monkeypatch):
    from heartbeat import service as heartbeat
    monkeypatch.setattr(service, "run_if_due", Mock(side_effect=RuntimeError("test")))
    assert heartbeat.backups_tick() is None


def test_encryption_marker_is_a_literal_passphrase(monkeypatch, fake_conn):
    row = settings()
    conn = fake_conn(monkeypatch, service, description=list(row), fetchone_results=[tuple(row.values())])
    value = "enc:v1:my literal passphrase"
    service.set_passphrase(value)
    stored = conn.executed[1][1][0]
    assert stored != value
    assert service.passphrase({"passphrase_enc": stored}) == value


def test_remove_destination_preserves_passphrase(monkeypatch, fake_conn):
    row = settings()
    conn = fake_conn(monkeypatch, service, description=list(row), fetchone_results=[tuple(row.values())])
    service.clear_destination()
    assert "FOR UPDATE" in conn.executed[0][0]
    query = conn.executed[1][0]
    assert "secret_key_enc = NULL" in query and "endpoint = NULL" in query
    assert "passphrase_enc" not in query
    assert "config_version = config_version + 1" in query
    assert conn.entries == 1


def test_first_destination_requires_secret(monkeypatch, fake_conn):
    row = settings()
    row["secret_key_enc"] = None
    conn = fake_conn(monkeypatch, service, description=list(row), fetchone_results=[tuple(row.values())])
    with pytest.raises(BackupError, match="Enter the bucket secret"):
        service.save_destination(endpoint=row["endpoint"], bucket="bucket", region="auto", prefix="",
                                 access_key_id="id", secret_key="", retention=30)
    assert len(conn.executed) == 1


def test_failed_upload_records_error_without_success_and_cleans(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "get_settings", settings)
    path = tmp_path / "dump"
    path.write_bytes(b"data")
    monkeypatch.setattr(service, "make_backup", lambda phrase: (path, "dump"))
    cleaned, writes = Mock(), Mock()
    monkeypatch.setattr(service, "cleanup", cleaned)
    monkeypatch.setattr(service, "pg_execute", writes)
    target = Mock(prefix="own/")
    target.put_file.side_effect = BackupError("AccessDenied")
    monkeypatch.setattr(service, "destination", lambda row: target)
    service.run_backup()
    assert "last_attempt_at = now()" in writes.call_args_list[0].args[0]
    assert "last_error = %s" in writes.call_args.args[0]
    assert writes.call_args.args[1][0] == "AccessDenied"
    assert all("last_success_at = now()" not in call.args[0] for call in writes.call_args_list)
    cleaned.assert_called_once_with(path)
    target.list_keys.assert_not_called()
    assert not service.backup_lock.locked()


def test_backup_scheduler_job_is_separate_and_coalesced(monkeypatch):
    from apscheduler.schedulers import background

    from heartbeat import scheduler, service as heartbeat

    fake = Mock()
    monkeypatch.setattr(background, "BackgroundScheduler", lambda: fake)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.start_scheduler()
    jobs = [call for call in fake.add_job.call_args_list if call.kwargs.get("id") == "backups"]
    assert len(jobs) == 1
    assert jobs[0].args == (heartbeat.backups_tick, "interval")
    assert jobs[0].kwargs == {"seconds": 300, "id": "backups", "max_instances": 1, "coalesce": True}
    scheduler.shutdown_scheduler()
