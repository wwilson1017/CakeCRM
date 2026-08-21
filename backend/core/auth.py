"""
CakeCRM — Authentication utilities.

Email + password login against the `users` table, with JWT sessions and optional
per-user TOTP two-factor authentication. The login endpoint checks the credential
and, if that user has 2FA on, issues a short-lived pending token requiring a TOTP
code before granting access.

**Authorization is exactly two enforcement points** (issue #60 Phase A):

* ``get_current_user`` — authentication *and* liveness. It does one indexed
  primary-key lookup per request, so a deactivated user, a changed role and an ended
  session all bite on the very next request rather than at token expiry.
* ``require_admin`` — applied per-route to an enumerated list of install-configuration
  and destructive operations.

Record ownership is deliberately NOT a third one. ``owner_id`` is an assignment, a
filter and an analytics dimension; any member can read, edit, delete and reassign any
record. "No per-object ACLs" is a product decision, stated here rather than implied by
the absence of code.

The JWT carries ``sub`` (the user id) and ``pwd_epoch``. It does **not** carry the
role: with a DB-backed dependency a role in the token could only ever be stale, and a
demotion has to take effect on the next request, not at expiry. ``pwd_epoch`` is #78's
session-invalidation scheme re-keyed per user — ``users.token_epoch`` is bumped by a
password change, an admin reset and a deactivation, so those end that person's other
sessions without touching anybody else's.

Bootstrap: ``users.bootstrap.ensure_bootstrap_admin`` seeds the first admin in the
lifespan. ``AUTH_PASSWORD`` is a first-boot seed only and is inert once a user exists;
``AUTH_PASSWORD_RESET`` is the operator's recovery lever (see below).
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel

from core.config import settings
from core.postgres import get_connection
from users import service as users_service

logger = logging.getLogger(__name__)

router = APIRouter()

PENDING_TOKEN_EXPIRE_MINUTES = 5

# Minimal strength floor. The upper bound exists because bcrypt only considers the
# first 72 bytes of a password — beyond that the extra characters are silently
# ignored, so accepting them would overstate the strength being stored.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 72

# One message for "no such email" and "wrong password" alike. Telling them apart
# turns the login form into an account-enumeration oracle.
_BAD_CREDENTIALS = "Incorrect email or password"


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
    """Sign a JWT with the given claims and an expiry.

    Unlike the single-user version this does NOT inject an epoch: the epoch is
    per-user now, so only a caller that knows which user is being minted for can
    supply it. Use ``create_user_token`` for that — it is the only correct way to
    mint a session token.
    """
    minutes = expire_minutes if expire_minutes is not None else settings.jwt.expire_minutes
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode({**data, "exp": expire}, settings.jwt.secret_key, algorithm=settings.jwt.algorithm)


def create_user_token(user: dict, expire_minutes: int | None = None, **extra) -> str:
    """Mint a session (or pending) token for one user, stamped with their epoch."""
    return create_access_token(
        {
            "sub": str(user["id"]),
            "pwd_epoch": int(user.get("token_epoch") or 0),
            **extra,
        },
        expire_minutes=expire_minutes,
    )


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, settings.jwt.secret_key, algorithms=[settings.jwt.algorithm])


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(request: Request) -> dict:
    """FastAPI dependency — validate the bearer token and load the live user row.

    Deliberately a **sync** ``def``. It performs blocking psycopg2 I/O, and an
    ``async def`` dependency runs directly on the event loop — which would stall
    every other request, including the assistant's SSE streams, on each
    authenticated call. As a sync dependency FastAPI runs it in its worker
    threadpool, where blocking I/O belongs.

    Returns the user as a dict (``id``, ``email``, ``name``, ``role``, ``is_active``),
    never the password hash. Raises 401 if the header is missing, the token is
    invalid/expired, it is a 2FA pending token, the user no longer exists, has been
    deactivated, or the session predates their current password.

    This costs one indexed PK lookup per authenticated request. The single-user code
    cached the epoch in-process to keep the happy path at zero DB reads; that cache
    cannot survive multi-user (it was one global epoch for the whole install), and
    liveness has to be read anyway for ``is_active`` and the current role. Folding all
    three into one row read is both simpler and stricter than a cache that could be
    stale — and it is why deactivating a user locks them out effectively immediately.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise _unauthorized("Not authenticated")

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise _unauthorized("Invalid or expired token")

    if payload.get("purpose") == "2fa_pending":
        raise _unauthorized("2FA verification required")

    # Pre-#60 tokens carry sub="user", which is not an int. They fail here, the
    # frontend's existing 401 path shows the login screen, and the upgrade costs one
    # re-login and nothing else.
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise _unauthorized("Invalid or expired token")

    try:
        user = users_service.get_user(user_id)
    except Exception:
        # A database outage must not read as "your session ended" — that would eject
        # everyone to the login screen, where they also could not get in. 503 tells
        # the truth and the frontend leaves the session alone.
        logger.exception("Could not load the current user — failing closed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )

    if not user:
        raise _unauthorized("Invalid or expired token")
    if not user["is_active"]:
        raise _unauthorized("This account has been deactivated")
    if int(payload.get("pwd_epoch", 0)) != int(user["token_epoch"] or 0):
        raise _unauthorized("Session ended by a password change")

    return {**users_service.public_view(user), "sub": str(user["id"])}


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency for install-configuration and destructive routes.

    Returns the same user dict, so a route needs only this one dependency — FastAPI
    caches ``get_current_user`` within a request, so gating a route costs no extra
    database read.

    403, not 404: the caller is authenticated and the route exists — hiding that
    would only make a member's UI harder to debug, and the route list is in the
    OpenAPI schema regardless.
    """
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an admin account.",
        )
    return user


# ── Password verification ────────────────────────────────────────────────────

def verify_password_for(user_id: int, plain: str) -> bool:
    """Verify a plaintext password against one user's stored hash.

    The single re-authentication primitive: login and all four confirmation
    endpoints (2FA setup/disable/regenerate-codes, change-password) route through it,
    so the rules live in exactly one place. Bcrypt-only and fails closed — see
    ``users.service.verify_user_password``.
    """
    user = users_service.get_user(user_id)
    if not user:
        return False
    return users_service.verify_user_password(plain, user.get("password_hash"))


def apply_password_reset_env() -> None:
    """Operator recovery lever: AUTH_PASSWORD_RESET resets the primary admin.

    Runs at startup, after the bootstrap. A self-hosted operator who only has env-var
    access (Railway, say) needs a way back in when the admin password is lost —
    plain AUTH_PASSWORD deliberately can't do it, since it is inert once accounts
    exist, and there is no mailer to send a reset link.

    Target: the **lowest-id admin**, preferring an active one.

    The rescue is deliberately complete, because a partial one is useless. In ONE
    transaction it resets the password, re-activates the account, **disables that
    admin's TOTP and revokes their trusted devices**, and bumps the epoch. Resetting
    only the password would still leave an operator who also lost their authenticator
    locked out — the login flow demands a second factor before it ever issues a
    token — which is exactly the situation this lever exists for.

    Clearing 2FA here is not a privilege escalation: whoever can set an environment
    variable on the deployment already controls the process, the database URL and the
    encryption key. It IS a security-relevant event, so it is logged loudly and the
    warning tells the operator to turn 2FA back on.

    It re-applies on every boot while the variable is set, so the operator has to
    remove it before an in-app change will survive a restart. That is deliberate: a
    lever that disarms itself can only be pulled once.
    """
    reset = settings.auth.password_reset.strip()
    if not reset:
        return

    cleared_2fa = False
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT id, email FROM users WHERE role = 'admin'
                    ORDER BY is_active DESC, id ASC LIMIT 1 FOR UPDATE"""
            )
            row = cur.fetchone()
            if not row:
                logger.error(
                    "AUTH_PASSWORD_RESET is set but this install has no admin account. "
                    "Nothing was reset."
                )
                return
            admin_id, admin_email = row[0], row[1]
            # Bump the epoch too: a rescue is exactly the moment outstanding sessions
            # (possibly the ones that caused the lockout) must stop working.
            cur.execute(
                """UPDATE users
                      SET password_hash = %s, is_active = TRUE,
                          token_epoch = token_epoch + 1, updated_at = now()
                    WHERE id = %s""",
                (users_service.hash_password(reset), admin_id),
            )
            cur.execute(
                """UPDATE totp_config
                      SET enabled = FALSE, secret_enc = '', backup_codes = '[]',
                          last_used_at = '', updated_at = now()
                    WHERE user_id = %s AND enabled
                RETURNING user_id""",
                (admin_id,),
            )
            cleared_2fa = cur.fetchone() is not None
            cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (admin_id,))
    except Exception:
        # Log and keep booting. Raising here would turn a failed rescue attempt into
        # a total outage — worse than staying locked out, since the old password
        # still works.
        logger.exception(
            "AUTH_PASSWORD_RESET is set but the password could not be reset. The "
            "previous password is unchanged; check database connectivity and restart."
        )
        return

    logger.warning(
        "AUTH_PASSWORD_RESET is set — the password for admin %s has been reset to its "
        "value and the account re-activated. REMOVE this variable and redeploy: while "
        "it is set, every restart resets the password again and in-app password "
        "changes will not survive a restart.",
        admin_email,
    )
    if cleared_2fa:
        logger.warning(
            "Two-factor authentication was DISABLED for admin %s as part of the "
            "recovery, and their trusted devices were revoked — a password-only reset "
            "cannot restore access to someone who also lost their authenticator. "
            "Re-enable 2FA from Settings once you are back in.",
            admin_email,
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
    email: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """Email + password login. Returns a JWT, or a 2FA challenge."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in a few minutes.")

    try:
        user = users_service.get_user_by_email(body.email)
    except Exception:
        # Fail closed: an unreadable credential store is a 503, never a fallback to
        # an env var, or a database outage would resurrect a superseded password.
        logger.exception("Credential lookup failed — blocking login")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )

    if not user or not user["is_active"]:
        # Spend a bcrypt verification anyway so an unknown or disabled address costs
        # the same wall-clock time as a real one.
        users_service.spend_dummy_verify()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_BAD_CREDENTIALS)

    if not users_service.verify_user_password(body.password, user.get("password_hash")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_BAD_CREDENTIALS)

    try:
        from core.auth_2fa import TRUST_COOKIE_NAME, is_2fa_enabled, is_device_trusted
        if is_2fa_enabled(user["id"]):
            trust_token = request.cookies.get(TRUST_COOKIE_NAME, "")
            if not is_device_trusted(trust_token, user["id"]):
                pending = create_user_token(
                    user,
                    expire_minutes=PENDING_TOKEN_EXPIRE_MINUTES,
                    purpose="2fa_pending",
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

    return JSONResponse({"access_token": create_user_token(user), "token_type": "bearer"})


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """The signed-in user. Backs the frontend's session validation and "Mine" filters."""
    return user


# ── Change your own password (issue #78, re-keyed per user in #60) ────────────

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
    """Change your own password.

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

    user_id = user["id"]

    # Check the current password BEFORE the two-factor code, because verifying a code
    # spends it: verify_totp_code burns the timeslot and consume_backup_code destroys a
    # single-use backup code. Without this pre-check, one typo in the current-password
    # field would cost the user a recovery code and return an error anyway. This read is
    # advisory — change_own_password below re-checks under the row lock and stays
    # authoritative.
    if not verify_password_for(user_id, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    if is_2fa_enabled(user_id):
        code = (body.code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Two-factor code required.")
        # Same ladder as the login flow: TOTP first, then a backup code — someone who
        # lost their authenticator must still be able to rotate a leaked password.
        valid = False
        if len(code.replace("-", "")) == 6 and code.replace("-", "").isdigit():
            valid = verify_totp_code(user_id, code)
        if not valid:
            valid = consume_backup_code(user_id, code)
        if not valid:
            raise HTTPException(status_code=400, detail="Invalid two-factor code.")

    new_epoch = users_service.change_own_password(user_id, body.current_password, new_password)
    if new_epoch is None:
        # Lost the race with a concurrent change — the pre-check above passed against a
        # credential that is no longer current.
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    # This user's other devices must re-authenticate with 2FA. Scoped to them:
    # revoking the whole install's trusted devices because one person rotated their
    # password would be a team-wide surprise.
    revoke_all_trusted_devices(user_id)

    # Hand the acting session a fresh token stamped with the epoch THIS write
    # produced — not a re-read, which could pick up a concurrent change's later epoch
    # and mint a token that outlives the password it was issued against. Every other
    # session for this user is now stale and 401s on its next request.
    token = create_user_token({"id": user_id, "token_epoch": new_epoch})
    return {"access_token": token, "token_type": "bearer"}
