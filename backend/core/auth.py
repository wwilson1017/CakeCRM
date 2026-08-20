"""
CakeCRM — Authentication utilities.

Single-user password login with JWT. Optional TOTP two-factor authentication.
The login endpoint checks the password and, if 2FA is enabled, issues a
short-lived pending token requiring a TOTP code before granting access.

The credential itself is DB-backed (issue #78): the `auth_credential` singleton
holds a bcrypt hash the logged-in user can change from Settings. AUTH_PASSWORD is
only the bootstrap value, consulted while that hash IS NULL — so once the user
picks their own password the env var is inert and cannot silently override it on
the next boot. AUTH_PASSWORD_RESET is the operator's recovery lever; see
apply_password_reset_env.

Multi-user (seats) is roughed in behind MULTI_USER_ENABLED=false for a
future phase.
"""

import hmac
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel

from core.config import settings
from core.postgres import get_connection, pg_fetchone

logger = logging.getLogger(__name__)

router = APIRouter()

PENDING_TOKEN_EXPIRE_MINUTES = 5

# Minimal strength floor. The upper bound exists because bcrypt only considers the
# first 72 bytes of a password — beyond that the extra characters are silently
# ignored, so accepting them would overstate the strength being stored.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 72

# ── Rate limiting (in-memory) ────────────────────────────────────────────────

_attempts: dict[str, list[float]] = defaultdict(list)


def _check_rate(key: str, max_attempts: int, window: int) -> bool:
    """Sliding-window limiter shared by the credential endpoints."""
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < window]
    if len(_attempts[key]) >= max_attempts:
        return False
    _attempts[key].append(now)
    return True


def _check_login_rate(ip: str) -> bool:
    return _check_rate(f"login:{ip}", max_attempts=10, window=300)


# ── JWT helpers ──────────────────────────────────────────────────────────────

def create_access_token(data: dict, expire_minutes: int | None = None) -> str:
    """Create a signed JWT with the given claims and configured expiry."""
    minutes = expire_minutes if expire_minutes is not None else settings.jwt.expire_minutes
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    to_encode = {**data, "exp": expire}
    return jwt.encode(to_encode, settings.jwt.secret_key, algorithm=settings.jwt.algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, settings.jwt.secret_key, algorithms=[settings.jwt.algorithm])


async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency — extracts and validates the JWT from the
    Authorization: Bearer <token> header.

    Raises 401 if missing, invalid, expired, or if the token is a
    2FA pending token (which cannot be used for API access).
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("purpose") == "2fa_pending":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="2FA verification required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# ── Password storage + verification ──────────────────────────────────────────

def _hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def _verify_env_password(plain: str) -> bool:
    """Verify against the AUTH_PASSWORD bootstrap value (bcrypt hash or plaintext)."""
    stored = settings.auth.password
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        return _bcrypt.checkpw(plain.encode(), stored.encode())
    return hmac.compare_digest(plain, stored)


def get_stored_hash() -> str | None:
    """Return the DB-backed password hash, or None while the user has set none."""
    row = pg_fetchone("SELECT password_hash FROM auth_credential WHERE id = 1")
    return row["password_hash"] if row else None


def verify_password(plain: str) -> bool:
    """Verify a password against the live credential.

    Resolution order: the auth_credential hash wins whenever one is set; the
    AUTH_PASSWORD env var is the bootstrap credential, consulted only until the
    user sets their own. Raises if the credential cannot be read — callers on the
    login path convert that to a 503 rather than silently falling back to the env
    var, which would let a database outage resurrect a superseded password.
    """
    stored = get_stored_hash()
    if stored is not None:
        return _bcrypt.checkpw(plain.encode(), stored.encode())
    return _verify_env_password(plain)


def set_password(current_plain: str, new_plain: str) -> bool:
    """Verify `current_plain` and store `new_plain`, in ONE transaction.

    Returns False when the current password doesn't match. The row is locked
    FOR UPDATE across the check and the write so two concurrent changes can't both
    validate against the same old credential — the migration seeds the row
    precisely so this lock always has something to hold.
    """
    new_hash = _hash_password(new_plain)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM auth_credential WHERE id = 1 FOR UPDATE")
        row = cur.fetchone()
        current_hash = row[0] if row else None

        if current_hash is not None:
            if not _bcrypt.checkpw(current_plain.encode(), current_hash.encode()):
                return False
        elif not _verify_env_password(current_plain):
            return False

        cur.execute(
            """INSERT INTO auth_credential (id, password_hash, updated_at)
               VALUES (1, %s, now())
               ON CONFLICT (id) DO UPDATE SET
                   password_hash = excluded.password_hash,
                   updated_at = now()""",
            (new_hash,),
        )
    return True


