"""Real-Postgres integration for the todo app's browser sessions (#293).

The hermetic suites pin the transaction shape; these prove what only a real database can:
a code redeems once even when two redemptions race, the caps are exact, idle sessions and
expired codes die, and deactivating a seat ends its devices. Same throwaway-database
pattern as the sibling integration suites.
"""

import os
import threading
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from crm import todo_sessions as ts

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_todo_sessions_{os.getpid()}"
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()

    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{dbname}"
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = dsn
    postgres.close_pool()
    postgres.init_pool()
    postgres.run_migrations()
    yield dsn

    postgres.close_pool()
    if prev is not None:
        os.environ["DATABASE_URL"] = prev
    else:
        os.environ.pop("DATABASE_URL", None)
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.fixture
def seats(pg_db):
    """Two fresh member seats; the session tables start empty."""
    from core.postgres import pg_execute
    from users import service as users

    pg_execute("TRUNCATE todo_web_sessions, todo_web_signin_tokens")
    tag = os.urandom(4).hex()
    a = users.create_user(f"a-{tag}@cakecrm.test", "Seat A", "pw-long-enough-1")
    b = users.create_user(f"b-{tag}@cakecrm.test", "Seat B", "pw-long-enough-2")
    return a["id"], b["id"]


def _sign_in(user_id: int, now=NOW) -> str:
    code, _ = ts.issue_signin_token(user_id, now=now)
    return ts.redeem_signin_token(code, now=now)["session_token"]


def _rows(sql: str, params=()) -> list:
    from core.postgres import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def test_a_code_signs_a_browser_in_and_stores_only_hashes(seats):
    a, _ = seats
    code, expires = ts.issue_signin_token(a, now=NOW)
    assert expires == NOW + ts.SIGNIN_TTL
    out = ts.redeem_signin_token(code, now=NOW)
    assert out["user_id"] == a
    session = ts.resolve_session(out["session_token"], now=NOW)
    assert session["user_id"] == a
    stored = [r[0] for r in _rows("SELECT token_hash FROM todo_web_sessions")]
    assert stored == [ts.hash_token(out["session_token"])]
    assert _rows("SELECT 1 FROM todo_web_signin_tokens") == []  # spent


def test_a_code_redeems_once(seats):
    a, _ = seats
    code, _ = ts.issue_signin_token(a, now=NOW)
    assert ts.redeem_signin_token(code, now=NOW)
    assert ts.redeem_signin_token(code, now=NOW) is None


def test_an_expired_or_made_up_code_is_refused_and_leaves_no_row(seats):
    a, _ = seats
    code, _ = ts.issue_signin_token(a, now=NOW)
    assert ts.redeem_signin_token(code, now=NOW + ts.SIGNIN_TTL) is None
    assert ts.redeem_signin_token("made-up", now=NOW) is None
    assert _rows("SELECT 1 FROM todo_web_signin_tokens") == []
    assert _rows("SELECT 1 FROM todo_web_sessions") == []


def test_two_simultaneous_redemptions_make_one_session(seats):
    a, _ = seats
    code, _ = ts.issue_signin_token(a, now=NOW)
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(ts.redeem_signin_token(code, now=NOW))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sorted(r is None for r in results) == [False, True]
    assert ts.count_sessions(a, now=NOW) == 1


def test_a_second_device_leaves_the_first_signed_in(seats):
    a, _ = seats
    phone = _sign_in(a)
    laptop = _sign_in(a)
    assert ts.resolve_session(phone, now=NOW) and ts.resolve_session(laptop, now=NOW)
    assert ts.count_sessions(a, now=NOW) == 2


def test_signing_in_again_replaces_this_browsers_old_row_only_within_the_seat(seats):
    a, b = seats
    old = _sign_in(a)
    code, _ = ts.issue_signin_token(a, now=NOW)
    ts.redeem_signin_token(code, replaces=old, now=NOW)
    assert ts.resolve_session(old, now=NOW) is None
    assert ts.count_sessions(a, now=NOW) == 1
    # Switching this browser to another seat leaves the first seat's row alone.
    code_b, _ = ts.issue_signin_token(b, now=NOW)
    other = _sign_in(a)
    ts.redeem_signin_token(code_b, replaces=other, now=NOW)
    assert ts.resolve_session(other, now=NOW)


def test_sign_out_ends_one_device_and_sign_out_everywhere_ends_all_and_cancels_codes(seats):
    a, b = seats
    phone, laptop, theirs = _sign_in(a), _sign_in(a), _sign_in(b)
    ts.revoke_session(phone)
    assert ts.resolve_session(phone, now=NOW) is None
    assert ts.resolve_session(laptop, now=NOW)
    unspent, _ = ts.issue_signin_token(a, now=NOW)
    ts.revoke_all(a)
    assert ts.resolve_session(laptop, now=NOW) is None
    assert ts.redeem_signin_token(unspent, now=NOW) is None
    assert ts.resolve_session(theirs, now=NOW), "another seat's devices are untouched"


