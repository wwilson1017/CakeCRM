"""Todo-GTD — public quick-capture (#70).

  GET  /capture[/{token}]      — self-contained, mobile-first HTML page
  POST /api/capture[/{token}]  — {"text": ...} -> inbox todo (deterministic, no AI)

No JWT by design: this is the phone-bookmark capture path. An optional secret
(`crm_meta.todo_capture_token`) switches the URLs from public /capture to
/capture/{token}; while a token is set, the bare paths 404.

This surface is WRITE-ONLY. It creates one inbox todo and answers `{ok, id}` —
nothing stored is ever readable through it. The full read/write no-login surface is
a separate, off-by-default feature (`crm/todo_web.py`) behind its own token.

Ported from chatty's `core/todo/capture.py`, with the settings moved from its JSON
admin-settings file onto the `crm_meta` singleton.
"""

import asyncio
import hmac
import html
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from core.ratelimit import IPRateLimiter
from crm import gtd_service, service
from crm.gtd_common import ValidationError
from crm.todo_pwa import manifest_response

logger = logging.getLogger(__name__)
router = APIRouter()

# Generous enough for a real burst of thoughts, tight enough to stop flooding.
capture_limiter = IPRateLimiter(window=300, max_hits=30)


def _configured_token() -> str:
    return service.get_todo_public_settings()["todo_capture_token"]


def _tokenless_or_404() -> None:
    # The bare public paths go dark the moment a secret token is configured.
    if _configured_token():
        raise HTTPException(status_code=404, detail="Not found")


