"""Seed-data idempotency + shape (hermetic, via FakeConn).

Verifies the seed skips a non-empty CRM (all-tables guard, not just contacts),
and on an empty CRM issues INSERTs for all five tables (companies included since
issue #13) plus the five setval calls that advance the SERIAL sequences past the
fixed demo ids, with the FK-safe insert order and correct company links.
"""

from datetime import datetime, timedelta, timezone

from conftest import FakeConn

from crm.analytics_service import DEFAULT_DEAL_STALE_DAYS
from crm.seed_data import seed_demo_data


def test_seed_skips_when_any_table_has_rows():
    conn = FakeConn(fetchone_results=[(3,)])  # all-tables count > 0
    assert seed_demo_data(conn) is False
    assert not any("INSERT INTO" in sql for sql, _ in conn.executed)


def test_seed_inserts_all_tables_and_setvals_when_empty():
    conn = FakeConn(fetchone_results=[(0,)])  # all-tables count == 0
    assert seed_demo_data(conn) is True
    stmts = [sql for sql, _ in conn.executed]
    for table in ("companies", "contacts", "deals", "todos", "activity_log"):
        assert any(f"INSERT INTO {table}" in s for s in stmts), table
        assert any(f"pg_get_serial_sequence('{table}', 'id')" in s for s in stmts), table
    # placeholders are Postgres %s, never SQLite ?
    assert all("?" not in s for s in stmts if "INSERT INTO" in s)


def test_seed_row_counts_match_the_dataset():
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    inserts = [sql for sql, _ in conn.executed if "INSERT INTO" in sql]
    assert len(inserts) == 6  # one executemany per table (companies + the original 4 + crm_chatter)
    stages = [row[3] for row in next(p for sql, p in conn.executed if "INSERT INTO deals" in sql)]
    # every open stage has at least three deals so a fresh board is never half empty
    for stage in ("lead", "qualified", "proposal"):
        assert stages.count(stage) >= 3, stage
    assert stages.count("negotiation") >= 2 and stages.count("won") >= 2 and stages.count("lost") >= 1


def test_seed_empty_guard_counts_field_values_not_definitions():
    # Decision 9: field VALUES count toward "non-empty" (a stray value must not seed
    # into a reused id); field DEFINITIONS do not (user config, no fixed-id collision).
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    guard = conn.executed[0][0]  # the empty-guard count SELECT runs first
    assert "crm_field_values" in guard
    assert "crm_field_definitions" not in guard


def test_seed_setval_loop_excludes_field_tables():
    # Field tables ship EMPTY (no fixed-id inserts) → nothing to advance; they must
    # not appear in the sequence-advance loop.
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    setvals = [sql for sql, _ in conn.executed if "setval" in sql]
    assert not any("crm_field_values" in s or "crm_field_definitions" in s for s in setvals)


def test_seed_inserts_companies_before_contacts_before_deals():
    """FK order: companies must be inserted before the contacts/deals that link them."""
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    order = [sql for sql, _ in conn.executed if "INSERT INTO" in sql]
    idx = {t: next(i for i, s in enumerate(order) if f"INSERT INTO {t}" in s)
           for t in ("companies", "contacts", "deals")}
    assert idx["companies"] < idx["contacts"] < idx["deals"]


def test_seed_links_contacts_and_deals_to_companies():
    """The seeded contact/deal company_id values match the company text names."""
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    batches = {sql.split("INSERT INTO ")[1].split()[0]: params
               for sql, params in conn.executed if "INSERT INTO" in sql}
    # 6 companies with ids 1..6
    assert [row[0] for row in batches["companies"]] == [1, 2, 3, 4, 5, 6]
    # contact company_id is the trailing column; matches the plan mapping
    contact_company = {row[0]: row[-1] for row in batches["contacts"]}
    assert contact_company == {1: 1, 2: None, 3: 2, 4: 3, 5: 4, 6: None, 7: 5, 8: 6,
                               9: None, 10: None, 11: None}
    # deal company_id mirrors each deal's contact's company
    deal_company = {row[0]: row[-1] for row in batches["deals"]}
    assert deal_company == {1: 1, 2: None, 3: 3, 4: 4, 5: 2, 6: None, 7: 6, 8: 1, 9: 3, 10: None,
                            11: None, 12: 4, 13: 6, 14: None, 15: 2, 16: None, 17: None}


def _seed_batches(conn):
    return {sql.split("INSERT INTO ")[1].split()[0]: params
            for sql, params in conn.executed if "INSERT INTO" in sql}


def _last_touch_by_deal(batches):
    """The newest of `updated_at`, deal activity and deal chatter, per deal — the same
    three inputs `LAST_TOUCH_SQL` reads, so the test asks the question the panel asks."""
    touch = {row[0]: row[10] for row in batches["deals"]}  # updated_at
    for row in batches["activity_log"]:
        if row[2] is not None:
            touch[row[2]] = max(touch[row[2]], row[5])
    for row in batches["crm_chatter"]:
        if row[1] == "deal":
            touch[row[2]] = max(touch[row[2]], row[4])
    return touch


def test_seed_marks_one_stale_and_one_fresh_deal_hot():
    """Rank 2 of the Today panel (#131) is hot AND stale; a hot deal touched recently
    only rides the expanded tail. The seed has to show both, and `updated_at` alone is
    not enough — an activity or note from yesterday keeps a deal fresh."""
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    batches = _seed_batches(conn)
    hot_sql = next(sql for sql, _ in conn.executed if "deal_temperature = 'hot'" in sql)
    hot_ids = {int(x) for x in hot_sql.split("IN (")[1].rstrip(")").split(",")}
    touch = _last_touch_by_deal(batches)
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEFAULT_DEAL_STALE_DAYS)
    idle = {d for d in hot_ids if datetime.fromisoformat(touch[d]) < cutoff}
    assert idle, f"no hot deal is stale on every last-touch input: {hot_ids}"
    assert hot_ids - idle, "no hot deal is fresh enough for the expanded tail"
    stages = {row[0]: row[3] for row in batches["deals"]}
    assert all(stages[d] not in ("won", "lost") for d in hot_ids)


def test_seed_never_passes_none_for_a_not_null_text_column():
    """`todos.due_date` and `description` are `TEXT NOT NULL DEFAULT ''` (crm_core
    migration), and an explicit NULL in an INSERT bypasses the default — so a row
    that spells "no due date" as None fails the whole seed on a real Postgres, which
    the hermetic suite cannot see. #227's three inbox captures did exactly that and
    left the demo empty; the app's own `service.create_todo` writes "" for an absent
    date, and so must the seed."""
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    batches = _seed_batches(conn)
    for row in batches["todos"]:
        title, description, due_date = row[3], row[4], row[5]
        assert isinstance(description, str), (title, "description")
        assert isinstance(due_date, str), (title, "due_date")
