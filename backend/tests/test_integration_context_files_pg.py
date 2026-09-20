"""Real-Postgres integration for the context-file store (issue #72).

Proves the things mocks structurally cannot: that the migration applies, that `kind` and
`is_protected` are GENERATED (so they cannot drift from the filename no matter what a
writer passes), that the generated tsvector actually finds a slashed filename, and — the
one this file exists for — that two concurrent appends to the same daily note BOTH
survive on independent connections.

Marked ``integration`` and excluded from the default no-DB run.
"""

import json
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
    # Reset EVERY mutable column, not just the body: the file-dreaming tests below
    # backdate timestamps and archive rows, and a protected row left backdated or
    # archived would silently change what the next test's cycle scores.
    pg_execute(
        "UPDATE assistant_context_files SET content = '', headline = '', "
        "  archived_at = NULL, read_count = 0, last_read_at = NULL, "
        "  created_at = now(), updated_at = now()"
    )
    pg_execute("DELETE FROM dreaming_runs")
    pg_execute("DELETE FROM memory_facts")


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


class _StubRegistry:
    """Stands in for ToolRegistry but routes to the REAL executors, so an approved write
    actually reaches Postgres — the point of putting this test in the integration file."""

    def is_write(self, tool):
        return True

    def execute_tool_sync(self, tool, args):
        from context_files.tools import CONTEXT_FILE_TOOL_EXECUTORS

        return CONTEXT_FILE_TOOL_EXECUTORS[tool](**args)


async def test_approving_a_stale_assistant_overwrite_keeps_the_user_edit(pg_db, monkeypatch):
    """The whole reason the pending write is version-bound, end to end on real Postgres.

    Baker proposes a full rewrite of soul.md, which ALWAYS waits for approval. The user
    edits the same file in the Memory page while it waits. Approving must not discard
    their edit — and the token has to survive the JSON round-trip through the placeholder,
    which is exactly where a naive str()/ISO mismatch would silently stop matching.
    """
    from assistant import engine, history
    from context_files import service, tools as cf_tools

    service.write_file("soul.md", "I am Baker.", written_by="user")

    # 1. The confirmation gate stamps the version Baker composed against.
    placeholder = await engine._pending_placeholder("write_context_file", {"filename": "soul.md"})
    assert "context_updated_at" in json.loads(placeholder)

    # 2. The user saves their own edit through the editor while it waits.
    loaded = service.read_file("soul.md")
    service.write_file("soul.md", "I am Baker, and I am the user's.", written_by="user",
                       expected_updated_at=str(loaded["updated_at"]))

    # 3. Approve. Same wiring resolve_confirmation uses, minus the history layer.
    registry = _StubRegistry()
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **k: {
        "msg_id": "m1", "tool": "write_context_file",
        "args": {"filename": "soul.md", "content": "BAKER'S STALE REWRITE"},
        "content": placeholder,
    })
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)
    out = engine.resolve_confirmation(registry, "c1", "t1", "approve", msg_id="m1", user=None)

    # The user's edit survived and Baker was told what to do about it.
    assert service.read_file("soul.md")["content"] == "I am Baker, and I am the user's."
    assert "error" in out["result"]
    assert "read the file again" in out["result"]["error"].lower()

    # And with no competing edit the very same flow DOES write — the guard is not a wall.
    placeholder2 = await engine._pending_placeholder("write_context_file", {"filename": "soul.md"})
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **k: {
        "msg_id": "m2", "tool": "write_context_file",
        "args": {"filename": "soul.md", "content": "BAKER'S FRESH REWRITE"},
        "content": placeholder2,
    })
    engine.resolve_confirmation(registry, "c1", "t2", "approve", msg_id="m2", user=None)
    assert service.read_file("soul.md")["content"] == "BAKER'S FRESH REWRITE"
    assert cf_tools.binding_kwargs(json.loads(placeholder2))["expected_updated_at"]


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


# ── file-dreaming: the real SQL, against a real mixed corpus (#72 Phase 4) ───────
#
# The hermetic processor tests feed the fake only ELIGIBLE rows, so they prove the
# Python branch and nothing about the WHERE clauses. These tests hand Postgres a corpus
# that deliberately contains the rows the SQL must refuse.

def _age_file(filename: str, *, days_old: float, days_since_written: float,
              read_count: int = 0, days_since_read: float | None = None) -> None:
    """Backdate a file's timestamps so the scorer sees the age we want."""
    from core.postgres import pg_execute

    pg_execute(
        "UPDATE assistant_context_files SET "
        "  created_at = now() - make_interval(secs => %s), "
        "  updated_at = now() - make_interval(secs => %s), "
        "  read_count = %s, "
        "  last_read_at = CASE WHEN %s::double precision IS NULL THEN NULL "
        "                      ELSE now() - make_interval(secs => %s::double precision) END "
        "WHERE filename = %s",
        (days_old * 86400, days_since_written * 86400, read_count,
         days_since_read, (days_since_read or 0) * 86400, filename),
    )


