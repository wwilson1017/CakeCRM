"""`/api/mcp` — the signed-in side of connecting your own AI (#322).

Members manage their own connections under Settings → Personal; admins see and revoke
every seat's and own the switch "Members may connect their own AI tools" (off by
default; admins can always connect). A member with the switch off is refused every
create and consent with 403, and the Settings card shows them no control that could
only 403.

The consent endpoints serve the SPA route `/oauth/consent`, opened by ChatGPT or Claude.
Approving a third-party AI requires a FRESH sign-in in that window: the bearer must
have been issued in the last ten minutes, so an old session token cannot approve
anything even if a page got hold of one. Every handler is a sync `def` (psycopg2).
"""

import time
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import BaseModel, Field

from core.auth import decode_access_token, get_current_user, require_admin
from core.config import settings
from crm.todo_web import _same_origin
from mcp_endpoint import oauth, store

router = APIRouter()

FRESH_SIGN_IN = timedelta(minutes=10)


def _may_connect(user: dict) -> bool:
    return user.get("role") == "admin" or store.members_enabled()


def _require_may_connect(user: dict) -> None:
    if not _may_connect(user):
        raise HTTPException(status_code=403,
                            detail="An admin has not switched on connecting your own AI tools.")


def _require_fresh_sign_in(request: Request) -> None:
    """The bearer was minted within FRESH_SIGN_IN. A session token carries `exp` only,
    issued `expire_minutes` before it, so its issue time is derived from that."""
    raw = request.headers.get("authorization", "").partition(" ")[2].strip()
    try:
        exp = float(decode_access_token(raw)["exp"])
    except Exception:
        exp = 0.0
    issued = exp - settings.jwt.expire_minutes * 60
    if time.time() - issued > FRESH_SIGN_IN.total_seconds():
        raise HTTPException(status_code=401, detail="Sign in again to continue.")


# ── Consent (the SPA route /oauth/consent) ───────────────────────────────────

def _pending(rid: str) -> tuple[dict, dict | None]:
    if oauth.oauth_base_url() is None:
        raise HTTPException(status_code=409, detail="Sign-in from AI apps is not set up here.")
    request = store.load_request(rid)
    if request is None:
        raise HTTPException(status_code=404, detail="This sign-in request has expired.")
    row = store.get_client(request["client_id"])
    return request, (row or {}).get("metadata")


def _write_requested(params: dict) -> bool:
    scopes = params.get("scopes")
    return scopes is None or "crm:write" in scopes


@router.get("/consent/{rid}")
def get_consent(rid: str, request: Request, user: dict = Depends(get_current_user)):
    _require_fresh_sign_in(request)
    _require_may_connect(user)
    pending, metadata = _pending(rid)
    params = pending["params"]
    host = urlsplit(str(params.get("redirect_uri") or "")).hostname or ""
    return {
        "client_name": oauth.client_name(metadata, pending["client_id"]),
        "client_id": pending["client_id"],
        "redirect_host": host,
        "loopback": host in ("localhost", "127.0.0.1", "::1", "[::1]"),
        "write_requested": _write_requested(params),
        "email": user.get("email"),
        "expires_at": pending["expires_at"],
    }


class ConsentBody(BaseModel):
    approve: bool
    read_only: bool = False


@router.post("/consent/{rid}")
def answer_consent(rid: str, body: ConsentBody, request: Request,
                   user: dict = Depends(get_current_user)):
    _same_origin(request)
    _require_fresh_sign_in(request)
    _require_may_connect(user)
    pending, metadata = _pending(rid)
    params = pending["params"]
    read_only = body.read_only or not _write_requested(params)
    requested = params.get("scopes") or []
    granted = ["crm:read"] + ([] if read_only else ["crm:write"]) + (
        ["offline_access"] if "offline_access" in requested else [])
    answered = store.approve_request(
        rid, approve=body.approve, user_id=user["id"],
        client_name=oauth.client_name(metadata, pending["client_id"]),
        read_only=read_only, scopes=" ".join(granted),
    )
    if answered is None:
        raise HTTPException(status_code=404, detail="This sign-in request has expired.")
    _, code = answered
    base = oauth.oauth_base_url()
    redirect_uri = str(params["redirect_uri"])
    if code is None:
        return {"redirect": construct_redirect_uri(
            redirect_uri, error="access_denied", state=params.get("state"), iss=base)}
    return {"redirect": construct_redirect_uri(
        redirect_uri, code=code, state=params.get("state"), iss=base)}


# ── A member's own connections ───────────────────────────────────────────────

@router.get("/connections")
def my_connections(user: dict = Depends(get_current_user)):
    base = oauth.oauth_base_url()
    return {
        "may_connect": _may_connect(user),
        "oauth_url": f"{base}/mcp" if base else None,
        "connections": store.list_connections(user_id=user["id"]),
    }


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    read_only: bool = False
    lifetime_days: int | None = 90


@router.post("/tokens")
def create_token(body: TokenBody, user: dict = Depends(get_current_user)):
    _require_may_connect(user)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the token a name.")
    if body.lifetime_days is not None and body.lifetime_days not in store.LIFETIME_DAYS:
        raise HTTPException(status_code=422, detail="Choose 30, 90 or 365 days, or never.")
    try:
        token, row = store.create_personal_token(
            user["id"], name=name, read_only=body.read_only, lifetime_days=body.lifetime_days,
        )
    except store.CapReached:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {store.MAX_CONNECTIONS_PER_SEAT} connections. Revoke one first.",
        ) from None
    return {**row, "token": token}


@router.delete("/connections/{connection_id}")
def revoke_mine(connection_id: int, user: dict = Depends(get_current_user)):
    if not store.revoke(connection_id, user_id=user["id"]):
        raise HTTPException(status_code=404, detail="No such connection.")
    return {"ok": True}


@router.get("/connections/{connection_id}/calls")
def my_calls(connection_id: int, user: dict = Depends(get_current_user)):
    return {"calls": store.list_calls(connection_id, user_id=user["id"])}


# ── Admin: every seat's connections and the members switch ───────────────────

@router.get("/admin/connections")
def all_connections(_admin: dict = Depends(require_admin)):
    return {"members_enabled": store.members_enabled(),
            "connections": store.list_connections()}


class SwitchBody(BaseModel):
    members_enabled: bool


@router.put("/admin/settings")
def set_switch(body: SwitchBody, _admin: dict = Depends(require_admin)):
    return {"members_enabled": store.set_members_enabled(body.members_enabled)}


@router.delete("/admin/connections/{connection_id}")
def revoke_any(connection_id: int, _admin: dict = Depends(require_admin)):
    if not store.revoke(connection_id):
        raise HTTPException(status_code=404, detail="No such connection.")
    return {"ok": True}


@router.get("/admin/connections/{connection_id}/calls")
def any_calls(connection_id: int, _admin: dict = Depends(require_admin)):
    return {"calls": store.list_calls(connection_id)}
