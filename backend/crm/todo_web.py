"""Todo-GTD — the no-login todo web app (#70).

  GET /todo[/{token}][/...]      — serves the SPA shell in todo-only mode
  /api/todo-web[/{token}]/...    — the GTD CRUD endpoints, token-gated

Same trust model as `/capture` (crm/todo_capture.py): no JWT by design, an optional
secret path token (`crm_meta.todo_web_token`) instead. The whole surface is OFF until
`todo_web_enabled` is turned on in Settings.

Unlike capture — which is write-only into the inbox — this exposes the todo store for
reading AND editing, which is why it defaults to off and why the Settings UI defaults
it to the tokened URL and says plainly what tokenless mode means.

It mounts ONLY `gtd_router.build_router` — the GTD todos/projects/filters CRUD. No
CRM router, no assistant, no settings are reachable through this token.

Ported from chatty's `core/todo/web.py`.
"""

import hmac
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from core.ratelimit import IPRateLimiter
from crm import service
from crm.gtd_router import build_router
from crm.todo_pwa import manifest_response

logger = logging.getLogger(__name__)
router = APIRouter()

# Generous: one todo page view is several API calls, and this is the owner's own
# phone hitting it. It exists only to cap outright flooding.
web_limiter = IPRateLimiter(window=300, max_hits=600)
# Strict, and burned only by WRONG tokens, so guessing the secret costs 30 tries per
# 5 minutes per IP while normal use never touches this budget.
guess_limiter = IPRateLimiter(window=300, max_hits=30)

# backend/crm/todo_web.py -> backend -> repo root -> frontend/dist (the same build
# main.py serves).
_FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"

_UNBUILT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Todos</title></head><body style="font-family:system-ui;padding:32px">
<p>Frontend build not found. Run <code>python run.py</code> (or <code>npm run build</code>
in <code>frontend/</code>) and reload.</p></body></html>"""

# Every page response, including the unbuilt-frontend 503 — which fires AFTER a
# successful token match, so it must not become a cacheable/indexable oracle.
_PAGE_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}

_index_cache: tuple[float, str] | None = None


def _settings() -> dict:
    return service.get_todo_public_settings()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_or_429(request: Request) -> None:
    if not web_limiter.allow(_client_ip(request)):
        raise HTTPException(
            status_code=429, detail="Too many requests — try again in a few minutes"
        )


def _not_found() -> HTTPException:
    # Always 404, never 403: an unauthorized caller learns nothing about whether the
    # todo web app exists or what the token looks like.
    return HTTPException(status_code=404, detail="Not found")


def _match_token(token: str, configured: str, request: Request) -> str:
    """Return the configured token the caller matched.

    Callers must build responses from the RETURNED value, never re-read the setting:
    a regenerate landing between the two reads would serve the NEW secret to a caller
    who authenticated with the old one.
    """
    # Compare as BYTES: compare_digest raises TypeError on non-ASCII str, which would
    # turn a scanner's /todo/ü guess into a 500.
    if configured and hmac.compare_digest(token.encode(), configured.encode()):
        return configured
    if not guess_limiter.allow(_client_ip(request)):
        raise HTTPException(
            status_code=429, detail="Too many requests — try again in a few minutes"
        )
    raise _not_found()


def _index_html() -> str:
    """Read the built SPA shell, cached on mtime."""
    global _index_cache
    try:
        mtime = _FRONTEND_INDEX.stat().st_mtime
    except OSError:
        return ""
    if _index_cache and _index_cache[0] == mtime:
        return _index_cache[1]
    html = _FRONTEND_INDEX.read_text(encoding="utf-8")
    _index_cache = (mtime, html)
    return html


def _page(base_path: str) -> HTMLResponse:
    """Serve the SPA with the todo-only mode and its router basename injected."""
    html = _index_html()
    if not html:
        return HTMLResponse(_UNBUILT_HTML, status_code=503, headers=_PAGE_HEADERS)
    # base_path is either "/todo" or "/todo/<token>", both already restricted to
    # URL-safe characters by clamp_token, so it is safe inside a JSON string literal.
    inject = (
        f'<script>window.__CAKECRM_TODO_BASE__ = "{base_path}";</script>\n'
        # Installability: the manifest makes Add to Home Screen produce a real
        # standalone app (own icon, no browser chrome, opens at base_path).
        f'  <link rel="manifest" href="{base_path}/manifest.webmanifest">\n'
        # Pre-16.4 iOS ignores the manifest; these metas are its equivalent.
        '  <meta name="mobile-web-app-capable" content="yes">\n'
        '  <meta name="apple-mobile-web-app-capable" content="yes">\n'
        '  <meta name="apple-mobile-web-app-title" content="Todos">'
    )
    if "</head>" in html:
        html = html.replace("</head>", f"  {inject}\n  </head>", 1)
    else:
        html = inject + html
    return HTMLResponse(html, headers=_PAGE_HEADERS)


def _manifest(base_path: str) -> Response:
    return manifest_response(
        name="Todos", description="Your todo list", base_path=base_path
    )


# ── Manifest ──────────────────────────────────────────────────────────────────
# Registered before the page catch-all so /todo/{...}/manifest.webmanifest matches
# here first. No collision with tokens: the clamp strips dots, so a token can never
# literally be "manifest.webmanifest".

@router.get("/todo/manifest.webmanifest")
async def todo_manifest(request: Request):
    s = _settings()
    if not s["todo_web_enabled"] or s["todo_web_token"]:
        raise _not_found()
    _rate_or_429(request)
    return _manifest("/todo")


@router.get("/todo/{token}/manifest.webmanifest")
async def todo_manifest_token(token: str, request: Request):
    s = _settings()
    if not s["todo_web_enabled"]:
        raise _not_found()
    _rate_or_429(request)
    configured = _match_token(token, s["todo_web_token"], request)
    return _manifest(f"/todo/{configured}")


# ── Page ──────────────────────────────────────────────────────────────────────

@router.get("/todo", response_class=HTMLResponse)
@router.get("/todo/{rest:path}", response_class=HTMLResponse)
async def todo_web_page(request: Request, rest: str = ""):
    """One handler for every in-app path so deep links and reloads work.

    In token mode the first path segment is the secret; everything after it is a
    client-side route the SPA resolves itself.
    """
    s = _settings()
    if not s["todo_web_enabled"]:
        raise _not_found()
    _rate_or_429(request)
    if not s["todo_web_token"]:
        return _page("/todo")
    first = rest.split("/", 1)[0]
    configured = _match_token(first, s["todo_web_token"], request)
    return _page(f"/todo/{configured}")


# ── API ───────────────────────────────────────────────────────────────────────

def _no_store(response: Response) -> None:
    # These endpoints authenticate via the URL path, not an Authorization header, so
    # RFC 7234's authenticated-response cache exemption does not apply — and
    # regenerating the token must kill any cached copies.
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"


async def _public_api_guard(request: Request, response: Response):
    s = _settings()
    if not s["todo_web_enabled"] or s["todo_web_token"]:
        raise _not_found()
    _rate_or_429(request)
    _no_store(response)


async def _token_api_guard(token: str, request: Request, response: Response):
    s = _settings()
    if not s["todo_web_enabled"]:
        raise _not_found()
    _rate_or_429(request)
    _match_token(token, s["todo_web_token"], request)
    _no_store(response)


public_api_router = build_router(_public_api_guard)
token_api_router = build_router(_token_api_guard)
