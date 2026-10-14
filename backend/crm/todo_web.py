"""Todo-GTD — the no-login todo web app (#70; browser sessions since #293).

  GET  /todo[/...]                    — the SPA shell for a signed-in browser, the
                                        signed-out page otherwise, the "no longer used"
                                        page for an old tokened address
  GET  /todo/signin/{token}           — the sign-in page; loading it changes NOTHING
  GET  /todo/manifest.webmanifest     — one manifest for everyone
  /api/todo-web/...                   — the GTD CRUD (`gtd_router.build_router`), behind
                                        a live session
  GET  /api/todo-web/session          — the app's bootstrap: 200 or 401
  POST /api/todo-web/signin/{token}   — redeem a sign-in code: sets the cookie
  POST /api/todo-web/signout          — sign THIS browser out

The credential is an HttpOnly cookie naming a row in `todo_web_sessions`
(crm/todo_sessions.py). A browser gets one only by redeeming a one-hour, single-use
code that a signed-in seat makes in Settings (`/api/crm/gtd/web-signin-link`, on the
authenticated router — never here). The whole surface is OFF (every route 404) until
`todo_web_enabled` is turned on in Settings.

It mounts ONLY `gtd_router.build_router` — the GTD todos/projects/filters CRUD. No CRM
router, no assistant, no settings are reachable through the cookie, and nothing outside
this module reads it.

The rules that made the old link safe still hold: the generous per-IP limiter on every
request, the strict guess limiter (charged only by a sign-in code that matched nothing),
404-never-401 on anything token-shaped, `no-store` + `noindex`. A cookie adds two:
every non-GET and the redemption must come from this site's own pages (`_same_origin`),
and the signed-out answer on the API is a 401 with NO `WWW-Authenticate` — never a
redirect to the CRM login.

Ported from chatty's `core/todo/web.py`; sessions from todo-gtd `b55cacb` / cake_os #3764.
"""

import logging
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from core.config import settings
from core.ratelimit import IPRateLimiter
from crm import service, todo_sessions
from crm.gtd_router import build_router
from crm.todo_pwa import manifest_response

logger = logging.getLogger(__name__)
router = APIRouter()

# Generous: one todo page view is several API calls, and this is the owner's own
# phone hitting it. It exists only to cap outright flooding.
web_limiter = IPRateLimiter(window=300, max_hits=600)
# Strict, and burned only by sign-in codes that match nothing, so guessing one costs
# 30 tries per 5 minutes per IP while normal use never touches this budget.
guess_limiter = IPRateLimiter(window=300, max_hits=30)

# backend/crm/todo_web.py -> backend -> repo root -> frontend/dist (the same build
# main.py serves).
_FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"

_UNBUILT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Todos</title></head><body style="font-family:system-ui;padding:32px">
<p>Frontend build not found. Run <code>python run.py</code> (or <code>npm run build</code>
in <code>frontend/</code>) and reload.</p></body></html>"""

# Every response on the surface, refusals included: an HTTPException does NOT inherit
# headers a handler stamps on its success response, so each raiser below carries these.
_PAGE_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}

# The SPA's own first path segments under /todo. Anything else there is an old tokened
# address. Pinned against frontend/src/crm/gtd/PublicTodoApp.tsx by
# tests/test_todo_public.py::test_page_slugs_match_the_spa_routes.
_PAGE_SLUGS = ("inbox", "next", "projects", "waiting", "someday", "done", "review", "search")
# Reserved, not a page: /todo/signin/{token} is the sign-in page.
_SIGNIN_SEGMENT = "signin"

_HOW_TO_SIGN_IN = (
    "On a computer, open CakeCRM &rarr; <strong>Settings</strong> &rarr; "
    "<strong>Personal</strong> &rarr; <strong>Todo app devices</strong> &rarr; "
    "<strong>Show a sign-in code</strong>. Scan "
    "the code with this phone and press <strong>Open my list on this device</strong>."
)


def _status_page(body_html: str) -> str:
    """A tiny standalone page. Literal copy only: nothing from the request reaches it."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow"><title>Todos</title></head>'
        '<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:0 auto;'
        'padding:32px 20px;line-height:1.5">' + body_html + "</body></html>"
    )


# What a browser with no live session gets at /todo — a 200 that says what to do.
_SIGNED_OUT_HTML = _status_page(
    "<h1>Sign in to your Todos</h1>"
    f"<p>This browser isn&rsquo;t signed in. {_HOW_TO_SIGN_IN}</p>"
    "<p>Once you are signed in, add this page to your home screen.</p>"
)

