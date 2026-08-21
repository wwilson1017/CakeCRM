"""
CakeCRM — User management API (issue #60 Phase A).

GET    /api/users               — list users (ANY authenticated user)
POST   /api/users               — create a user (admin)
PATCH  /api/users/{id}          — rename / change role / activate / deactivate (admin)
POST   /api/users/{id}/password — reset another user's password (admin)

There is deliberately **no DELETE**. A departed rep's name should keep rendering on
the records they worked, so accounts are deactivated, never removed — which also
means no owner_id ever dangles.

Changing your OWN password is `POST /api/auth/change-password` (issue #78), not a
route here: that endpoint already handles the 2FA ladder and the non-consuming
current-password pre-check, and a second self-service path would be a second place
for those rules to drift.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    get_current_user,
    require_admin,
)

from . import service

logger = logging.getLogger(__name__)
router = APIRouter()


class UserCreateRequest(BaseModel):
    email: str
    name: str = ""
    password: str
    role: str = "member"


class UserUpdateRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class PasswordResetRequest(BaseModel):
    new_password: str
    # Opt-in, never automatic — see the route docstring.
    clear_two_factor: bool = False


def _validate_password(password: str) -> None:
    """Same floor and ceiling as the self-service change-password route."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )
    # bcrypt only considers the first 72 bytes, so accepting more would overstate
    # the strength actually stored.
    if len(password.encode()) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Password must be at most {MAX_PASSWORD_LENGTH} bytes."
        )


@router.get("")
def list_users(_user: dict = Depends(get_current_user)):
    """Every user on the install. Not admin-gated on purpose.

    Owner dropdowns, the pipeline owner facet and the per-rep analytics table all
    need to turn an owner_id into a human name. Restricting this to admins would
    only mean members see bare numeric ids — it would hide nothing, since ownership
    is already visible on every record.
    """
    return {"users": service.list_users()}


@router.post("")
def create_user(body: UserCreateRequest, _admin: dict = Depends(require_admin)):
    """Create a user account (admin only)."""
    _validate_password(body.password)
    try:
        return service.create_user(
            email=body.email, name=body.name, password=body.password, role=body.role
        )
    except service.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{user_id}")
def update_user(
    user_id: int, body: UserUpdateRequest, admin: dict = Depends(require_admin)
):
    """Rename, change role, or activate/deactivate a user (admin only)."""
    # Locking yourself out is the one self-inflicted error with no in-app way back,
    # so it is refused before the last-admin guard even runs — that guard would let
    # this through whenever a second admin exists, which is still not what anyone
    # means to do by clicking "deactivate" on their own row.
    if user_id == admin["id"] and body.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    if user_id == admin["id"] and body.role is not None and body.role != "admin":
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role.")
    try:
        return service.update_user(
            user_id, name=body.name, role=body.role, is_active=body.is_active
        )
    except service.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{user_id}/password")
def reset_password(
    user_id: int, body: PasswordResetRequest, admin: dict = Depends(require_admin)
):
    """Set ANOTHER user's password (admin only).

    This is the whole password-reset story for a self-hosted install: there is no
    mail infrastructure, so an email-link reset is not implementable and none is
    faked (gate decision on #60). It ends that user's existing sessions.

    Resetting your OWN password here is refused. This route deliberately skips the
    current-password check and the 2FA code that `/api/auth/change-password`
    enforces — which is right for helping a colleague who is locked out, and wrong
    as a self-service path, because it would let anyone who got hold of an admin
    session set a new password without knowing the old one or passing 2FA.

    `clear_two_factor` also disables the target's TOTP and drops their trusted
    devices. Without it there is no recovery for someone who lost both their
    authenticator and their backup codes — a new password still leaves them stuck at
    the second factor. It is opt-in because it genuinely weakens that account, so it
    should be a decision, not a side effect.
    """
    if user_id == admin["id"]:
        raise HTTPException(
            status_code=400,
            detail="Use Settings → Change password to change your own password.",
        )
    _validate_password(body.new_password)
    if not service.set_password_as_admin(
        user_id, body.new_password, clear_two_factor=body.clear_two_factor
    ):
        raise HTTPException(status_code=404, detail="User not found.")
    return {"ok": True, "two_factor_cleared": body.clear_two_factor}
