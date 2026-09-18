"""Real-Postgres integration for memory + dreaming — proves what mocks can't:
the migration compiles (generated tsvector via IMMUTABLE two-arg to_tsvector, GIN,
archived_at), FTS round-trips under the `simple` config (including names that
`english` would stopword away, like "Will"), hostile query strings are safe, the
retrieval throttle counts once, and a full dreaming cycle soft-archives exactly the
dormant non-tier-1 old facts and is idempotent on re-run.

Marked ``integration`` and excluded from the default (no-DB) run. Admin DSN via
TEST_ADMIN_DSN; defaults to the local dev container.
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_mem_it_{os.getpid()}"
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
    pg_execute("TRUNCATE memory_facts, dreaming_runs")
    yield


def _age_fact(fact_id, days):
    """Backdate a fact's created_at (and null out retrieval) to simulate an old fact."""
    from core.postgres import pg_execute
    pg_execute(
        "UPDATE memory_facts SET created_at = now() - make_interval(days => %s), "
        "last_retrieved_at = NULL, retrieval_count = 0 WHERE id = %s",
        (days, fact_id),
    )


# ── migration shape ─────────────────────────────────────────────────────────────

def test_migration_created_tables_and_indexes(pg_db):
    from core.postgres import pg_fetchone
    cols = pg_fetchone(
        "SELECT count(*) AS n FROM information_schema.columns "
        "WHERE table_name = 'memory_facts' AND column_name IN "
        "('search_tsv','archived_at','retrieval_count','last_retrieved_at')"
    )
    assert cols["n"] == 4
    idx = pg_fetchone("SELECT count(*) AS n FROM pg_indexes WHERE indexname = 'idx_memory_facts_tsv'")
    assert idx["n"] == 1
    assert pg_fetchone("SELECT to_regclass('dreaming_runs') AS t")["t"] == "dreaming_runs"


# ── FTS round-trip ──────────────────────────────────────────────────────────────

def test_add_and_search_roundtrip(pg_db):
    from memory import service
    service.add_fact("Dana Chen", "works at", "Acme Corp", memory_type="person")
    hits = service.search_facts("dana acme")
    assert any(h["subject"] == "Dana Chen" for h in hits)


def test_simple_config_finds_names_english_would_stopword(pg_db):
    # "Will" is an english stopword; under `simple` it must remain searchable.
    from memory import service
    service.add_fact("Will", "prefers", "morning calls", memory_type="preference")
    assert service.search_facts("Will")   # non-empty


def test_hostile_queries_are_safe(pg_db):
    from memory import service
    service.add_fact("Acme", "renews", "2026-09-15")
    for q in ["'); DROP TABLE memory_facts;--", "foo & bar | ! (", 'unbalanced "quote', "&|!()"]:
        assert isinstance(service.search_facts(q), list)   # no exception, no damage
    # table still intact
    assert service.search_facts("acme")


def test_search_excludes_invalidated_and_archived(pg_db):
    from core.postgres import pg_execute
    from memory import service
    f = service.add_fact("Beta", "status", "cold")
    assert service.search_facts("beta")
    service.invalidate_fact(f["id"])
    assert service.search_facts("beta") == []
    # re-add + archive
    f2 = service.add_fact("Gamma", "status", "warm")
    pg_execute("UPDATE memory_facts SET archived_at = now() WHERE id = %s", (f2["id"],))
    assert service.search_facts("gamma") == []


# ── retrieval throttle ──────────────────────────────────────────────────────────

def test_retrieval_throttled_to_once_per_hour(pg_db):
    from core.postgres import pg_fetchone
    from memory import service
    f = service.add_fact("Nina", "role", "buyer")
    service.query_facts(subject="Nina")   # tracks
    service.query_facts(subject="Nina")   # throttled — same hour
    cnt = pg_fetchone("SELECT retrieval_count FROM memory_facts WHERE id = %s", (f["id"],))
    assert cnt["retrieval_count"] == 1


