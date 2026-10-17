"""Real-Postgres integration for connected AI tools (#322).

The hermetic suites drive the protocol with the store faked; these prove what only a
real database can: tokens are stored as hashes and resolve to their seat, every way a
connection dies (expiry, epoch bump, deactivation, the members switch, 90 idle days)
holds in the one lookup, the cap is exact under concurrency, codes and refresh tokens
are single use even when two racers try, the sweep prunes what can never be used, and a
CRM reset clears the call log but disconnects nobody.
"""

import os
import threading
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from mcp_endpoint import store

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
TABLES = ("mcp_audit, mcp_credentials, mcp_connections, mcp_oauth_codes, "
          "mcp_oauth_requests, mcp_oauth_clients")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_mcp_{os.getpid()}"
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
    """A fresh member and admin; the MCP tables start empty and the switch is on."""
    from core.postgres import pg_execute
    from users import service as users

    pg_execute(f"TRUNCATE {TABLES}")
    store.set_members_enabled(True)
    tag = os.urandom(4).hex()
    member = users.create_user(f"m-{tag}@cakecrm.test", "Member", "pw-long-enough-1")
    admin = users.create_user(f"a-{tag}@cakecrm.test", "Admin", "pw-long-enough-2", role="admin")
    return member["id"], admin["id"]


def _one(sql: str, params=()):
    from core.postgres import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()


def _token(user_id, now=NOW, **kw):
    kw.setdefault("name", "Claude Code")
    kw.setdefault("read_only", False)
    kw.setdefault("lifetime_days", 90)
    return store.create_personal_token(user_id, now=now, **kw)


def _oauth(user_id, *, now=NOW, read_only=False, scopes="crm:read crm:write offline_access"):
    rid = os.urandom(8).hex()
    store.put_client("client-1", "dcr", {"client_id": "client-1", "client_name": "ChatGPT"}, now=now)
    store.save_request(rid, "client-1", {"redirect_uri": "https://chatgpt.com/cb", "state": "s"}, now=now)
    request, code = store.approve_request(rid, approve=True, user_id=user_id, client_name="ChatGPT",
                                          read_only=read_only, scopes=scopes, now=now)
    return store.exchange_code(store.hash_token(code), now=now)


def test_the_migration_creates_the_tables_and_the_switch_defaults_off(pg_db):
    for table in TABLES.split(", "):
        assert _one("SELECT to_regclass(%s)", (table,))[0] == table
    assert _one("SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'crm_meta' AND column_name = 'mcp_members_enabled'")[0] == "false"
    check = _one("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'todos_source_check'")
    assert "'mcp'" in check[0]


def test_a_personal_token_is_stored_hashed_and_resolves_to_its_seat(seats):
    member, _ = seats
    token, row = _token(member)
    assert _one("SELECT count(*) FROM mcp_credentials WHERE token_hash = %s", (token,))[0] == 0
    assert _one("SELECT count(*) FROM mcp_credentials WHERE token_hash = %s",
                (store.hash_token(token),))[0] == 1
    conn = store.resolve_bearer(token, now=NOW)
    assert conn["id"] == row["id"] and conn["seat"]["id"] == member and conn["seat"]["role"] == "member"
    assert store.resolve_bearer("not-a-token", now=NOW) is None


def test_an_expired_token_an_epoch_bump_and_a_deactivation_each_kill_a_connection(seats):
    from users import service as users

    member, admin = seats
    token, _ = _token(member, lifetime_days=30)
    assert store.resolve_bearer(token, now=NOW + timedelta(days=29))
    assert store.resolve_bearer(token, now=NOW + timedelta(days=30)) is None
    never, _ = _token(member, lifetime_days=None)
    assert store.resolve_bearer(never, now=NOW + timedelta(days=3650))
    users.set_password_as_admin(member, "a-new-long-password")
    assert store.resolve_bearer(never, now=NOW) is None
    again, _ = _token(member, lifetime_days=None)
    users.update_user(member, is_active=False)
    assert store.resolve_bearer(again, now=NOW) is None


