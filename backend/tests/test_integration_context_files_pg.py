"""Real-Postgres integration for the context-file store (issue #72).

Proves the things mocks structurally cannot: that the migration applies, that `kind` and
`is_protected` are GENERATED (so they cannot drift from the filename no matter what a
writer passes), that the generated tsvector actually finds a slashed filename, and — the
one this file exists for — that two concurrent appends to the same daily note BOTH
survive on independent connections.

Marked ``integration`` and excluded from the default no-DB run.
"""

import os
import threading

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_ctxfiles_{os.getpid()}"
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
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.fixture(autouse=True)
def clean(pg_db):
    from core.postgres import pg_execute

    pg_execute("DELETE FROM assistant_context_files WHERE filename NOT IN ('soul.md','MEMORY.md')")
    pg_execute("UPDATE assistant_context_files SET content = '', headline = ''")


def test_migration_seeds_the_two_protected_files(pg_db):
    from assistant.identity import DEFAULT_SOUL
    from context_files import service
    from core.postgres import pg_fetchone

    for name in ("soul.md", "MEMORY.md"):
        row = service.read_file(name)
        assert row is not None, f"{name} should be seeded by the migration"
        assert row["is_protected"] is True
        # STORED empty on purpose, checked against the column rather than through
        # read_file: the default text lives in identity.DEFAULT_SOUL so a later boot can
        # never overwrite a soul the user or the assistant rewrote.
        stored = pg_fetchone(
            "SELECT content FROM assistant_context_files WHERE filename = %s", (name,)
        )
        assert stored["content"] == ""

    # ...but a READ of the blank soul resolves the built-in text, so the prompt, the
    # assistant's own read tool and the Memory editor all show the identity that is live.
    assert service.read_file("soul.md")["content"] == DEFAULT_SOUL
    assert service.read_file("MEMORY.md")["content"] == ""


def test_kind_is_generated_and_cannot_drift(pg_db):
    """The whole reason `kind` is a generated column: a writer cannot set it wrong."""
    from context_files import service
    from core.postgres import pg_fetchone

    service.write_file("topics/pricing.md", "# Pricing\n\nrules")
    service.append_daily_note("something happened", day="2026-08-21")

    kinds = {
        r["filename"]: r["kind"]
        for r in [
            pg_fetchone("SELECT filename, kind FROM assistant_context_files WHERE filename = %s", (n,))
            for n in ("soul.md", "MEMORY.md", "topics/pricing.md", "daily/2026-08-21.md")
        ]
    }
    assert kinds == {
        "soul.md": "soul",
        "MEMORY.md": "memory",
        "topics/pricing.md": "topic",
        "daily/2026-08-21.md": "daily",
    }


def test_generated_columns_reject_direct_assignment(pg_db):
    """Belt and braces: Postgres itself refuses a hand-written kind."""
    from core.postgres import pg_execute

    with pytest.raises(psycopg2.Error):
        pg_execute(
            "INSERT INTO assistant_context_files (filename, kind) VALUES (%s, %s)",
            ("topics/x.md", "soul"),
        )


def test_search_finds_a_slashed_filename(pg_db):
    """The translate() half of the tsvector — 'simple' emits one compound lexeme for
    'topics/pricing.md' that a plain-word query could never reproduce."""
    from context_files import service

    service.write_file("topics/pricing.md", "how we quote")
    assert any(r["filename"] == "topics/pricing.md" for r in service.search_files("pricing"))
    assert any(r["filename"] == "topics/pricing.md" for r in service.search_files("quote"))


def test_write_unarchives(pg_db):
    from context_files import service
    from core.postgres import pg_execute, pg_fetchone

    service.write_file("topics/old.md", "body")
    pg_execute("UPDATE assistant_context_files SET archived_at = now() WHERE filename = %s",
               ("topics/old.md",))
    service.write_file("topics/old.md", "revived")
    row = pg_fetchone(
        "SELECT archived_at FROM assistant_context_files WHERE filename = %s", ("topics/old.md",)
    )
    assert row["archived_at"] is None


def test_concurrent_appends_both_survive(pg_db):
    """The single-statement upsert claim, on independent connections.

    Each pg helper call is its own committed transaction, so these really do race. Order
    is NOT asserted — only that neither entry was lost, which is the actual guarantee.
    """
    from context_files import service

    day = "2026-08-22"
    entries = [f"entry-{i}" for i in range(8)]
    errors: list[BaseException] = []

    def append(text: str):
        try:
            service.append_daily_note(text, day=day)
        except BaseException as exc:   # noqa: BLE001 — recorded and re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(e,)) for e in entries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent appends raised: {errors}"
    note = service.read_daily_note(day)
    missing = [e for e in entries if e not in note]
    assert not missing, f"appends lost under concurrency: {missing}"
    assert note.count("### ") == len(entries)


def test_stale_write_precondition_conflicts(pg_db):
    from context_files import service

    service.write_file("topics/race.md", "v1")
    stale = service.read_file("topics/race.md")["updated_at"]
    service.write_file("topics/race.md", "v2")   # someone else saves in between

    with pytest.raises(service.ContextFileError) as exc:
        service.write_file("topics/race.md", "v3", expected_updated_at=str(stale))
    assert exc.value.code == "conflict"
    assert service.read_file("topics/race.md")["content"] == "v2"


def test_a_normal_save_round_trips_over_HTTP(pg_db):
    """The bug this test exists for: the version token crosses a JSON boundary. psycopg
    returns a datetime whose str() is space-separated, while FastAPI serializes the ISO
    'T' form the browser sends back — so comparing them as Python strings rejected EVERY
    legitimate save with a 409. Service-level tests missed it because they never
    serialized. Exercise the real round trip.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from context_files.router import router as cf_router
    from core.auth import get_current_user

    app = FastAPI()
    app.include_router(cf_router, prefix="/api/context-files")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    client = TestClient(app)

    loaded = client.get("/api/context-files/file/soul.md").json()
    saved = client.put(
        "/api/context-files/file/soul.md",
        json={"content": "I am Baker.", "expected_updated_at": loaded["updated_at"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["content"] == "I am Baker."
    assert saved.json()["written_by"] == "user"

    # Re-using the now-stale token must conflict.
    again = client.put(
        "/api/context-files/file/soul.md",
        json={"content": "clobber", "expected_updated_at": loaded["updated_at"]},
    )
    assert again.status_code == 409
    assert client.get("/api/context-files/file/soul.md").json()["content"] == "I am Baker."


def test_a_stale_token_cannot_resurrect_a_deleted_file(pg_db):
    from context_files import service

    service.write_file("topics/gone.md", "body")
    token = str(service.read_file("topics/gone.md")["updated_at"])
    service.delete_file("topics/gone.md")

    with pytest.raises(service.ContextFileError) as exc:
        service.write_file("topics/gone.md", "resurrected", expected_updated_at=token)
    assert exc.value.code == "conflict"
    assert service.read_file("topics/gone.md") is None


def test_headline_is_derived_on_write(pg_db):
    from context_files import service

    service.write_file("topics/h.md", "# Renewal playbook\n\nsteps")
    assert service.read_file("topics/h.md")["headline"] == "Renewal playbook"


def test_daily_manifest_excludes_today(pg_db):
    """Today's note is injected in full elsewhere; listing it too is duplication."""
    from context_files import service

    service.append_daily_note("today's thing")
    service.append_daily_note("older thing", day="2026-01-02")
    names = {r["filename"] for r in service.daily_manifest()}
    assert service.daily_filename() not in names
    assert "daily/2026-01-02.md" in names