_CAPTURE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="referrer" content="same-origin">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Capture</title>
<link rel="manifest" href="__BASE_PATH__/manifest.webmanifest">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Capture">
<meta name="theme-color" content="#fffffa">
<style>
  * { box-sizing: border-box; margin: 0; }
  body {
    background: #fffffa; color: #292929; min-height: 100dvh;
    font-family: 'Open Sans', system-ui, sans-serif;
    display: flex; flex-direction: column; align-items: center;
    padding: max(24px, env(safe-area-inset-top)) 16px 24px;
  }
  main { width: 100%; max-width: 560px; display: flex; flex-direction: column; gap: 12px; }
  h1 { font-size: 18px; font-weight: 700; letter-spacing: 0.3px; }
  h1 span { color: #666666; font-weight: 400; font-size: 13px; margin-left: 8px; }
  textarea {
    width: 100%; min-height: 140px; resize: vertical;
    background: #ffffff; color: #292929; border: 1px solid #d1d5db;
    border-radius: 10px; padding: 14px; font: inherit; font-size: 16px;
  }
  textarea:focus { outline: none; border-color: #e31d3b; }
  button {
    background: #e31d3b; color: #ffffff; border: 0; border-radius: 10px;
    padding: 14px; font: inherit; font-size: 16px; font-weight: 700; cursor: pointer;
  }
  button:disabled { opacity: 0.5; }
  #msg { min-height: 22px; font-size: 14px; text-align: center; }
  #msg.ok { color: #2f7a43; }
  #msg.err { color: #b3261e; }
  @media (prefers-color-scheme: dark) {
    body { background: #1a1a1a; color: #f2f2f2; }
    textarea { background: #242424; color: #f2f2f2; border-color: #3d3d3d; }
    h1 span { color: #a3a3a3; }
    #msg.ok { color: #7BC47F; }
    #msg.err { color: #ff8f80; }
  }
</style>
</head>
<body>
<main>
  <h1>Capture<span>straight to your inbox</span></h1>
  <!-- `autofocus` is the first focus attempt and the only one a desktop tab needs;
       the retry ladder in the script below is layered ON TOP of it. Keep both. -->
  <textarea id="t" autofocus placeholder="What's on your mind?"></textarea>
  <button id="b">Send</button>
  <div id="msg"></div>
</main>
<script>
  var t = document.getElementById('t'), b = document.getElementById('b'), m = document.getElementById('msg');
  function send() {
    var text = t.value.trim();
    if (!text) { t.focus(); return; }
    b.disabled = true;
    fetch('__POST_PATH__', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    }).then(function (r) {
      if (r.ok) {
        t.value = '';
        m.className = 'ok';
        m.textContent = 'captured \\u2713';
        setTimeout(function () { m.textContent = ''; }, 2500);
      } else {
        return r.json().catch(function () { return {}; }).then(function (d) {
          m.className = 'err';
          m.textContent = d.detail || ('failed (' + r.status + ') \\u2014 try again');
        });
      }
    }).catch(function () {
      m.className = 'err';
      m.textContent = 'network error \\u2014 try again';
    }).finally(function () {
      b.disabled = false;
      t.focus();
    });
  }
  b.addEventListener('click', send);
  t.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send();
  });

  // Focus on open (#233, port of the blueprint's capture-page fix). `autofocus` is ONE
  // focus() at parse time and never repeats, and a resumed home-screen app is not
  // re-navigated, so switching back to it left the caret nowhere and the keyboard down.
  // Two rules: the resume re-attempt is GATED to a standalone launch, so a browser tab
  // never has its caret grabbed when the user switches back to it; and no attempt ever
  // takes focus from an element the user is already in (after a resume that could be
  // the Send button). Known ceiling, same as upstream: iOS raises the keyboard only
  // under user activation, so a cold launch lands with the caret placed and the
  // keyboard down. No tap-to-start overlay — ruled out upstream.
  function media(q) {
    try { return !!(window.matchMedia && window.matchMedia(q).matches); } catch (e) { return false; }
  }
  // Two signals because neither covers both platforms: `navigator.standalone` is WebKit's
  // non-standard flag (the only one on older iOS); the media query is the standard one.
  function isStandalone() {
    return navigator.standalone === true || media('(display-mode: standalone)');
  }
  function focusAttempt() {
    var active = document.activeElement;
    if (active !== t) {
      if (active && active !== document.body) return;
      t.focus();
      if (document.activeElement !== t) return;
    }
    // Touch-primary only; Chromium's VirtualKeyboard API is the one lever that can raise
    // the keyboard for a control the script focused. Best-effort: WebKit lacks it, and
    // Chromium may refuse it without activation — the textarea is the tap fallback.
    if (media('(pointer: coarse)')) {
      try {
        if (navigator.virtualKeyboard && navigator.virtualKeyboard.show) navigator.virtualKeyboard.show();
      } catch (e) {}
    }
  }
  var focusRaf = 0;
  function focusRetry() {
    focusAttempt();
    if (window.requestAnimationFrame) {
      cancelAnimationFrame(focusRaf);
      focusRaf = requestAnimationFrame(focusAttempt);
    }
  }
  focusRetry();
  // Load only: the short ladder covers the frames where the page is still settling.
  setTimeout(focusAttempt, 150);
  setTimeout(focusAttempt, 400);
  function onResume() {
    if (document.visibilityState === 'hidden') return;
    if (!isStandalone()) return;
    focusRetry();
  }
  window.addEventListener('pageshow', onResume);
  document.addEventListener('visibilitychange', onResume);
</script>
</body>
</html>"""


def _page(post_path: str, base_path: str) -> HTMLResponse:
    # Both values embed a configured token. It is clamped to URL-safe characters on
    # the way in, but this is the output boundary and the value comes from the
    # database — so each is escaped for the context it lands in: post_path sits in a
    # JS string literal, base_path in an HTML attribute. Defense in depth; neither
    # layer is load-bearing alone.
    page = (_CAPTURE_HTML
            .replace("__POST_PATH__", json.dumps(post_path)[1:-1])
            .replace("__BASE_PATH__", html.escape(base_path, quote=True)))
    # The tokened variant embeds the secret POST path, so caches and search indexes
    # must never keep a copy.
    return HTMLResponse(
        page,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


def _manifest(base_path: str) -> Response:
    return manifest_response(
        name="Capture",
        description="Quick capture to your todo inbox",
        base_path=base_path,
    )


# Bounds request processing BEFORE JSON parsing (the 20k-char cap in the service runs
# only after the body is materialized). 64 KiB fits any legal capture with UTF-8 +
# JSON-escaping headroom.
_MAX_BODY_BYTES = 64 * 1024


def _rate_or_429(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    if not capture_limiter.allow(ip):
        raise HTTPException(
            status_code=429, detail="Too many captures — try again in a few minutes"
        )


async def _read_capture_text(request: Request) -> str:
    """Parse {"text": ...} by hand so the size cap applies before parsing."""
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Capture body too large")
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Capture body too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    text = data.get("text", "") if isinstance(data, dict) else None
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text must be a string")
    return text


def _do_capture(text: str) -> dict:
    try:
        todo = gtd_service.capture(text, source="capture_web")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Only the new id — never the stored row. This endpoint stays write-only.
    return {"ok": True, "id": todo["id"]}


# ── Public mode (no token configured) ─────────────────────────────────────────
#
# ⚠️ EVERY handler below is a plain `def`, never `async def`, and that is
# load-bearing on an UNAUTHENTICATED surface. All of them reach Postgres (the
# settings read, and the capture insert), and psycopg2 blocks. FastAPI offloads a
# sync handler to its threadpool; a blocking call inside an `async def` runs on the
# event loop instead and never yields — so with the pool exhausted, one burst of
# anonymous requests would stall EVERY request the app is serving (the deploy pins a
# single worker). The two POSTs must stay async to `await request.body()`, so they
# push the blocking half through `asyncio.to_thread` instead. Same rule as
# crm/gtd_router.py's plain-def handlers and telegram/service.py's to_thread capture.

@router.get("/capture", response_class=HTMLResponse)
def capture_page(request: Request):
    _rate_or_429(request)
    _tokenless_or_404()
    return _page("/api/capture", "/capture")


# Registered before /capture/{token} so this path is never read as a token guess.
# No ambiguity either way: the token clamp strips dots, so a token can never
# literally be "manifest.webmanifest".
@router.get("/capture/manifest.webmanifest")
def capture_manifest(request: Request):
    _rate_or_429(request)
    _tokenless_or_404()
    return _manifest("/capture")


@router.post("/api/capture")
async def capture_post(request: Request):
    _rate_or_429(request)
    await asyncio.to_thread(_tokenless_or_404)
    text = await _read_capture_text(request)
    return await asyncio.to_thread(_do_capture, text)


# ── Token mode ────────────────────────────────────────────────────────────────

def _require_token(token: str) -> str:
    configured = _configured_token()
    # Compare as BYTES: compare_digest raises TypeError on non-ASCII str input, which
    # would turn a scanner's /capture/ü guess into a 500.
    if not configured or not hmac.compare_digest(token.encode(), configured.encode()):
        # Always 404, never 403: an unauthorized caller learns nothing about whether
        # a capture surface exists or what the token looks like.
        raise HTTPException(status_code=404, detail="Not found")
    return configured


@router.get("/capture/{token}", response_class=HTMLResponse)
def capture_page_token(token: str, request: Request):
    # Rate-check BEFORE the token comparison: failed guesses must burn the same
    # per-IP budget as captures, or the secret is brute-forceable at line speed.
    _rate_or_429(request)
    configured = _require_token(token)
    return _page(f"/api/capture/{configured}", f"/capture/{configured}")


@router.get("/capture/{token}/manifest.webmanifest")
def capture_manifest_token(token: str, request: Request):
    _rate_or_429(request)
    configured = _require_token(token)
    return _manifest(f"/capture/{configured}")


@router.post("/api/capture/{token}")
async def capture_post_token(token: str, request: Request):
    _rate_or_429(request)
    await asyncio.to_thread(_require_token, token)
    text = await _read_capture_text(request)
    return await asyncio.to_thread(_do_capture, text)
