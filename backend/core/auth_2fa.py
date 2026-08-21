"""
CakeCRM — Two-factor authentication (TOTP).

Opt-in TOTP via authenticator apps (Google Authenticator, Authy, 1Password).
Includes backup codes, trusted device cookies, and rate limiting.

Storage: Postgres (totp_config + trusted_devices), schema in backend/migrations/.
Both tables are keyed by ``user_id`` since issue #60 — every read filters on it, so
one person's authenticator secret, replay slot, backup codes and trusted devices are
entirely their own. A row whose ``user_id`` is NULL (the pre-#60 singleton, before the
bootstrap claims it) matches nothing, which is the safe direction: an unclaimed
trusted device is trusted by nobody.

State-changing checks (TOTP replay slot, backup-code consumption) run as
SELECT ... FOR UPDATE + UPDATE in one transaction so two concurrent logins can't
reuse the same code.
"""

import base64
import hashlib
import io
import json
import logging
import secrets
import string
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import JWTError
from pydantic import BaseModel

from core.auth import (
    create_user_token,
    decode_access_token,
    get_current_user,
    verify_password_for,
)
from core.encryption import decrypt_value, encrypt_value
from core.postgres import get_connection, pg_execute, pg_fetchone, row_to_dict
from users import service as users_service

logger = logging.getLogger(__name__)

TOTP_ISSUER = "CakeCRM"
TRUST_COOKIE_NAME = "cakecrm_2fa_trust"
TRUST_COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds
BACKUP_CODE_COUNT = 10

router = APIRouter()


# ── Rate limiting (in-memory) ────────────────────────────────────────────────

_rate_limits: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str, max_attempts: int, window_seconds: int) -> bool:
    now = time.time()
    _rate_limits[key] = [t for t in _rate_limits[key] if now - t < window_seconds]
    if len(_rate_limits[key]) >= max_attempts:
        return False
    _rate_limits[key].append(now)
    return True


# ── Storage layer ────────────────────────────────────────────────────────────

def get_totp_config(user_id: int) -> dict | None:
    return pg_fetchone("SELECT * FROM totp_config WHERE user_id = %s", (user_id,))


def is_2fa_enabled(user_id: int) -> bool:
    config = get_totp_config(user_id)
    return bool(config and config["enabled"])


def save_totp_config(user_id: int, secret_enc: str, backup_codes_json: str) -> None:
    """Enable 2FA for one user and clear their trusted devices, atomically.

    The device wipe belongs in the same transaction as the enable: if it were a
    separate statement that failed, 2FA would be on while old trust cookies still
    matched — and those cookies are precisely what lets a browser skip the second
    factor. Turning 2FA on has to mean every device re-proves itself.

    ``last_used_at`` is reset because the replay slot belongs to the OLD secret;
    carrying a high watermark across a re-enrol would reject the new authenticator's
    first codes.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO totp_config (user_id, enabled, secret_enc, backup_codes, updated_at)
               VALUES (%s, TRUE, %s, %s, now())
               ON CONFLICT (user_id) DO UPDATE SET
                   enabled = TRUE, secret_enc = excluded.secret_enc,
                   backup_codes = excluded.backup_codes,
                   last_used_at = '',
                   updated_at = now()""",
            (user_id, secret_enc, backup_codes_json),
        )
        cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))


def disable_totp(user_id: int) -> None:
    """Turn 2FA off for one user and drop their trusted devices, atomically.

    One transaction on purpose: as two statements, a failure between them leaves 2FA
    disabled while trusted-device rows survive — cookies that would then be honoured
    again the moment 2FA is re-enabled, by a browser the user may have meant to
    de-trust.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE totp_config SET enabled = FALSE, secret_enc = '', backup_codes = '[]',
               last_used_at = '', updated_at = now() WHERE user_id = %s""",
            (user_id,),
        )
        cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))


def consume_backup_code(user_id: int, code: str) -> bool:
    normalized = code.strip().upper().replace("-", "").encode()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT backup_codes FROM totp_config WHERE user_id = %s FOR UPDATE", (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return False
        hashes: list[str] = json.loads(row[0])
        for i, h in enumerate(hashes):
            if _bcrypt.checkpw(normalized, h.encode()):
                hashes.pop(i)
                cur.execute(
                    "UPDATE totp_config SET backup_codes = %s, updated_at = now() WHERE user_id = %s",
                    (json.dumps(hashes), user_id),
                )
                return True
    return False


