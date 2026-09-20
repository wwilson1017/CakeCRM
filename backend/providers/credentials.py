"""
CakeCRM — Credential store for AI provider profiles.

Single-tenant, admin-global storage backed by PostgreSQL (chatty kept this in
data/auth-profiles.json; CakeCRM is Postgres-mandatory). Two tables:

  * ai_providers   — one row per provider; per-field columns, api_key Fernet-
                     encrypted in api_key_enc (never plaintext).
  * ai_settings    — singleton (id=1) row for active_provider / active_model.

In-memory shape (self.data) mirrors chatty's so the get_ai_provider factory reads
work unchanged:

    {
        "active_provider": "anthropic" | "openai" | "google" | "ollama" | "together",
        "active_model": "claude-opus-4-8",
        "profiles": {
            "anthropic:default": {"type": "api_key", "key": "<plaintext>"},
            "ollama:default":    {"type": "ollama_local", "base_url": "http://localhost:11434"},
            ...
        }
    }

Row-shape note: inside get_connection() a raw conn.cursor() returns TUPLES, so
reads index by position (row[0]). Only key-based auth and Ollama's keyless local
mode exist — all OAuth/subscription paths were excised for CakeCRM.
"""

import logging

from core.encryption import decrypt_value, encrypt_value
from core.postgres import get_connection, pg_execute

logger = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "google", "ollama", "together")

# Last-resort fallback when no model is specified. _resolved_default_model()
# (below) prefers the inferred "top" tier from ai_model_tiers when available.
PROVIDER_DEFAULTS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.4",
    "google": "gemini-2.5-flash",
    "ollama": "",
    "together": "Qwen/Qwen3.5-7B",
}


def _resolved_default_model(provider: str) -> str:
    """Default model when none is specified: the inferred/resolved 'top' tier if
    materialized, else the hardcoded PROVIDER_DEFAULTS. Keeps fresh connections
    pointed at a current model even outside the connect-flow inference path.

    Reads the tiers table — callers MUST resolve this BEFORE opening a write
    transaction, never while holding a pooled connection (avoids a nested
    checkout that could stall a small/exhausted pool)."""
    try:
        from providers.model_tiers import get_resolved
        top = get_resolved(provider).get("top")
        if top:
            return top
    except Exception:
        pass
    return PROVIDER_DEFAULTS.get(provider, "")


