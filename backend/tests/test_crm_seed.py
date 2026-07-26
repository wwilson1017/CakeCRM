"""Seed-data idempotency + shape (hermetic, via FakeConn).

Verifies the seed skips a non-empty CRM (all-tables guard, not just contacts),
and on an empty CRM issues INSERTs for all five tables (companies included since
issue #13) plus the five setval calls that advance the SERIAL sequences past the
fixed demo ids, with the FK-safe insert order and correct company links.
"""

from crm.seed_data import seed_demo_data
from tests.conftest import FakeConn


def test_seed_skips_when_any_table_has_rows():
    conn = FakeConn(fetchone_results=[(3,)])  # all-tables count > 0
    assert seed_demo_data(conn) is False
    assert not any("INSERT INTO" in sql for sql, _ in conn.executed)


def test_seed_inserts_all_tables_and_setvals_when_empty():
    conn = FakeConn(fetchone_results=[(0,)])  # all-tables count == 0
    assert seed_demo_data(conn) is True
    stmts = [sql for sql, _ in conn.executed]
    for table in ("companies", "contacts", "deals", "tasks", "activity_log"):
        assert any(f"INSERT INTO {table}" in s for s in stmts), table
        assert any(f"pg_get_serial_sequence('{table}', 'id')" in s for s in stmts), table
    # placeholders are Postgres %s, never SQLite ?
    assert all("?" not in s for s in stmts if "INSERT INTO" in s)


def test_seed_row_counts_match_the_dataset():
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    inserts = [sql for sql, _ in conn.executed if "INSERT INTO" in sql]
    assert len(inserts) == 6  # one executemany per table (companies + the original 4 + crm_chatter)


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
    assert contact_company == {1: 1, 2: None, 3: 2, 4: 3, 5: 4, 6: None, 7: 5, 8: 6}
    # deal company_id mirrors each deal's contact's company
    deal_company = {row[0]: row[-1] for row in batches["deals"]}
    assert deal_company == {1: 1, 2: None, 3: 3, 4: 4, 5: 2, 6: None, 7: 6}