def _hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def add_trusted_device(user_id: int, token: str, label: str, expires_at: str) -> None:
    token_hash = _hash_device_token(token)
    pg_execute(
        """INSERT INTO trusted_devices (token_hash, user_id, label, expires_at)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (token_hash) DO UPDATE SET
               user_id = excluded.user_id,
               label = excluded.label, expires_at = excluded.expires_at""",
        (token_hash, user_id, label, expires_at),
    )


def is_device_trusted(token: str, user_id: int) -> bool:
    """Is this browser cookie a live trust token FOR THIS USER?

    Scoped to the user on purpose: two people sharing a browser profile must not
    inherit each other's 2FA trust, and a NULL-user_id row (an unclaimed pre-#60
    device) matches nobody rather than everybody.
    """
    if not token:
        return False
    token_hash = _hash_device_token(token)
    row = pg_fetchone(
        """SELECT 1 AS trusted FROM trusted_devices
            WHERE token_hash = %s AND user_id = %s AND expires_at > now()""",
        (token_hash, user_id),
    )
    return row is not None


def revoke_all_trusted_devices(user_id: int) -> None:
    """Revoke one user's trusted devices. Never the whole install's."""
    pg_execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))


def cleanup_expired_devices() -> None:
    pg_execute("DELETE FROM trusted_devices WHERE expires_at < now()")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _generate_backup_codes() -> tuple[list[str], list[str]]:
    """Generate backup codes. Returns (plaintext_codes, bcrypt_hashes)."""
    charset = string.ascii_uppercase + string.digits
    codes = []
    hashes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(charset) for _ in range(8))
        display = f"{raw[:4]}-{raw[4:]}"
        codes.append(display)
        hashes.append(_bcrypt.hashpw(raw.encode(), _bcrypt.gensalt()).decode())
    return codes, hashes


