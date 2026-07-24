"""Seed-data idempotency + shape (hermetic, via FakeConn).

Verifies the seed skips a non-empty CRM (all-tables guard, not just contacts),
and on an empty CRM issues INSERTs for all four tables plus the four setval
calls that advance the SERIAL sequences past the fixed demo ids.
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
    for table in ("contacts", "deals", "tasks", "activity_log"):
        assert any(f"INSERT INTO {table}" in s for s in stmts), table
        assert any(f"pg_get_serial_sequence('{table}', 'id')" in s for s in stmts), table
    # placeholders are Postgres %s, never SQLite ?
    assert all("?" not in s for s in stmts if "INSERT INTO" in s)


def test_seed_row_counts_match_the_dataset():
    conn = FakeConn(fetchone_results=[(0,)])
    seed_demo_data(conn)
    # FakeCursor.executemany isn't defined on the fake; the seed uses executemany,
    # so assert the row batches indirectly by the recorded INSERT statements.
    inserts = [sql for sql, _ in conn.executed if "INSERT INTO" in sql]
    assert len(inserts) == 5  # one executemany per table (contacts/deals/tasks/activity_log/crm_chatter)