class CredentialStore:
    def __init__(self):
        # True when the load below hit a database error and fell back to the empty
        # shape. Reads still never raise — this only lets a caller that CARES about the
        # difference tell "nobody has configured a provider" from "we could not look"
        # (issue #200's setup-status read, whose contract is that a null field means
        # unknown and never "off"). Every existing caller ignores it, unchanged.
        self.load_failed = False
        self.data = self._load()

    def _load(self) -> dict:
        """Load active settings + provider profiles under a single REPEATABLE READ
        snapshot so a concurrent connect/disconnect can't split the two SELECTs
        (Postgres' default READ COMMITTED gives each statement its own snapshot).
        On ANY DB error, log, set ``load_failed`` and return the empty shape (store
        reads never raise).
        Each row is decoded defensively so one malformed/undecryptable row can't
        sink the load (decrypt_value already returns "" on tamper/key-mismatch,
        which the factory then treats as unconfigured)."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                # First statement of the transaction — sets the snapshot for both
                # reads; resets when the connection returns to the pool.
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cur.execute(
                    "SELECT active_provider, active_model FROM ai_settings WHERE id = 1"
                )
                settings_row = cur.fetchone()
                cur.execute(
                    "SELECT provider, auth_type, api_key_enc, base_url FROM ai_providers"
                )
                provider_rows = cur.fetchall()
        except Exception as e:
            logger.error("Failed to load provider credentials: %s", e)
            self.load_failed = True
            return {"active_provider": "", "active_model": "", "profiles": {}}

        profiles: dict[str, dict] = {}
        for row in provider_rows:
            try:
                provider, auth_type, api_key_enc, base_url = row
                if auth_type == "ollama_local":
                    profiles[f"{provider}:default"] = {
                        "type": "ollama_local",
                        "base_url": base_url,
                    }
                else:
                    profiles[f"{provider}:default"] = {
                        "type": "api_key",
                        "key": decrypt_value(api_key_enc),
                    }
            except Exception as e:  # never log ciphertext/key material
                logger.error("Skipping malformed ai_providers row: %s", e)
                continue

        return {
            "active_provider": (settings_row[0] if settings_row else "") or "",
            "active_model": (settings_row[1] if settings_row else "") or "",
            "profiles": profiles,
        }

    # -- settings helper -----------------------------------------------------

    def _upsert_settings(self, cur, provider: str, model: str) -> None:
        cur.execute(
            """
            INSERT INTO ai_settings (id, active_provider, active_model)
            VALUES (1, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET active_provider = EXCLUDED.active_provider,
                active_model = EXCLUDED.active_model,
                updated_at = now()
            """,
            (provider, model),
        )

    # -- reads ---------------------------------------------------------------

    def get_active_profile(self, provider_override: str | None = None) -> tuple[str, dict | None]:
        """Return (profile_name, profile_dict) for the active (or overridden) provider."""
        provider = provider_override or self.data.get("active_provider", "")
        if not provider:
            return ("", None)
        profile_name = f"{provider}:default"
        profile = self.data.get("profiles", {}).get(profile_name)
        return (profile_name, profile)

    def is_provider_usable(self, provider: str) -> bool:
        """True if this provider's stored profile has USABLE credentials — a
        non-empty key for key providers, a non-empty base_url for Ollama. This is
        the gate the factory and the status endpoint rely on (an empty-key row or
        empty Ollama URL is NOT usable)."""
        p = self.data.get("profiles", {}).get(f"{provider}:default")
        if not p:
            return False
        if p.get("type") == "api_key":
            return bool(p.get("key"))
        if p.get("type") == "ollama_local":
            return bool(p.get("base_url"))
        return False

    def is_configured(self) -> bool:
        """True if at least one provider has usable credentials."""
        return any(self.is_provider_usable(p) for p in PROVIDERS)

    def configured_providers(self) -> list[str]:
        """Names of providers with usable credentials."""
        return [p for p in PROVIDERS if self.is_provider_usable(p)]

    def get_api_key(self, provider: str) -> str | None:
        """The single key-extraction seam for downstream callers (the assistant
        engine in issue #3+). Returns the plaintext API key for a key-based
        provider, or None if not configured / not a key provider."""
        p = self.data.get("profiles", {}).get(f"{provider}:default")
        if p and p.get("type") == "api_key" and p.get("key"):
            return p["key"]
        return None

    # -- writes --------------------------------------------------------------

    def set_api_key(self, provider: str, key: str, model: str | None = None) -> None:
        """Store an API key for the given provider and set it as active."""
        resolved_model = model or _resolved_default_model(provider)  # before txn (no nested checkout)
        enc = encrypt_value(key)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO ai_providers (provider, auth_type, api_key_enc, base_url)
                VALUES (%s, 'api_key', %s, '')
                ON CONFLICT (provider) DO UPDATE
                SET auth_type = 'api_key', api_key_enc = EXCLUDED.api_key_enc,
                    base_url = '', updated_at = now()
                """,
                (provider, enc),
            )
            self._upsert_settings(cur, provider, resolved_model)
        self.data.setdefault("profiles", {})[f"{provider}:default"] = {
            "type": "api_key",
            "key": key,
        }
        self.data["active_provider"] = provider
        self.data["active_model"] = resolved_model

    def set_ollama(self, base_url: str, model: str | None = None) -> None:
        """Store Ollama connection info and set as active (no default-model
        resolution — Ollama's model is chosen from its installed set)."""
        resolved_model = model or ""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO ai_providers (provider, auth_type, api_key_enc, base_url)
                VALUES ('ollama', 'ollama_local', '', %s)
                ON CONFLICT (provider) DO UPDATE
                SET auth_type = 'ollama_local', api_key_enc = '',
                    base_url = EXCLUDED.base_url, updated_at = now()
                """,
                (base_url,),
            )
            self._upsert_settings(cur, "ollama", resolved_model)
        self.data.setdefault("profiles", {})["ollama:default"] = {
            "type": "ollama_local",
            "base_url": base_url,
        }
        self.data["active_provider"] = "ollama"
        self.data["active_model"] = resolved_model

    def set_active(self, provider: str, model: str) -> str:
        """Atomically set the active provider AND model in ONE transaction. An
        empty/"default" model resolves to the provider's top-tier default. Returns
        the PERSISTED model so the caller reports what was actually stored."""
        resolved_model = model if (model and model != "default") else _resolved_default_model(provider)
        with get_connection() as conn:
            cur = conn.cursor()
            self._upsert_settings(cur, provider, resolved_model)
        self.data["active_provider"] = provider
        self.data["active_model"] = resolved_model
        return resolved_model

    def set_active_model(self, model: str) -> None:
        """Update only the active model (empty/"default" → provider top-tier)."""
        if model and model != "default":
            resolved_model = model
        else:
            resolved_model = _resolved_default_model(self.data.get("active_provider", ""))
        pg_execute(
            "UPDATE ai_settings SET active_model = %s, updated_at = now() WHERE id = 1",
            (resolved_model,),
        )
        self.data["active_model"] = resolved_model

    def set_active_provider(self, provider: str) -> None:
        """Switch active provider, resolving a model that belongs to THAT provider.

        Never inherit the previous provider's active_model — the factory attributes
        active_model to active_provider, so keeping e.g. a Claude id while switching
        to Together would mis-point Together at an Anthropic model. Prefer set_active()
        when the caller already knows the model.
        """
        new_model = _resolved_default_model(provider)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ai_settings SET active_provider = %s, active_model = %s, "
                "updated_at = now() WHERE id = 1",
                (provider, new_model),
            )
        self.data["active_provider"] = provider
        self.data["active_model"] = new_model

    def remove_provider(self, provider: str) -> None:
        """Remove credentials for a provider; clear active atomically iff it was active."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM ai_providers WHERE provider = %s", (provider,))
            cur.execute(
                "UPDATE ai_settings SET active_provider = '', active_model = '', "
                "updated_at = now() WHERE id = 1 AND active_provider = %s",
                (provider,),
            )
        self.data.get("profiles", {}).pop(f"{provider}:default", None)
        if self.data.get("active_provider") == provider:
            self.data["active_provider"] = ""
            self.data["active_model"] = ""

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict:
        """Sanitized summary (no raw keys) for the frontend, keyed by bare provider."""
        profiles: dict[str, dict] = {}
        for name, p in self.data.get("profiles", {}).items():
            provider = name.split(":")[0]
            if p.get("type") == "api_key":
                key = p.get("key", "")
                profiles[provider] = {
                    "type": "api_key",
                    "configured": bool(key),
                    "key_preview": f"...{key[-4:]}" if len(key) > 4 else "",
                }
            elif p.get("type") == "ollama_local":
                profiles[provider] = {
                    "type": "ollama_local",
                    "configured": bool(p.get("base_url")),
                    "base_url": p.get("base_url", "http://localhost:11434"),
                }

        return {
            "active_provider": self.data.get("active_provider", ""),
            "active_model": self.data.get("active_model", ""),
            "profiles": profiles,
        }
