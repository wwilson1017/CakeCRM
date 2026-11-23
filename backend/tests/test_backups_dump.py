from unittest.mock import Mock

import pytest

from backups import BackupError, dump


@pytest.mark.parametrize("failure", ["dump", "verify", "missing", "encrypt"])
def test_dump_failures_remove_temporary_files(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(dump.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:test-password@localhost/database?sslmode=require")
    monkeypatch.setenv("PGSSLROOTCERT", "/certs/root.crt")
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        if failure == "missing":
            raise FileNotFoundError()
        code = int((failure == "dump" and args[0] == "pg_dump") or (failure == "verify" and args[0] == "pg_restore"))
        return Mock(returncode=code, stderr=b"failure test-password")
    monkeypatch.setattr(dump.subprocess, "run", run)
    monkeypatch.setattr(dump, "encrypt_file", Mock(side_effect=BackupError("encryption failed")))
    with pytest.raises(BackupError) as exc:
        dump.make_backup("phrase" if failure == "encrypt" else None)
    assert "test-password" not in str(exc.value)
    assert list((tmp_path / "cakecrm-backups").iterdir()) == []
    assert calls[0][1]["env"]["PGSSLMODE"] == "require"
    assert calls[0][1]["env"]["PGPASSWORD"] == "test-password"
    assert calls[0][1]["env"]["PGSSLROOTCERT"] == "/certs/root.crt"
    assert "test-password" not in " ".join(calls[0][0])


def test_abandoned_temp_directories_are_reaped_but_recent_ones_survive(monkeypatch, tmp_path):
    import os
    import time
    from pathlib import Path

    monkeypatch.setattr(dump.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/database")
    root = tmp_path / "cakecrm-backups"
    old, recent = root / "abandoned", root / "recent"
    old.mkdir(parents=True)
    recent.mkdir()
    os.utime(old, (time.time() - 90000, time.time() - 90000))
    # Two hours old: a slow download may still be streaming it.
    os.utime(recent, (time.time() - 7200, time.time() - 7200))
    calls = []

    def run(args, env):
        calls.append(args[0])
        if args[0] == "pg_dump":
            Path(args[-1]).write_bytes(b"PGDMP")
    monkeypatch.setattr(dump, "_run", run)
    path, name = dump.make_backup(None)
    assert calls == ["pg_dump", "pg_restore"]
    assert path.exists() and name.endswith(".dump")
    assert not old.exists() and recent.exists()
    dump.cleanup(path)
    assert not path.parent.exists()
