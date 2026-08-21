"""Real-Postgres integration for issue #22 Phases 2+3 — proves what the mocks can't.

The hermetic suites pin query SHAPE and the pure shapers. These run the actual SQL
against the actual migrated schema, which is the only way to catch the things that
would otherwise surface in production:

  * the migration applies and `proactive_nudges` / the new heartbeat_state columns exist,
  * the LEAD()/DISTINCT ON/FILTER window queries are valid and mean what I think,
  * the nudge upsert really is an atomic claim — two concurrent claimers, one winner,
  * the digest claim fires once per day under concurrency,
  * an empty CRM produces a zero state rather than an exception.

Marked ``integration`` and excluded from the default no-DB run. Admin DSN via
TEST_ADMIN_DSN (defaults to the local dev container).
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

    dbname = f"cakecrm_it22_{os.getpid()}"
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
            "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,))
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.fixture(autouse=True)
def _clean(pg_db):
    from core.postgres import pg_execute
    pg_execute("TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
               "crm_field_values, crm_field_provenance, deal_stage_events, "
               "proactive_nudges RESTART IDENTITY")
    pg_execute("UPDATE heartbeat_state SET last_digest_at = NULL, last_digest_status = '', "
               "last_nudge_at = NULL, proactive_enabled = TRUE WHERE id = 1")


# ── migration ─────────────────────────────────────────────────────────────────

def test_migration_created_the_table_and_columns():
    from core.postgres import pg_fetchone
    cols = pg_fetchone(
        "SELECT proactive_enabled, last_digest_at, last_digest_status, last_nudge_at "
        "FROM heartbeat_state WHERE id = 1")
    assert cols["proactive_enabled"] is True     # default ON for an existing install
    assert pg_fetchone("SELECT COUNT(*) AS c FROM proactive_nudges")["c"] == 0


def test_truncate_sweep_includes_proactive_nudges():
    """proactive_nudges has no FK, so nothing cascades it — the reset must clear it or
    a reseeded CRM inherits cooldowns for records it never mentioned."""
    from core.postgres import pg_execute, pg_fetchone
    from crm import service
    pg_execute("INSERT INTO proactive_nudges (entity_type, entity_id, kind) "
               "VALUES ('deal', 1, 'stale_deal')")
    service.clear_all()
    assert pg_fetchone("SELECT COUNT(*) AS c FROM proactive_nudges")["c"] == 0


# ── Phase 2 SQL actually runs ─────────────────────────────────────────────────

def test_pipeline_analytics_on_an_empty_crm():
    """A brand-new install has no stage events at all. This is reachable from an
    unattended background turn, so a zero state — not an exception — is required."""
    from crm import analytics_service
    out = analytics_service.get_pipeline_analytics()
    assert out["history_since"] is None
    assert out["velocity"]["won_in_window"] == 0
    assert all(s["samples"] == 0 for s in out["time_in_stage"])
    assert all(c["entered"] == 0 for c in out["conversion"])


def test_pipeline_analytics_counts_a_re_entering_deal_once():
    """A deal that bounces back into a stage must not inflate that stage's denominator
    — the DISTINCT ON is what enforces it, and only a real DB can prove the ordering."""
    from core.postgres import pg_execute
    from crm import analytics_service

    pg_execute("INSERT INTO deals (id, title, stage) VALUES (1, 'Bouncer', 'won')")
    for old, new, days in (("", "lead", 20), ("lead", "qualified", 18),
                           ("qualified", "lead", 15), ("lead", "qualified", 12),
                           ("qualified", "won", 10)):
        pg_execute("INSERT INTO deal_stage_events (deal_id, old_stage, new_stage, changed_at) "
                   "VALUES (1, %s, %s, now() - make_interval(days => %s))", (old, new, days))

    out = analytics_service.get_pipeline_analytics(window_days=90)
    qualified = next(c for c in out["conversion"] if c["stage"] == "qualified")
    assert qualified["entered"] == 1        # entered twice, counted once
    assert qualified["won"] == 1
    assert out["velocity"]["won_in_window"] == 1
    assert out["velocity"]["avg_days_to_won"] == 10.0
    assert out["history_covers_window"] is False   # log is 20 days old, window is 90


def test_time_in_stage_excludes_the_deal_still_sitting_there():
    """An open interval has no exit time. Counting it as if the deal left now would
    drag every average down."""
    from core.postgres import pg_execute
    from crm import analytics_service

    pg_execute("INSERT INTO deals (id, title, stage) VALUES (2, 'Still here', 'qualified')")
    pg_execute("INSERT INTO deal_stage_events (deal_id, old_stage, new_stage, changed_at) "
               "VALUES (2, '', 'lead', now() - make_interval(days => 6))")
    pg_execute("INSERT INTO deal_stage_events (deal_id, old_stage, new_stage, changed_at) "
               "VALUES (2, 'lead', 'qualified', now() - make_interval(days => 3))")

    out = analytics_service.get_pipeline_analytics(window_days=90)
    lead = next(s for s in out["time_in_stage"] if s["stage"] == "lead")
    qualified = next(s for s in out["time_in_stage"] if s["stage"] == "qualified")
    assert lead["samples"] == 1 and lead["avg_days"] == 3.0
    assert qualified["samples"] == 0        # never left it — open interval, excluded


def test_deal_health_runs_against_the_real_schema():
    from core.postgres import pg_execute
    from crm import analytics_service

    pg_execute("INSERT INTO contacts (id, name) VALUES (1, 'Person')")
    pg_execute("INSERT INTO deals (id, title, stage, value, contact_id, created_at) "
               "VALUES (3, 'Neglected', 'proposal', 5000, 1, now() - make_interval(days => 60))")
    pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => 40) WHERE id = 3")

    out = analytics_service.get_deal_health(3)
    assert out is not None
    assert out["deal"]["title"] == "Neglected"
    assert out["score"] is not None          # #18 scored it
    assert "stale" in out["flags"]           # 40 days untouched
    assert "no_next_step" in out["flags"]    # no open task
    assert "missing_company" in out["flags"]
    assert "missing_contact" not in out["flags"]


def test_deal_health_returns_none_for_a_missing_deal():
    from crm import analytics_service
    assert analytics_service.get_deal_health(999999) is None


# ── Phase 3 claims are atomic ─────────────────────────────────────────────────

def test_concurrent_nudge_claims_produce_exactly_one_winner():
    """The whole cooldown guarantee rests on this upsert being one atomic statement.
    Split into SELECT-then-UPDATE, both threads would read a stale row and both push."""
    from proactive import service as ps

    results = []
    lock = threading.Lock()

    def claim():
        got = ps._claim_nudge("deal", 42, ps.KIND_STALE_DEAL)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r) == 1, results


def test_nudge_claim_reopens_after_the_cooldown():
    from core.postgres import pg_execute
    from proactive import service as ps

    assert ps._claim_nudge("contact", 7, ps.KIND_UNTOUCHED_CONTACT) is True
    assert ps._claim_nudge("contact", 7, ps.KIND_UNTOUCHED_CONTACT) is False
    pg_execute("UPDATE proactive_nudges SET last_sent_at = now() - make_interval(days => 30) "
               "WHERE entity_type = 'contact' AND entity_id = 7")
    assert ps._claim_nudge("contact", 7, ps.KIND_UNTOUCHED_CONTACT) is True


def test_concurrent_digest_claims_send_once(monkeypatch):
    """Two ticks landing together must not both push a digest."""
    from core.postgres import pg_execute
    from proactive import service as ps

    pg_execute("UPDATE heartbeat_state SET last_digest_at = NULL WHERE id = 1")
    claims = []
    lock = threading.Lock()

    def claim():
        got = pg_execute(
            "UPDATE heartbeat_state SET last_digest_at = now(), last_digest_status = 'running' "
            "WHERE id = 1 AND (last_digest_at IS NULL "
            "OR (last_digest_at AT TIME ZONE 'UTC')::date < (now() AT TIME ZONE 'UTC')::date)")
        with lock:
            claims.append(got)

    threads = [threading.Thread(target=claim) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(claims) == 1, claims
    assert ps is not None


def test_collect_digest_on_an_empty_crm():
    """Runs unattended on a brand-new install — must produce a zero state, not raise."""
    from proactive import service as ps
    summary = ps.collect_digest()
    assert summary["open_deals"] == 0
    assert summary["top_deals"] == []
    title, message = ps.format_digest(summary)
    assert title and "0 open deals" in message


def test_collect_digest_reads_real_pipeline_numbers():
    from core.postgres import pg_execute
    from proactive import service as ps

    pg_execute("INSERT INTO deals (id, title, stage, value) VALUES (10, 'Live', 'lead', 2500)")
    pg_execute("INSERT INTO deals (id, title, stage, value) VALUES (11, 'Closed', 'won', 9000)")
    pg_execute("INSERT INTO deals (id, title, stage, value, archived_at) "
               "VALUES (12, 'Archived', 'lead', 7000, now())")
    pg_execute("INSERT INTO tasks (title, due_date, completed) "
               "VALUES ('Overdue', to_char(now() - interval '3 days', 'YYYY-MM-DD'), 0)")

    summary = ps.collect_digest()
    assert summary["open_deals"] == 1               # won and archived both excluded
    assert summary["open_value"] == 2500.0
    assert summary["overdue_tasks"] == 1
    assert [d["title"] for d in summary["top_deals"]] == ["Live"]