def _seed_mixed_corpus():
    """One dormant old topic file plus four rows the SQL must refuse to archive."""
    from context_files import service

    service.write_file("topics/dormant.md", "an abandoned topic")
    service.write_file("topics/fresh.md", "written just now")
    service.write_file("MEMORY.md", "the protected memory file")
    service.write_file("soul.md", "the protected identity file")
    service.append_daily_note("an old daily entry", day="2020-01-02")

    _age_file("topics/dormant.md", days_old=200, days_since_written=200)
    # Protected + daily rows are made JUST AS dormant, so only the WHERE clause can
    # save them. If the exemptions were dropped, these would archive.
    _age_file("MEMORY.md", days_old=400, days_since_written=400)
    _age_file("soul.md", days_old=400, days_since_written=400)
    _age_file("daily/2020-01-02.md", days_old=400, days_since_written=400)
    # fresh.md keeps its real timestamps -> active.


def _archived() -> set:
    from core.postgres import pg_fetchall

    return {r["filename"] for r in pg_fetchall(
        "SELECT filename FROM assistant_context_files WHERE archived_at IS NOT NULL")}


def test_cycle_archives_only_the_dormant_old_topic_file(pg_db):
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()
    out = run_dreaming_cycle()

    assert _archived() == {"topics/dormant.md"}
    assert out["files_archived"] == 1
    assert out["archived_files"] == ["topics/dormant.md"]
    # Protected files and daily notes are not even SCORED (the SELECT excludes them).
    assert out["files_scored"] == 2      # dormant.md + fresh.md


def test_cycle_is_idempotent_on_rerun(pg_db):
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()
    run_dreaming_cycle()
    second = run_dreaming_cycle()
    assert _archived() == {"topics/dormant.md"}
    assert second["files_archived"] == 0          # already archived -> not live -> not scored
    assert second["files_scored"] == 1            # only fresh.md remains live


def test_archiving_does_not_bump_updated_at(pg_db):
    """updated_at is the write-recency signal AND the editor's concurrency token."""
    from core.postgres import pg_fetchone
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()
    before = pg_fetchone(
        "SELECT updated_at FROM assistant_context_files WHERE filename = %s",
        ("topics/dormant.md",))["updated_at"]
    run_dreaming_cycle()
    after = pg_fetchone(
        "SELECT updated_at, archived_at FROM assistant_context_files WHERE filename = %s",
        ("topics/dormant.md",))
    assert after["archived_at"] is not None
    assert after["updated_at"] == before


def test_a_write_unarchives_what_dreaming_put_away(pg_db):
    from context_files import service
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()
    run_dreaming_cycle()
    assert _archived() == {"topics/dormant.md"}

    service.write_file("topics/dormant.md", "back in use")
    assert _archived() == set()
    assert service.read_file("topics/dormant.md")["content"] == "back in use"


def test_read_file_still_returns_an_archived_file(pg_db):
    from context_files import service
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()
    run_dreaming_cycle()
    row = service.read_file("topics/dormant.md")
    assert row is not None and row["archived_at"] is not None
    # ...but it is gone from the manifest and from search.
    assert "topics/dormant.md" not in {r["filename"] for r in service.topic_manifest()}
    assert "topics/dormant.md" not in {r["filename"] for r in service.search_files("abandoned")}


def test_the_audit_row_records_both_units(pg_db):
    from core.postgres import pg_fetchone
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()
    run_dreaming_cycle()
    run = pg_fetchone(
        "SELECT files_scored, files_archived, details FROM dreaming_runs "
        "WHERE status = 'ok' ORDER BY id DESC LIMIT 1")
    assert run["files_scored"] == 2 and run["files_archived"] == 1
    details = run["details"] if isinstance(run["details"], dict) else json.loads(run["details"])
    assert details["archived_files"] == ["topics/dormant.md"]
    assert {f["filename"] for f in details["file_scores"]} == {"topics/dormant.md", "topics/fresh.md"}


def test_track_read_for_throttles_to_once_an_hour_and_skips_archived(pg_db):
    from context_files import service
    from core.postgres import pg_execute, pg_fetchone
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()

    def counts(name):
        return pg_fetchone(
            "SELECT read_count, last_read_at FROM assistant_context_files WHERE filename = %s",
            (name,))

    service.track_read_for(["topics/fresh.md"])
    first = counts("topics/fresh.md")
    assert first["read_count"] == 1 and first["last_read_at"] is not None

    service.track_read_for(["topics/fresh.md"])          # inside the hour -> throttled
    assert counts("topics/fresh.md")["read_count"] == 1

    pg_execute("UPDATE assistant_context_files SET last_read_at = now() - interval '2 hours' "
               "WHERE filename = %s", ("topics/fresh.md",))
    service.track_read_for(["topics/fresh.md"])          # past the hour -> counted
    assert counts("topics/fresh.md")["read_count"] == 2

    # An archived file is never bumped: reading one must not resurrect it.
    run_dreaming_cycle()
    before = counts("topics/dormant.md")["read_count"]
    service.track_read_for(["topics/dormant.md"])
    assert counts("topics/dormant.md")["read_count"] == before


def test_a_recently_read_old_file_survives_the_cycle(pg_db):
    """The read signal is what keeps an old file alive — the whole point of tracking it."""
    from dreaming.processor import run_dreaming_cycle

    _seed_mixed_corpus()
    _age_file("topics/dormant.md", days_old=200, days_since_written=200,
              read_count=3, days_since_read=5)
    run_dreaming_cycle()
    assert _archived() == set()
