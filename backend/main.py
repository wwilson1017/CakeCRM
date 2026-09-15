"""
CakeCRM — FastAPI entry point.

Mounts routers, initializes the Postgres pool and applies migrations, sets up
CORS, and serves the built frontend in production. The CRM core is mounted at
/api/crm (issue #3); this shell also carries auth, 2FA, branding, and health.

Postgres is mandatory — startup fails loudly without DATABASE_URL.
"""

import asyncio
import contextvars
import logging
import os
import re
import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette._utils import get_route_path
from starlette.routing import compile_path

from alerts.router import router as alerts_router
from assistant.router import router as assistant_router
from branding.router import MAX_LOGO_BYTES, router as branding_router
from context_files.router import router as context_files_router
from core import postgres
from core.auth import router as auth_router
from core.auth_2fa import router as auth_2fa_router
from core.config import settings
from core.storage import atomic_write
from crm import attachment_service
from crm.gtd_router import router as gtd_router
from crm.router import MAX_UPLOAD_BYTES as crm_upload_max_bytes, router as crm_router
from crm.todo_capture import router as todo_capture_router
from crm.todo_web import (
    public_api_router as todo_web_public_api,
    router as todo_web_router,
    token_api_router as todo_web_token_api,
)
from gmail.router import router as gmail_router
from heartbeat.router import router as heartbeat_router
from memory.router import router as memory_router
from notifications.router import router as notifications_router
from providers.router import router as providers_router, setup_router as ai_setup_router
from reminders.router import router as reminders_router
from telegram import poller as telegram_poller
from telegram.router import router as telegram_router
from users.router import router as users_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
# httpx logs every request at INFO with the full URL. The Telegram Bot API embeds the
# bot token in the URL path (/bot<TOKEN>/...), so INFO-level httpx request logs would
# leak the token into application logs — quiet httpx to WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)

VERSION = "0.1.0"

