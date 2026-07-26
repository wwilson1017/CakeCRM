"""Google OAuth 2.0 for the BYO Gmail connection (issue #8).

Direct confidential-client authorization-code flow — no proxy, no PKCE. Each
self-hosted instance registers its own redirect URI (derived from BACKEND_URL) in
its own Google Cloud OAuth client. The code exchange is one httpx POST (imported
lazily); token refresh is handled by google-auth's Credentials in client.py.

The requested scopes are the minimal set for read + create-draft:
  * gmail.readonly  — search/read threads, users.getProfile (the connected email)
  * gmail.compose   — create drafts
gmail.compose ALSO permits sending at the Google API level; Google has no
"draft but never send" scope, which is exactly why the no-send guarantee is
enforced at the tool layer (no send op/executor/def exists) — see SECURITY.md.
Identity scopes (openid/email/profile) are intentionally NOT requested: the email
comes from users.getProfile, and no id_token is parsed.
"""

from __future__ import annotations

import urllib.parse

from core.config import settings

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"

# The EXACT minimal scope set. If this ever grows a send/modify scope, the guard
# test (test_gmail_guard.py) fails.
SCOPES = [GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE]


def redirect_uri() -> str:
    """The OAuth redirect URI the user must register in their Google OAuth client."""
    return f"{settings.backend_url.rstrip('/')}/api/gmail/oauth/callback"


def build_auth_url(client_id: str, state: str) -> str:
    """Google consent URL. access_type=offline + prompt=consent guarantee a
    refresh_token on every connect."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    """Exchange an authorization code for tokens. Returns the parsed token JSON
    ({access_token, refresh_token?, expires_in, scope, ...}). Raises on HTTP error."""
    import httpx

    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def revoke_token(token: str) -> None:
    """Best-effort token revocation at Google. Swallows all errors — a local
    disconnect must always succeed even if Google is unreachable."""
    if not token:
        return
    try:
        import httpx

        httpx.post(REVOKE_ENDPOINT, data={"token": token}, timeout=15)
    except Exception:
        pass
