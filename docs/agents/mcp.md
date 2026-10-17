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

The bearer middleware (`RequireAuthMiddleware`) is not used: one lookup serves both personal
tokens and OAuth access tokens, and it must re-check seat, epoch, switch, expiry and the
read-only flag on every request.
