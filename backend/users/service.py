"""User accounts: lookup, CRUD, roles and password storage (issue #60 Phase A).

This module owns the credential primitives for a *user row*. ``core.auth`` owns
JWTs, the login route and the request dependencies, and calls in here for anything
that touches a password — so the import direction is one-way (``core.auth`` →
``users.service``) and there is no cycle.

Two invariants are worth stating up front because they are load-bearing elsewhere:

* **Bcrypt only, failing closed.** ``verify_user_password`` requires a ``$2a$``/``$2b$``
  prefix and returns ``False`` for anything else. There is no plaintext comparison
  anywhere. That is what lets #61's importer create a placeholder seat for an
  imported rep with a ``'!'`` sentinel hash: the row preserves attribution and can be
  activated later, but it can never authenticate.
* **Email normalization is one rule expressed twice**, in Python here and in SQL in
  ``20260821100126_multi_user.sql``'s ``uq_users_email_ci``. Both sides use LOWER plus
  a trim of the same six ASCII whitespace bytes. Keep them in lockstep — see
  ``_WS``/``SQL_EMAIL``.
"""

import logging

import bcrypt as _bcrypt

from core.postgres import get_connection, pg_fetchall, pg_fetchone, row_to_dict

logger = logging.getLogger(__name__)

ROLES = ("admin", "member")

# The six ASCII whitespace bytes (space, tab, LF, CR, FF, VT) — the same fixed set
# the CRM uses for company names. Deliberately NOT Python's Unicode-aware
# str.strip(): SQL btrim() strips these bytes only, so using str.strip() here would
# let a pasted NBSP normalize differently on the two sides. cake_os shipped exactly
# that bug, where an NBSP in an email made owner filters silently match zero rows.
_WS = " \t\n\r\f\v"

# The SQL half of the same rule. Any query matching on email must use this fragment
# so it can use uq_users_email_ci rather than scanning.
SQL_EMAIL = "LOWER(btrim(email, E' \\t\\n\\r\\f\\x0b'))"

# Columns safe to return over the API. password_hash is never among them.
_PUBLIC_COLUMNS = "id, email, name, role, is_active, created_at, updated_at"


def normalize_email(raw: str) -> str:
    """Normalize an email for comparison. Must agree with SQL_EMAIL byte for byte."""
    return (raw or "").strip(_WS).lower()


# ── Password primitives ──────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def verify_user_password(plain: str, stored_hash: str | None) -> bool:
    """Verify a plaintext password against a stored bcrypt hash.

    Fails closed on anything that is not a bcrypt hash — None, empty, the ``'!'``
    placeholder sentinel, or a corrupt value. There is deliberately no fallback to a
    plaintext comparison: the pre-#60 code had one for the AUTH_PASSWORD bootstrap
    value, and carrying it into a per-user world would mean a row whose hash got
    mangled silently accepts its own mangled text as the password.
    """
    if not stored_hash or not stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        return False
    try:
        return _bcrypt.checkpw(plain.encode(), stored_hash.encode())
    except ValueError:
        # Malformed salt/hash — treat as a failed check rather than a 500.
        return False


# A real bcrypt hash of a value nobody can supply, used to spend roughly the same
# time on an unknown email as on a known one. Computed once at import.
_DUMMY_HASH = hash_password("cakecrm-timing-equalizer")


def spend_dummy_verify() -> None:
    """Burn one bcrypt verification, so an unknown email costs what a known one does."""
    verify_user_password("x", _DUMMY_HASH)


# ── Lookup ───────────────────────────────────────────────────────────────────

def get_user(user_id: int) -> dict | None:
    """Fetch one user by id, including the password hash (internal callers only)."""
    return pg_fetchone(
        f"SELECT {_PUBLIC_COLUMNS}, password_hash, token_epoch FROM users WHERE id = %s",
        (user_id,),
    )


def get_user_by_email(email: str) -> dict | None:
    """Fetch one user by normalized email, including the password hash."""
    return pg_fetchone(
        f"""SELECT {_PUBLIC_COLUMNS}, password_hash, token_epoch
              FROM users WHERE {SQL_EMAIL} = %s""",
        (normalize_email(email),),
    )


