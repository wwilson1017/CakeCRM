# Connect your own AI (MCP endpoint)

> Topic doc for `/mcp`, its OAuth 2.1 authorization server and personal tokens (#322,
> designed in #268). Implementation notes and "landed #N" records for this area go here.

## Stage-0 spike: the official `mcp` SDK v2 auth handlers (2026-10-17)

**Answer: yes, use them.** `mcp==2.3.0` (the official SDK, not FastMCP) mounts cleanly under
our FastAPI app at the origin root, beside the `@app.middleware("http")` stack and in front of
the SPA catch-all, with our own `OAuthAuthorizationServerProvider`. The spike drove a
FastAPI app shaped like `main.py` with `mcp.Client` over streamable HTTP (in-process through
`httpx2.ASGITransport`) and passed every leg:

- no token on `/mcp` gives our 401 with `WWW-Authenticate: Bearer ... resource_metadata=...`;
- a personal token reaches tool handlers with the seat attached, in both the legacy
  (`initialize` handshake) and modern (2026-07-28, `server/discover`) protocol modes;
- the SDK client's full OAuth flow (PRM, AS metadata, `/register` or CIMD, `/authorize`,
  our consent step, `/token` with PKCE S256, `iss` checked) completes for **both**
  registration methods, then refresh rotates and reusing the old refresh token is
  `invalid_grant`;
- the SPA catch-all still serves every other path.

What the SDK gives us: `create_auth_routes()` returns Starlette `Route`s for
`/.well-known/oauth-authorization-server`, `/authorize`, `/token`, `/register`, `/revoke`
(request parsing, PKCE verification, redirect-URI and scope checks, RFC error shapes, a
4 MiB body cap, CORS on the machine endpoints) and `create_protected_resource_routes()`
gives `/.well-known/oauth-protected-resource/mcp`. They are appended to `app.router.routes`
before `_mount_spa`. The transport is a low-level `mcp.server.lowlevel.Server` with
`on_list_tools` / `on_call_tool`, served by a `StreamableHTTPSessionManager(stateless=True,
json_response=True)`.

What stays ours, and the gotchas the spike hit (each one is a rule for the build):

1. **Issuer string.** Build issuer and resource URLs through `AuthSettings(...)` (it sets
   `url_preserve_empty_path`). A bare `AnyHttpUrl("https://host")` renders as
   `https://host/`, the client compares `iss` as an exact string, and sign-in fails with
   "iss mismatch".
2. **Metadata route is replaced.** `build_metadata()` hard-codes
   `token_endpoint_auth_methods_supported: [client_secret_post, client_secret_basic]`.
   Claude only picks CIMD when `none` is listed and `client_id_metadata_document_supported`
   is true, so we swap route 0 for a `MetadataHandler` over
   `metadata.model_copy(update=...)` with `none`, the CIMD flag and
   `authorization_response_iss_parameter_supported`.
3. **Public clients only.** The DCR handler defaults a missing auth method to
   `client_secret_post` and mints a secret. Our `register_client` rewrites the record to
   `token_endpoint_auth_method="none"` and drops the secret before the handler echoes it,
   so nothing secret is ever stored for a client.
4. **CIMD is ours.** The SDK fetches CIMD only on its client side. Our `get_client` treats an
   `https://` client id as a metadata-document URL: fetch with SSRF guard, size cap and
   timeout; require `client_id` to equal the URL; cache it (one sign-in called `get_client`
   four times). The document usually has no `scope`, so the loader sets our scopes or the
   SDK's scope check refuses the authorize request.
5. **Loopback redirects.** `validate_redirect_uri` is exact-match. Claude Code registers
   `http://localhost/callback` and calls back on a random port, so the client model we
   return overrides it to ignore the port for loopback hosts (RFC 8252).
6. **Single-use codes are the provider's job.** The token handler loads the code, checks
   PKCE, then calls `exchange_authorization_code`; that call must consume the code
   atomically (`DELETE ... RETURNING`) or two racing exchanges both succeed. Same for
   refresh-token rotation.
7. **The consent step is ours.** `authorize()` parks the validated request and returns the
   SPA consent URL; the SPA signs in, the consent API mints the code and returns the final
   redirect with `code`, `state` and `iss`.
8. **Identity hand-off.** Our `/mcp` ASGI endpoint authenticates, puts the resolved
   connection on `scope["state"]`, and handlers read it from `ctx.request.scope`. A
   per-request session manager (`async with mgr.run()`) keeps the transport off the app
   lifespan, so tests need no lifespan.
9. **Async seam.** Provider methods and handlers are async; our psycopg2 and bcrypt work
   runs through `asyncio.to_thread` inside them.
10. **No `/revoke`** (found while building). The SDK's `RevocationRequest` declares
    `client_secret: str | None` with no default, so a public client that sends none gets a
    400. Every client we register is public, so the endpoint is off
    (`RevocationOptions(enabled=False)`) and not advertised; people revoke in Settings.

