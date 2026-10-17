"""Connect your own AI (#322) — every SQL statement behind `/mcp` and its OAuth server.

What is stored (migration `*_mcp_connections.sql`)
  * `mcp_connections` — the thing a person sees, names and revokes: one personal token,
    or one OAuth sign-in from one client. It carries the seat, read-only flag, the seat's
    `token_epoch` when it was made, and its timestamps. No secret.
  * `mcp_credentials` — every bearer secret as a SHA-256 hash: personal tokens, OAuth
    access tokens (one hour) and refresh tokens (rotated on every use).
  * `mcp_oauth_clients` — dynamic registrations (public clients, never a secret) and
    cached client-ID metadata documents.
  * `mcp_oauth_requests` / `mcp_oauth_codes` — a parked /authorize request (ten minutes)
    and an approved, unexchanged code (five minutes, hash only). Both single use.
  * `mcp_audit` — one row per tool call. Never arguments, never results.

The one lookup. `/mcp` resolves a bearer with ONE query (`resolve_bearer`) and decides
with ONE predicate (`_judge`), re-run on every request: the seat is active, the
connection's epoch still equals the seat's (a password change, admin reset or
deactivation bumps it), the token has not expired, an OAuth connection has been used in
the last 90 days, and a member's connection is dead while the admin switch is off.
Refusals all return None, so the HTTP layer has one 401 shape and no oracle. Nothing is
cached across requests, so a revoke bites on the very next call.

Concurrency. Single use is `DELETE … RETURNING` (codes, consent requests, refresh
tokens): two racers yield exactly one winner. Per-seat multi-row writes (create a token,
exchange a code) take `pg_advisory_xact_lock(3220, user_id)` first, which makes the cap
exact; lock order is seat lock, THEN row locks. Refresh rotation re-judges the connection
inside its own transaction and rolls back (the refresh token survives) when it is dead,
so a check made before the write is never the authority for it.

Every function takes a keyword-only `now` where time matters; raw cursors throughout
(`pg_fetchone` would turn datetimes into strings).
"""

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from core.postgres import get_connection
from crm.todo_sessions import hash_token

logger = logging.getLogger(__name__)

MAX_CONNECTIONS_PER_SEAT = 25
ACCESS_TOKEN_LIFETIME = timedelta(hours=1)
OAUTH_IDLE_CUTOFF = timedelta(days=90)
TOUCH_INTERVAL = timedelta(hours=1)
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)
AUDIT_RETENTION = timedelta(days=90)
UNUSED_CLIENT_GRACE = timedelta(days=7)
LIFETIME_DAYS = (30, 90, 365)  # a personal token's choices; None means never

_TOKEN_BYTES = 32  # 256 bits
_LOCK_CLASSID = 3220  # mirrored in the registry comment in core/postgres.py

__all__ = [
    "CapReached", "hash_token", "members_enabled", "set_members_enabled",
    "create_personal_token", "resolve_bearer", "touch", "list_connections", "revoke",
    "record_call", "list_calls", "get_client", "put_client", "save_request",
    "delete_client", "load_request", "approve_request", "load_code", "exchange_code", "load_refresh",
    "rotate_refresh", "load_access", "revoke_by_credential", "sweep",
]


class CapReached(Exception):
    """The seat already has MAX_CONNECTIONS_PER_SEAT connections."""


