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


class AuthSettings:
    # Plaintext or bcrypt-hashed password for single-user login
    password: str = os.getenv("AUTH_PASSWORD", "changeme")


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


settings = Settings()
