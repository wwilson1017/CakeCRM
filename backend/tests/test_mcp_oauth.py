"""CakeCRM's OAuth 2.1 authorization server for `/mcp` (#322), end to end.

The SDK's own `OAuthClientProvider` drives the real routes (discovery, registration or a
client-ID metadata document, /authorize, our consent API, /token with PKCE) and then
calls a tool, for BOTH registration methods. Hermetic: an in-memory store stands in for
Postgres (the SQL, including the single-use races, is proved in
test_mcp_integration.py), and its bearer lookup runs the real `store._judge`.
"""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from conftest import fake_admin, fake_member
from fastapi import FastAPI
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

from assistant.registry import ToolRegistry
from core.auth import create_user_token, get_current_user
from core.config import settings
from gmail import tools as gmail_tools
from mcp_endpoint import oauth, server, store
from mcp_endpoint.router import router

BASE = "https://crm.example"
CIMD_URL = "https://client.example/oauth/client.json"
REDIRECT = "https://client.example/callback"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeStore:
    """The OAuth half of `store` over dicts. Single use is a dict pop."""

    def __init__(self):
        self.clients, self.requests, self.codes = {}, {}, {}
        self.connections, self.credentials = {}, {}
        self.members = True
        self.epoch = 0

    # clients
    def get_client(self, client_id):
        return self.clients.get(client_id)

    def put_client(self, client_id, kind, metadata, *, now=None):
        self.clients[client_id] = {"client_id": client_id, "kind": kind, "metadata": metadata,
                                   "fetched_at": now or _now()}

    def delete_client(self, client_id):
        self.clients.pop(client_id, None)

    # consent
    def save_request(self, rid, client_id, params, *, now=None):
        self.requests[rid] = {"id": rid, "client_id": client_id, "params": params,
                              "expires_at": _now() + store.REQUEST_TTL}

    def load_request(self, rid, *, now=None):
        return self.requests.get(rid)

    def approve_request(self, rid, *, approve, user_id, client_name, read_only, scopes, now=None):
        request = self.requests.pop(rid, None)
        if request is None:
            return None
        if not approve:
            return request, None
        code = secrets.token_urlsafe(32)
        self.codes[store.hash_token(code)] = {
            "client_id": request["client_id"], "user_id": user_id, "token_epoch": self.epoch,
            "client_name": client_name, "read_only": read_only, "scopes": scopes,
            "params": request["params"], "expires_at": _now() + store.CODE_TTL,
        }
        return request, code

    def load_code(self, code_hash, *, now=None):
        return self.codes.get(code_hash)

    # tokens
    def _pair(self, cid, scopes):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.credentials[store.hash_token(access)] = ("access", cid, "", _now() + timedelta(hours=1))
        self.credentials[store.hash_token(refresh)] = ("refresh", cid, scopes, None)
        return {"connection_id": cid, "access_token": access, "refresh_token": refresh,
                "scopes": scopes.split(), "expires_in": 3600}

    def exchange_code(self, code_hash, *, now=None):
        code = self.codes.pop(code_hash, None)
        if code is None or code["token_epoch"] != self.epoch:
            return None
        cid = len(self.connections) + 1
        self.connections[cid] = {"user_id": code["user_id"], "name": code["client_name"],
                                 "client_id": code["client_id"], "read_only": code["read_only"]}
        return self._pair(cid, code["scopes"])

    def _row(self, cid, cred_exp=None):
        c = self.connections[cid]
        seat = fake_admin() if c["user_id"] == fake_admin()["id"] else fake_member()
        return {"id": cid, "user_id": c["user_id"], "kind": "oauth", "name": c["name"],
                "client_id": c["client_id"], "read_only": c["read_only"], "token_epoch": 0,
                "seat_epoch": self.epoch, "expires_at": None, "last_used_at": _now(),
                "created_at": _now(), "email": seat["email"], "user_name": seat["name"],
                "role": seat["role"], "is_active": True, "mcp_members_enabled": self.members,
                "credential_expires_at": cred_exp}

    def _live(self, token_hash, kind):
        cred = self.credentials.get(token_hash)
        if cred is None or cred[0] != kind or cred[1] not in self.connections:
            return None
        return store._judge(self._row(cred[1], cred[3]), now=_now()), cred

    def resolve_bearer(self, token, *, now=None):
        found = self._live(store.hash_token(token), "access")
        return found[0] if found else None

    def load_refresh(self, token_hash, *, now=None):
        found = self._live(token_hash, "refresh")
        return {**found[0], "scopes": found[1][2].split()} if found and found[0] else None

    def rotate_refresh(self, token_hash, scopes, *, now=None):
        found = self._live(token_hash, "refresh")
        if not found or not found[0]:
            return None
        cid = found[1][1]
        del self.credentials[token_hash]
        for h in [h for h, c in self.credentials.items() if c[1] == cid and c[0] == "access"]:
            del self.credentials[h]
        if "crm:write" not in scopes:
            self.connections[cid]["read_only"] = True
        return self._pair(cid, " ".join(scopes))

    def members_enabled(self):
        return self.members

    def touch(self, *a, **k):
        return False

    def record_call(self, *a, **k):
        return None