# What every old /todo/<token> address gets. Byte-identical whatever the segment was:
# it looks nothing up, so it cannot tell a once-valid token from a made-up one.
_RETIRED_LINK_HTML = _status_page(
    "<h1>This address is no longer used</h1>"
    '<p>Your Todos now open at one address for everyone: <a href="/todo">/todo</a>. '
    "Each device signs in once.</p>"
    f"<p>To sign this device in: {_HOW_TO_SIGN_IN}</p>"
    "<p>If you added this to your home screen, remove it and add "
    '<a href="/todo">/todo</a> again once you are signed in &mdash; an installed app '
    "keeps the old address.</p>"
)

_index_cache: tuple[float, str] | None = None


def _settings() -> dict:
    return service.get_todo_public_settings()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Too many requests — try again in a few minutes",
        headers=_PAGE_HEADERS,
    )


def _rate_or_429(request: Request) -> None:
    if not web_limiter.allow(_client_ip(request)):
        raise _too_many()


def _not_found() -> HTTPException:
    # One 404 for a switched-off app, a wrong, spent or expired sign-in code and the
    # retired tokened API: the caller learns nothing about which it was.
    return HTTPException(status_code=404, detail="Not found", headers=_PAGE_HEADERS)


def _signed_out() -> HTTPException:
    # No WWW-Authenticate on purpose: this is not a Bearer surface, and the header would
    # invite a browser credential prompt.
    return HTTPException(status_code=401, detail="Not signed in", headers=_PAGE_HEADERS)


def _enabled_or_404() -> None:
    if not _settings()["todo_web_enabled"]:
        raise _not_found()


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _same_origin(request: Request) -> None:
    """Refuse a request that did not come from this site's own pages (403).

    `Sec-Fetch-Site` is set by the browser and cannot be forged by page script, so when
    present it decides, and only `same-origin` passes (`same-site` is a sibling
    subdomain or another local port; `none` is a typed address, which never POSTs).
    Browsers that do not send it fall back to `Origin`, compared as a whole origin
    (scheme, host and port) with this install's configured address, or — on an install
    nobody configured, such as a LAN box — with the address the request was made to.
    Neither header: refused, so a script or curl cannot write either.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        allowed = site == "same-origin"
    else:
        origin = request.headers.get("origin") or ""
        if settings.frontend_url_is_default:
            own = f"{request.url.scheme}://{request.headers.get('host', '')}"
        else:
            own = _origin_of(settings.frontend_url)
        allowed = bool(origin) and origin == own
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Refused: this request did not come from this site.",
            headers=_PAGE_HEADERS,
        )


def _cookie_token(request: Request) -> str | None:
    return request.cookies.get(todo_sessions.cookie_name())


def _live_session(request: Request) -> dict | None:
    return todo_sessions.resolve_session(_cookie_token(request))


def _set_session_cookie(response: Response, token: str) -> None:
    """The ONE place the cookie is set, and only with a token the redemption just made.

    Nothing ever clears it: sign-out deletes the row, so a stale cookie is just a
    signed-out browser, while a clearing response could erase a sign-in another tab
    had just finished.
    """
    response.set_cookie(
        todo_sessions.cookie_name(),
        token,
        max_age=int(todo_sessions.SESSION_LIFETIME.total_seconds()),
        path="/",
        secure=todo_sessions.cookie_secure(),
        httponly=True,
        samesite="lax",
    )


def _no_store(response: Response) -> None:
    response.headers.update(_PAGE_HEADERS)


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


def _page() -> HTMLResponse:
    """Serve the SPA shell for the todo app. The frontend recognises the fixed /todo
    address itself (crm/gtd/publicMode.ts), so nothing per-request is injected."""
    shell = _index_html()
    if not shell:
        return HTMLResponse(_UNBUILT_HTML, status_code=503, headers=_PAGE_HEADERS)
    inject = (
        # Installability: the manifest makes Add to Home Screen produce a standalone
        # app (own icon, no browser chrome, opens at /todo).
        '<link rel="manifest" href="/todo/manifest.webmanifest">\n'
        # The sign-in page's URL carries a code; keep it out of any Referer.
        '  <meta name="referrer" content="no-referrer">\n'
        # Pre-16.4 iOS ignores the manifest; these metas are its equivalent.
        '  <meta name="mobile-web-app-capable" content="yes">\n'
        '  <meta name="apple-mobile-web-app-capable" content="yes">\n'
        '  <meta name="apple-mobile-web-app-title" content="Todos">'
    )
    # The phone bottom tab bar (#266) pads itself by `env(safe-area-inset-bottom)`, which
    # iOS reports as 0 unless the page opts into `viewport-fit=cover` — the capture page
    # already does. Scoped to THIS surface rather than written into index.html, because
    # the CRM pads no safe-area insets and would slide under a landscape notch.
    if "viewport-fit" not in shell:
        shell = shell.replace(
            '<meta name="viewport" content="', '<meta name="viewport" content="viewport-fit=cover, ', 1
        )
    if "</head>" in shell:
        shell = shell.replace("</head>", f"  {inject}\n  </head>", 1)
    else:
        shell = inject + shell
    return HTMLResponse(shell, headers=_PAGE_HEADERS)


# ── Pages ─────────────────────────────────────────────────────────────────────
#
# ⚠️ Every handler and dependency below is a plain `def`, never `async def`. They all
# read Postgres, and psycopg2 blocks: FastAPI offloads a sync handler/dependency to its
# threadpool, whereas a blocking call inside an `async def` runs on the event loop and
# never yields. On this UNAUTHENTICATED surface that turns an anonymous request burst
# into a stall of every request the app is serving, since the deploy pins a single
# worker. Same rule as crm/todo_capture.py.
#
# Registration order is load-bearing: the manifest and the sign-in page before the
# catch-all.

@router.get("/todo/manifest.webmanifest")
def todo_manifest(request: Request):
    _enabled_or_404()
    _rate_or_429(request)
    return manifest_response(name="Todos", description="Your todo list", base_path="/todo")


@router.get("/todo/signin/{token}", response_class=HTMLResponse)
def todo_signin_page(token: str, request: Request):
    """The sign-in page. Resolves and redeems NOTHING — link previews and scanners
    fetch links. Only its button's POST to /api/todo-web/signin/{token} spends the code."""
    _enabled_or_404()
    _rate_or_429(request)
    return _page()


