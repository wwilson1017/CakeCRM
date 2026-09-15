"""Real-Postgres integration for saved views (#181) — what the mocks cannot prove:
the migration applies, the case-insensitive unique index really collides, the FK to
users really sets NULL, and the creator-or-admin rule really refuses a second member
against live rows.

Marked ``integration`` and excluded from the default (no-DB) run.
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_sv_{os.getpid()}"
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
def people(pg_db):
    """Two members and an admin, plus a clean saved_views table for each test."""
    from core.postgres import pg_execute, pg_fetchone

    pg_execute("TRUNCATE saved_views RESTART IDENTITY")
    pg_execute("DELETE FROM users")
    made = {}
    for key, email, name, role in (
        ("creator", "creator@x.test", "Casey Creator", "member"),
        ("other", "other@x.test", "", "member"),
        ("admin", "admin@x.test", "Ada Admin", "admin"),
    ):
        row = pg_fetchone(
            "INSERT INTO users (email, name, password_hash, role) VALUES (%s, %s, 'x', %s) "
            "RETURNING id",
            (email, name, role),
        )
        made[key] = {"id": row["id"], "email": email, "name": name, "role": role}
    return made


def test_migration_creates_the_table_and_index(pg_db):
    from core.postgres import pg_fetchone

    assert pg_fetchone("SELECT to_regclass('saved_views') AS t")["t"] == "saved_views"
    idx = pg_fetchone(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'saved_views' "
        "AND indexname = 'uq_saved_views_surface_name_ci'"
    )
    assert idx is not None


def test_create_list_round_trip_with_creator_name(people):
    from saved_views import service

    made = service.create_view("crm_pipeline", "Q3 pipeline", 1, {"query": "acme"},
                               people["creator"])
    assert made["id"] > 0
    assert made["payload"] == {"query": "acme"}

    views = service.list_views("crm_pipeline", people["creator"])
    assert [v["name"] for v in views] == ["Q3 pipeline"]
    assert views[0]["created_by_name"] == "Casey Creator"
    assert views[0]["can_edit"] is True


def test_creator_name_falls_back_to_email_when_the_name_is_blank(people):
    from saved_views import service

    service.create_view("crm_pipeline", "Theirs", 1, {}, people["other"])
    views = service.list_views("crm_pipeline", people["other"])
    assert views[0]["created_by_name"] == "other@x.test"


def test_duplicate_name_collides_on_case_and_surrounding_whitespace(people):
    """LOWER + btrim, which is surrounding whitespace only — internal spacing is significant.

    "Q3  pipeline" (two spaces) is therefore a DIFFERENT name from "Q3 pipeline", matching
    uq_users_email_ci's normalisation exactly rather than inventing a second rule.
    """
    from saved_views import service

    assert "error" not in service.create_view("crm_pipeline", "Q3 pipeline", 1, {},
                                              people["creator"])
    dup = service.create_view("crm_pipeline", "  q3 PIPELINE\t", 1, {}, people["creator"])
    assert dup["code"] == "conflict"

    spaced = service.create_view("crm_pipeline", "Q3  pipeline", 1, {}, people["creator"])
    assert "error" not in spaced


def test_the_same_name_is_free_on_another_surface(people):
    from saved_views import service

    service.create_view("crm_pipeline", "Mine", 1, {}, people["creator"])
    assert "error" not in service.create_view("crm_tasks", "Mine", 1, {}, people["creator"])


def test_another_member_may_read_but_not_edit_or_delete(people):
    from saved_views import service

    view = service.create_view("crm_pipeline", "Q3 pipeline", 1, {}, people["creator"])

    listed = service.list_views("crm_pipeline", people["other"])
    assert [v["name"] for v in listed] == ["Q3 pipeline"]
    assert listed[0]["can_edit"] is False

    assert service.update_view(view["id"], people["other"], name="hijacked")["code"] == "forbidden"
    assert service.delete_view(view["id"], people["other"])["code"] == "forbidden"
    assert service.list_views("crm_pipeline", people["creator"])[0]["name"] == "Q3 pipeline"


def test_creator_renames_and_overwrites_and_admin_deletes(people):
    from saved_views import service

    view = service.create_view("crm_pipeline", "Q3 pipeline", 1, {"query": "old"},
                               people["creator"])
    renamed = service.update_view(view["id"], people["creator"], name="Q4 pipeline")
    assert renamed["name"] == "Q4 pipeline"

    rewritten = service.update_view(view["id"], people["creator"],
                                    payload={"query": "new"}, version=2)
    assert rewritten["payload"] == {"query": "new"} and rewritten["version"] == 2

    assert service.delete_view(view["id"], people["admin"])["ok"] is True
    assert service.list_views("crm_pipeline", people["admin"]) == []


def test_deleting_the_author_keeps_the_view_and_makes_it_admin_only(people):
    from core.postgres import pg_execute
    from saved_views import service

    view = service.create_view("crm_pipeline", "Orphan", 1, {}, people["creator"])
    pg_execute("DELETE FROM users WHERE id = %s", (people["creator"]["id"],))

    listed = service.list_views("crm_pipeline", people["other"])
    assert len(listed) == 1
    assert listed[0]["created_by"] is None
    assert listed[0]["created_by_name"] is None
    assert listed[0]["can_edit"] is False
    assert service.list_views("crm_pipeline", people["admin"])[0]["can_edit"] is True
    assert service.delete_view(view["id"], people["admin"])["ok"] is True
