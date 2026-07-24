"""
CakeCRM — FastAPI entry point.

Mounts routers, initializes the Postgres pool and applies migrations, sets up
CORS, and serves the built frontend in production. The CRM core is mounted at
/api/crm (issue #3); this shell also carries auth, 2FA, branding, and health.

Postgres is mandatory — startup fails loudly without DATABASE_URL.
"""

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

from assistant.router import router as assistant_router
from branding.router import router as branding_router
from core import postgres
from core.auth import router as auth_router
from core.auth_2fa import router as auth_2fa_router
from core.config import settings
from core.storage import atomic_write
from crm.router import router as crm_router
from providers.router import router as providers_router, setup_router as ai_setup_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

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

    logger.info("CakeCRM backend started. Data dir: %s", data_root)
    yield

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
app.include_router(branding_router, prefix="/api/branding", tags=["branding"])
app.include_router(providers_router, prefix="/api/providers", tags=["providers"])
app.include_router(ai_setup_router, prefix="/api/setup", tags=["setup"])
app.include_router(crm_router, prefix="/api/crm", tags=["crm"])
app.include_router(assistant_router, prefix="/api/assistant", tags=["assistant"])


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
