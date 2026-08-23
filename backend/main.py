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
import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from alerts.router import router as alerts_router
from assistant.router import router as assistant_router
from branding.router import router as branding_router
from context_files.router import router as context_files_router
from core import postgres
from core.auth import router as auth_router
from core.auth_2fa import router as auth_2fa_router
from core.config import settings
from core.storage import atomic_write
from crm.router import router as crm_router
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