# ── full dreaming cycle ─────────────────────────────────────────────────────────

def test_dreaming_archives_only_dormant_nontier1_old(pg_db):
    from core.postgres import pg_fetchone
    from dreaming.processor import run_dreaming_cycle
    from memory import service

    old = service.add_fact("Zeta Ltd", "note", "dormant old fact")
    decision = service.add_fact("Pricing", "decided", "annual billing", memory_type="decision")
    fresh = service.add_fact("Live Co", "note", "fresh fact")
    _age_fact(old["id"], 200)
    _age_fact(decision["id"], 200)   # old but tier-1 → protected

    out = run_dreaming_cycle()
    assert out["facts_archived"] == 1
    assert out["archived"] == [old["id"]]

    # audit row recorded
    run = pg_fetchone("SELECT status, facts_scored, facts_archived FROM dreaming_runs ORDER BY id DESC LIMIT 1")
    assert run["status"] == "ok" and run["facts_archived"] == 1

    # archived fact vanishes from search + default query, visible with include_archived
    assert service.search_facts("zeta") == []
    assert not any(frow["id"] == old["id"] for frow in service.query_facts())
    assert any(frow["id"] == old["id"] for frow in service.query_facts(include_archived=True))
    # protected + fresh survive
    assert any(frow["id"] == decision["id"] for frow in service.query_facts())
    assert any(frow["id"] == fresh["id"] for frow in service.query_facts())


def test_dreaming_rerun_is_idempotent(pg_db):
    from core.postgres import pg_fetchone
    from dreaming.processor import run_dreaming_cycle
    from memory import service

    old = service.add_fact("Omega", "note", "dormant")
    _age_fact(old["id"], 200)
    assert run_dreaming_cycle()["facts_archived"] == 1
    assert run_dreaming_cycle()["facts_archived"] == 0   # already archived → nothing to do
    runs = pg_fetchone("SELECT count(*) AS n FROM dreaming_runs")
    assert runs["n"] == 2   # both cycles recorded an audit row


def test_empty_db_cycle_records_run(pg_db):
    from core.postgres import pg_fetchone
    from dreaming.processor import run_dreaming_cycle
    out = run_dreaming_cycle()
    # The cycle scores TWO units since #72 Phase 4. An empty database has no live facts
    # and no topic files — the migration's two seeded files are protected, so they are
    # not scored — and every counter is therefore zero.
    assert out == {
        "facts_scored": 0, "facts_archived": 0, "archived": [],
        "files_scored": 0, "files_archived": 0, "archived_files": [],
        "duration_ms": out["duration_ms"],
    }
    assert pg_fetchone("SELECT count(*) AS n FROM dreaming_runs")["n"] == 1


# ── context injection against real storage ──────────────────────────────────────

def test_build_memory_context_surfaces_stored_fact(pg_db):
    from memory import context, service
    service.add_fact("Dana", "works at", "Acme", memory_type="person")
    block = context.build_memory_context("what do we know about dana")
    assert "<recorded_memory id=" in block   # nonce-fenced
    assert "Dana" in block and "Acme" in block


def test_run_dreaming_if_due_runs_then_noops(pg_db):
    # Exercises the REAL production entrypoint end-to-end: pg_try_advisory_xact_lock,
    # max(finished_at) returning a psycopg2 aware datetime, and is_due consuming it.
    from dreaming.processor import run_dreaming_if_due

    first = run_dreaming_if_due()               # fresh DB → due → runs a cycle
    assert first is not None and first.get("status") != "error"

    # A status='ok' run now exists at ~now, which is at/after the most-recent slot,
    # so the due-guard blocks the immediate re-run regardless of wall-clock time.
    assert run_dreaming_if_due() is None


