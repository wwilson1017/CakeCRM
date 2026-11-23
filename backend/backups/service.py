"""Singleton settings and daily off-site backups. No transaction spans network work."""

import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from backups import BackupError
from backups.dump import cleanup, make_backup
from backups.s3 import S3Destination, validate_endpoint
from core.encryption import decrypt_value, encrypt_value
from core.postgres import get_connection, pg_execute, pg_fetchone, row_to_dict

backup_lock = threading.Lock()  # Shared with downloads; deploy runs one worker.
_BACKUP_NAME = re.compile(r"cakecrm-\d{8}T\d{6}Z\.dump(?:\.age)?\Z")
_PUBLIC = ("endpoint", "bucket", "region", "prefix", "access_key_id", "retention",
           "last_success_at", "last_success_bytes", "last_success_key", "last_error", "last_error_at")
_RESET = ("last_attempt_at = NULL, last_success_at = NULL, last_success_key = NULL, "
          "last_success_bytes = NULL, last_error = NULL, last_error_at = NULL")


def get_settings() -> dict:
    row = pg_fetchone("SELECT * FROM backup_settings WHERE id = 1")
    if row is None:
        raise BackupError("Backup settings are unavailable; check database migrations")
    return row


def _configured(row: dict) -> bool:
    return all(row.get(k) for k in ("endpoint", "bucket", "access_key_id", "secret_key_enc"))


def _time(value) -> datetime | None:
    if not value:
        return None
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def public_status() -> dict:
    row = get_settings()
    since = _time(row.get("last_success_at") or row.get("configured_at"))
    configured = _configured(row)
    return {**{key: row.get(key) for key in _PUBLIC}, "configured": configured,
            "has_secret": bool(row.get("secret_key_enc")),
            "has_passphrase": bool(row.get("passphrase_enc")),
            "stale": bool(configured and since and
                          datetime.now(timezone.utc) - since > timedelta(hours=48))}


def read_secret(row: dict, column: str, label: str) -> str | None:
    stored = row.get(column)
    if not stored:
        return None
    try:
        value = json.loads(decrypt_value(stored))
    except (ValueError, TypeError):
        value = None
    if not isinstance(value, str) or not value:
        raise BackupError(f"The {label} can't be read (the encryption key changed). "
                          "Set it again on the Backups card.")
    return value


def _encrypt_secret(value: str) -> str:
    # The shared helper accepts already-encrypted enc:v1: strings. An envelope ensures
    # even a user-entered passphrase beginning with that marker is encrypted literally.
    return encrypt_value(json.dumps(value))


def passphrase(row: dict) -> str | None:
    return read_secret(row, "passphrase_enc", "backup passphrase")


def _locked_row(cur) -> dict:
    cur.execute("SELECT * FROM backup_settings WHERE id = 1 FOR UPDATE")
    row = cur.fetchone()
    if row is None:
        raise BackupError("Backup settings are unavailable")
    return row_to_dict(cur, row)


def save_destination(*, endpoint: str, bucket: str, region: str, prefix: str,
                     access_key_id: str, secret_key: str | None, retention: int) -> None:
    endpoint = validate_endpoint(endpoint)
    bucket, region, access_key_id = bucket.strip(), region.strip(), access_key_id.strip()
    prefix = prefix.strip("/")
    if prefix:
        prefix += "/"
    if (not bucket or bucket in {".", ".."} or "/" in bucket or not region or not access_key_id
            or any(part in {".", ".."} for part in prefix.split("/"))
            or any(ord(c) < 32 for c in bucket + region + access_key_id + prefix)):
        raise BackupError("Enter a bucket, region and access key; the prefix cannot contain dot segments or control characters")
    if not 1 <= retention <= 365:
        raise BackupError("Retention must be between 1 and 365")
    with get_connection() as conn:
        cur = conn.cursor()
        row = _locked_row(cur)
        encrypted = _encrypt_secret(secret_key) if secret_key else row.get("secret_key_enc")
        if not encrypted:
            raise BackupError("Enter the bucket secret key")
        changed = any(row.get(k) != v for k, v in
                      (("endpoint", endpoint), ("bucket", bucket), ("region", region), ("prefix", prefix)))
        reset = f", configured_at = now(), {_RESET}" if changed else ""
        cur.execute(
            "UPDATE backup_settings SET endpoint = %s, bucket = %s, region = %s, prefix = %s, "
            "access_key_id = %s, secret_key_enc = %s, retention = %s, "
            f"config_version = config_version + 1{reset} WHERE id = 1",
            (endpoint, bucket, region, prefix, access_key_id, encrypted, retention),
        )


