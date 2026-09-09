"""Real-Postgres integration for Todo-GTD task mode (issue #70).

The hermetic suites pin the SQL shape; these prove the parts only a real database can
answer:

- the `completed`/`status` coherence CHECK actually REFUSES drift (a mock cursor will
  happily accept any UPDATE, so the constraint is untested without this),
- the migration's backfill-before-CHECK ordering survives rows that already existed,
- the row-locked completion transition spawns exactly one next occurrence, through a
  real transaction rather than a scripted fake cursor,
- `task_projects` sits inside the CRM-reset TRUNCATE despite being FK-referenced by
  `tasks` (Postgres rejects truncating a referenced table on its own),
- the demo seed satisfies the CHECK on a first run.

Marked ``integration`` and excluded from the default no-DB run (see pytest.ini). Same
throwaway-database pattern as the sibling integration suites.
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_gtd_{os.getpid()}"
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
    from core.postgres import pg_execute
    pg_execute(
        "TRUNCATE companies, contacts, deals, activity_log, tasks, task_projects, "
        "crm_chatter, crm_chatter_attachments, crm_field_definitions, "
        "crm_field_values, crm_field_provenance, deal_stage_events, "
        "proactive_nudges RESTART IDENTITY"
    )
    yield


# ── Schema + the coherence invariant ──────────────────────────────────────────

def test_the_migration_added_the_gtd_columns_and_the_projects_table(pg_db):
    from core.postgres import pg_fetchall

    cols = {r["column_name"] for r in pg_fetchall(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'")}
    assert {"status", "star", "context", "tags", "repeat", "auto_star_on_due",
            "project_id", "completed_at", "source"} <= cols
    assert pg_fetchall("SELECT 1 FROM information_schema.tables "
                       "WHERE table_name = 'task_projects'")


def test_the_check_constraint_refuses_drift(pg_db):
    """The whole one-store design rests on this. A mock cursor cannot test it."""
    from core.postgres import pg_execute
    from crm import service

    task = service.create_task("open work")
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_execute("UPDATE tasks SET completed = 1 WHERE id = %s", (task["id"],))
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_execute("UPDATE tasks SET status = 'done' WHERE id = %s", (task["id"],))


def test_the_service_write_path_keeps_both_columns_in_step(pg_db):
    from core.postgres import pg_fetchone
    from crm import service

    task = service.create_task("open work")
    service.complete_task(task["id"])
    row = pg_fetchone("SELECT completed, status, completed_at FROM tasks WHERE id = %s",
                      (task["id"],))
    assert row["completed"] == 1 and row["status"] == "done" and row["completed_at"]

    service.update_task(task["id"], completed=False)
    row = pg_fetchone("SELECT completed, status, completed_at FROM tasks WHERE id = %s",
                      (task["id"],))
    assert row["completed"] == 0 and row["status"] == "next_action"
    assert row["completed_at"] is None


def test_an_invalid_repeat_is_refused_by_the_column_check(pg_db):
    from core.postgres import pg_execute
    from crm import service

    task = service.create_task("x")
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_execute("UPDATE tasks SET repeat = 'fortnightly' WHERE id = %s", (task["id"],))


# ── Recurrence, through a real transaction ────────────────────────────────────

def test_completing_a_repeating_todo_spawns_exactly_one_occurrence(pg_db, monkeypatch):
    import datetime

    from core.postgres import pg_fetchall
    from crm import gtd_service, service

    monkeypatch.setattr(service, "today_local", lambda: datetime.date(2026, 8, 21))
    todo = gtd_service.create_todo(
        "water the plants", status="next_action",
        due_date="2026-08-14", repeat="weekly", auto_star_on_due=True,
    )
    service.update_task(todo["id"], status="done")

    rows = pg_fetchall("SELECT status, completed, due_date, star FROM tasks "
                       "WHERE title = 'water the plants' ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["status"] == "done" and rows[0]["completed"] == 1
    # Completed exactly one interval late, so the spawn is due today — and
    # auto_star_on_due brings it back already starred.
    assert rows[1]["status"] == "next_action" and rows[1]["due_date"] == "2026-08-21"
    assert rows[1]["star"] is True

    # A second complete must NOT spawn again — the transition already happened.
    service.update_task(todo["id"], status="done")
    assert len(pg_fetchall("SELECT 1 FROM tasks WHERE title = 'water the plants'")) == 2


def test_clearing_repeat_while_completing_does_not_spawn(pg_db):
    from core.postgres import pg_fetchall
    from crm import gtd_service, service

    todo = gtd_service.create_todo("monthly report", status="next_action",
                                   due_date="2026-08-01", repeat="monthly")
    service.update_task(todo["id"], status="done", repeat="")
    assert len(pg_fetchall("SELECT 1 FROM tasks WHERE title = 'monthly report'")) == 1


# ── The dropped sweep, end to end ─────────────────────────────────────────────

def test_a_dropped_todo_leaves_normal_mode_open_work(pg_db):
    from crm import gtd_service, service

    keep = service.create_task("still mine")
    drop = gtd_service.create_todo("abandoned idea", status="next_action")
    before = service.get_dashboard_stats()["pending_tasks"]

    gtd_service.update_todo(drop["id"], {"status": "dropped"})

    assert service.get_dashboard_stats()["pending_tasks"] == before - 1
    titles = {t["title"] for t in service.list_tasks(limit=100)}
    assert "abandoned idea" not in titles and "still mine" in titles
    # It is hidden, not destroyed — GTD's own list still sees it.
    assert [t["id"] for t in gtd_service.list_todos(status="dropped")] == [drop["id"]]
    assert keep["id"]


# ── Reset + seed ──────────────────────────────────────────────────────────────

def test_clear_all_truncates_the_fk_referenced_project_table(pg_db):
    """task_projects is referenced BY tasks, so Postgres rejects truncating it alone —
    it has to share the statement, exactly like deal_stage_events."""
    from core.postgres import pg_fetchone
    from crm import gtd_service, service

    gtd_service.create_project("Website")
    gtd_service.create_todo("build the thing", project="Website")
    service.clear_all()
    assert pg_fetchone("SELECT COUNT(*) AS c FROM task_projects")["c"] == 0
    assert pg_fetchone("SELECT COUNT(*) AS c FROM tasks")["c"] == 0


def test_the_demo_seed_satisfies_the_coherence_check(pg_db):
    """seed_data derives status/completed_at from `completed`; hand-writing either
    would make first-run seeding fail on the CHECK."""
    from core.postgres import get_connection, pg_fetchall
    from crm.seed_data import seed_demo_data

    with get_connection() as conn:
        assert seed_demo_data(conn) is True
    rows = pg_fetchall("SELECT completed, status, completed_at FROM tasks")
    assert rows
    for r in rows:
        assert (r["status"] == "done") == (r["completed"] == 1)
        assert (r["completed_at"] is not None) == (r["completed"] == 1)


def test_project_delete_orphans_its_todos_rather_than_deleting_them(pg_db):
    from crm import gtd_service

    project = gtd_service.create_project("Website")
    todo = gtd_service.create_todo("build the thing", project_id=project["id"])
    gtd_service.delete_project(project["id"])
    survivor = gtd_service.get_todo(todo["id"])
    assert survivor is not None and survivor["project_id"] is None


def test_case_insensitive_project_resolution_reuses_one_row(pg_db):
    """#Groceries and #groceries in quick-add must land on ONE project."""
    from core.postgres import pg_fetchone
    from crm import gtd_service

    gtd_service.create_todo("a", project="Website")
    gtd_service.create_todo("b", project="website")
    assert pg_fetchone("SELECT COUNT(*) AS c FROM task_projects")["c"] == 1
