"""
CakeCRM — Central configuration loader.

Reads settings from a .env file (repo root or backend directory).
Auto-detects Railway environment via RAILWAY_PUBLIC_DOMAIN.
"""

import os
import secrets

from dotenv import load_dotenv

from core.secret_store import PersistedSecret

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root_dir = os.path.dirname(_backend_dir)
load_dotenv(os.path.join(_root_dir, ".env"))
load_dotenv(os.path.join(_backend_dir, ".env"))

# Railway injects RAILWAY_PUBLIC_DOMAIN (e.g. "cakecrm-production.up.railway.app")
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_PUBLIC_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}" if RAILWAY_PUBLIC_DOMAIN else ""

# The JWT signing secret. Resolved through the SAME ladder as the encryption key
# (env -> OS keychain -> a file on the Railway volume -> generate once), because a
# secret that only lived in the process meant every restart minted a new signing
# key and signed every seat out — issue #222. `.env.example` ships the placeholder,
# so it is declared here as "not a real value" rather than checked at each use.
JWT_SECRET_STORE = PersistedSecret(
    env_var="JWT_SECRET",
    filename=".jwt-secret",
    generate=lambda: secrets.token_hex(32),
    ignored_env_values=("change-me-in-production",),
    # Losing every session is bad; refusing to boot is worse. An install with no
    # writable volume still starts, loudly, on a process-local secret.
    ephemeral_fallback=True,
    # The one rung this does NOT share with the encryption key. A keychain entry
    # is scoped to the OS ACCOUNT, so two checkouts under one login would sign
    # with the same key and accept each other's tokens — a fresh install seeds
    # admin id 1 at epoch 0, so a token from one is an admin session on the other.
    # The file under `backend/data/` is install-local by construction.
    use_keychain=False,
)


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


def _configured_base_url() -> str:
    """This install's public base URL as an OPERATOR set it — ``""`` when nobody did.

    Separate from ``Settings.frontend_url``, which falls back to a dev default and so can
    never answer "was this configured?". Both readings come from here so they cannot
    disagree: a link builder that thinks the address is known while the URL is the
    localhost default would paste that hostname into a message sent to someone's phone
    (issue #145).
    """
    return os.getenv("FRONTEND_URL", "") or RAILWAY_PUBLIC_URL


def _hour_env(name: str, default: int) -> int:
    """Read an hour-of-day env var (0-23). Separate from ``_positive_int_env`` because
    0 is a legitimate hour (midnight) but not a legitimate interval — reusing that
    helper here would silently rewrite a midnight digest to the default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if 0 <= value <= 23 else default


class AuthSettings:
    # First-boot bootstrap password (plaintext or bcrypt-hashed) for the admin
    # account. Consulted ONLY while the users table is empty; once any account
    # exists, passwords live in the database and this value is inert. See
    # users.bootstrap.ensure_bootstrap_admin.
    password: str = os.getenv("AUTH_PASSWORD", "changeme")

    # Identity of that first admin. Only used at bootstrap — renaming the account
    # afterwards is done in Settings, not here.
    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@cakecrm.local")
    admin_name: str = os.getenv("ADMIN_NAME", "Admin")

    # Operator recovery lever — see core.auth.apply_password_reset_env
    password_reset: str = os.getenv("AUTH_PASSWORD_RESET", "")


class JWTSettings:
    secret_key: str = JWT_SECRET_STORE.resolve()
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

    # URLs — auto-detect from Railway if not explicitly set
    frontend_url: str = _configured_base_url() or "http://localhost:5173"
    backend_url: str = os.getenv("BACKEND_URL", "") or RAILWAY_PUBLIC_URL or "http://localhost:8000"

    # Railway environment detection
    is_railway: bool = bool(RAILWAY_PUBLIC_DOMAIN)
    # "The operator did not supply JWT_SECRET" — NOT "the secret is unstable".
    # Since #222 an auto secret persists across restarts; the startup warning in
    # main.py reads `JWT_SECRET_STORE.source` to say which of those it is.
    jwt_secret_is_auto: bool = not JWT_SECRET_STORE.from_env
    # Whether `frontend_url` above fell all the way through to the dev default, i.e.
    # nobody configured this install's public address (issue #145). Tracked the same way
    # `jwt_secret_is_auto` is, and for the same reason: the value is always present, so
    # "was it actually configured?" is a separate question the fallback chain erases.
    # `crm.links.deal_url` reads it to decide between an absolute and a relative link —
    # asserting `http://localhost:5173` in a message sent to someone's phone is worse
    # than handing them a path their browser can resolve.
    frontend_url_is_default: bool = not _configured_base_url()

    # ── Heartbeat / notifications (issue #6) ────────────────────────────────
    # The heartbeat AI turn is env-gated: unset → enabled on Railway, disabled
    # locally (a local `python run.py` must not spam real background AI turns).
    # The maintenance tick and push delivery ALWAYS run (they are
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

    # ── Proactive heartbeat: digest + nudges (issue #22, Phase 3) ───────────
    # Cadence knobs only. The on/off switch is a DB column (heartbeat_state.
    # proactive_enabled) rather than an env var, because it is a user-facing
    # preference toggled in Settings — not a deploy-time concern.
    #
    # The digest fires once per UTC day at or after this hour. UTC (not a local
    # timezone) because the app stores no timezone preference; documented, not
    # solved, and the same approximation get_analytics makes for its day windows.
    proactive_digest_hour: int = _hour_env("PROACTIVE_DIGEST_HOUR", 8)
    # How often the nudge sweep may run. Nudges are additionally rate-limited
    # per record by proactive_nudge_cooldown_days, so this only bounds the sweep.
    proactive_nudge_interval_minutes: int = _positive_int_env("PROACTIVE_NUDGE_INTERVAL_MINUTES", 240)
    # Don't mention the same record/kind again inside this many days.
    proactive_nudge_cooldown_days: int = _positive_int_env("PROACTIVE_NUDGE_COOLDOWN_DAYS", 7)
    # Hard cap per sweep so a long-neglected CRM produces a nudge, not a firehose.
    proactive_max_nudges_per_run: int = _positive_int_env("PROACTIVE_MAX_NUDGES_PER_RUN", 3)

    # Web Push (VAPID). Leave blank to auto-generate a keypair once and persist it
    # in Postgres (vapid_keys singleton, private key Fernet-encrypted). Set both
    # to operator-manage the keys via env instead (never persisted then).
    vapid_public_key: str = os.getenv("VAPID_PUBLIC_KEY", "")
    vapid_private_key: str = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_subject: str = os.getenv("VAPID_SUBJECT", "mailto:admin@cakecrm.local")


settings = Settings()
