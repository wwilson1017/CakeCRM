"""First-run seeding of the admin account (issue #60 Phase A).

Runs once, in the lifespan, right after ``run_migrations()``. One function handles
three situations that look different but are the same problem — "this install has no
users yet, give it one" — which is why they share a code path rather than being
special-cased:

* a fresh local ``python run.py``,
* a fresh Railway deploy,
* **an existing single-user install with live data**, which is the one that can go
  badly wrong. Its owner may long ago have changed their password in-app, at which
  point #78 made ``AUTH_PASSWORD`` inert — so that env var is NOT their password and
  seeding from it would lock them out of their own CRM. The live credential is the
  bcrypt hash in ``auth_credential``, and the bootstrap carries it across verbatim.
  Their 2FA keeps working, their trusted devices keep working, and every record they
  own becomes theirs.

Everything below happens in ONE transaction under an advisory lock, so two workers
booting at the same moment cannot both seed, and a crash halfway cannot leave a
half-claimed 2FA config.
"""

import logging

from core.config import settings
from core.postgres import get_connection

from . import service

logger = logging.getLogger(__name__)

# Advisory lock key — registered in core/postgres.py's key list.
_BOOTSTRAP_LOCK = 1901

# Tables that carry an owner_id and, on an upgrade, should be attributed to the
# person who has been the only user until now.
_OWNED_TABLES = ("contacts", "companies", "deals", "tasks")


DEFAULT_ADMIN_EMAIL = "admin@cakecrm.local"


def _bootstrap_email() -> str:
    """The address to seed, guaranteed to be one the login form will actually submit.

    ``create_user`` validates this for every account made through the API, but the
    bootstrap INSERTs directly, so nothing checked it here. That gap is worse than it
    sounds: seeding a malformed value like ``ADMIN_EMAIL=admin`` produces an account
    nobody can sign in to, because LoginPage's ``type="email"`` input refuses to
    submit it — and the seeding never runs again, since ``users`` is no longer empty.
    Correcting the env var afterwards does nothing. A fresh install would need manual
    database surgery to recover.

    So a value that isn't usable is replaced with the documented default and logged,
    rather than refusing to boot (which would strand a deploy over a typo) or seeding
    it anyway.
    """
    raw = settings.auth.admin_email.strip()
    if not raw:
        return DEFAULT_ADMIN_EMAIL
    # The same shape the login form enforces: something before an @, something after,
    # and no whitespace anywhere in it.
    local, _, domain = raw.partition("@")
    if local and domain and not any(c.isspace() for c in raw):
        return raw
    logger.error(
        "ADMIN_EMAIL=%r is not a usable email address, so the admin account was "
        "created as %s instead. The login form will not submit a malformed address, "
        "and this seeding runs only once — fixing the variable later has no effect.",
        raw,
        DEFAULT_ADMIN_EMAIL,
    )
    return DEFAULT_ADMIN_EMAIL


