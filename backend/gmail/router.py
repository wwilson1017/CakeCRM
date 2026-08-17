"""Gmail connection endpoints (issue #8), mounted at /api/gmail.

Flow: save BYO OAuth app credentials → start the redirect (single-use CSRF state)
→ the UNAUTHENTICATED browser callback exchanges the code and stores tokens →
status / disconnect. There is no send endpoint — the assistant's only Gmail write
is gmail_create_draft (a draft), gated by the confirmation flow.

The callback and other endpoints that touch psycopg2/httpx/encryption are sync
`def` so FastAPI runs them in a worker thread (never blocking the event loop).
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from core.auth import get_current_user
from core.config import settings
from gmail import client, oauth, ops, store

logger = logging.getLogger(__name__)

router = APIRouter()


class AppCredentials(BaseModel):
    client_id: str
    client_secret: str


def _settings_redirect(status: str, reason: str = "") -> RedirectResponse:
    """Redirect the browser back to the SPA settings page with a result code."""
    base = settings.frontend_url.rstrip("/")
    url = f"{base}/crm/settings?gmail={status}"
    if reason:
        url += f"&reason={reason}"
    return RedirectResponse(url, status_code=302)


@router.get("/status")
def gmail_status(user=Depends(get_current_user)):
    """Sanitized connection status (never secrets/ciphertext)."""
    return store.status_dict()


@router.post("/app")
def save_app(body: AppCredentials, user=Depends(get_current_user)):
    """Store BYO Google OAuth app credentials (client_id + client_secret).

    Replacing the app invalidates any tokens minted under the old client, so the
    outgoing refresh token is revoked at Google (best-effort, mirroring disconnect)
    — otherwise rotating credentials after a suspected secret leak would leave the
    old grant live and untracked. The save returns the ciphertext it actually
    cleared, so we revoke exactly that grant rather than one read beforehand (#43)."""
    client_id = body.client_id.strip()
    client_secret = body.client_secret.strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Both client ID and client secret are required.")
    from core.encryption import decrypt_value

    old_refresh = decrypt_value(store.save_app_credentials(client_id, client_secret) or "")
    if old_refresh:
        oauth.revoke_token(old_refresh)
    return store.status_dict()


@router.post("/oauth/start")
def oauth_start(user=Depends(get_current_user)):
    """Mint a single-use CSRF state and return the Google consent URL."""
    client_id, client_secret = store.get_app_credentials()
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Save your Google OAuth app credentials first.")
    state = secrets.token_urlsafe(32)
    store.set_oauth_state_hash(state)
    return {"auth_url": oauth.build_auth_url(client_id, state)}


@router.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """UNAUTHENTICATED browser callback from Google (protected by single-use state).

    Claims the state FIRST so both success and denial consume it, then exchanges
    the code and persists tokens. Any post-grant failure revokes the received
    tokens before redirecting, so no live Google authorization is left behind.

    The claim also captures the connection generation, which gates the persist
    below: seconds of Google round-trips sit between the two, and the admin may
    disconnect or replace the OAuth app in that window (#43).
    """
    try:
        # 1. Consume the CSRF state before doing anything else. `claimed_generation`
        #    is None on a failed claim; 0 is a legitimate generation, so test for
        #    None explicitly rather than truthiness.
        claimed_generation = store.claim_oauth_state(state) if state else None
        if claimed_generation is None:
            return _settings_redirect("error", "state")

        # 2. User declined on Google's consent screen.
        if error:
            return _settings_redirect("error", "denied")

        if not code:
            return _settings_redirect("error", "state")

        client_id, client_secret = store.get_app_credentials()
        if not client_id or not client_secret:
            return _settings_redirect("error", "exchange")

        # 3. Exchange the code for tokens.
        try:
            tokens = oauth.exchange_code(code, client_id, client_secret)
        except Exception as e:
            logger.warning("gmail oauth exchange failed: %s", e)
            return _settings_redirect("error", "exchange")

        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")

        # 4. A refresh token is mandatory (offline access). Revoke and bail if absent.
        if not refresh_token:
            oauth.revoke_token(access_token)
            return _settings_redirect("error", "no_refresh_token")

        # 5. Both Gmail scopes must have been granted (granular consent).
        granted = set(tokens.get("scope", "").split())
        if not {oauth.GMAIL_READONLY_SCOPE, oauth.GMAIL_COMPOSE_SCOPE} <= granted:
            oauth.revoke_token(refresh_token)
            return _settings_redirect("error", "scopes")

        # 6. Fetch the connected address — also proves the grant actually works.
        # Routed through the allow-list seam (call_with_token), so this second
        # service-building path can't invoke anything outside the read/draft ops.
        try:
            email = client.call_with_token(access_token, ops.get_profile_op).get("email", "")
        except Exception as e:
            logger.warning("gmail oauth profile fetch failed: %s", e)
            oauth.revoke_token(refresh_token)
            return _settings_redirect("error", "profile")

        # 7. Persist. Expiry = now + expires_in seconds. If persistence fails, the
        # just-granted tokens are unusable to us — revoke them so no live grant is
        # orphaned at Google.
        from datetime import datetime, timedelta, timezone

        expires_in = int(tokens.get("expires_in", 3600) or 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        # Persist the fixed minimal scope set we requested, NOT whatever Google
        # returned — if the user's OAuth app is configured with extra scopes, Google
        # could grant more, but we never record or refresh under anything broader
        # than gmail.readonly + gmail.compose. The write is a compare-and-swap on
        # the generation captured at state-claim: a disconnect or app-replace during
        # the Google round-trips above makes it a no-op instead of resurrecting a
        # connection the admin just ended (#43).
        try:
            persisted = store.save_tokens(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scopes=" ".join(oauth.SCOPES),
                email=email,
                expected_generation=claimed_generation,
            )
        except Exception as e:
            logger.error("gmail oauth token persist failed: %s", e)
            oauth.revoke_token(refresh_token)
            return _settings_redirect("error", "exchange")
        if not persisted:
            # The connection changed underneath this handshake. Same discipline as
            # every other post-grant failure: never leave a live grant orphaned.
            logger.info("gmail oauth persist superseded by a concurrent connection change")
            oauth.revoke_token(refresh_token)
            return _settings_redirect("error", "conflict")
        return _settings_redirect("connected")
    except Exception as e:  # never 500 the browser callback
        logger.error("gmail oauth callback error: %s", e)
        return _settings_redirect("error", "exchange")


@router.delete("/connection")
def disconnect(user=Depends(get_current_user)):
    """Disconnect Gmail: clear the connection locally — capturing the token it held
    in the same statement — then best-effort revoke exactly that token at Google
    (keeping app credentials for a one-click reconnect)."""
    from core.encryption import decrypt_value

    # One atomic clear-and-capture: the store returns the ciphertext it actually
    # cleared, so the token we revoke is by construction the token we ended (#43).
    # This replaces the old read-then-clear, which could revoke a stale token while
    # clearing a newer one, or clear without revoking when the pre-read failed.
    # Residual: if the process dies between the clear and the revoke below, the
    # grant stays live at Google until the user removes it from their account —
    # strictly narrower than before, and revoke_token was always best-effort.
    refresh_secret = decrypt_value(store.clear_connection() or "")
    if refresh_secret:
        oauth.revoke_token(refresh_secret)
    return {"ok": True}
