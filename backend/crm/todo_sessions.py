"""Todo GTD — browser sessions and one-time sign-in codes for the no-login app (#293).

Port of todo-gtd `b55cacb` (sessions.py + signin.py) by way of cake_os #3764
(`web_session_service.py`), keyed by the seat (`users.id`) that made the code.

The phone app at `/todo` used to be a bearer URL. It is now a session surface: a
browser cookie names a session, and the only way a browser gets that cookie is a
one-hour, single-use sign-in code made in Settings by a signed-in seat. This module
owns both stores; `crm/todo_web.py` owns the HTTP rules.

What is stored
  * `todo_web_sessions` — one row per signed-in browser. The cookie holds a random
    token; the table holds only its SHA-256 hash, the seat, and two timestamps.
  * `todo_web_signin_tokens` — unspent sign-in codes, hashes only.
  `crm_meta.todo_web_token` (the old link) is no longer read; its drop is a follow-up.

Lifetimes
  * The cookie is a fixed year from sign-in. Only a sign-in sets it and nothing clears
    it: signing out deletes the row, because a stale cookie is harmless while a
    response that cleared it could erase a sign-in another tab had just finished.
  * The server-side idle year slides: `last_used_at` moves on use; a row idle a year
    is dead.
  * simplification: hour-granular touch — most requests take no write; a finer
    `last_used_at` would need a write per request.

A session belongs to its seat: every lookup joins `users.is_active`, so deactivating
the seat ends its sessions and its unspent codes on the next request. The session
does NOT act as its seat inside the todo app, which keeps the install-wide view.

Caps and cleanup (no scheduled job): at most 20 sessions per seat (one more evicts
the least recently used) and 10 unspent codes per seat. Dead rows are ignored by
every read and deleted for that seat whenever a session or a code is made.

Concurrency (nothing here leans on the single worker):
  * Single use is the atomic `DELETE … WHERE token_hash = %s RETURNING …`: two
    simultaneous redemptions of one code yield exactly one session.
  * Every multi-row write for one seat (make a code, redeem one, sign out
    everywhere) first takes `pg_advisory_xact_lock(2930, user_id)`, which is what
    makes the two caps exact. Lock order, the same for every writer: seat lock, THEN
    row locks. Redemption reads the seat with a plain SELECT so it can take the seat
    lock before its DELETE. Assumes READ COMMITTED (Postgres' default).

Every public function takes a keyword-only `now` so tests run expiry and caps on an
explicit clock. Raw cursors throughout: `pg_fetchone` turns datetimes into strings.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from core.config import settings
from core.postgres import get_connection

MAX_SESSIONS_PER_SEAT = 20
MAX_UNSPENT_CODES_PER_SEAT = 10
SESSION_LIFETIME = timedelta(days=365)  # the cookie's Max-Age AND the idle cutoff
TOUCH_INTERVAL = timedelta(hours=1)
SIGNIN_TTL = timedelta(hours=1)

_COOKIE_BASE = "cakecrm_todo_session"
_TOKEN_BYTES = 32  # 256 bits
_LOCK_CLASSID = 2930  # mirrored in the registry comment in core/postgres.py


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def cookie_secure() -> bool:
    """`Secure` whenever this install's configured address is https (Railway sets it).
    A plain-http LAN install would never store a Secure cookie, so it gets none."""
    return settings.frontend_url.startswith("https://")


def cookie_name() -> str:
    """The ONE place the cookie's name is decided. `__Host-` over https: the browser
    then accepts it only with Secure, Path=/ and no Domain, so a sibling subdomain can
    never plant one. Plain over http, where the prefix would make the browser drop it."""
    return f"__Host-{_COOKIE_BASE}" if cookie_secure() else _COOKIE_BASE


def hash_token(token: str) -> str:
    """sha256 hex — the only form of either token ever stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def signin_path(token: str) -> str:
    """The one definition of a sign-in link's path."""
    return f"/todo/signin/{token}"


def _lock_seat(cur, user_id: int) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_CLASSID, user_id))


def _sweep_dead_cur(cur, user_id: int, now: datetime) -> None:
    """Delete this seat's dead rows of both kinds. Caller holds the seat lock."""
    cur.execute(
        "DELETE FROM todo_web_sessions WHERE user_id = %s AND last_used_at < %s",
        (user_id, now - SESSION_LIFETIME),
    )
    cur.execute(
        "DELETE FROM todo_web_signin_tokens WHERE user_id = %s AND expires_at <= %s",
        (user_id, now),
    )


def issue_signin_token(user_id: int, *, now: datetime | None = None) -> tuple[str, datetime]:
    """Make a sign-in code for `user_id`. Returns `(plaintext, expires_at)`.

    The plaintext is returned once and never stored. It rotates nothing: every
    session and every other unspent code keeps working.
    """
    now = now or _utcnow()
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = now + SIGNIN_TTL
    with get_connection() as conn:
        cur = conn.cursor()
        _lock_seat(cur, user_id)
        _sweep_dead_cur(cur, user_id, now)
        # Prune to one under the cap BEFORE the insert, so the new row is never the
        # one dropped. Exact only because of the seat lock.
        cur.execute(
            "DELETE FROM todo_web_signin_tokens WHERE id IN ("
            " SELECT id FROM todo_web_signin_tokens WHERE user_id = %s"
            " ORDER BY expires_at DESC, id DESC OFFSET %s)",
            (user_id, MAX_UNSPENT_CODES_PER_SEAT - 1),
        )
        cur.execute(
            "INSERT INTO todo_web_signin_tokens (token_hash, user_id, expires_at) "
            "VALUES (%s, %s, %s)",
            (hash_token(token), user_id, expires_at),
        )
    return token, expires_at


