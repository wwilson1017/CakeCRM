"""CakeCRM as its own OAuth 2.1 authorization server for `/mcp` (#322).

Built on the official `mcp` SDK's handlers (`create_auth_routes`), which own request
parsing, PKCE S256, redirect and scope checks and the RFC error shapes. This module is
the provider behind them plus the rules the stage-0 spike found (docs/agents/mcp.md):

  1. issuer and resource URLs go through `AuthSettings`, so `https://host` never
     becomes `https://host/` (clients compare `iss` as an exact string);
  2. the metadata route is replaced to advertise `none`, client-ID metadata documents
     (CIMD) and the `iss` response parameter;
  3. dynamic registration is public-clients-only: no secret is ever minted or stored;
  4. CIMD is fetched here, only from a public https host, connecting to the address we
     validated (TLS still checks the real hostname), streamed under a hard size cap;
  5. a loopback redirect may change its port (RFC 8252 §7.3);
  6. codes and refresh tokens are consumed atomically in `store`;
  7. there is no /revoke: the SDK's revocation request requires a client secret, which a
     public client never has. People revoke in CakeCRM Settings.

The consent step is ours: `authorize()` parks the request and sends the browser to the
SPA route `/oauth/consent`, which signs the person in in that window and calls
`router.approve_consent`. OAuth exists only when the install has a configured https
address (or http on localhost, for development); otherwise only personal tokens work.
"""

import asyncio
import ipaddress
import json
import logging
import secrets
import socket
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import (
    build_metadata,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
    validate_issuer_url,
)
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.shared.auth import (
    InvalidRedirectUriError,
    OAuthClientInformationFull,
    OAuthToken,
)
from pydantic import AnyUrl, ValidationError
from starlette.responses import JSONResponse
from starlette.routing import Route

from core import config
from core.ratelimit import IPRateLimiter
from mcp_endpoint import store
from mcp_endpoint.server import McpEndpoint
from mcp_endpoint.store import CapReached, hash_token

logger = logging.getLogger(__name__)

SCOPES = ["crm:read", "crm:write", "offline_access"]
CONSENT_PATH = "/oauth/consent"
OAUTH_PATHS = frozenset({
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource/mcp",
    "/authorize", "/token", "/register",
})

CIMD_CACHE_TTL = timedelta(hours=24)
CIMD_MAX_BYTES = 64 * 1024
CIMD_TIMEOUT_SECONDS = 5.0
_FETCH_SLOTS = threading.BoundedSemaphore(4)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
_SCRIPTABLE_SCHEMES = frozenset({"javascript", "data", "vbscript", "file", "blob", "about", "filesystem"})


def _redirect_allowed(uri) -> bool:
    """Anonymous clients pick their own redirect URIs and the consent page navigates to
    them, so refuse schemes that run code in our origin. Plain http only for loopback;
    desktop clients' own schemes (cursor://, vscode://) are fine."""
    parts = urlsplit(str(uri))
    scheme = parts.scheme.lower()
    if scheme in _SCRIPTABLE_SCHEMES:
        return False
    if scheme == "http":
        return parts.hostname in _LOOPBACK_HOSTS or f"[{parts.hostname}]" in _LOOPBACK_HOSTS
    return True

# Anonymous surfaces that write rows or trigger a fetch get a per-IP budget.
register_limiter = IPRateLimiter(window=3600, max_hits=20)
authorize_limiter = IPRateLimiter(window=300, max_hits=60)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def oauth_base_url() -> str | None:
    """The issuer, or None when OAuth must stay hidden (no configured address, or one
    the SDK would refuse: not https and not localhost)."""
    base = config._configured_base_url().strip().rstrip("/")
    if not base:
        return None
    try:
        validate_issuer_url(AuthSettings(issuer_url=base, resource_server_url=base + "/mcp",
                                         validate_token_resource=False).issuer_url)
    except (ValueError, ValidationError):
        logger.info("mcp: OAuth sign-in is off: %s is not https", base)
        return None
    return base


# ── Clients ──────────────────────────────────────────────────────────────────

