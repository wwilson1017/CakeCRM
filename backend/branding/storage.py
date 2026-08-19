"""
CakeCRM — Branding storage.

Saves/loads branding config (company name) and logo from data/branding/.
"""

import json
import logging
from pathlib import Path

from core.storage import atomic_write_bytes, atomic_write_json

logger = logging.getLogger(__name__)

BRANDING_DIR = Path(__file__).resolve().parent.parent / "data" / "branding"
CONFIG_FILE = BRANDING_DIR / "config.json"
LOGO_FILE = BRANDING_DIR / "logo.png"

DEFAULT_CONFIG = {
    "company_name": "CakeCRM",
    "has_logo": False,
}


def ensure_dir():
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load branding config. Returns defaults if the config file doesn't exist.

    ``has_logo`` is always derived from the logo file's existence — never from the
    persisted config — because a logo can be uploaded before any config write
    (POST /logo touches only the image file). Deriving it here keeps GET /api/branding
    honest after a reload on a fresh install.

    A stale ``accent_color`` left by an install that predates the fixed theme (#54)
    is stripped on read, so it is never echoed back to a client or re-persisted by
    the next save.
    """
    ensure_dir()
    if not CONFIG_FILE.exists():
        return {**DEFAULT_CONFIG, "has_logo": LOGO_FILE.exists()}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        merged = {**DEFAULT_CONFIG, **data, "has_logo": LOGO_FILE.exists()}
        merged.pop("accent_color", None)  # retired in #54 — the theme is fixed
        return merged
    except Exception as e:
        logger.warning("Failed to load branding config: %s", e)
        return {**DEFAULT_CONFIG, "has_logo": LOGO_FILE.exists()}


def save_config(company_name: str | None = None) -> dict:
    """Update branding config fields. Returns the updated config."""
    ensure_dir()
    current = load_config()
    if company_name is not None:
        current["company_name"] = company_name
    current.pop("has_logo", None)  # derived field, don't persist
    atomic_write_json(CONFIG_FILE, current)
    return load_config()


def save_logo(data: bytes) -> bool:
    """Save logo PNG bytes. Returns True on success."""
    ensure_dir()
    try:
        atomic_write_bytes(LOGO_FILE, data)
        return True
    except Exception as e:
        logger.error("Failed to save logo: %s", e)
        return False


def delete_logo() -> bool:
    """Delete the logo file. Returns True if it existed."""
    if LOGO_FILE.exists():
        LOGO_FILE.unlink()
        return True
    return False


def get_logo_path() -> Path | None:
    """Return the logo file path if it exists, else None."""
    return LOGO_FILE if LOGO_FILE.exists() else None