def _create_session_cur(cur, user_id: int, now: datetime) -> str:
    """Insert one session and return its PLAINTEXT token. Caller holds the seat lock.
    The prune keeps the 19 most recently used, so the new row is never the evicted one."""
    _sweep_dead_cur(cur, user_id, now)
    cur.execute(
        "DELETE FROM todo_web_sessions WHERE id IN ("
        " SELECT id FROM todo_web_sessions WHERE user_id = %s"
        " ORDER BY last_used_at DESC, id DESC OFFSET %s)",
        (user_id, MAX_SESSIONS_PER_SEAT - 1),
    )
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    cur.execute(
        "INSERT INTO todo_web_sessions (token_hash, user_id, created_at, last_used_at) "
        "VALUES (%s, %s, %s, %s)",
        (hash_token(token), user_id, now, now),
    )
    return token


def redeem_signin_token(
    token: str, *, replaces: str | None = None, now: datetime | None = None
) -> dict | None:
    """Spend a sign-in code and create a session, in ONE transaction.

    `replaces` is the session token the redeeming browser already holds, if any. Its
    cookie is about to be overwritten, so that row is ended here too — but only when
    it belongs to the same seat: deleting another seat's row would take a row lock
    outside this seat's lock, which two browsers swapping seats could deadlock on. A
    cross-seat switch leaves the old row to sign-out-everywhere, the cap or the year.

    Returns `{"user_id", "session_token"}`, or None for a wrong, spent or expired code
    (callers answer all three the same). An expired code is deleted on the way out.
    """
    now = now or _utcnow()
    token_hash = hash_token(token or "")
    with get_connection() as conn:
        cur = conn.cursor()
        # A plain read: a guess takes no lock, and the seat lock can come first.
        cur.execute(
            "SELECT t.user_id FROM todo_web_signin_tokens t "
            "JOIN users u ON u.id = t.user_id WHERE t.token_hash = %s AND u.is_active",
            (token_hash,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        _lock_seat(cur, row[0])
        # THE single-use step: whoever's DELETE returns the row has the code.
        cur.execute(
            "DELETE FROM todo_web_signin_tokens WHERE token_hash = %s "
            "RETURNING user_id, expires_at",
            (token_hash,),
        )
        spent = cur.fetchone()
        if spent is None:
            return None  # another redemption, or sign-out-everywhere, got there first
        user_id, expires_at = spent
        if expires_at <= now:
            return None
        if replaces:
            cur.execute(
                "DELETE FROM todo_web_sessions WHERE token_hash = %s AND user_id = %s",
                (hash_token(replaces), user_id),
            )
        return {"user_id": user_id, "session_token": _create_session_cur(cur, user_id, now)}


def resolve_session(token: str | None, *, now: datetime | None = None) -> dict | None:
    """The live session a cookie value names, or None. Read-only.

    Returns `{"token_hash", "user_id", "last_used_at"}`. Idle a year, or a seat that
    is deactivated, reads as None even before anything deletes the row.
    """
    if not token:
        return None
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT s.token_hash, s.user_id, s.last_used_at FROM todo_web_sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.last_used_at >= %s AND u.is_active",
            (hash_token(token), now - SESSION_LIFETIME),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"token_hash": row[0], "user_id": row[1], "last_used_at": row[2]}


def touch_session(token_hash: str, last_used_at: datetime, *, now: datetime | None = None) -> bool:
    """Slide `last_used_at` — at most one write an hour per device. The Python check
    skips the round trip; the WHERE stops a delayed request moving it BACKWARD."""
    now = now or _utcnow()
    threshold = now - TOUCH_INTERVAL
    if last_used_at >= threshold:
        return False
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE todo_web_sessions SET last_used_at = %s "
            "WHERE token_hash = %s AND last_used_at < %s",
            (now, token_hash, threshold),
        )
        return cur.rowcount == 1


def revoke_session(token: str | None) -> None:
    """Sign ONE browser out. A missing or unknown token is a no-op."""
    if not token:
        return
    with get_connection() as conn:
        conn.cursor().execute(
            "DELETE FROM todo_web_sessions WHERE token_hash = %s", (hash_token(token),)
        )


def revoke_all(user_id: int) -> None:
    """Sign every browser of one seat out and cancel its unspent codes."""
    with get_connection() as conn:
        cur = conn.cursor()
        _lock_seat(cur, user_id)
        cur.execute("DELETE FROM todo_web_sessions WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM todo_web_signin_tokens WHERE user_id = %s", (user_id,))


def count_sessions(user_id: int, *, now: datetime | None = None) -> int:
    """How many browsers this seat has signed in (dead rows excluded)."""
    now = now or _utcnow()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM todo_web_sessions WHERE user_id = %s AND last_used_at >= %s",
            (user_id, now - SESSION_LIFETIME),
        )
        return cur.fetchone()[0]