def ensure_bootstrap_admin() -> dict | None:
    """Seed the first admin when ``users`` is empty. Returns the row, or None if skipped."""
    email = _bootstrap_email()
    name = settings.auth.admin_name.strip() or "Admin"

    weak_password = False
    with get_connection() as conn:
        cur = conn.cursor()
        # Serialize against a concurrent worker booting the same install.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_BOOTSTRAP_LOCK,))

        cur.execute("SELECT COUNT(*) FROM users")
        if int(cur.fetchone()[0]) > 0:
            return None

        # Carry the live credential from the pre-multi-user singleton. A NULL hash
        # means the user never changed their password, so AUTH_PASSWORD is still
        # genuinely their password and is the right seed.
        cur.execute("SELECT to_regclass('public.auth_credential')")
        carried_hash, carried_epoch = None, 0
        if cur.fetchone()[0] is not None:
            cur.execute("SELECT password_hash, token_epoch FROM auth_credential WHERE id = 1")
            row = cur.fetchone()
            if row:
                carried_hash, carried_epoch = row[0], int(row[1] or 0)

        if carried_hash:
            password_hash = carried_hash
        else:
            bootstrap_password = settings.auth.password
            weak_password = bootstrap_password in ("", "changeme")
            # Already-hashed AUTH_PASSWORD values are supported (the .env.example
            # comment has always allowed a bcrypt string), so don't double-hash.
            password_hash = (
                bootstrap_password
                if bootstrap_password.startswith(("$2a$", "$2b$", "$2y$"))
                else service.hash_password(bootstrap_password or "changeme")
            )

        cur.execute(
            """INSERT INTO users (email, name, password_hash, role, is_active, token_epoch)
               VALUES (%s, %s, %s, 'admin', TRUE, %s)
               RETURNING id, email, name, role, is_active, created_at, updated_at""",
            (email, name, password_hash, carried_epoch),
        )
        admin_row = cur.fetchone()
        admin_id = admin_row[0]

        # Claim the legacy 2FA state. Until this runs the rows are invisible (every
        # auth_2fa read filters on user_id and NULL never matches), which is the safe
        # direction — but it also means an install with 2FA enabled would silently
        # have it switched off, so the claim belongs in the same transaction as the
        # INSERT above rather than in a later step that could be skipped.
        cur.execute("UPDATE totp_config SET user_id = %s WHERE user_id IS NULL", (admin_id,))
        cur.execute(
            "UPDATE trusted_devices SET user_id = %s WHERE user_id IS NULL", (admin_id,)
        )

        # Attribute existing records. Ownership is safe to backfill: on a single-user
        # install these records really were this person's.
        #
        # actor_id / author_id are deliberately NOT backfilled. We cannot tell which
        # historical activity the person performed and which the assistant performed
        # on their behalf, and Phase A's stated rule is that it may undercount
        # assistant-delegated work but must never misattribute it. Historical rows
        # therefore roll up as "Unattributed" in per-rep analytics.
        for table in _OWNED_TABLES:
            cur.execute(
                f"UPDATE {table} SET owner_id = %s WHERE owner_id IS NULL",  # noqa: S608 — fixed literals
                (admin_id,),
            )

        # auth_credential is left EXACTLY as it is — deliberately not cleared.
        #
        # What that buys, precisely: the pre-#60 code reads a NULL hash as permission
        # to fall back to AUTH_PASSWORD (core.auth.verify_password, before this
        # issue). Clearing the hash would therefore let an old process accept a
        # password the user had already replaced. Leaving it means the old code, if it
        # ever runs again, still checks the credential the user actually chose.
        #
        # What it does NOT buy — and the release notes say so — is a reversible
        # upgrade. This migration re-keys totp_config by user_id and drops its `id`
        # column, so the pre-#60 2FA reads (WHERE id = 1) fail against the new schema.
        # An old binary running after this migration cannot complete a login for an
        # account with 2FA enabled. Rolling back means restoring a pre-upgrade dump,
        # which is what the README's upgrade note tells operators to take. CakeCRM
        # runs single-process (gunicorn --workers 1), so there is no mixed-version
        # window to design around; a compatibility shim would be carrying real
        # complexity for a deployment topology this product does not have.
        #
        # The table is vestigial from here on and cannot be dropped in this PR's
        # migration — migrations all run BEFORE this bootstrap, which is the one
        # thing that still needs to read it. A later release drops it.

        seeded = {
            "id": admin_id,
            "email": admin_row[1],
            "name": admin_row[2],
            "role": admin_row[3],
        }

    logger.warning(
        "Created the first CakeCRM admin account: %s. Sign in with this email address "
        "%s.",
        seeded["email"],
        "and your existing password" if carried_hash else "and AUTH_PASSWORD",
    )

    if weak_password:
        # An alert rather than a refusal: refusing to boot would strand a fresh
        # Railway deploy whose operator simply hasn't set the variable yet, and
        # login still requires knowing the email address.
        logger.warning(
            "The admin account was created with the default bootstrap password. "
            "Change it from Settings immediately."
        )
        try:
            from alerts import service as alerts_service

            alerts_service.create_alert(
                title="Default admin password in use",
                message=(
                    f"The admin account {seeded['email']} was created with the default "
                    "bootstrap password because AUTH_PASSWORD was unset or left as "
                    "'changeme'. Change it from Settings → Change password."
                ),
                source="bootstrap",
                source_id="weak-admin-password",
            )
        except Exception:
            # A missing alert must never stop the app booting.
            logger.exception("Could not raise the weak-password alert")

    return seeded