class _Dead(Exception):
    """Raised inside a transaction to roll it back when the connection is dead."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _row(cur) -> dict | None:
    rows = _rows(cur)
    return rows[0] if rows else None


def _lock_seat(cur, user_id: int) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_CLASSID, user_id))


# The columns `_judge` reads, selected from mcp_connections c JOIN users u CROSS JOIN crm_meta m.
_JUDGED_COLUMNS = """
    c.id, c.user_id, c.kind, c.name, c.client_id, c.read_only, c.token_epoch,
    c.expires_at, c.last_used_at, c.created_at,
    u.email, u.name AS user_name, u.role, u.is_active, u.token_epoch AS seat_epoch,
    m.mcp_members_enabled"""

# The same rules as `_judge`, for listing and sweeping in SQL. Params: (now, now - idle).
_LIVE_SQL = """
    u.is_active AND c.token_epoch = u.token_epoch
    AND (c.expires_at IS NULL OR c.expires_at > %s)
    AND (c.kind <> 'oauth' OR c.last_used_at >= %s)"""


def _judge(row: dict, *, now: datetime) -> dict | None:
    """THE refusal predicate: the connection `row` may act right now, or None.

    `row` carries `_JUDGED_COLUMNS` (plus `credential_expires_at` when a credential was
    looked up). Returns the connection with a `seat` dict shaped like the
    `get_current_user` row, which is what `ToolRegistry(user=…)` expects.
    """
    if not row.get("is_active"):
        return None
    if row["token_epoch"] != row["seat_epoch"]:
        return None
    cred_exp = row.get("credential_expires_at")
    if cred_exp is not None and cred_exp <= now:
        return None
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return None
    if row["kind"] == "oauth" and row["last_used_at"] < now - OAUTH_IDLE_CUTOFF:
        return None
    if row["role"] != "admin" and not row["mcp_members_enabled"]:
        return None
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "kind": row["kind"],
        "name": row["name"],
        "client_id": row["client_id"],
        "read_only": bool(row["read_only"]),
        "last_used_at": row["last_used_at"],
        "seat": {
            "id": row["user_id"],
            "email": row["email"],
            "name": row["user_name"],
            "role": row["role"],
            "is_active": True,
        },
    }


# ── The admin switch ─────────────────────────────────────────────────────────

def members_enabled() -> bool:
    """Whether members may connect their own AI tools. Fail-safe: off on any error."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT mcp_members_enabled FROM crm_meta WHERE id = 1")
            row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        logger.exception("mcp: could not read the members switch; treating it as off")
        return False


def set_members_enabled(flag: bool) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE crm_meta SET mcp_members_enabled = %s, updated_at = %s WHERE id = 1",
            (bool(flag), _utcnow()),
        )
    return bool(flag)


# ── Connections ──────────────────────────────────────────────────────────────

def _sweep_seat_cur(cur, user_id: int, now: datetime) -> None:
    """Delete this seat's dead connections, so the cap counts only live ones. Caller
    holds the seat lock."""
    cur.execute(
        f"""DELETE FROM mcp_connections c USING users u
             WHERE c.user_id = %s AND u.id = c.user_id AND NOT ({_LIVE_SQL})""",
        (user_id, now, now - OAUTH_IDLE_CUTOFF),
    )


def _insert_connection_cur(cur, *, user_id, kind, name, client_id, read_only, token_epoch,
                           expires_at, now) -> int:
    _sweep_seat_cur(cur, user_id, now)
    cur.execute("SELECT count(*) FROM mcp_connections WHERE user_id = %s", (user_id,))
    if cur.fetchone()[0] >= MAX_CONNECTIONS_PER_SEAT:
        raise CapReached()
    cur.execute(
        """INSERT INTO mcp_connections
               (user_id, kind, name, client_id, read_only, token_epoch, created_at,
                last_used_at, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, kind, name, client_id, read_only, token_epoch, now, now, expires_at),
    )
    return cur.fetchone()[0]


def _insert_credential_cur(cur, connection_id: int, kind: str, *, scopes: str = "",
                           expires_at: datetime | None = None) -> str:
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    cur.execute(
        """INSERT INTO mcp_credentials (connection_id, kind, token_hash, scopes, expires_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (connection_id, kind, hash_token(token), scopes, expires_at),
    )
    return token


def create_personal_token(user_id: int, *, name: str, read_only: bool,
                          lifetime_days: int | None, now: datetime | None = None
                          ) -> tuple[str, dict]:
    """Mint a personal token for `user_id`. Returns `(plaintext, connection)`.

    The plaintext exists only in this return value; the table holds its hash.
    Raises CapReached at the cap and ValueError for an inactive seat or a bad lifetime.
    """
    now = now or _utcnow()
    if lifetime_days is not None and lifetime_days not in LIFETIME_DAYS:
        raise ValueError("lifetime_days must be 30, 90, 365 or None")
    expires_at = now + timedelta(days=lifetime_days) if lifetime_days else None
    with get_connection() as conn:
        cur = conn.cursor()
        _lock_seat(cur, user_id)
        cur.execute("SELECT token_epoch, is_active FROM users WHERE id = %s", (user_id,))
        seat = cur.fetchone()
        if seat is None or not seat[1]:
            raise ValueError("seat is not active")
        connection_id = _insert_connection_cur(
            cur, user_id=user_id, kind="token", name=name, client_id=None,
            read_only=read_only, token_epoch=seat[0], expires_at=expires_at, now=now,
        )
        token = _insert_credential_cur(cur, connection_id, "personal")
    return token, {
        "id": connection_id, "kind": "token", "name": name, "client_id": None,
        "read_only": read_only, "created_at": now, "last_used_at": now,
        "expires_at": expires_at,
    }


def resolve_bearer(token: str, *, now: datetime | None = None) -> dict | None:
    """The connection a personal token or OAuth access token may act as, or None."""
    now = now or _utcnow()
    if not token:
        return None
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT {_JUDGED_COLUMNS}, k.expires_at AS credential_expires_at
                  FROM mcp_credentials k
                  JOIN mcp_connections c ON c.id = k.connection_id
                  JOIN users u ON u.id = c.user_id
                  CROSS JOIN crm_meta m
                 WHERE k.token_hash = %s AND k.kind IN ('personal', 'access') AND m.id = 1""",
            (hash_token(token),),
        )
        row = _row(cur)
    return _judge(row, now=now) if row else None


def touch(connection_id: int, last_used_at: datetime, *, now: datetime | None = None) -> bool:
    """Move `last_used_at` forward, at most once an hour and never backward."""
    now = now or _utcnow()
    if last_used_at >= now - TOUCH_INTERVAL:
        return False
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE mcp_connections SET last_used_at = %s WHERE id = %s AND last_used_at < %s",
            (now, connection_id, now - TOUCH_INTERVAL),
        )
        return cur.rowcount == 1


def list_connections(*, user_id: int | None = None, now: datetime | None = None) -> list[dict]:
    """Live connections, newest first: one seat's, or every seat's when user_id is None."""
    now = now or _utcnow()
    seat_filter = "AND c.user_id = %s" if user_id is not None else ""
    params: tuple = (now, now - OAUTH_IDLE_CUTOFF) + ((user_id,) if user_id is not None else ())
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT c.id, c.user_id, u.name AS user_name, u.email AS user_email, c.kind,
                       c.name, c.client_id, c.read_only, c.created_at, c.last_used_at,
                       c.expires_at
                  FROM mcp_connections c JOIN users u ON u.id = c.user_id
                 WHERE {_LIVE_SQL} {seat_filter}
                 ORDER BY c.created_at DESC, c.id DESC""",
            params,
        )
        return _rows(cur)