def clear_destination() -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        _locked_row(cur)
        cur.execute("UPDATE backup_settings SET endpoint = NULL, bucket = NULL, "
                    "access_key_id = NULL, secret_key_enc = NULL, configured_at = NULL, "
                    f"config_version = config_version + 1, {_RESET} WHERE id = 1")


def set_passphrase(value: str | None) -> None:
    if value is not None and len(value) < 8:
        raise BackupError("Use a backup passphrase of at least 8 characters")
    with get_connection() as conn:
        cur = conn.cursor()
        _locked_row(cur)
        cur.execute("UPDATE backup_settings SET passphrase_enc = %s, "
                    "config_version = config_version + 1 WHERE id = 1",
                    (_encrypt_secret(value) if value else None,))


def destination(row: dict) -> S3Destination:
    if not _configured(row):
        raise BackupError("Save a backup destination first")
    return S3Destination(row["endpoint"], row["bucket"], row["region"], row["access_key_id"],
                         read_secret(row, "secret_key_enc", "bucket secret key"), row["prefix"])


def test_connection() -> None:
    dest = destination(get_settings())
    key = dest.prefix + "cakecrm-connection-test-" + secrets.token_hex(12) + ".txt"
    try:
        dest.put_bytes(key, b"CakeCRM backup connection test\n")
        if key not in dest.list_keys(dest.prefix):
            raise BackupError("S3 wrote the test object but could not list it")
    except Exception:
        try:
            dest.delete(key)
        except BackupError:
            pass  # Do not mask the original connection failure.
        raise
    else:
        dest.delete(key)  # A missing DeleteObject permission must fail the test too.


def prune(dest: S3Destination, retention: int) -> None:
    keys = sorted(key for key in dest.list_keys(dest.prefix)
                  if key.startswith(dest.prefix) and _BACKUP_NAME.fullmatch(key[len(dest.prefix):]))
    for key in keys[:-retention]:
        dest.delete(key)


def _outcome(row: dict, assignments: str, params: tuple = ()) -> None:
    pg_execute(f"UPDATE backup_settings SET {assignments} WHERE id = 1 AND config_version = %s",
               (*params, row["config_version"]))


def run_backup() -> None:
    if not backup_lock.acquire(blocking=False):
        return  # Not an attempt; the next tick retries.
    path = None
    try:
        row = get_settings()
        if not _configured(row):
            return
        _outcome(row, "last_attempt_at = now()")
        uploaded = False
        try:
            dest = destination(row)
            path, name = make_backup(passphrase(row))
            key = dest.prefix + name
            dest.put_file(key, path)
            uploaded = True
            _outcome(row, "last_success_at = now(), last_success_key = %s, last_success_bytes = %s, "
                     "last_error = NULL, last_error_at = NULL", (key, path.stat().st_size))
            prune(dest, row["retention"])
        except Exception as exc:
            # Only deliberately safe errors cross into durable/UI state.
            message = str(exc) if isinstance(exc, BackupError) else "Backup failed; check server storage and database availability"
            if uploaded:
                message = "Uploaded, but removing old backups failed: " + message
            _outcome(row, "last_error = %s, last_error_at = now()", (message[:1200],))
    finally:
        if path is not None:
            cleanup(path)
        backup_lock.release()


def run_if_due() -> None:
    row = get_settings()
    if not _configured(row):
        return
    now = datetime.now(timezone.utc)
    success, attempt = _time(row.get("last_success_at")), _time(row.get("last_attempt_at"))
    if success and now - success < timedelta(hours=24):
        return
    if attempt and now - attempt < timedelta(hours=1):
        return
    run_backup()