def test_deactivating_a_seat_ends_its_sessions_and_codes(seats):
    from users import service as users

    a, b = seats
    session = _sign_in(a)
    code, _ = ts.issue_signin_token(a, now=NOW)
    theirs = _sign_in(b)
    users.update_user(a, is_active=False)
    assert ts.resolve_session(session, now=NOW) is None
    assert ts.redeem_signin_token(code, now=NOW) is None
    assert ts.resolve_session(theirs, now=NOW)
    # Reactivating the seat does not bring the old device or code back.
    users.update_user(a, is_active=True)
    assert ts.resolve_session(session, now=NOW) is None
    assert ts.redeem_signin_token(code, now=NOW) is None
    assert _rows("SELECT 1 FROM todo_web_sessions WHERE user_id = %s", (a,)) == []


def test_the_21st_session_evicts_the_least_recently_used(seats):
    a, _ = seats
    tokens = [_sign_in(a, now=NOW + timedelta(minutes=i)) for i in range(ts.MAX_SESSIONS_PER_SEAT)]
    newest = _sign_in(a, now=NOW + timedelta(hours=2))
    assert ts.count_sessions(a, now=NOW + timedelta(hours=2)) == ts.MAX_SESSIONS_PER_SEAT
    assert ts.resolve_session(tokens[0], now=NOW + timedelta(hours=2)) is None
    assert ts.resolve_session(tokens[1], now=NOW + timedelta(hours=2))
    assert ts.resolve_session(newest, now=NOW + timedelta(hours=2))


def test_unspent_codes_are_capped_at_ten_keeping_the_newest(seats):
    a, _ = seats
    codes = [ts.issue_signin_token(a, now=NOW + timedelta(seconds=i))[0]
             for i in range(ts.MAX_UNSPENT_CODES_PER_SEAT + 1)]
    assert len(_rows("SELECT 1 FROM todo_web_signin_tokens WHERE user_id = %s", (a,))) == 10
    assert ts.redeem_signin_token(codes[0], now=NOW + timedelta(minutes=1)) is None
    assert ts.redeem_signin_token(codes[-1], now=NOW + timedelta(minutes=1))


def test_two_redemptions_at_nineteen_sessions_leave_exactly_twenty(seats):
    a, _ = seats
    for i in range(ts.MAX_SESSIONS_PER_SEAT - 1):
        _sign_in(a, now=NOW + timedelta(seconds=i))
    later = NOW + timedelta(minutes=5)
    codes = [ts.issue_signin_token(a, now=later)[0] for _ in range(2)]
    barrier = threading.Barrier(2)

    def go(code):
        barrier.wait()
        ts.redeem_signin_token(code, now=later)

    threads = [threading.Thread(target=go, args=(c,)) for c in codes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert ts.count_sessions(a, now=later) == ts.MAX_SESSIONS_PER_SEAT


def test_an_idle_year_ends_a_session_and_the_next_sign_in_sweeps_it(seats):
    a, _ = seats
    old = _sign_in(a)
    a_year_on = NOW + ts.SESSION_LIFETIME + timedelta(seconds=1)
    assert ts.resolve_session(old, now=a_year_on) is None
    _sign_in(a, now=a_year_on)
    assert len(_rows("SELECT 1 FROM todo_web_sessions WHERE user_id = %s", (a,))) == 1


def test_touch_is_at_most_hourly_and_never_backward(seats):
    a, _ = seats
    token = _sign_in(a)
    s = ts.resolve_session(token, now=NOW)
    assert ts.touch_session(s["token_hash"], s["last_used_at"], now=NOW + timedelta(minutes=30)) is False
    assert ts.touch_session(s["token_hash"], s["last_used_at"], now=NOW + timedelta(hours=2)) is True
    # A delayed request carrying the older reading cannot move it back.
    assert ts.touch_session(s["token_hash"], s["last_used_at"], now=NOW + timedelta(hours=1, minutes=30)) is False
    assert ts.resolve_session(token, now=NOW)["last_used_at"] == NOW + timedelta(hours=2)


def test_a_redemption_racing_sign_out_everywhere_never_deadlocks(seats):
    a, _ = seats
    for i in range(5):
        code, _ = ts.issue_signin_token(a, now=NOW)
        errors = []
        barrier = threading.Barrier(2)

        def redeem():
            barrier.wait()
            try:
                ts.redeem_signin_token(code, now=NOW)
            except Exception as e:  # a deadlock surfaces as psycopg2's error here
                errors.append(e)

        def revoke():
            barrier.wait()
            try:
                ts.revoke_all(a)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=redeem), threading.Thread(target=revoke)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []


def test_a_crm_reset_leaves_the_devices_signed_in(seats):
    """Auth state, like saved_views: outside both _truncate_all branches."""
    from crm import service

    a, _ = seats
    token = _sign_in(a)
    service.clear_all()
    assert ts.resolve_session(token, now=NOW)