CIMD_DOC = {"client_id": CIMD_URL, "client_name": "Test CIMD Client", "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}


@pytest.fixture
def fake(monkeypatch, todo_mode):
    todo_mode("gtd")
    f = FakeStore()
    for name in ("get_client", "put_client", "delete_client", "save_request", "load_request",
                 "approve_request", "load_code", "exchange_code", "load_refresh", "rotate_refresh",
                 "resolve_bearer", "touch", "record_call",
                 "members_enabled"):
        monkeypatch.setattr(store, name, getattr(f, name))
    monkeypatch.setattr(oauth, "oauth_base_url", lambda: BASE)
    monkeypatch.setattr(oauth, "_fetch_json", lambda url: dict(CIMD_DOC))
    monkeypatch.setattr(gmail_tools.store, "is_connected", lambda: False)
    monkeypatch.setattr(ToolRegistry, "execute_tool_sync", lambda self, n, a: {"ok": True, "tool": n})
    for limiter in (server.call_limiter, server.bad_token_limiter, oauth.register_limiter,
                    oauth.authorize_limiter):
        limiter.clear()
    return f


@pytest.fixture
def seat():
    """Who signs in on the consent page. Tests may swap it for an admin."""
    return {"user": fake_member()}


@pytest.fixture
def app(fake, seat):
    a = FastAPI()
    a.router.routes.extend(oauth.build_routes(BASE))
    a.include_router(router, prefix="/api/mcp")
    a.dependency_overrides[get_current_user] = lambda: seat["user"]
    return a


def _http(app, **kw):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE, **kw)


def _fresh_jwt(user=None) -> str:
    return create_user_token(user or fake_member())


async def _consent(app, authorize_url, *, approve=True, read_only=False, jwt=None,
                   same_origin=True):
    """What the consent page does: follow /authorize, then answer the consent API."""
    async with _http(app) as c:
        r = await c.get(authorize_url.replace(BASE, ""), follow_redirects=False)
        assert r.status_code == 302, r.text
        location = urlsplit(r.headers["location"])
        assert location.path == oauth.CONSENT_PATH
        rid = parse_qs(location.query)["request"][0]
        headers = {"Authorization": f"Bearer {jwt or _fresh_jwt()}"}
        if same_origin:
            headers["Sec-Fetch-Site"] = "same-origin"
        info = await c.get(f"/api/mcp/consent/{rid}", headers=headers)
        answer = await c.post(f"/api/mcp/consent/{rid}", headers=headers,
                              json={"approve": approve, "read_only": read_only})
        return rid, info, answer


class _Mem:
    def __init__(self):
        self.tokens = self.info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.info

    async def set_client_info(self, info):
        self.info = info


def _sdk_client(app, *, cimd: bool, read_only=False):
    captured, mem = {}, _Mem()

    async def redirect_handler(url):
        _, info, answer = await _consent(app, url, read_only=read_only)
        assert info.status_code == 200, info.text
        captured["info"] = info.json()
        captured["q"] = parse_qs(urlsplit(answer.json()["redirect"]).query)

    async def callback_handler():
        q = captured["q"]
        return AuthorizationCodeResult(code=q["code"][0], state=q.get("state", [None])[0],
                                       iss=q["iss"][0])

    auth = OAuthClientProvider(
        f"{BASE}/mcp",
        OAuthClientMetadata(client_name="Test DCR Client", redirect_uris=[REDIRECT],
                            grant_types=["authorization_code", "refresh_token"],
                            response_types=["code"], token_endpoint_auth_method="none",
                            scope="crm:read crm:write offline_access"),
        mem, redirect_handler=redirect_handler, callback_handler=callback_handler,
        client_metadata_url=CIMD_URL if cimd else None,
    )
    client = Client(streamable_http_client(f"{BASE}/mcp", http_client=_http(app, auth=auth)))
    return client, mem, captured