def _generate_qr_data_uri(provisioning_uri: str) -> str:
    img = qrcode.make(provisioning_uri, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def verify_totp_code(user_id: int, code: str) -> bool:
    """Verify a TOTP code against one user's stored secret. True on a valid code."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM totp_config WHERE user_id = %s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if row is None:
            return False
        config = row_to_dict(cur, row)
        if not config["secret_enc"]:
            return False

        secret = decrypt_value(config["secret_enc"])
        if not secret:
            return False

        totp = pyotp.TOTP(secret)
        code_stripped = code.strip()
        if not totp.verify(code_stripped, valid_window=1):
            return False

        # Find which timeslot the code actually belongs to (could be T-1, T, or T+1)
        now_ts = totp.timecode(datetime.now(timezone.utc))
        matched_slot = now_ts
        for offset in (-1, 0, 1):
            candidate = now_ts + offset
            if totp.generate_otp(candidate) == code_stripped:
                matched_slot = candidate
                break

        # Replay prevention: reject if this or a later timeslot was already used
        if config["last_used_at"] and int(config["last_used_at"]) >= matched_slot:
            return False

        cur.execute(
            "UPDATE totp_config SET last_used_at = %s, updated_at = now() WHERE user_id = %s",
            (str(matched_slot), user_id),
        )
        return True


# ── API request/response models ──────────────────────────────────────────────

class VerifySetupRequest(BaseModel):
    secret: str
    code: str
    password: str


class Verify2FARequest(BaseModel):
    pending_token: str
    code: str
    trust_device: bool = False


class PasswordConfirmRequest(BaseModel):
    password: str


# ── API endpoints ────────────────────────────────────────────────────────────

@router.get("/auth/2fa/status")
async def get_2fa_status(user: dict = Depends(get_current_user)):
    """This user's own 2FA state. 2FA is per-account, never an install-wide setting."""
    config = get_totp_config(user["id"])
    if not config:
        return {"enabled": False, "has_backup_codes": False, "trusted_device_count": 0}

    backup_hashes = json.loads(config["backup_codes"])
    device_row = pg_fetchone(
        "SELECT COUNT(*) AS cnt FROM trusted_devices WHERE user_id = %s AND expires_at > now()",
        (user["id"],),
    )
    device_count = device_row["cnt"] if device_row else 0

    return {
        "enabled": bool(config["enabled"]),
        "has_backup_codes": len(backup_hashes) > 0,
        "backup_code_count": len(backup_hashes),
        "trusted_device_count": device_count,
    }


@router.post("/auth/2fa/setup")
async def setup_2fa(user: dict = Depends(get_current_user)):
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    # Label the authenticator entry with the account it belongs to. With several
    # seats on one install, "CakeCRM: user" would be indistinguishable in the app.
    provisioning_uri = totp.provisioning_uri(
        name=user.get("email") or f"user-{user['id']}", issuer_name=TOTP_ISSUER
    )
    qr_data_uri = _generate_qr_data_uri(provisioning_uri)

    return {
        "secret": secret,
        "qr_code_data_uri": qr_data_uri,
        "provisioning_uri": provisioning_uri,
    }


@router.post("/auth/2fa/verify-setup")
async def verify_setup(body: VerifySetupRequest, user: dict = Depends(get_current_user)):
    if not verify_password_for(user["id"], body.password):
        raise HTTPException(status_code=401, detail="Incorrect password")

    totp = pyotp.TOTP(body.secret)
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid code. Check your authenticator app and try again.")

    encrypted_secret = encrypt_value(body.secret)
    plaintext_codes, hashed_codes = _generate_backup_codes()
    # save_totp_config revokes this user's trusted devices in the same transaction.
    save_totp_config(user["id"], encrypted_secret, json.dumps(hashed_codes))

    return {"enabled": True, "backup_codes": plaintext_codes}


@router.post("/auth/2fa/disable")
async def disable_2fa(body: PasswordConfirmRequest, user: dict = Depends(get_current_user)):
    if not verify_password_for(user["id"], body.password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    disable_totp(user["id"])
    return {"disabled": True}


@router.post("/auth/2fa/backup-codes/regenerate")
async def regenerate_backup_codes(body: PasswordConfirmRequest, user: dict = Depends(get_current_user)):
    if not verify_password_for(user["id"], body.password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    if not is_2fa_enabled(user["id"]):
        raise HTTPException(status_code=400, detail="2FA is not enabled")

    plaintext_codes, hashed_codes = _generate_backup_codes()
    pg_execute(
        "UPDATE totp_config SET backup_codes = %s, updated_at = now() WHERE user_id = %s",
        (json.dumps(hashed_codes), user["id"]),
    )

    return {"backup_codes": plaintext_codes}


@router.post("/login/verify-2fa")
async def verify_2fa_login(body: Verify2FARequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"2fa:{client_ip}"
    if not _check_rate_limit(rate_key, max_attempts=5, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many attempts. Please log in again.")

    # Validate pending token
    try:
        payload = decode_access_token(body.pending_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Expired or invalid session. Please log in again.")

    if payload.get("purpose") != "2fa_pending":
        raise HTTPException(status_code=401, detail="Invalid token")

    # The pending token names the account that passed the password step. Re-load it
    # rather than trusting the claims: between the two steps the account may have
    # been deactivated, or its password changed (which bumps the epoch), and either
    # must stop the login rather than complete it.
    try:
        pending_user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = users_service.get_user(pending_user_id)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="Expired or invalid session. Please log in again.")
    if int(payload.get("pwd_epoch", 0)) != int(user["token_epoch"] or 0):
        raise HTTPException(status_code=401, detail="Expired or invalid session. Please log in again.")

    # Try TOTP code first, then backup code
    code = body.code.strip()
    valid = False
    if len(code.replace("-", "")) == 6 and code.replace("-", "").isdigit():
        valid = verify_totp_code(user["id"], code)
    if not valid:
        valid = consume_backup_code(user["id"], code)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid code")

    # Issue real access token
    token = create_user_token(user)

    # Set trusted device cookie if requested
    if body.trust_device:
        device_token = secrets.token_hex(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=TRUST_COOKIE_MAX_AGE)).isoformat()
        ua = request.headers.get("user-agent", "")
        label = ua[:100] if ua else "Unknown device"
        add_trusted_device(user["id"], device_token, label, expires_at)
        response.set_cookie(
            key=TRUST_COOKIE_NAME,
            value=device_token,
            max_age=TRUST_COOKIE_MAX_AGE,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )

    return {"access_token": token, "token_type": "bearer"}