The bearer middleware (`RequireAuthMiddleware`) is not used: one lookup serves both personal
tokens and OAuth access tokens, and it must re-check seat, epoch, switch, expiry and the
read-only flag on every request.

## Landed #322

Package `backend/mcp_endpoint/` (not `mcp/`: `backend/` is on `sys.path` and would shadow the
SDK). `store.py` is the only SQL, `surface.py` the tool list, `server.py` the `/mcp` ASGI
endpoint and its two handlers, `oauth.py` the provider and route builder, `router.py` the
signed-in `/api/mcp/*` routes. `main.py` appends `oauth.build_routes(oauth_base_url())` to
`app.router.routes` before the SPA catch-all.

**Data model** (one migration): `mcp_connections` is what a person sees and revokes (seat,
kind `token`/`oauth`, name, read-only, the seat's `token_epoch` at creation, timestamps);
`mcp_credentials` holds every bearer as a SHA-256 hash (`personal`, `access` one hour,
`refresh` rotating); `mcp_oauth_clients` (DCR rows and cached CIMD documents, never a
secret); `mcp_oauth_requests` and `mcp_oauth_codes` (single use, ten and five minutes, the
code carries the seat's epoch); `mcp_audit` (one row per call, never arguments or results).
`crm_meta.mcp_members_enabled` is the admin switch. Credential tables are in
`export.NOT_EXPORTED` and outside `_truncate_all`; `mcp_audit` is in `NOT_EXPORTED` and in
both `_truncate_all` branches. The maintenance tick runs `store.sweep()`.

**The one lookup.** `store.resolve_bearer` joins credential, connection, seat and `crm_meta`
in one query; `store._judge` is the single refusal predicate (inactive, epoch moved, token or
connection expired, OAuth idle 90 days, member while the switch is off). Every refusal is
the same 401. The hermetic fakes call `_judge` too, so they exercise the real rules. Refresh
rotation re-judges inside its own transaction and rolls back (the refresh token survives)
when the connection is dead; codes and consent requests are consumed with
`DELETE ... RETURNING`; per-seat creates take `pg_advisory_xact_lock(3220, user_id)` and
count only live connections against the cap of 25.

**Surface.** `ToolRegistry(user=<seat>)` per request, minus `surface.is_excluded`: kinds
`gmail` and `notification`, writes of kinds `memory` and `context`, and `crm_delete_contact`.
Hints: reads `readOnlyHint`; routine writes not in `confirm_tier.HIDE_CAPABLE_TOOLS`
`destructiveHint: false`; every other write `destructiveHint: true`; `openWorldHint: false`
everywhere. A read-only connection is offered reads only, and `tools/call` refuses anything
`tools/list` would not show. Results go through `delimiters.fence_tool_result`; the server
`instructions` explain the fences. `tests/golden/mcp_surface.json` pins names, titles and
hints in both todo modes (66 in GTD, 59 in normal); regenerate with
`MCP_SURFACE_REGENERATE=1 python -m pytest -q tests/test_mcp_surface.py` and review the diff.

**Provenance.** `crm.provenance_service.WRITE_SOURCE` is a context var the handlers set to
`("mcp", <connection name>)` around registry build and execution: field provenance gets
`source='mcp'` with the name in `source_detail`, custom-field values `via <name>`, todos
`source='mcp'` (bound at registry build, which is why the var is set before it).

**OAuth** mounts only when `_configured_base_url()` is https, or http on localhost. Consent is
the SPA route `/oauth/consent?request=<id>`: it signs in with raw `fetch` (never the shared
session or its BroadcastChannel), and `router.answer_consent` requires a JWT minted in the
last ten minutes (derived from `exp - JWT_EXPIRE_MINUTES`, so `core/auth` is untouched), the
same-origin check, and the members switch. Read-only is derived from the granted scopes:
`crm:write` only when the client asked for it and the person chose Read and write. CIMD
fetches resolve the host, refuse unless every address is global, then connect to that
address with `sni_hostname` set so TLS still checks the real name; streamed under 64 KiB;
only transient failures fall back to a stale cached document.

**Tests.** Hermetic (CI): `test_mcp_surface.py`, `test_mcp_protocol.py` (the SDK's
`mcp.Client` over `httpx2.ASGITransport`, legacy and modern protocol), `test_mcp_oauth.py`
(the SDK's `OAuthClientProvider` through the full code flow for DCR and CIMD),
`test_mcp_router.py`, plus extensions to `test_gmail_guard.py`, `test_route_authz.py`
(Starlette routes have no `.dependant`, so `test_non_fastapi_routes_are_exactly_pinned`
pins them), `test_request_size_limit.py` and `test_prompt_genericization.py`. Real Postgres
(`-m integration`, local only): `test_mcp_integration.py`, including the cap, code and
refresh races.

**Known limits.** Browser-hosted MCP clients (an Inspector page) are refused by the
`Origin` check and would also hit the app-level CORS preflight; not a v1 target. Rate-limit
buckets are in process, which the single worker allows.