@router.get("/todo", response_class=HTMLResponse)
@router.get("/todo/{rest:path}", response_class=HTMLResponse)
def todo_web_page(request: Request, rest: str = ""):
    """One handler for every in-app path so deep links and reloads work. Three answers,
    none a redirect: an old tokened address, the signed-out page, or the app."""
    _enabled_or_404()
    _rate_or_429(request)
    first = rest.split("/", 1)[0]
    if first and first not in _PAGE_SLUGS and first != _SIGNIN_SEGMENT:
        return HTMLResponse(_RETIRED_LINK_HTML, status_code=404, headers=_PAGE_HEADERS)
    if _live_session(request) is None:
        return HTMLResponse(_SIGNED_OUT_HTML, status_code=200, headers=_PAGE_HEADERS)
    return _page()


# ── API ───────────────────────────────────────────────────────────────────────

def _session_api_guard(request: Request, response: Response) -> None:
    """The guard on every /api/todo-web route but the redemption: the app is on, a
    write came from this site, and the browser holds a live session. A signed-out
    browser is a 401 and never charges the guess limiter."""
    _enabled_or_404()
    if request.method not in ("GET", "HEAD"):
        _same_origin(request)
    _rate_or_429(request)
    session = _live_session(request)
    if session is None:
        raise _signed_out()
    _no_store(response)
    todo_sessions.touch_session(session["token_hash"], session["last_used_at"])


session_api_router = APIRouter()
session_api_router.include_router(build_router(_session_api_guard))


@session_api_router.get("/session", dependencies=[Depends(_session_api_guard)])
def todo_web_session():
    """The app's bootstrap: 200 with a live session, 401 without."""
    return {"ok": True}


@session_api_router.post("/signin/{token}")
def todo_web_redeem(token: str, request: Request, response: Response):
    """Spend a sign-in code and sign this browser in.

    Same-origin FIRST, before any limiter spend or lookup: SameSite=Lax cannot protect
    a request that reads no cookie, so this check is what stops a cross-site page
    signing a victim's browser into someone else's session. One 404 for a wrong, spent
    or expired code; only those charge the strict guess limiter, and once it is spent
    every redemption from that address is a 429 until it refills.
    """
    _enabled_or_404()
    _same_origin(request)
    _rate_or_429(request)
    # A spent guess budget stops the next try too, even one that would succeed —
    # otherwise guessing past the budget costs only the 429s on the misses.
    if guess_limiter.exhausted(_client_ip(request)):
        raise _too_many()
    redeemed = todo_sessions.redeem_signin_token(token, replaces=_cookie_token(request))
    if redeemed is None:
        if not guess_limiter.allow(_client_ip(request)):
            raise _too_many()
        raise _not_found()
    _set_session_cookie(response, redeemed["session_token"])
    _no_store(response)
    return {"ok": True}


@session_api_router.post("/signout", dependencies=[Depends(_session_api_guard)])
def todo_web_signout(request: Request):
    """Sign THIS browser out: delete its row, leave its cookie. Every other device
    stays signed in."""
    todo_sessions.revoke_session(_cookie_token(request))
    return {"ok": True}


# Registered LAST. The old client called /api/todo-web/{token}/…; without this a GET
# there would fall through to the SPA catch-all and answer index.html with a 200.
@session_api_router.api_route(
    "/{token}/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False
)
def retired_token_api(token: str, rest: str):
    raise _not_found()
