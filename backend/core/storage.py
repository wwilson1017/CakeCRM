"""
CakeCRM — Database safety and file-write helpers.

  safe_init_sqlite()    — integrity-checked init with corruption recovery
  atomic_write_json()   — crash-safe JSON file writes
  atomic_write()        — crash-safe text file writes
  atomic_write_bytes()  — crash-safe binary file writes
"""

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def safe_init_sqlite(
    db_path: Path,
    name: str,
    *,
    init_fn: Callable[[], None],
) -> dict:
    """Integrity-checked database initialization with corruption recovery.

    1. If the db file doesn't exist, call init_fn() to create a fresh database.
    2. If it exists, run PRAGMA integrity_check:
       - OK → call init_fn() to open connection / apply migrations.
       - Corrupt → quarantine the file and call init_fn() for a fresh start.

    Returns a status dict: {"db": name, "status": "ok"|"fresh"|"quarantined"|"error"}.
    Never raises.
    """
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)

        if not db_path.exists():
            init_fn()
            return {"db": name, "status": "fresh"}

        try:
            check_conn = sqlite3.connect(str(db_path))
            try:
                result = check_conn.execute("PRAGMA integrity_check").fetchone()
                healthy = result[0] == "ok"
            finally:
                check_conn.close()
        except sqlite3.DatabaseError:
            healthy = False

        if healthy:
            init_fn()
            _verify_wal(db_path)
            return {"db": name, "status": "ok"}

        # Quarantine the corrupt file — preserve it for manual inspection.
        # Content-hash filename prevents overwriting previous quarantines.
        try:
            file_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()[:12]
        except Exception:
            file_hash = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        quarantine_path = db_path.with_suffix(f".corrupt.{file_hash}")
        db_path.rename(quarantine_path)
        logger.warning(
            "Database %s failed integrity check — quarantined to %s, creating fresh",
            db_path, quarantine_path,
        )
        for ext in ("-wal", "-shm"):
            wal = db_path.with_name(db_path.name + ext)
            if wal.exists():
                wal.rename(quarantine_path.with_name(quarantine_path.name + ext))

        init_fn()
        _verify_wal(db_path)
        return {"db": name, "status": "quarantined"}

    except Exception:
        logger.exception("safe_init_sqlite failed for %s", name)
        return {"db": name, "status": "error"}


def _verify_wal(db_path: Path) -> None:
    """Check that WAL mode actually applied after init."""
    try:
        check = sqlite3.connect(str(db_path))
        try:
            jm = check.execute("PRAGMA journal_mode").fetchone()
            if jm and jm[0] != "wal":
                logger.warning("Database %s: WAL mode did not stick (got %s)", db_path, jm[0])
        finally:
            check.close()
    except Exception:
        pass


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write JSON atomically via tempfile + fsync + os.replace.

    The temp file is created in the same directory as the target to
    guarantee same-filesystem semantics for os.replace (atomic on POSIX).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def atomic_write(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text atomically via tempfile + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write binary data atomically via tempfile + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