def revoke(connection_id: int, *, user_id: int | None = None) -> bool:
    """Delete a connection (its credentials cascade; its audit rows stay). Scoped to
    `user_id` unless None (an admin). True if a row went."""
    with get_connection() as conn:
        cur = conn.cursor()
        if user_id is None:
            cur.execute("DELETE FROM mcp_connections WHERE id = %s", (connection_id,))
        else:
            cur.execute(
                "DELETE FROM mcp_connections WHERE id = %s AND user_id = %s",
                (connection_id, user_id),
            )
        return cur.rowcount == 1


# ── Audit ────────────────────────────────────────────────────────────────────

def record_call(connection_id: int, user_id: int, tool: str, detail: str, ok: bool,
                refused: str | None, duration_ms: int, *, now: datetime | None = None) -> None:
    with get_connection() as conn:
        conn.cursor().execute(
            """INSERT INTO mcp_audit
                   (connection_id, user_id, tool, detail, ok, refused, duration_ms, at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (connection_id, user_id, tool[:120], detail[:80], ok, refused, duration_ms,
             now or _utcnow()),
        )


def list_calls(connection_id: int, *, user_id: int | None = None, limit: int = 200) -> list[dict]:
    """A connection's call log, newest first. Scoped to the seat unless user_id is None."""
    seat_filter = "AND user_id = %s" if user_id is not None else ""
    params: tuple = (connection_id,) + ((user_id,) if user_id is not None else ()) + (limit,)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT id, tool, detail, ok, refused, duration_ms, at FROM mcp_audit
                 WHERE connection_id = %s {seat_filter}
                 ORDER BY at DESC, id DESC LIMIT %s""",
            params,
        )
        return _rows(cur)


# ── OAuth clients and the consent hand-off ───────────────────────────────────

def get_client(client_id: str) -> dict | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT client_id, kind, metadata, fetched_at FROM mcp_oauth_clients WHERE client_id = %s",
            (client_id,),
        )
        return _row(cur)


def put_client(client_id: str, kind: str, metadata: dict, *, now: datetime | None = None) -> None:
    now = now or _utcnow()
    with get_connection() as conn:
        conn.cursor().execute(
            """INSERT INTO mcp_oauth_clients (client_id, kind, metadata, created_at, fetched_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (client_id) DO UPDATE
                   SET metadata = EXCLUDED.metadata, fetched_at = EXCLUDED.fetched_at""",
            (client_id, kind, json.dumps(metadata), now, now),
        )


def delete_client(client_id: str) -> None:
    with get_connection() as conn:
        conn.cursor().execute("DELETE FROM mcp_oauth_clients WHERE client_id = %s", (client_id,))


def save_request(rid: str, client_id: str, params: dict, *, now: datetime | None = None) -> None:
    now = now or _utcnow()
    with get_connection() as conn:
        conn.cursor().execute(
            """INSERT INTO mcp_oauth_requests (id, client_id, params, expires_at)
               VALUES (%s, %s, %s, %s)""",
            (rid, client_id, json.dumps(params), now + REQUEST_TTL),
        )


def load_request(rid: str, *, now: datetime | None = None) -> dict | None:
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, client_id, params, expires_at FROM mcp_oauth_requests
                WHERE id = %s AND expires_at > %s""",
            (rid, now),
        )
        return _row(cur)


def approve_request(rid: str, *, approve: bool, user_id: int, client_name: str,
                    read_only: bool, scopes: str, now: datetime | None = None
                    ) -> tuple[dict, str | None] | None:
    """Consume the parked request `rid` and, when approved, mint its code — one txn.

    Returns `(request, code_plaintext or None)`, or None when the request is gone,
    expired, or already answered (deny consumes it too). The code carries the seat's
    epoch now, so a password change before the exchange kills it.
    """
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """DELETE FROM mcp_oauth_requests WHERE id = %s AND expires_at > %s
               RETURNING id, client_id, params""",
            (rid, now),
        )
        request = _row(cur)
        if request is None or not approve:
            return (request, None) if request else None
        cur.execute("SELECT token_epoch FROM users WHERE id = %s AND is_active", (user_id,))
        seat = cur.fetchone()
        if seat is None:
            return None
        code = secrets.token_urlsafe(_TOKEN_BYTES)
        cur.execute(
            """INSERT INTO mcp_oauth_codes
                   (code_hash, client_id, user_id, token_epoch, client_name, read_only,
                    scopes, params, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (hash_token(code), request["client_id"], user_id, seat[0], client_name,
             read_only, scopes, json.dumps(request["params"]), now + CODE_TTL),
        )
    return request, code


def load_code(code_hash: str, *, now: datetime | None = None) -> dict | None:
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT client_id, user_id, scopes, params, expires_at FROM mcp_oauth_codes
                WHERE code_hash = %s AND expires_at > %s""",
            (code_hash, now),
        )
        return _row(cur)


