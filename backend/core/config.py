"""
CakeCRM — Central configuration loader.

Reads settings from a .env file (repo root or backend directory).
Auto-detects Railway environment via RAILWAY_PUBLIC_DOMAIN.
"""

import os
import secrets

from dotenv import load_dotenv

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root_dir = os.path.dirname(_backend_dir)
load_dotenv(os.path.join(_root_dir, ".env"))
load_dotenv(os.path.join(_backend_dir, ".env"))

# Railway injects RAILWAY_PUBLIC_DOMAIN (e.g. "cakecrm-production.up.railway.app")
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_PUBLIC_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}" if RAILWAY_PUBLIC_DOMAIN else ""

# Track whether JWT_SECRET was explicitly provided or auto-generated
_jwt_secret_from_env = os.getenv("JWT_SECRET", "")
_jwt_secret_is_auto = not _jwt_secret_from_env or _jwt_secret_from_env == "change-me-in-production"


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive-integer env var, falling back to ``default`` on a missing,
    non-integer, or non-positive value (so a bad HEARTBEAT_INTERVAL_MINUTES can
    neither crash startup nor make the heartbeat cadence fire every tick)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


class AuthSettings:
    # Bootstrap password (plaintext or bcrypt-hashed). Only consulted while the
    # auth_credential row holds no hash — once the user sets their own password
    # in-app, this value is inert. See core.auth.verify_password.
    password: str = os.getenv("AUTH_PASSWORD", "changeme")

    # Operator recovery lever — see core.auth.apply_password_reset_env
    password_reset: str = os.getenv("AUTH_PASSWORD_RESET", "")


class JWTSettings:
    secret_key: str = _jwt_secret_from_env if not _jwt_secret_is_auto else secrets.token_hex(32)
    algorithm: str = "HS256"
    expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))


class Settings:
    auth = AuthSettings()
    jwt = JWTSettings()

    # CORS — parse from env + auto-add Railway domain if detected
    allowed_origins: list[str] = [
        o.strip()
        for o in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000",
        ).split(",")
        if o.strip()
    ] + ([RAILWAY_PUBLIC_URL] if RAILWAY_PUBLIC_URL else [])

    # Multi-user (seats) is roughed in for a future phase
    multi_user_enabled: bool = os.getenv("MULTI_USER_ENABLED", "false").lower() in ("1", "true", "yes")

    # URLs — auto-detect from Railway if not explicitly set
    frontend_url: str = os.getenv("FRONTEND_URL", "") or RAILWAY_PUBLIC_URL or "http://localhost:5173"
    backend_url: str = os.getenv("BACKEND_URL", "") or RAILWAY_PUBLIC_URL or "http://localhost:8000"

    # Railway environment detection
    is_railway: bool = bool(RAILWAY_PUBLIC_DOMAIN)
    jwt_secret_is_auto: bool = _jwt_secret_is_auto

    # ── Heartbeat / notifications (issue #6) ────────────────────────────────
    # The heartbeat AI turn is env-gated: unset → enabled on Railway, disabled
    # locally (a local `python run.py` must not spam real background AI turns —
    # coach lesson). Reminder processing and push delivery ALWAYS run (they are
    # keyless and cost-free), only the AI turn keys off this flag.
    heartbeat_enabled: bool = (
        os.getenv("HEARTBEAT_ENABLED", "").lower() in ("1", "true", "yes")
        if os.getenv("HEARTBEAT_ENABLED") is not None
        else bool(RAILWAY_PUBLIC_DOMAIN)
    )
    # How stale last_turn_at must be before the tick runs another system heartbeat
    # turn. The tick fires every 60s but a per-tick AI turn would be ~1,440/day of
    # token burn, so the turn itself is throttled (Chatty's heartbeat cadence).
    heartbeat_interval_minutes: int = _positive_int_env("HEARTBEAT_INTERVAL_MINUTES", 30)

    # Cadence of the read-only Gmail inbox touch scan (issue #17), driven by its own
    # gmail_scan scheduler job. Gated ONLY on Gmail being connected — deliberately no
    # per-feature enable flag (product rule: the integration connection IS the gate).
    gmail_scan_interval_minutes: int = _positive_int_env("GMAIL_SCAN_INTERVAL_MINUTES", 15)

    # Web Push (VAPID). Leave blank to auto-generate a keypair once and persist it
    # in Postgres (vapid_keys singleton, private key Fernet-encrypted). Set both
    # to operator-manage the keys via env instead (never persisted then).
    vapid_public_key: str = os.getenv("VAPID_PUBLIC_KEY", "")
    vapid_private_key: str = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_subject: str = os.getenv("VAPID_SUBJECT", "mailto:admin@cakecrm.local")


settings = Settings()