def test_members_switch_off_kills_a_member_connection_but_not_an_admins(seats):
    member, admin = seats
    m, _ = _token(member)
    a, _ = _token(admin)
    store.set_members_enabled(False)
    assert store.resolve_bearer(m, now=NOW) is None
    assert store.resolve_bearer(a, now=NOW)
    store.set_members_enabled(True)
    assert store.resolve_bearer(m, now=NOW), "a toggle must not delete the connection"


def test_the_connection_cap_is_exact_under_concurrent_creates(seats):
    member, _ = seats
    for _ in range(store.MAX_CONNECTIONS_PER_SEAT - 1):
        _token(member)
    barrier = threading.Barrier(2)
    outcomes = []

    def go():
        barrier.wait()
        try:
            _token(member)
            outcomes.append("ok")
        except store.CapReached:
            outcomes.append("cap")

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["cap", "ok"]


def test_dead_connections_do_not_count_toward_the_cap(seats):
    member, _ = seats
    for _ in range(store.MAX_CONNECTIONS_PER_SEAT):
        _token(member, lifetime_days=30)
    later = NOW + timedelta(days=31)
    _token(member, now=later)  # every earlier token is expired: swept, not counted


def test_a_code_exchanges_once_even_when_two_exchanges_race(seats):
    member, _ = seats
    store.put_client("client-1", "dcr", {"client_id": "client-1"}, now=NOW)
    store.save_request("r1", "client-1", {"redirect_uri": "https://x.example/cb"}, now=NOW)
    _, code = store.approve_request("r1", approve=True, user_id=member, client_name="ChatGPT",
                                    read_only=False, scopes="crm:read crm:write", now=NOW)
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(store.exchange_code(store.hash_token(code), now=NOW))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1
    assert _one("SELECT count(*) FROM mcp_connections WHERE kind = 'oauth'")[0] == 1


def test_a_consent_request_is_answered_once(seats):
    member, _ = seats
    store.save_request("r2", "client-1", {"redirect_uri": "https://x.example/cb"}, now=NOW)
    assert store.approve_request("r2", approve=False, user_id=member, client_name="x",
                                 read_only=True, scopes="crm:read", now=NOW)[1] is None
    assert store.approve_request("r2", approve=True, user_id=member, client_name="x",
                                 read_only=True, scopes="crm:read", now=NOW) is None


def test_a_code_approved_before_a_password_change_cannot_be_redeemed_after(seats):
    from users import service as users

    member, admin = seats
    store.save_request("r3", "client-1", {"redirect_uri": "https://x.example/cb"}, now=NOW)
    _, code = store.approve_request("r3", approve=True, user_id=member, client_name="x",
                                    read_only=False, scopes="crm:read crm:write", now=NOW)
    users.set_password_as_admin(member, "another-long-password")
    assert store.exchange_code(store.hash_token(code), now=NOW) is None


def test_refresh_rotation_invalidates_the_old_pair_and_narrowing_makes_it_read_only(seats):
    member, _ = seats
    first = _oauth(member)
    assert store.resolve_bearer(first["access_token"], now=NOW)
    second = store.rotate_refresh(store.hash_token(first["refresh_token"]), ["crm:read"], now=NOW)
    assert second and store.resolve_bearer(first["access_token"], now=NOW) is None
    assert store.rotate_refresh(store.hash_token(first["refresh_token"]), ["crm:read"], now=NOW) is None
    assert store.resolve_bearer(second["access_token"], now=NOW)["read_only"] is True


def test_two_refreshes_racing_leave_exactly_one_live_pair(seats):
    member, _ = seats
    first = _oauth(member)
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(store.rotate_refresh(store.hash_token(first["refresh_token"]),
                                            ["crm:read", "crm:write"], now=NOW))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1
    assert _one("SELECT count(*) FROM mcp_credentials WHERE kind = 'refresh'")[0] == 1