def _token_pair_cur(cur, connection_id: int, scopes: str, now: datetime) -> dict:
    access = _insert_credential_cur(
        cur, connection_id, "access", expires_at=now + ACCESS_TOKEN_LIFETIME,
    )
    refresh = _insert_credential_cur(cur, connection_id, "refresh", scopes=scopes)
    return {
        "connection_id": connection_id,
        "access_token": access,
        "refresh_token": refresh,
        "scopes": scopes.split(),
        "expires_in": int(ACCESS_TOKEN_LIFETIME.total_seconds()),
    }


def exchange_code(code_hash: str, *, now: datetime | None = None) -> dict | None:
    """Redeem a code for a new OAuth connection and its first token pair.

    None when the code is unknown, used, expired, or its seat can no longer connect
    (inactive, epoch moved, member while the switch is off). Raises CapReached.
    """
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM mcp_oauth_codes WHERE code_hash = %s", (code_hash,))
        found = cur.fetchone()
        if found is None:
            return None
        _lock_seat(cur, found[0])
        cur.execute(
            """DELETE FROM mcp_oauth_codes WHERE code_hash = %s
               RETURNING client_id, user_id, token_epoch, client_name, read_only, scopes,
                         expires_at""",
            (code_hash,),
        )
        code = _row(cur)
        if code is None or code["expires_at"] <= now:
            return None
        cur.execute(
            """SELECT u.is_active, u.token_epoch, u.role, m.mcp_members_enabled
                 FROM users u CROSS JOIN crm_meta m WHERE u.id = %s AND m.id = 1""",
            (code["user_id"],),
        )
        seat = cur.fetchone()
        if (seat is None or not seat[0] or seat[1] != code["token_epoch"]
                or (seat[2] != "admin" and not seat[3])):
            return None
        connection_id = _insert_connection_cur(
            cur, user_id=code["user_id"], kind="oauth", name=code["client_name"],
            client_id=code["client_id"], read_only=code["read_only"],
            token_epoch=code["token_epoch"], expires_at=None, now=now,
        )
        return _token_pair_cur(cur, connection_id, code["scopes"], now)


