"""Real-Postgres integration for chatter @-mentions (issue #235).

What a FakeConn cannot check: that the migration applies, that the FK cascade removes
mention rows when their note (or its contact) goes, that BOTH `_truncate_all` variants
still execute now that crm_chatter has a second referencing table, that only ACTIVE
seats can be newly mentioned, that the frozen name is the server's, and that an edit's
diff tells only the newly added seat — once.

Marked ``integration`` and excluded from the default no-DB run. Fixture data is fictional.
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_mentions_{os.getpid()}"
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


@pytest.fixture(autouse=True)
def _clean(pg_db):
    from core.postgres import get_connection, pg_execute
    from crm import service

    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=True)
    pg_execute("DELETE FROM users")
    yield


def _user(email, name, active=True) -> int:
    from core.postgres import pg_fetchone

    return pg_fetchone(
        "INSERT INTO users (email, name, password_hash, is_active) VALUES (%s, %s, 'x', %s) "
        "RETURNING id",
        (email, name, active),
    )["id"]


def _contact() -> int:
    from crm import service

    return service.create_contact(name="Robin Vale", email="robin@example.test")["id"]


def _rows(note_id):
    from core.postgres import pg_fetchall

    return pg_fetchall(
        "SELECT user_id, display_name FROM crm_chatter_mentions WHERE note_id = %s ORDER BY id",
        (note_id,),
    )


def test_post_stores_active_seats_with_the_servers_name_and_skips_the_author(pg_db):
    from crm import chatter_service

    me = _user("me@example.test", "Me")
    ada = _user("ada@example.test", "  ")  # blank name → the email is the label
    gone = _user("gone@example.test", "Gone", active=False)
    cid = _contact()
    note, recipients = chatter_service.post_note(
        "contact", cid, "hey", author_id=me, mentions=[ada, gone, me, 999_999])
    assert recipients == [ada]
    assert _rows(note["id"]) == [
        {"user_id": ada, "display_name": "ada@example.test"},
        {"user_id": me, "display_name": "Me"},
    ]
    assert note["mentions"] == [
        {"user_id": ada, "name": "ada@example.test"}, {"user_id": me, "name": "Me"},
    ]
    listed = chatter_service.get_chatter("contact", cid)
    assert listed[0]["mentions"] == note["mentions"]


def test_edit_diff_tells_only_the_added_seat_and_keeps_a_since_deactivated_one(pg_db):
    from core.postgres import pg_execute
    from crm import chatter_service

    me, bo, cy = (_user(f"{n}@example.test", n) for n in ("me", "bo", "cy"))
    cid = _contact()
    note, _ = chatter_service.post_note("contact", cid, "x", author_id=me, mentions=[bo])
    pg_execute("UPDATE users SET is_active = FALSE WHERE id = %s", (bo,))

    # Omitted → unchanged, nobody told.
    _, told = chatter_service.edit_note(note["id"], "y", mentions=None, actor_id=me)
    assert told == [] and [r["user_id"] for r in _rows(note["id"])] == [bo]

    # Bo (now inactive) is KEPT because already stored; Cy is added and told once.
    _, told = chatter_service.edit_note(note["id"], "z", mentions=[bo, cy], actor_id=me)
    assert told == [cy]
    _, told = chatter_service.edit_note(note["id"], "z2", mentions=[bo, cy], actor_id=me)
    assert told == []  # an unrelated re-save re-tells nobody
    assert [r["user_id"] for r in _rows(note["id"])] == [bo, cy]

    # [] clears.
    _, told = chatter_service.edit_note(note["id"], "z3", mentions=[], actor_id=me)
    assert told == [] and _rows(note["id"]) == []


def test_deleting_the_contact_cascades_its_notes_mentions(pg_db):
    from core.postgres import pg_fetchone
    from crm import chatter_service, service

    bo = _user("bo@example.test", "Bo")
    cid = _contact()
    chatter_service.post_note("contact", cid, "x", mentions=[bo])
    service.delete_contact(cid)
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter_mentions")["n"] == 0


@pytest.mark.parametrize("include_definitions", [False, True])
def test_both_truncate_variants_run_and_empty_the_table(pg_db, include_definitions):
    from core.postgres import get_connection, pg_fetchone
    from crm import chatter_service, service

    bo = _user("bo@example.test", "Bo")
    chatter_service.post_note("contact", _contact(), "x", mentions=[bo])
    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=include_definitions)
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter_mentions")["n"] == 0


def test_the_notifications_link_column_round_trips(pg_db):
    from notifications import service

    me = _user("me@example.test", "Me")
    service.create_notification("t", "m", [], user_id=me, link="/crm/contacts/4")
    assert service.list_notifications(user_id=me)[0]["link"] == "/crm/contacts/4"