def test_a_refresh_on_a_dead_connection_fails_without_spending_the_token(seats):
    member, _ = seats
    first = _oauth(member)
    store.set_members_enabled(False)
    assert store.rotate_refresh(store.hash_token(first["refresh_token"]), ["crm:read"], now=NOW) is None
    store.set_members_enabled(True)
    assert store.rotate_refresh(store.hash_token(first["refresh_token"]), ["crm:read"], now=NOW)


def test_oauth_idle_ninety_days_kills_the_connection_and_the_sweep_deletes_it(seats):
    member, _ = seats
    first = _oauth(member)
    later = NOW + timedelta(days=91)
    assert store.load_refresh(store.hash_token(first["refresh_token"]), now=later) is None
    assert store.rotate_refresh(store.hash_token(first["refresh_token"]), ["crm:read"], now=later) is None
    assert store.sweep(now=later)["connections"] == 1


def test_touch_is_at_most_hourly_and_never_backward(seats):
    member, _ = seats
    _, row = _token(member)
    assert store.touch(row["id"], NOW, now=NOW + timedelta(minutes=30)) is False
    assert store.touch(row["id"], NOW, now=NOW + timedelta(hours=2)) is True
    assert store.touch(row["id"], NOW, now=NOW + timedelta(hours=1, minutes=30)) is False


def test_revoke_deletes_credentials_but_keeps_audit_rows(seats):
    member, _ = seats
    token, row = _token(member)
    store.record_call(row["id"], member, "crm_find_contact", "3 rows", True, None, 12, now=NOW)
    assert store.revoke(row["id"], user_id=member + 1000) is False
    assert store.revoke(row["id"], user_id=member) is True
    assert store.resolve_bearer(token, now=NOW) is None
    assert _one("SELECT count(*) FROM mcp_audit WHERE connection_id = %s", (row["id"],))[0] == 1


def test_list_calls_is_scoped_to_the_seat(seats):
    member, admin = seats
    _, row = _token(member)
    store.record_call(row["id"], member, "crm_find_contact", "", True, None, 5, now=NOW)
    assert len(store.list_calls(row["id"], user_id=member)) == 1
    assert store.list_calls(row["id"], user_id=admin) == []
    assert len(store.list_calls(row["id"])) == 1


def test_list_connections_shows_live_ones_newest_first(seats):
    member, admin = seats
    _token(member, name="older")
    _token(member, name="newer", now=NOW + timedelta(minutes=1))
    _token(admin, name="admin's")
    assert [c["name"] for c in store.list_connections(user_id=member, now=NOW)] == ["newer", "older"]
    assert len(store.list_connections(now=NOW)) == 3


def test_sweep_prunes_old_audit_expired_requests_codes_and_unused_clients(seats):
    member, _ = seats
    _, row = _token(member)
    store.record_call(row["id"], member, "t", "", True, None, 1, now=NOW - timedelta(days=91))
    store.record_call(row["id"], member, "t", "", True, None, 1, now=NOW)
    store.put_client("stale", "dcr", {"client_id": "stale"}, now=NOW - timedelta(days=8))
    store.save_request("old", "stale", {}, now=NOW - timedelta(hours=1))
    out = store.sweep(now=NOW)
    assert out["audit"] == 1 and out["requests"] == 1 and out["clients"] == 1


def test_a_crm_reset_keeps_connections_and_clears_the_audit(seats):
    from core.postgres import get_connection
    from crm import service

    member, _ = seats
    token, row = _token(member)
    store.record_call(row["id"], member, "t", "", True, None, 1, now=NOW)
    for include_definitions in (False, True):
        with get_connection() as conn:
            service._truncate_all(conn.cursor(), include_definitions=include_definitions)
        assert store.resolve_bearer(token, now=NOW)
        assert _one("SELECT count(*) FROM mcp_audit")[0] == 0


def test_a_todo_may_carry_the_mcp_source(seats):
    from core.postgres import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO todos (title, source, created_at, updated_at) "
                    "VALUES ('x', 'mcp', now(), now()) RETURNING source")
        assert cur.fetchone()[0] == "mcp"