async def test_metadata_advertises_public_clients_cimd_s256_and_iss(app):
    async with _http(app) as c:
        md = (await c.get("/.well-known/oauth-authorization-server")).json()
    assert md["issuer"] == BASE  # exact string: no trailing slash
    assert md["token_endpoint_auth_methods_supported"] == ["none"]
    assert md["code_challenge_methods_supported"] == ["S256"]
    assert md["client_id_metadata_document_supported"] is True
    assert md["authorization_response_iss_parameter_supported"] is True
    assert md["registration_endpoint"] == f"{BASE}/register"
    assert set(md["scopes_supported"]) == set(oauth.SCOPES)


async def test_protected_resource_metadata_names_the_mcp_url_and_the_issuer_first(app):
    async with _http(app) as c:
        prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert prm["resource"] == f"{BASE}/mcp"
    assert prm["authorization_servers"][0] == BASE


async def test_dynamic_registration_makes_a_public_client_and_stores_no_secret(app, fake):
    async with _http(app) as c:
        r = await c.post("/register", json={"client_name": "X", "redirect_uris": [REDIRECT],
                                            "token_endpoint_auth_method": "client_secret_post"})
    body = r.json()
    assert r.status_code == 201
    assert body["token_endpoint_auth_method"] == "none" and "client_secret" not in body
    stored = fake.clients[body["client_id"]]["metadata"]
    assert "client_secret" not in stored and stored["token_endpoint_auth_method"] == "none"


@pytest.mark.parametrize("cimd", [False, True], ids=["dcr", "cimd"])
async def test_the_full_code_flow_completes_then_calls_a_tool(app, fake, cimd):
    client, mem, captured = _sdk_client(app, cimd=cimd)
    async with client:
        result = await client.call_tool("crm_create_contact", {"name": "Ada"})
    assert not result.is_error
    assert mem.info.client_id == (CIMD_URL if cimd else mem.info.client_id)
    assert (CIMD_URL in fake.clients) is cimd
    assert captured["info"]["client_name"] == ("Test CIMD Client" if cimd else "Test DCR Client")
    assert captured["info"]["redirect_host"] == "client.example"
    assert mem.tokens.refresh_token


async def test_refresh_rotates_and_reusing_the_old_refresh_token_is_invalid_grant(app, fake):
    client, mem, _ = _sdk_client(app, cimd=False)
    async with client:
        await client.list_tools()
    old = mem.tokens.refresh_token
    form = {"grant_type": "refresh_token", "refresh_token": old, "client_id": mem.info.client_id}
    async with _http(app) as c:
        first = await c.post("/token", data=form)
        again = await c.post("/token", data=form)
    assert first.status_code == 200 and first.json()["refresh_token"] != old
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def _registered(app) -> str:
    async with _http(app) as c:
        r = await c.post("/register", json={"client_name": "Hand", "redirect_uris": [REDIRECT],
                                            "token_endpoint_auth_method": "none"})
    return r.json()["client_id"]


def _authorize_url(client_id, challenge, *, redirect=REDIRECT, scope="crm:read crm:write",
                   resource=None):
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
         "code_challenge": challenge, "code_challenge_method": "S256", "state": "st",
         "scope": scope}
    if resource:
        q["resource"] = resource
    from urllib.parse import urlencode
    return f"{BASE}/authorize?{urlencode(q)}"


async def _hand_flow(app, *, verifier_override=None, **consent_kw):
    client_id = await _registered(app)
    verifier, challenge = _pkce()
    _, _, answer = await _consent(app, _authorize_url(client_id, challenge), **consent_kw)
    q = parse_qs(urlsplit(answer.json()["redirect"]).query)
    form = {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
            "client_id": client_id, "code_verifier": verifier_override or verifier}
    async with _http(app) as c:
        return client_id, form, await c.post("/token", data=form)


async def test_reusing_an_authorization_code_is_invalid_grant(app):
    _, form, first = await _hand_flow(app)
    async with _http(app) as c:
        again = await c.post("/token", data=form)
    assert first.status_code == 200
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


async def test_a_wrong_pkce_verifier_is_invalid_grant(app):
    _, _, r = await _hand_flow(app, verifier_override="x" * 50)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


async def test_a_read_only_consent_grants_only_crm_read_and_the_token_sees_no_writes(app):
    _, _, r = await _hand_flow(app, read_only=True)
    tokens = r.json()
    assert tokens["scope"].split() == ["crm:read"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with _http(app, headers=headers) as http:
        async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as client:
            tools = (await client.list_tools()).tools
    assert tools and all(t.annotations.read_only_hint for t in tools)


async def test_a_client_asking_only_to_read_gets_a_read_only_connection_whatever_the_body_says(app):
    client_id = await _registered(app)
    verifier, challenge = _pkce()
    _, info, answer = await _consent(app, _authorize_url(client_id, challenge, scope="crm:read"),
                                     read_only=False)
    assert info.json()["write_requested"] is False
    q = parse_qs(urlsplit(answer.json()["redirect"]).query)
    async with _http(app) as c:
        r = await c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                         "redirect_uri": REDIRECT, "client_id": client_id,
                                         "code_verifier": verifier})
    assert r.json()["scope"].split() == ["crm:read"]