class _Client(OAuthClientInformationFull):
    """A registered or CIMD client, always public, with loopback ports ignored."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None and not _redirect_allowed(redirect_uri):
            raise InvalidRedirectUriError(f"Redirect URI '{redirect_uri}' is not allowed")
        if redirect_uri is not None and redirect_uri.host in _LOOPBACK_HOSTS:
            for registered in self.redirect_uris or []:
                if (registered.scheme, registered.host, registered.path or "/") == (
                        redirect_uri.scheme, redirect_uri.host, redirect_uri.path or "/"):
                    return redirect_uri
            raise InvalidRedirectUriError(f"Redirect URI '{redirect_uri}' not registered for client")
        return super().validate_redirect_uri(redirect_uri)


def _as_client(metadata: dict) -> _Client:
    client = _Client.model_validate(metadata)
    client.token_endpoint_auth_method = "none"
    client.client_secret = None
    client.client_secret_expires_at = None
    client.scope = " ".join(SCOPES)  # this server decides scopes
    return client


def client_name(metadata: dict | None, client_id: str) -> str:
    name = ((metadata or {}).get("client_name") or "").strip()
    if name:
        return name[:60]
    host = urlsplit(client_id).hostname
    return host or "An AI tool"


class _Transient(Exception):
    """The metadata host could not be reached right now."""


def _fetch_json(url: str) -> dict:
    """Fetch a client-ID metadata document. ValueError = refused; _Transient = retry later."""
    import httpx

    parts = urlsplit(url)
    host = parts.hostname
    if (parts.scheme != "https" or not host or parts.username or parts.password
            or parts.fragment or parts.path in ("", "/")):
        raise ValueError("a client ID document must be an https URL with a path")
    port = parts.port or 443
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise _Transient(str(e)) from e
    addresses = sorted({ipaddress.ip_address(i[4][0]) for i in infos}, key=str)
    if not addresses or not all(a.is_global for a in addresses):
        raise ValueError("a client ID document must live on a public host")
    ip = addresses[0]
    netloc = f"[{ip}]:{port}" if ip.version == 6 else f"{ip}:{port}"
    target = urlunsplit(("https", netloc, parts.path, parts.query, ""))
    host_header = host if port == 443 else f"{host}:{port}"
    if not _FETCH_SLOTS.acquire(timeout=CIMD_TIMEOUT_SECONDS):
        raise _Transient("too many fetches in flight")
    try:
        with httpx.Client(timeout=CIMD_TIMEOUT_SECONDS, follow_redirects=False) as client:
            with client.stream(
                "GET", target,
                headers={"Host": host_header, "Accept": "application/json"},
                # Connect to the address we checked, while TLS verifies the certificate
                # against the real hostname (SNI + hostname check use this value).
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code >= 500:
                    raise _Transient(f"status {response.status_code}")
                if response.status_code != 200:
                    raise ValueError(f"status {response.status_code}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > CIMD_MAX_BYTES:
                        raise ValueError("client ID document too large")
    except httpx.TransportError as e:
        raise _Transient(str(e)) from e
    finally:
        _FETCH_SLOTS.release()
    try:
        doc = json.loads(bytes(body))
    except ValueError as e:
        raise ValueError("client ID document is not JSON") from e
    if not isinstance(doc, dict):
        raise ValueError("client ID document is not a JSON object")
    return doc


def _validate_cimd(url: str, doc: dict) -> dict:
    if doc.get("client_id") != url:
        raise ValueError("client_id in the document must equal its URL")
    if doc.get("token_endpoint_auth_method") not in (None, "none"):
        raise ValueError("only public clients may use a client ID document")
    if not doc.get("redirect_uris"):
        raise ValueError("the document names no redirect URIs")
    if not all(_redirect_allowed(u) for u in doc["redirect_uris"]):
        raise ValueError("the document names a redirect URI that is not allowed")
    try:
        return _as_client(doc).model_dump(mode="json", exclude_none=True)
    except ValidationError as e:
        raise ValueError("invalid client ID document") from e


def cimd_client(url: str, *, now: datetime | None = None) -> dict | None:
    """The metadata for a CIMD client id, from the 24-hour cache or a fresh fetch.

    A document that fails validation evicts the cache. Only a transient failure (the
    host is down, a 5xx) falls back to a stale cached copy, so a connected client keeps
    refreshing through a metadata-host hiccup.
    """
    now = now or _utcnow()
    cached = store.get_client(url)
    if cached and cached["kind"] == "cimd" and cached["fetched_at"] > now - CIMD_CACHE_TTL:
        return cached["metadata"]
    try:
        metadata = _validate_cimd(url, _fetch_json(url))
    except _Transient as e:
        logger.warning("mcp: client ID document %s unreachable (%s)", url, e)
        return cached["metadata"] if cached and cached["kind"] == "cimd" else None
    except ValueError as e:
        logger.info("mcp: refused client ID document %s: %s", url, e)
        if cached:
            store.delete_client(url)
        return None
    store.put_client(url, "cimd", metadata, now=now)
    return metadata


def _load_client_metadata(client_id: str) -> dict | None:
    if client_id.startswith("https://"):
        return cimd_client(client_id)
    row = store.get_client(client_id)
    return row["metadata"] if row else None


# ── The provider the SDK handlers call ───────────────────────────────────────

class Provider:
    """`OAuthAuthorizationServerProvider` over `store`. Every store call runs in a
    thread: the handlers are async, psycopg2 is blocking."""

    async def get_client(self, client_id: str) -> _Client | None:
        metadata = await asyncio.to_thread(_load_client_metadata, client_id)
        return _as_client(metadata) if metadata else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Public clients only: the SDK minted a secret for a client that asked for
        # client_secret_post (or nothing); we never store or honour one. The handler
        # echoes `client_info` after this returns, so the client learns it is public.
        if not all(_redirect_allowed(u) for u in client_info.redirect_uris or []):
            raise RegistrationError("invalid_redirect_uri", "redirect URI scheme is not allowed")
        client_info.token_endpoint_auth_method = "none"
        client_info.client_secret = None
        client_info.client_secret_expires_at = None
        await asyncio.to_thread(
            store.put_client, client_info.client_id, "dcr",
            client_info.model_dump(mode="json", exclude_none=True),
        )

    async def authorize(self, client, params) -> str:
        base = oauth_base_url()
        if base is None:
            raise AuthorizeError("server_error", "sign-in is not configured on this install")
        if params.resource and params.resource.rstrip("/") != f"{base}/mcp":
            raise AuthorizeError("invalid_target", "this server only issues tokens for its /mcp")
        rid = secrets.token_urlsafe(16)
        await asyncio.to_thread(
            store.save_request, rid, client.client_id, params.model_dump(mode="json"),
        )
        return f"{base}{CONSENT_PATH}?request={rid}"

    async def load_authorization_code(self, client, authorization_code: str):
        row = await asyncio.to_thread(store.load_code, hash_token(authorization_code))
        if row is None or row["client_id"] != client.client_id:
            return None
        p = row["params"]
        return AuthorizationCode(
            code=authorization_code,
            scopes=row["scopes"].split(),
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=p["code_challenge"],
            redirect_uri=p["redirect_uri"],
            redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"],
            resource=p.get("resource"),
            subject=str(row["user_id"]),
        )

    @staticmethod
    def _token(out: dict) -> OAuthToken:
        return OAuthToken(
            access_token=out["access_token"], token_type="Bearer",
            expires_in=out["expires_in"], refresh_token=out["refresh_token"],
            scope=" ".join(out["scopes"]),
        )

    async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
        try:
            out = await asyncio.to_thread(store.exchange_code, hash_token(authorization_code.code))
        except CapReached as e:
            raise TokenError("invalid_grant", "this person has too many connected AI tools; "
                                              "revoke one in CakeCRM Settings first") from e
        if out is None:
            raise TokenError("invalid_grant", "authorization code already used or expired")
        return self._token(out)

    async def load_refresh_token(self, client, refresh_token: str):
        live = await asyncio.to_thread(store.load_refresh, hash_token(refresh_token))
        if live is None or live["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=client.client_id,
                            scopes=live["scopes"], subject=str(live["user_id"]))

    async def exchange_refresh_token(self, client, refresh_token, scopes) -> OAuthToken:
        out = await asyncio.to_thread(store.rotate_refresh, hash_token(refresh_token.token), scopes)
        if out is None:
            raise TokenError("invalid_grant", "refresh token already used or revoked")
        return self._token(out)

    # The SDK calls these two only from /revoke and its bearer middleware, neither of
    # which is mounted (/mcp does its own one lookup, store.resolve_bearer).
    async def load_access_token(self, token: str) -> None:
        return None

    async def revoke_token(self, token) -> None:
        return None


provider = Provider()


class _Limited:
    """Wrap an anonymous route in a per-IP budget."""

    def __init__(self, app, limiter: IPRateLimiter):
        self.app, self.limiter = app, limiter

    async def __call__(self, scope, receive, send):
        client = scope.get("client")
        if not self.limiter.allow(client[0] if client else "unknown"):
            response = JSONResponse({"error": "temporarily_unavailable"}, status_code=429,
                                    headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


def build_routes(oauth_base: str | None) -> list[Route]:
    """`/mcp`, plus the OAuth routes when `oauth_base` (the issuer) is set."""
    routes: list[Route] = []
    if oauth_base:
        settings = AuthSettings(issuer_url=oauth_base, resource_server_url=f"{oauth_base}/mcp",
                                validate_token_resource=False)
        registration = ClientRegistrationOptions(enabled=True, valid_scopes=SCOPES,
                                                 default_scopes=SCOPES)
        # No /revoke: the SDK's revocation request model REQUIRES a client_secret field,
        # so a public client (the only kind we register) cannot call it. A person revokes
        # in CakeCRM Settings, which is also where they can see the connection.
        revocation = RevocationOptions(enabled=False)
        routes = create_auth_routes(provider, settings.issuer_url,
                                    client_registration_options=registration,
                                    revocation_options=revocation)
        metadata = build_metadata(settings.issuer_url, None, registration, revocation).model_copy(
            update={
                "token_endpoint_auth_methods_supported": ["none"],
                "client_id_metadata_document_supported": True,
                "authorization_response_iss_parameter_supported": True,
            },
        )
        assert routes[0].path == "/.well-known/oauth-authorization-server"
        routes[0] = Route(
            routes[0].path,
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
        for route in routes:
            if route.path == "/register":
                route.app = _Limited(route.app, register_limiter)
            elif route.path == "/authorize":
                route.app = _Limited(route.app, authorize_limiter)
        routes += create_protected_resource_routes(
            settings.resource_server_url, [settings.issuer_url], SCOPES, "CakeCRM",
        )
    routes.append(Route("/mcp", endpoint=McpEndpoint(), methods=["GET", "POST", "DELETE"]))
    return routes