def load_refresh(token_hash: str, *, now: datetime | None = None) -> dict | None:
    """The live connection behind a refresh token, with `scopes`, or None."""
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT {_JUDGED_COLUMNS}, k.scopes
                  FROM mcp_credentials k
                  JOIN mcp_connections c ON c.id = k.connection_id
                  JOIN users u ON u.id = c.user_id
                  CROSS JOIN crm_meta m
                 WHERE k.token_hash = %s AND k.kind = 'refresh' AND m.id = 1""",
            (token_hash,),
        )
        row = _row(cur)
    live = _judge(row, now=now) if row else None
    return {**live, "scopes": row["scopes"].split()} if live else None


def rotate_refresh(token_hash: str, scopes: list[str], *, now: datetime | None = None
                   ) -> dict | None:
    """Spend a refresh token for a new access + refresh pair, re-judging the connection
    inside this transaction. None (and nothing consumed) when the connection is dead;
    None when the token was already spent. Dropping `crm:write` makes the connection
    read-only for good."""
    now = now or _utcnow()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """DELETE FROM mcp_credentials WHERE token_hash = %s AND kind = 'refresh'
                   RETURNING connection_id""",
                (token_hash,),
            )
            spent = cur.fetchone()
            if spent is None:
                return None
            cur.execute(
                f"""SELECT {_JUDGED_COLUMNS}
                      FROM mcp_connections c
                      JOIN users u ON u.id = c.user_id
                      CROSS JOIN crm_meta m
                     WHERE c.id = %s AND m.id = 1
                     FOR UPDATE OF c""",
                (spent[0],),
            )
            row = _row(cur)
            if row is None or _judge(row, now=now) is None:
                raise _Dead()
            cur.execute(
                "DELETE FROM mcp_credentials WHERE connection_id = %s AND kind = 'access'",
                (spent[0],),
            )
            cur.execute(
                """UPDATE mcp_connections
                      SET last_used_at = %s, read_only = read_only OR %s
                    WHERE id = %s""",
                (now, "crm:write" not in scopes, spent[0]),
            )
            return _token_pair_cur(cur, spent[0], " ".join(scopes), now)
    except _Dead:
        return None


def load_access(token_hash: str, *, now: datetime | None = None) -> dict | None:
    """An unexpired access token's connection id and client, for the revoke endpoint."""
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT c.id, c.client_id, c.user_id, k.expires_at
                 FROM mcp_credentials k JOIN mcp_connections c ON c.id = k.connection_id
                WHERE k.token_hash = %s AND k.kind = 'access' AND k.expires_at > %s""",
            (token_hash, now),
        )
        return _row(cur)


def revoke_by_credential(token_hash: str) -> bool:
    """Any token of a grant revokes the whole grant (RFC 7009 lets us)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """DELETE FROM mcp_connections WHERE id =
                   (SELECT connection_id FROM mcp_credentials WHERE token_hash = %s)""",
            (token_hash,),
        )
        return cur.rowcount == 1


# ── Maintenance ──────────────────────────────────────────────────────────────

def sweep(*, now: datetime | None = None) -> dict:
    """Delete what can never be used again. Runs on the heartbeat maintenance tick.
    A member connection dead only because the switch is off is NOT dead: it survives."""
    now = now or _utcnow()
    out: dict[str, int] = {}
    with get_connection() as conn:
        cur = conn.cursor()
        statements = {
            "requests": ("DELETE FROM mcp_oauth_requests WHERE expires_at <= %s", (now,)),
            "codes": ("DELETE FROM mcp_oauth_codes WHERE expires_at <= %s", (now,)),
            "access_tokens": (
                "DELETE FROM mcp_credentials WHERE kind = 'access' AND expires_at <= %s", (now,),
            ),
            "connections": (
                f"""DELETE FROM mcp_connections c USING users u
                     WHERE u.id = c.user_id AND NOT ({_LIVE_SQL})""",
                (now, now - OAUTH_IDLE_CUTOFF),
            ),
            "audit": ("DELETE FROM mcp_audit WHERE at < %s", (now - AUDIT_RETENTION,)),
            "clients": (
                """DELETE FROM mcp_oauth_clients cl
                    WHERE cl.fetched_at < %s
                      AND NOT EXISTS (SELECT 1 FROM mcp_connections c WHERE c.client_id = cl.client_id)
                      AND NOT EXISTS (SELECT 1 FROM mcp_oauth_codes k WHERE k.client_id = cl.client_id)
                      AND NOT EXISTS (SELECT 1 FROM mcp_oauth_requests r WHERE r.client_id = cl.client_id)""",
                (now - UNUSED_CLIENT_GRACE,),
            ),
        }
        for key, (sql, params) in statements.items():
            cur.execute(sql, params)
            out[key] = cur.rowcount
    return out