# Request-ID context variable for log tracing
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize directories, the Postgres pool, and migrations on startup."""
    data_root = Path(__file__).resolve().parent / "data"
    for subdir in ("branding",):
        (data_root / subdir).mkdir(parents=True, exist_ok=True)

    if not postgres.is_configured():
        raise RuntimeError(
            "DATABASE_URL is not set — CakeCRM requires PostgreSQL.\n"
            "  Local:   docker compose up -d   (then use the DATABASE_URL from .env.example)\n"
            "  Railway: add a PostgreSQL service and reference its DATABASE_URL variable."
        )

    postgres.init_pool()
    postgres.run_migrations()

    # ── Accounts (issues #78, #60) ───────────────────────────────────────────
    # Order matters. ensure_bootstrap_admin() seeds the first admin when `users` is
    # empty — including carrying an upgrading install's live password hash out of the
    # pre-#60 auth_credential singleton, which is why it cannot live in the migration
    # (migrations all run above this line, and it needs both that hash and an env
    # var). apply_password_reset_env() then consumes AUTH_PASSWORD_RESET, and must
    # run AFTER the bootstrap so the operator's lever has an admin row to target on a
    # first boot that also sets it.
    from core.auth import apply_password_reset_env
    from users.bootstrap import ensure_bootstrap_admin
    ensure_bootstrap_admin()
    apply_password_reset_env()

    # ── AI touch-count worker (issue #16) ────────────────────────────────────
    # The in-process daemon worker marshals its provider calls onto THIS event loop
    # (see crm/touch_count_service.capture_event_loop) so the module-level provider
    # client caches are never shared across event loops. Capture-only — the worker
    # itself starts lazily on the first note/activity write; nothing to stop on shutdown
    # (it's a daemon thread that dies with the process).
    from crm import touch_count_service
    touch_count_service.capture_event_loop(asyncio.get_running_loop())

    # ── Help library (issue #143) ────────────────────────────────────────────
    # Loaded here rather than at import so a bad topic file can never break the CI
    # import check (which runs with no database). warm() cannot raise; a library that
    # fails here still loads lazily on the first help tool call.
    from help.library import warm as warm_help_library
    warm_help_library()

    # ── Railway environment logging ─────────────────────────────────────────
    if settings.is_railway:
        from core.config import RAILWAY_PUBLIC_URL
        logger.info("Running on Railway: %s", RAILWAY_PUBLIC_URL)
    else:
        logger.info("Running locally (no RAILWAY_PUBLIC_DOMAIN detected)")

    if settings.jwt_secret_is_auto:
        logger.warning(
            "JWT_SECRET not set — using auto-generated secret. "
            "Sessions will reset on redeploy. Set JWT_SECRET env var for persistent sessions."
        )

    if not os.environ.get("ENCRYPTION_KEY"):
        logger.info(
            "ENCRYPTION_KEY not set — will auto-generate and store in %s",
            data_root / ".encryption-key",
        )

    # ── Volume health check ──────────────────────────────────────────────
    volume_marker = data_root / ".volume-marker"
    if volume_marker.exists():
        logger.info("Persistent volume verified (marker file present)")
    else:
        atomic_write(volume_marker, f"cakecrm:{datetime.now(timezone.utc).isoformat()}")
        if settings.is_railway:
            logger.info(
                "First boot — wrote volume marker to %s. "
                "If this message appears on every deploy, your persistent volume may not be configured. "
                "Mount a volume at /app/backend/data in Railway settings.",
                volume_marker,
            )

    # ── Background scheduler (issue #6) ─────────────────────────────────────
    # Capture the main event loop so scheduler-thread background AI turns run their
    # coroutines HERE (the provider async clients are module-cached + loop-bound to
    # this loop; a throwaway asyncio.run loop would break on the 2nd turn).
    import asyncio as _asyncio

    from assistant import background as _background
    _background.set_main_loop(_asyncio.get_running_loop())

    # Started after migrations so the tick's tables exist. The reminder tick
    # always runs (keyless); only the heartbeat AI turn is env-gated.
    from heartbeat.scheduler import start_scheduler
    start_scheduler()
    logger.info("Heartbeat scheduler started (60s tick; AI turn %s)",
                "enabled" if settings.heartbeat_enabled else "disabled")

    # Telegram long-poll task (issue #7): a single main-loop asyncio task. It idles
    # until a bot token is connected, so it is safe to start unconditionally here.
    telegram_poller.start()

    # Dreaming (issue #5) needs no wiring here: #6's reminder_tick calls
    # dreaming.processor.run_dreaming_if_due() every 60s through its own guarded seam
    # (heartbeat.service._maybe_run_dreaming). #5's interim lifespan scheduler was
    # always meant to be absorbed the moment #6 landed — this is that deletion.

    logger.info("CakeCRM backend started. Data dir: %s", data_root)
    try:
        yield
    finally:
        # Stop the scheduler (waiting for an in-flight tick) BEFORE closing the
        # pool, so a running tick never loses the Postgres pool under it. The
        # Telegram poller stops next — a tick's notification delivery goes through
        # telegram.service's own sync client, so it does not depend on the poller.
        from heartbeat.scheduler import shutdown_scheduler
        shutdown_scheduler()
        await telegram_poller.stop()
        postgres.close_pool()
        logger.info("CakeCRM backend shutting down.")


app = FastAPI(
    title="CakeCRM",
    description="Free, open-source, self-hostable CRM with an AI sales assistant built in",
    version=VERSION,
    lifespan=lifespan,
)


# ── Request-size ceiling ─────────────────────────────────────────────────────
#
# A backstop, NOT the per-route cap. Every upload route enforces its own limit with the
# repo's `read(cap + 1)` idiom — but for a MULTIPART request that check is too late to
# stop the damage: FastAPI parses the form (spooling the whole body, to /tmp past 1 MB)
# BEFORE it solves the route's dependencies, so neither the handler nor a `Depends` guard
# can prevent an oversized body from being written to disk first. Verified empirically,
# not assumed. Middleware is the only layer that runs before the body is consumed.
#
# The ceiling is therefore generous — it exists to stop "an authenticated user fills the
# container's disk", not to enforce any feature's limit. It must clear the largest
# legitimate request in the app, which is an assistant upload: MAX_FILES (5) x
# MAX_FILE_SIZE (10 MB) plus multipart overhead.
#
# Content-Length only: a chunked request that declares no length slips past this, and its
# per-route bounded read remains the authority. Rejecting those would mean counting bytes
# as they stream, which is real machinery for a case no browser produces.
MAX_REQUEST_BYTES = 64 * 1024 * 1024

# Room for the multipart envelope around one file part: the boundary pair, the
# Content-Disposition (including a filename the client may send longer than the 120 bytes
# the service will store), the part's Content-Type, and the CRLFs. A real browser envelope
# is well under 1 KB; 64 KB reserves ~64x that. Deliberately generous because it is
# headroom on a rejection threshold, not a budget anyone spends — the cost of being too
# tight is 413ing a correct upload, and the cost of being loose is 64 KB.
MULTIPART_ENVELOPE_BYTES = 64 * 1024

# Every upload route's admission ceiling, because for a multipart body the ceiling here is
# the ONLY thing standing between a caller and a full spool-to-disk plus parse (see above:
# the route's own bounded read runs too late). Sized per route at its real feature limit
# plus the envelope, rather than leaving each one on a 64 MB disk backstop it can overrun
# by 32-64x.
#
# Keyed by the route's own path TEMPLATE, compiled with Starlette's `compile_path` — the
# same function the router uses — so an entry matches exactly the set of paths that reach
# that endpoint. Writing these patterns by hand is what makes them wrong: a hand-written
# `\d+` for `{note_id}` looks right and is a bypass, because the router compiles that
# parameter to `[^/]+` (`note_id: int` is FastAPI VALIDATION, applied after the body is
# already parsed). `/note/abc/attachments` therefore reaches the parser while missing a
# `\d+` gate, spooling an oversized body under the 64 MB backstop — the exact hole this
# table exists to close. Deriving the pattern from the template makes that class of drift
# unrepresentable rather than merely fixed.
#
# The assistant upload route is deliberately ABSENT: its legitimate maximum is MAX_FILES x
# MAX_FILE_SIZE = 50 MB against the same 64 MB backstop, so the global ceiling is already
# the tight one there and a row would only duplicate it.
_ROUTE_REQUEST_LIMIT_SPECS: tuple[tuple[str, int], ...] = (
    (
        "/api/crm/chatter/note/{note_id}/attachments",
        attachment_service.MAX_ATTACHMENT_BYTES + MULTIPART_ENVELOPE_BYTES,
    ),
    ("/api/crm/import", crm_upload_max_bytes + MULTIPART_ENVELOPE_BYTES),
    ("/api/crm/smart-import/parse", crm_upload_max_bytes + MULTIPART_ENVELOPE_BYTES),
    ("/api/branding/logo", MAX_LOGO_BYTES + MULTIPART_ENVELOPE_BYTES),
)

# First match wins, falling back to MAX_REQUEST_BYTES.
_ROUTE_REQUEST_LIMITS: tuple[tuple[re.Pattern[str], int], ...] = tuple(
    (compile_path(template)[0], limit) for template, limit in _ROUTE_REQUEST_LIMIT_SPECS
)


def _request_limit_for(path: str) -> int:
    """The Content-Length ceiling admitting `path`: the first matching row, in declaration
    order. Nothing sorts by tightness, so overlapping rows resolve by position."""
    for pattern, limit in _ROUTE_REQUEST_LIMITS:
        if pattern.match(path):
            return limit
    return MAX_REQUEST_BYTES


@app.middleware("http")
async def request_size_limit_middleware(request: Request, call_next):
    declared = request.headers.get("content-length")
    if declared:
        try:
            # `get_route_path`, not `request.url.path`: the router matches on the path with
            # `root_path` stripped, so behind a path-prefixing proxy (or under an ASGI mount)
            # the raw path carries a prefix the compiled patterns do not have — the route
            # would still be reached while its ceiling silently reverted to the 64 MB
            # backstop. Same principle as compiling the patterns with `compile_path`: match
            # what the router matches. It is Starlette-private, which is deliberate — if it
            # moves, the import fails loudly at startup (and in CI's import check) rather
            # than drifting quietly, which is the failure mode that actually costs us here.
            if int(declared) > _request_limit_for(get_route_path(request.scope)):
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request too large."},
                )
        except ValueError:
            pass  # unparseable header — let the ASGI server deal with it
    return await call_next(request)


# ── Request-ID middleware ────────────────────────────────────────────────────

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = _uuid.uuid4().hex[:12]
    request_id_ctx.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(auth_router, prefix="/api")
app.include_router(auth_2fa_router, prefix="/api", tags=["auth-2fa"])
app.include_router(users_router, prefix="/api/users", tags=["users"])
app.include_router(branding_router, prefix="/api/branding", tags=["branding"])
app.include_router(providers_router, prefix="/api/providers", tags=["providers"])
app.include_router(ai_setup_router, prefix="/api/setup", tags=["setup"])
app.include_router(crm_router, prefix="/api/crm", tags=["crm"])
app.include_router(assistant_router, prefix="/api/assistant", tags=["assistant"])
app.include_router(telegram_router, prefix="/api/telegram", tags=["telegram"])
app.include_router(gmail_router, prefix="/api/gmail", tags=["gmail"])
app.include_router(reminders_router, prefix="/api/reminders", tags=["reminders"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["notifications"])
app.include_router(alerts_router, prefix="/api/alerts", tags=["alerts"])
app.include_router(heartbeat_router, prefix="/api/heartbeat", tags=["heartbeat"])
app.include_router(context_files_router, prefix="/api/context-files", tags=["context-files"])
app.include_router(memory_router, prefix="/api/memory", tags=["memory"])
app.include_router(gtd_router, prefix="/api/crm/gtd", tags=["todo-gtd"])

# ── No-login todo surfaces (#70) ──────────────────────────────────────────────
# All four mounts MUST come before the SPA catch-all at the bottom of this file, or
# `/todo` and `/capture` would be swallowed by it and always serve the app shell.
#
# The token mount is registered before the bare public one for the same reason chatty
# documents: `/api/todo-web/{token}` would otherwise read a literal path segment like
# `todos` as a token guess. `todos` is in RESERVED_TODO_WEB_SLUGS precisely so that
# collision cannot happen from the other direction either.
#
# The two surfaces are ASYMMETRIC, and neither consults the task mode (#102 made GTD the
# default; these mounts are unchanged by it). Capture is LIVE by default — the bare
# /capture path answers until a token is set, which is the deliberate #70 design: it is
# write-only, so an uninvited caller can add to the inbox but read nothing back. The web
# app is the one that grants reads, so it 404s entirely until todo_web_enabled.
app.include_router(todo_capture_router, tags=["todo-capture"])
app.include_router(todo_web_public_api, prefix="/api/todo-web", tags=["todo-web"])
app.include_router(todo_web_token_api, prefix="/api/todo-web/{token}", tags=["todo-web"])
app.include_router(todo_web_router, tags=["todo-web"])


# ── Health endpoints ──────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    pg_status = postgres.health_check()
    status = "ok" if pg_status == "ok" else "degraded"
    return {"status": status, "version": VERSION, "databases": {"postgres": pg_status}}


@app.get("/api/health/live")
async def health_live():
    return {"status": "ok"}


# ── Static files (production frontend build) ──────────────────────────────────
# Assets served directly; every other non-API path falls back to index.html so
# client-side routes (/login, /contacts/42) survive a hard refresh.

_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="assets")

    from fastapi.responses import FileResponse

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        candidate = (_frontend_dist / path).resolve()
        if path and candidate.is_relative_to(_frontend_dist) and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_frontend_dist / "index.html"))