def apply_password_reset_env() -> None:
    """Operator recovery lever: AUTH_PASSWORD_RESET overwrites the stored credential.

    Runs at startup. A self-hosted operator who only has env-var access (Railway,
    say) needs a way to rescue a user who forgot the password they set in-app —
    plain AUTH_PASSWORD deliberately can't do this, since it must stay inert once a
    DB credential exists.

    It re-applies on every boot while the variable is set, so the operator has to
    remove it before an in-app change will survive a restart. That is deliberate: a
    lever that disarms itself can only be pulled once, and the loud warning below
    says so.
    """
    reset = settings.auth.password_reset.strip()
    if not reset:
        return

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO auth_credential (id, password_hash, updated_at)
                   VALUES (1, %s, now())
                   ON CONFLICT (id) DO UPDATE SET
                       password_hash = excluded.password_hash,
                       updated_at = now()""",
                (_hash_password(reset),),
            )
    except Exception:
        # Log and keep booting. Raising here would turn a failed rescue attempt into a
        # total outage — worse than staying locked out, since the old password still works.
        logger.exception(
            "AUTH_PASSWORD_RESET is set but the password could not be reset. The previous "
            "password is unchanged; check database connectivity and restart to retry."
        )
        return

    logger.warning(
        "AUTH_PASSWORD_RESET is set — the login password has been reset to its value. "
        "REMOVE this variable and redeploy: while it is set, every restart resets the "
        "password again and in-app password changes will not survive a restart."
    )
    if len(reset) < MIN_PASSWORD_LENGTH:
        # Applied anyway — refusing would leave a locked-out operator with no lever.
        logger.warning(
            "AUTH_PASSWORD_RESET is shorter than the %d-character minimum enforced for "
            "in-app changes. Set a longer password from Settings once you are back in.",
            MIN_PASSWORD_LENGTH,
        )


# ── Login endpoint ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """Single-user password login. Returns JWT or 2FA challenge."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in a few minutes.")

    try:
        password_ok = verify_password(body.password)
    except Exception:
        # Fail closed: never fall back to AUTH_PASSWORD when the stored credential
        # is unreadable, or a database outage would resurrect a superseded password.
        logger.exception("Credential lookup failed — blocking login")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )

    if not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )

    try:
        from core.auth_2fa import TRUST_COOKIE_NAME, is_2fa_enabled, is_device_trusted
        if is_2fa_enabled():
            trust_token = request.cookies.get(TRUST_COOKIE_NAME, "")
            if not is_device_trusted(trust_token):
                pending = create_access_token(
                    {"sub": "user", "purpose": "2fa_pending"},
                    expire_minutes=PENDING_TOKEN_EXPIRE_MINUTES,
                )
                return JSONResponse({"requires_2fa": True, "pending_token": pending})
    except ImportError:
        logger.warning("auth_2fa module not available — 2FA check skipped")
    except Exception:
        logger.exception("2FA check failed — blocking login")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )

    token = create_access_token({"sub": "user", "role": "admin"})
    return JSONResponse({"access_token": token, "token_type": "bearer"})


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return current user info from token."""
    return {"sub": user.get("sub"), "role": user.get("role")}


# ── Change password (issue #78) ───────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    # TOTP or backup code — required only while 2FA is enabled.
    code: str | None = None


@router.post("/auth/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Change the login password, storing it in the DB-backed credential.

    A wrong current password answers 400, NOT 401: the caller is authenticated, so
    this is a bad body field rather than a dead session — and the frontend api()
    wrapper treats every 401 as an expired session and ejects the user to /login,
    which would make a simple typo look like a logout.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate(f"pwchange:{client_ip}", max_attempts=5, window=300):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")

    new_password = body.new_password
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"New password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )
    if len(new_password.encode()) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"New password must be at most {MAX_PASSWORD_LENGTH} bytes.",
        )
    if new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one.")

    # Lazy import: core.auth_2fa imports this module, so a top-level import is circular.
    from core.auth_2fa import (
        consume_backup_code,
        is_2fa_enabled,
        revoke_all_trusted_devices,
        verify_totp_code,
    )

    # Check the current password BEFORE the two-factor code, because verifying a code
    # spends it: verify_totp_code burns the timeslot and consume_backup_code destroys a
    # single-use backup code. Without this pre-check, one typo in the current-password
    # field would cost the user a recovery code and return an error anyway. This read is
    # advisory — set_password below re-checks under the row lock and stays authoritative.
    if not verify_password(body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    if is_2fa_enabled():
        code = (body.code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Two-factor code required.")
        # Same ladder as the login flow: TOTP first, then a backup code — someone who
        # lost their authenticator must still be able to rotate a leaked password.
        valid = False
        if len(code.replace("-", "")) == 6 and code.replace("-", "").isdigit():
            valid = verify_totp_code(code)
        if not valid:
            valid = consume_backup_code(code)
        if not valid:
            raise HTTPException(status_code=400, detail="Invalid two-factor code.")

    if not set_password(body.current_password, new_password):
        # Lost the race with a concurrent change — the pre-check above passed against a
        # credential that is no longer current.
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    # Other devices must re-authenticate with 2FA after a password change.
    revoke_all_trusted_devices()

    # Hand the acting session a fresh token. Bearer tokens already issued to OTHER
    # devices stay valid until they expire (JWT_EXPIRE_MINUTES) — see the PR body.
    token = create_access_token({"sub": "user", "role": "admin"})
    return {"access_token": token, "token_type": "bearer"}