def test_cycle_failure_rolls_back_and_records_error(pg_db, monkeypatch):
    from core.postgres import pg_fetchone
    from dreaming import processor
    from memory import service

    f = service.add_fact("Rollback Co", "note", "should stay live")
    _age_fact(f["id"], 200)   # old + dormant → would be archived on a clean cycle
    monkeypatch.setattr(processor.scorer, "score_fact",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        processor.run_dreaming_cycle()

    # Rollback: the archive UPDATE and the 'ok' audit INSERT are both undone.
    assert pg_fetchone("SELECT archived_at FROM memory_facts WHERE id = %s", (f["id"],))["archived_at"] is None
    assert pg_fetchone("SELECT count(*) AS n FROM dreaming_runs WHERE status = 'ok'")["n"] == 0
    # The failure is recorded on a separate transaction.
    assert pg_fetchone("SELECT count(*) AS n FROM dreaming_runs WHERE status = 'error'")["n"] == 1


def test_error_run_does_not_suppress_next_due(pg_db, monkeypatch):
    from dreaming import processor
    from memory import service

    service.add_fact("Trigger Co", "note", "a live row so scoring actually runs")
    monkeypatch.setattr(processor.scorer, "score_fact",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    first = processor.run_dreaming_if_due()          # fails → status='error', no 'ok' run
    assert first.get("status") == "error"

    monkeypatch.undo()                               # remove the failure
    second = processor.run_dreaming_if_due()         # error row must NOT count as 'ok' → still due
    assert second is not None and second.get("status") != "error"


def test_natural_language_search_matches_via_or(pg_db):
    from memory import service
    service.add_fact("Dana Chen", "works at", "Acme Corp", memory_type="person")
    # A conversational query must still match (OR-of-keywords, not AND-everything).
    assert any(h["subject"] == "Dana Chen" for h in service.search_facts("what do we know about Dana"))


def test_short_and_unicode_entities_are_searchable(pg_db):
    from memory import service
    service.add_fact("Li", "leads", "US sales")
    assert service.search_facts("Li")      # len-2 name findable
    assert service.search_facts("US")      # len-2 acronym findable


def test_invalidate_is_idempotent(pg_db):
    from memory import service
    f = service.add_fact("Switch Co", "status", "warm", valid_from="2026-01-01")
    first = service.invalidate_fact(f["id"], valid_to="2026-03-01")   # >= valid_from
    assert first["valid_to"] == "2026-03-01"
    # Re-invalidating must NOT rewrite the earlier valid_to forward (history integrity).
    again = service.invalidate_fact(f["id"])
    assert again.get("already_invalidated") is True
    assert again["valid_to"] == "2026-03-01"


def test_add_fact_valid_from_supports_as_of(pg_db):
    from memory import service
    service.add_fact("Nadia", "role", "VP", valid_from="2026-01-01")
    assert not any(x["subject"] == "Nadia" for x in service.query_facts(as_of="2025-12-01"))
    assert any(x["subject"] == "Nadia" for x in service.query_facts(as_of="2026-02-01"))


def test_email_url_date_facts_findable_by_parts(pg_db):
    from memory import service
    service.add_fact("Acme portal", "url", "https://acme.com/pricing")
    service.add_fact("Contact", "email", "dana@acme.com")
    service.add_fact("Acme", "renewal", "2026-09-15")
    assert service.search_facts("pricing")        # URL path segment
    assert service.search_facts("dana@acme.com")  # email split → dana|acme|com
    assert service.search_facts("2026")           # date year part


def test_invalidate_clamps_valid_to_up_to_valid_from(pg_db):
    from memory import service
    f = service.add_fact("FutureCo", "status", "pending", valid_from="2026-12-01")
    # Invalidating with a date BEFORE valid_from must clamp (not dead-end on the CHECK).
    out = service.invalidate_fact(f["id"], valid_to="2026-06-01")
    assert out.get("ok") is True
    assert out["valid_to"] == "2026-12-01"   # clamped up to valid_from
