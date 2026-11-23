"""Verified PostgreSQL dumps on disk; credentials never appear in process arguments."""

import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from psycopg2.extensions import parse_dsn

from backups import BackupError
from backups.age import encrypt_file

# libpq keyword names which differ from their environment variable suffix.
_PG_NAMES = {"dbname": "DATABASE", "user": "USER", "password": "PASSWORD",
             "connect_timeout": "CONNECT_TIMEOUT", "client_encoding": "CLIENTENCODING",
             "application_name": "APPNAME", "target_session_attrs": "TARGETSESSIONATTRS",
             "require_auth": "REQUIREAUTH", "channel_binding": "CHANNELBINDING",
             "ssl_min_protocol_version": "SSLMINPROTOCOLVERSION",
             "ssl_max_protocol_version": "SSLMAXPROTOCOLVERSION"}


def database_env() -> dict[str, str]:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise BackupError("DATABASE_URL is not configured")
    try:
        options = parse_dsn(dsn)
    except Exception:
        raise BackupError("DATABASE_URL is invalid") from None
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    for key, value in options.items():
        env["PG" + _PG_NAMES.get(key, key.upper())] = value
    return env


def cleanup(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)


def _run(args: list[str], env: dict[str, str]) -> None:
    try:
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=1800, check=False)
    except FileNotFoundError:
        raise BackupError(f"{args[0]} is not installed on this server") from None
    except subprocess.TimeoutExpired:
        raise BackupError(f"{args[0]} exceeded the 30 minute time limit") from None
    if result.returncode:
        error = result.stderr.decode(errors="replace")
        for value in (os.environ.get("DATABASE_URL"), env.get("PGPASSWORD")):
            if value:
                error = error.replace(value, "[redacted]")
        raise BackupError(f"{args[0]} failed: {error[:1000]}")


def make_backup(passphrase: str | None) -> tuple[Path, str]:
    root = Path(tempfile.gettempdir()) / "cakecrm-backups"
    root.mkdir(mode=0o700, exist_ok=True)
    for old in root.iterdir():
        if old.is_dir() and old.stat().st_mtime < time.time() - 3600:
            shutil.rmtree(old, ignore_errors=True)
    folder = Path(tempfile.mkdtemp(dir=root))
    name = datetime.now(timezone.utc).strftime("cakecrm-%Y%m%dT%H%M%SZ.dump")
    path = folder / name
    try:
        env = database_env()
        _run(["pg_dump", "-w", "-Fc", "--file", str(path)], env)
        _run(["pg_restore", "--list", str(path)], env)
        if passphrase:
            encrypted = path.with_suffix(".dump.age")
            encrypt_file(path, encrypted, passphrase)
            path.unlink()
            path = encrypted
        return path, path.name
    except Exception:
        cleanup(path)
        raise