def list_users(include_inactive: bool = True) -> list[dict]:
    """All users, newest role first then name. Never returns password hashes.

    Readable by ANY authenticated user, not just admins: owner dropdowns, the owner
    facet and the per-rep analytics table all need to turn an owner_id into a name,
    and hiding the roster would only mean rendering bare numeric ids.
    """
    where = "" if include_inactive else " WHERE is_active"
    return pg_fetchall(
        f"SELECT {_PUBLIC_COLUMNS} FROM users{where} ORDER BY is_active DESC, name, email"
    )


def earliest_admin_id() -> int | None:
    """The lowest-id admin — the install's de-facto owner, or None on a bare database.

    The single definition of "who owns what predates seats" (issue #191): migration M1
    claims legacy conversations with the identical ``MIN(id) WHERE role='admin'``
    expression, and Telegram stamps its seatless conversation with this until B4 (#193)
    gives that thread a real per-seat owner. Returns None when no admin exists yet —
    a fresh database mid-migration — which every caller treats as "unowned".
    """
    row = pg_fetchone("SELECT MIN(id) AS id FROM users WHERE role = 'admin'")
    return int(row["id"]) if row and row.get("id") is not None else None


def user_count() -> int:
    row = pg_fetchone("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


def public_view(user: dict) -> dict:
    """Strip the credential columns off a row before it crosses the API boundary."""
    return {k: v for k, v in user.items() if k not in ("password_hash", "token_epoch")}


# ── Writes ───────────────────────────────────────────────────────────────────

class UserError(Exception):
    """A user-management rule was violated. The message is safe to show the caller."""


def create_user(email: str, name: str, password: str, role: str = "member") -> dict:
    """Create a user. Raises UserError on a duplicate email or an unknown role."""
    email = (email or "").strip(_WS)
    if not email or "@" not in email:
        raise UserError("A valid email address is required.")
    if role not in ROLES:
        raise UserError(f"Role must be one of: {', '.join(ROLES)}.")

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""INSERT INTO users (email, name, password_hash, role)
                    VALUES (%s, %s, %s, %s)
                    RETURNING {_PUBLIC_COLUMNS}""",
                (email, (name or "").strip(), hash_password(password), role),
            )
            return row_to_dict(cur, cur.fetchone())
    except Exception as exc:  # noqa: BLE001 — re-raised as a domain error below
        # The unique index is the authority on duplicates, not a pre-check: a
        # check-then-insert races two concurrent creates onto the same address.
        if "uq_users_email_ci" in str(exc):
            raise UserError("A user with that email already exists.") from exc
        raise


def update_user(
    user_id: int,
    *,
    name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> dict:
    """Update a user's profile fields, guarding the last-admin invariant.

    The install must always retain at least one ACTIVE admin, or nobody can manage
    users, AI keys or integrations ever again — a self-inflicted lockout with no
    in-app way out. Demoting and deactivating are both routes to it, so the guard
    covers both, and the whole check-then-write runs in one transaction with the
    admin rows locked FOR UPDATE: two concurrent requests each demoting a different
    one of the last two admins would otherwise both see "one other admin remains".
    """
    if role is not None and role not in ROLES:
        raise UserError(f"Role must be one of: {', '.join(ROLES)}.")

    sets: list[str] = []
    params: list = []
    if name is not None:
        sets.append("name = %s")
        params.append(name.strip())
    if role is not None:
        sets.append("role = %s")
        params.append(role)
    if is_active is not None:
        sets.append("is_active = %s")
        params.append(is_active)
    if not sets:
        existing = get_user(user_id)
        if not existing:
            raise UserError("User not found.")
        return public_view(existing)
    sets.append("updated_at = now()")

    losing_admin = (role is not None and role != "admin") or is_active is False

    with get_connection() as conn:
        cur = conn.cursor()
        # Lock every active admin row, not just this user's: the invariant is about
        # the SET of admins, so the count and the write must see a stable set.
        # ORDER BY id is load-bearing, not cosmetic — two concurrent demotions that
        # grab the same rows in different orders deadlock, and Postgres gives no
        # ordering guarantee for FOR UPDATE without it.
        cur.execute(
            "SELECT id FROM users WHERE role = 'admin' AND is_active ORDER BY id FOR UPDATE"
        )
        admin_ids = [r[0] for r in cur.fetchall()]

        if losing_admin and user_id in admin_ids and len(admin_ids) <= 1:
            raise UserError(
                "This is the only active admin. Promote another user to admin first."
            )

        cur.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE id = %s RETURNING {_PUBLIC_COLUMNS}",
            (*params, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise UserError("User not found.")
        updated = row_to_dict(cur, row)

        # A deactivated user must lose their live sessions immediately, or they keep
        # working until their JWT expires. Bumping the epoch is exactly the lever
        # #78 built for password changes; deactivation needs the same one.
        if is_active is False:
            cur.execute(
                "UPDATE users SET token_epoch = token_epoch + 1 WHERE id = %s", (user_id,)
            )
        return updated


def set_password_as_admin(
    user_id: int, new_plain: str, clear_two_factor: bool = False
) -> bool:
    """Admin resets another user's password. No current-password check by design.

    Self-hosted CakeCRM has no mail infrastructure, so an email-link reset is not
    implementable and none is faked — an admin doing it in Settings is the reset
    story (gate decision on #60). Bumping token_epoch ends that user's other
    sessions, which is the point when the reset is because their account was
    compromised.

    ``clear_two_factor`` also disables that user's TOTP and drops their trusted
    devices. It is opt-in rather than automatic because it is a real reduction in
    that person's account security — but without it there is NO recovery path for a
    member who lost both their authenticator and their backup codes: a new password
    alone still leaves them stuck at the second factor. Same reasoning as
    ``core.auth.apply_password_reset_env``, which does this unconditionally for the
    operator's admin rescue.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE users
                  SET password_hash = %s, token_epoch = token_epoch + 1, updated_at = now()
                WHERE id = %s
            RETURNING id""",
            (hash_password(new_plain), user_id),
        )
        if cur.fetchone() is None:
            return False
        if clear_two_factor:
            cur.execute(
                """UPDATE totp_config
                      SET enabled = FALSE, secret_enc = '', backup_codes = '[]',
                          last_used_at = '', updated_at = now()
                    WHERE user_id = %s""",
                (user_id,),
            )
            cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))
        return True


def change_own_password(user_id: int, current_plain: str, new_plain: str) -> int | None:
    """Verify the current password, store a new one, and drop this user's trusted
    devices — all in ONE transaction.

    Returns the NEW token epoch, or None when the current password doesn't match.

    The device wipe belongs in here rather than in the caller: as a second
    transaction, a failure between them returns 500 to a caller whose old JWT is
    already dead, with no replacement token and with the device revocation the docs
    promise not actually done.

    Returning the epoch is not a convenience — it closes a race. The caller mints a
    replacement token for the acting session, and if it re-read the epoch afterwards
    it could pick up a *later* value written by a concurrent change, handing out a
    token that outlives the password it was issued against. The epoch that comes back
    from this statement is the one this password belongs to.

    The row is locked across the check and the write so two concurrent changes cannot
    both validate against the same old credential — the shape #78 used for the
    singleton, re-keyed per user.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM users WHERE id = %s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row or not verify_user_password(current_plain, row[0]):
            return None
        cur.execute(
            """UPDATE users
                  SET password_hash = %s, token_epoch = token_epoch + 1, updated_at = now()
                WHERE id = %s
            RETURNING token_epoch""",
            (hash_password(new_plain), user_id),
        )
        epoch = int(cur.fetchone()[0])
        # Scoped to this user: revoking the whole install's trusted devices because
        # one person rotated their password would be a team-wide surprise.
        cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))
        return epoch


def bump_token_epoch(user_id: int) -> int:
    """Invalidate every existing session for one user. Returns the new epoch."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET token_epoch = token_epoch + 1 WHERE id = %s RETURNING token_epoch",
            (user_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


# ── Analytics support ────────────────────────────────────────────────────────

def users_by_id() -> dict[int, dict]:
    """Map of id → public user row, for labelling owner/actor columns in reports."""
    return {int(u["id"]): u for u in list_users()}


def display_name(user: dict | None) -> str:
    """The label to show for a user row: their name, falling back to their email."""
    if not user:
        return "Unassigned"
    return (user.get("name") or "").strip() or user.get("email") or f"User {user.get('id')}"


__all__ = [
    "ROLES",
    "SQL_EMAIL",
    "UserError",
    "bump_token_epoch",
    "change_own_password",
    "create_user",
    "display_name",
    "earliest_admin_id",
    "get_user",
    "get_user_by_email",
    "hash_password",
    "list_users",
    "normalize_email",
    "public_view",
    "set_password_as_admin",
    "spend_dummy_verify",
    "update_user",
    "user_count",
    "users_by_id",
    "verify_user_password",
]