async def test_deny_redirects_with_access_denied_state_and_iss(app):
    client_id = await _registered(app)
    _, _, answer = await _consent(app, _authorize_url(client_id, _pkce()[1]), approve=False)
    q = parse_qs(urlsplit(answer.json()["redirect"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["st"] and q["iss"] == [BASE]
    assert "code" not in q


async def test_an_answered_consent_request_is_404(app):
    client_id = await _registered(app)
    rid, _, _ = await _consent(app, _authorize_url(client_id, _pkce()[1]))
    headers = {"Authorization": f"Bearer {_fresh_jwt()}", "Sec-Fetch-Site": "same-origin"}
    async with _http(app) as c:
        assert (await c.get(f"/api/mcp/consent/{rid}", headers=headers)).status_code == 404
        r = await c.post(f"/api/mcp/consent/{rid}", headers=headers, json={"approve": True})
    assert r.status_code == 404


async def test_consent_from_another_site_is_403(app):
    client_id = await _registered(app)
    _, _, answer = await _consent(app, _authorize_url(client_id, _pkce()[1]), same_origin=False)
    assert answer.status_code == 403


async def test_consent_needs_a_fresh_sign_in(app):
    client_id = await _registered(app)
    # A session token minted an hour ago: its expiry is an hour short of the full lifetime.
    stale = create_user_token(fake_member(), expire_minutes=settings.jwt.expire_minutes - 60)
    _, info, answer = await _consent(app, _authorize_url(client_id, _pkce()[1]), jwt=stale)
    assert info.status_code == 401 and answer.status_code == 401


async def test_a_member_cannot_consent_while_the_switch_is_off_but_an_admin_can(app, fake, seat):
    fake.members = False
    client_id = await _registered(app)
    _, info, answer = await _consent(app, _authorize_url(client_id, _pkce()[1]))
    assert info.status_code == 403 and answer.status_code == 403
    seat["user"] = fake_admin()
    _, info, answer = await _consent(app, _authorize_url(client_id, _pkce()[1]),
                                     jwt=_fresh_jwt(fake_admin()))
    assert answer.status_code == 200


async def test_a_loopback_redirect_may_change_its_port(app):
    async with _http(app) as c:
        r = await c.post("/register", json={"client_name": "CLI",
                                            "redirect_uris": ["http://localhost/callback"],
                                            "token_endpoint_auth_method": "none"})
        url = _authorize_url(r.json()["client_id"], _pkce()[1],
                             redirect="http://localhost:54321/callback")
        ok = await c.get(url.replace(BASE, ""), follow_redirects=False)
        bad = await c.get(_authorize_url(r.json()["client_id"], _pkce()[1],
                                         redirect="http://localhost:54321/elsewhere").replace(BASE, ""),
                          follow_redirects=False)
    assert urlsplit(ok.headers["location"]).path == oauth.CONSENT_PATH
    assert bad.status_code == 400


async def test_a_non_loopback_redirect_must_match_exactly(app):
    client_id = await _registered(app)
    async with _http(app) as c:
        r = await c.get(_authorize_url(client_id, _pkce()[1],
                                       redirect="https://client.example/other").replace(BASE, ""),
                        follow_redirects=False)
    assert r.status_code == 400


async def test_a_token_for_another_resource_is_refused(app):
    client_id = await _registered(app)
    async with _http(app) as c:
        r = await c.get(_authorize_url(client_id, _pkce()[1],
                                       resource="https://other.example/mcp").replace(BASE, ""),
                        follow_redirects=False)
    assert "error=invalid_target" in r.headers["location"]


async def test_there_is_no_revocation_endpoint(app):
    """The SDK's /revoke demands a client_secret no public client has; revoking is done
    in CakeCRM Settings, so the endpoint is neither mounted nor advertised."""
    async with _http(app) as c:
        md = (await c.get("/.well-known/oauth-authorization-server")).json()
        r = await c.post("/revoke", data={"token": "x", "client_id": "y"})
    assert "revocation_endpoint" not in md
    assert r.status_code in (404, 405)


def test_oauth_routes_are_absent_without_a_public_address():
    assert {r.path for r in oauth.build_routes(None)} == {"/mcp"}


@pytest.mark.parametrize("url", [
    "http://client.example/c.json",       # not https
    "https://client.example/",            # no path
    "https://user:pw@client.example/c.json",
])
def test_cimd_urls_that_are_not_https_with_a_path_are_refused(url):
    with pytest.raises(ValueError):
        oauth._fetch_json(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.5"])
def test_cimd_on_a_private_address_is_refused_before_any_connection(monkeypatch, address):
    import socket
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ValueError):
        oauth._fetch_json("https://client.example/c.json")


def test_cimd_dns_failure_is_transient(monkeypatch):
    import socket

    def boom(*a, **k):
        raise OSError("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(oauth._Transient):
        oauth._fetch_json("https://client.example/c.json")


@pytest.mark.parametrize("doc", [
    {**CIMD_DOC, "client_id": "https://evil.example/c.json"},
    {**CIMD_DOC, "token_endpoint_auth_method": "client_secret_post"},
    {k: v for k, v in CIMD_DOC.items() if k != "redirect_uris"},
])
def test_an_invalid_cimd_document_is_refused(doc):
    with pytest.raises(ValueError):
        oauth._validate_cimd(CIMD_URL, doc)


def test_cimd_is_cached_and_only_a_transient_failure_keeps_the_stale_copy(fake, monkeypatch):
    calls = []
    monkeypatch.setattr(oauth, "_fetch_json", lambda url: calls.append(url) or dict(CIMD_DOC))
    assert oauth.cimd_client(CIMD_URL)["client_name"] == "Test CIMD Client"
    assert oauth.cimd_client(CIMD_URL) and len(calls) == 1  # cached
    later = _now() + timedelta(days=2)

    def transient(url):
        raise oauth._Transient("down")

    monkeypatch.setattr(oauth, "_fetch_json", transient)
    assert oauth.cimd_client(CIMD_URL, now=later)["client_name"] == "Test CIMD Client"
    monkeypatch.setattr(oauth, "_fetch_json", lambda url: {**CIMD_DOC, "client_id": "https://x.example/y"})
    assert oauth.cimd_client(CIMD_URL, now=later) is None
    assert CIMD_URL not in fake.clients


async def _register(app, uri):
    async with _http(app) as c:
        return await c.post("/register", json={"client_name": "X", "redirect_uris": [uri],
                                               "token_endpoint_auth_method": "none"})


@pytest.mark.parametrize("uri", ["javascript:alert(1)//", "http://evil.example/cb"])
async def test_registering_an_unsafe_redirect_is_refused(app, uri):
    r = await _register(app, uri)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


async def test_registering_a_desktop_app_scheme_is_allowed(app):
    r = await _register(app, "cursor://anysphere.cursor-retrieval/oauth/callback")
    assert r.status_code == 201


def test_a_cimd_document_with_a_data_redirect_is_refused():
    with pytest.raises(ValueError):
        oauth._validate_cimd(CIMD_URL, {**CIMD_DOC, "redirect_uris": ["data:text/html,x"]})


# ── _fetch_json after the address check ──────────────────────────────────────

@pytest.fixture
def public_fetch(monkeypatch):
    """Resolve to a public IP and route httpx.Client through a MockTransport."""
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    seen = []

    def install(handler):
        real = httpx.Client

        def client(*a, **k):
            return real(*a, transport=httpx.MockTransport(
                lambda req: seen.append(req) or handler(req)), **k)

        monkeypatch.setattr(httpx, "Client", client)
        return seen

    return install


def test_fetch_json_does_not_follow_redirects(public_fetch):
    public_fetch(lambda req: httpx.Response(302, headers={"location": "https://x.example/"}))
    with pytest.raises(ValueError):
        oauth._fetch_json(CIMD_URL)


def test_fetch_json_5xx_is_transient(public_fetch):
    public_fetch(lambda req: httpx.Response(503))
    with pytest.raises(oauth._Transient):
        oauth._fetch_json(CIMD_URL)


@pytest.mark.parametrize("body", [b"x" * (oauth.CIMD_MAX_BYTES + 1), b"not json", b"[1, 2]"])
def test_fetch_json_refuses_bad_bodies(public_fetch, body):
    public_fetch(lambda req: httpx.Response(200, content=body))
    with pytest.raises(ValueError):
        oauth._fetch_json(CIMD_URL)


def test_fetch_json_connects_to_the_checked_ip_with_the_host_header(public_fetch):
    seen = public_fetch(lambda req: httpx.Response(200, json={"a": 1}))
    assert oauth._fetch_json(CIMD_URL) == {"a": 1}
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == urlsplit(CIMD_URL).hostname
