"""The `/mcp` endpoint (#322): stateless JSON over Streamable HTTP, one seat per request.

Every request: refuse a browser `Origin` (MCP clients are not web pages, and nothing a
page could do here is legitimate); read `Authorization: Bearer …`; resolve it with the
one lookup in `store.resolve_bearer`, which re-checks the seat, its epoch, expiry, idle
time and the members switch on every call; charge the per-connection budget; then hand
the request to the official SDK's transport with the connection on `scope["state"]`.
The handlers build the seat's own `ToolRegistry`, so the tools act exactly as that
person, and stamp writes "via <connection name>" through the provenance context var.

A request with no or a dead token gets 401. The challenge names the protected-resource
metadata only when OAuth is configured (a public https address), which is what makes
ChatGPT and Claude start their sign-in; a token-only install answers a bare challenge.

Every tool call writes one audit row: connection, seat, tool, the record it named or a
row count, ok/refused, duration. Never arguments, never results. If the audit insert
itself fails it is logged at ERROR and the call still answers: a write that committed
cannot be taken back, and hiding its result would only make the tool retry it.
"""

import asyncio
import logging
import time

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse

from assistant.delimiters import fence_tool_result
from assistant.registry import ToolRegistry
from core.ratelimit import IPRateLimiter
from crm import provenance_service
from mcp_endpoint import store, surface

logger = logging.getLogger(__name__)

# Per connection: 600 calls a minute (the todo-gtd budget). Per IP: 30 wrong tokens in
# five minutes, charged only by misses, so a valid token is never slowed by them.
call_limiter = IPRateLimiter(window=60, max_hits=600)
bad_token_limiter = IPRateLimiter(window=300, max_hits=30)

_NO_STORE = {"Cache-Control": "no-store"}
_DETAIL_ENTITIES = frozenset({"contact", "company", "deal", "todo", "project", "reminder"})


def _oauth_base() -> str | None:
    from mcp_endpoint.oauth import oauth_base_url
    return oauth_base_url()


def challenge(*, presented: bool) -> JSONResponse:
    """The 401 every refusal shares: missing, unknown, expired, revoked, dead seat."""
    header = 'Bearer error="invalid_token"' if presented else "Bearer"
    base = _oauth_base()
    if base:
        header += f', resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"'
    return JSONResponse(
        {"error": "invalid_token"}, status_code=401,
        headers={**_NO_STORE, "WWW-Authenticate": header},
    )


def _detail(result) -> str:
    """What the audit row names: one record (`contact:12`), a row count, or nothing.
    Only a known entity type with an integer id is copied out of a result."""
    if not isinstance(result, dict):
        return ""
    target = result.get("target")
    if isinstance(target, dict):
        kind, rid = target.get("entity_type"), target.get("id")
        if kind in _DETAIL_ENTITIES and type(rid) is int:
            return f"{kind}:{rid}"
    for value in result.values():
        if isinstance(value, list):
            return f"{len(value)} rows"
    return ""


def _connection(ctx) -> dict:
    return ctx.request.scope["state"]["mcp"]


async def _registry(conn: dict) -> ToolRegistry:
    # asyncio.to_thread copies the context, so the build-time source bindings (a todo's
    # `source`) see the WRITE_SOURCE the caller set.
    return await asyncio.to_thread(ToolRegistry, user=conn["seat"])


async def list_tools(ctx, params) -> types.ListToolsResult:
    conn = _connection(ctx)
    token = provenance_service.WRITE_SOURCE.set(("mcp", conn["name"]))
    try:
        registry = await _registry(conn)
    finally:
        provenance_service.WRITE_SOURCE.reset(token)
    return types.ListToolsResult(tools=surface.advertised(registry, read_only=conn["read_only"]))


async def call_tool(ctx, params) -> types.CallToolResult:
    conn = _connection(ctx)
    name, args = params.name, params.arguments or {}
    started = time.monotonic()
    refused = None
    token = provenance_service.WRITE_SOURCE.set(("mcp", conn["name"]))
    try:
        registry = await _registry(conn)
        if name not in surface.offered_names(registry, read_only=conn["read_only"]):
            if conn["read_only"] and name in surface.offered_names(registry, read_only=False):
                refused, result = "read_only", {"error": "This connection is read-only."}
            else:
                refused, result = "not_offered", {"error": f"Unknown tool: {name}"}
        else:
            result = await registry.execute_tool(name, args)
    finally:
        provenance_service.WRITE_SOURCE.reset(token)
    # Public-capture rows and notes files stay fenced for the outside model. The taint
    # flag has nothing to escalate into here: the connected tool's own approval prompt
    # is the gate (destructiveHint), and INSTRUCTIONS explain the fences.
    content, _tainted = fence_tool_result(name, result)
    ok = refused is None and not (isinstance(result, dict) and "error" in result)
    try:
        await asyncio.to_thread(
            store.record_call, conn["id"], conn["user_id"], name, _detail(result), ok,
            refused, int((time.monotonic() - started) * 1000),
        )
    except Exception:
        logger.error("mcp: could not write the audit row for %s on connection %s",
                     name, conn["id"], exc_info=True)
    return types.CallToolResult(content=[types.TextContent(type="text", text=content)],
                                isError=not ok)


server = Server(
    "cakecrm",
    title="CakeCRM",
    instructions=surface.INSTRUCTIONS,
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class McpEndpoint:
    """The ASGI app mounted at `/mcp`."""

    async def __call__(self, scope, receive, send) -> None:
        request = Request(scope)
        if request.headers.get("origin"):
            response = JSONResponse({"error": "forbidden"}, status_code=403, headers=_NO_STORE)
            return await response(scope, receive, send)
        ip = request.client.host if request.client else "unknown"
        token = _bearer(request)
        if token is None:
            return await challenge(presented=False)(scope, receive, send)
        if bad_token_limiter.exhausted(ip):
            response = JSONResponse({"error": "rate_limited"}, status_code=429, headers=_NO_STORE)
            return await response(scope, receive, send)
        conn = await asyncio.to_thread(store.resolve_bearer, token)
        if conn is None:
            bad_token_limiter.allow(ip)
            return await challenge(presented=True)(scope, receive, send)
        if not call_limiter.allow(f"c{conn['id']}"):
            response = JSONResponse({"error": "rate_limited"}, status_code=429, headers=_NO_STORE)
            return await response(scope, receive, send)
        try:
            await asyncio.to_thread(store.touch, conn["id"], conn["last_used_at"])
        except Exception:
            logger.warning("mcp: could not touch connection %s", conn["id"], exc_info=True)
        scope.setdefault("state", {})["mcp"] = conn
        # A manager per request keeps the transport off the app lifespan (stateless: no
        # session survives the request anyway).
        manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)
        async with manager.run():
            await manager.handle_request(scope, receive, send)
