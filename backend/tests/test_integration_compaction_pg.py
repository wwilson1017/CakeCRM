"""Real-Postgres integration for conversation compaction (issue #72 Phase 3).

Proves the things a mock structurally cannot: that the migration applies, that
``set_compaction`` really is a compare-and-set under two independent connections, that
the taint column only ever flips one way, and that the reading written alongside a
message is the one the fast path reads back.

Marked ``integration`` and excluded from the default no-DB run.
"""

import os
import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_compaction_{os.getpid()}"
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


@pytest.fixture
def conv(pg_db):
    from assistant import history
    return history.create_conversation()["id"]


def test_a_fresh_conversation_starts_clean(conv):
    from assistant import history

    state = history.get_compaction_state(conv)
    assert state == {"summary": None, "first_kept_seq": None,
                     "tainted": False, "last_context_tokens": None}
    assert history.is_conversation_tainted(conv) is False


def test_the_boundary_only_moves_forward(conv):
    """The CAS is what makes two turns racing on one conversation safe without a lock:
    the loser's summary is discarded rather than pairing with an older boundary."""
    from assistant import history

    assert history.set_compaction(conv, "gist@10", 10, False) is True
    assert history.set_compaction(conv, "gist@5", 5, False) is False   # rewind refused
    assert history.set_compaction(conv, "gist@10 again", 10, False) is False  # equal too
    assert history.set_compaction(conv, "gist@20", 20, False) is True

    state = history.get_compaction_state(conv)
    assert state["first_kept_seq"] == 20
    assert state["summary"] == "gist@20"


def test_the_taint_flag_only_ever_flips_one_way(conv):
    from assistant import history

    history.set_compaction(conv, "gist", 10, True)
    assert history.is_conversation_tainted(conv) is True
    # A later compaction of a perfectly clean span must not clear it.
    history.set_compaction(conv, "gist2", 20, False)
    assert history.is_conversation_tainted(conv) is True


def test_mark_untrusted_seen_is_idempotent_and_independent_of_compaction(conv):
    from assistant import history

    history.mark_untrusted_seen(conv)
    history.mark_untrusted_seen(conv)
    assert history.is_conversation_tainted(conv) is True
    assert history.get_compaction_state(conv)["first_kept_seq"] is None


def test_the_context_reading_round_trips_through_save_message(conv):
    from assistant import history

    history.save_message(conv, str(uuid.uuid4()), "user", "hi")
    assert history.get_compaction_state(conv)["last_context_tokens"] is None

    history.save_message(conv, str(uuid.uuid4()), "assistant", "ok", context_tokens=4096)
    assert history.get_compaction_state(conv)["last_context_tokens"] == 4096

    # A provider that reports no usage must not blank a good earlier reading.
    history.save_message(conv, str(uuid.uuid4()), "user", "again")
    assert history.get_compaction_state(conv)["last_context_tokens"] == 4096

    # Nor may a concurrent turn whose context was assembled earlier land last with a
    # stale LOW reading — between compactions the conversation only grows.
    history.save_message(conv, str(uuid.uuid4()), "assistant", "slow turn", context_tokens=50)
    assert history.get_compaction_state(conv)["last_context_tokens"] == 4096

    history.save_message(conv, str(uuid.uuid4()), "assistant", "bigger", context_tokens=9000)
    assert history.get_compaction_state(conv)["last_context_tokens"] == 9000


def test_compacting_clears_the_meter_so_the_next_turn_measures_again(conv):
    """The one legitimate decrease. Leaving a pre-compaction reading in place would send
    the next turn down the fast path on a number describing a context that no longer
    exists."""
    from assistant import history

    history.save_message(conv, str(uuid.uuid4()), "assistant", "big", context_tokens=150_000)
    assert history.get_compaction_state(conv)["last_context_tokens"] == 150_000

    assert history.set_compaction(conv, "gist", 4, False) is True
    assert history.get_compaction_state(conv)["last_context_tokens"] is None

    # A losing CAS writes nothing at all, so it cannot clear another turn's reading.
    # This reading comes from a turn that assembled AFTER the compaction, so it stamps
    # the boundary now in force — see the stale-reading test below.
    history.save_message(conv, str(uuid.uuid4()), "assistant", "after",
                         context_tokens=80_000, context_boundary_seq=4)
    assert history.set_compaction(conv, "older", 2, False) is False
    assert history.get_compaction_state(conv)["last_context_tokens"] == 80_000


def test_a_reading_assembled_before_a_compaction_is_rejected(conv):
    """The half the clear cannot cover. Clearing `last_context_tokens` settles the stored
    value; it cannot reach a turn already streaming, whose own reading lands afterwards
    and restores a pre-compaction number that GREATEST then defends. The next turn reads
    it as current fullness, sheds the difference, and gists recent rows the thread still
    had room for."""
    from assistant import history

    # Turn A assembled while the thread had never compacted, and is still streaming.
    # Turn B compacts underneath it.
    assert history.set_compaction(conv, "gist", 4, False) is True
    assert history.get_compaction_state(conv)["last_context_tokens"] is None

    # Turn A now lands, stamped with the boundary it actually assembled against (none).
    history.save_message(conv, str(uuid.uuid4()), "assistant", "slow turn",
                         context_tokens=150_000, context_boundary_seq=None)
    assert history.get_compaction_state(conv)["last_context_tokens"] is None

    # A turn that assembled AFTER the compaction is on the current boundary, so its
    # reading is authoritative and IS kept.
    history.save_message(conv, str(uuid.uuid4()), "assistant", "fresh",
                         context_tokens=60_000, context_boundary_seq=4)
    assert history.get_compaction_state(conv)["last_context_tokens"] == 60_000

    # And the same reading is stale in its turn once the boundary advances again.
    assert history.set_compaction(conv, "gist2", 9, False) is True
    history.save_message(conv, str(uuid.uuid4()), "assistant", "now old",
                         context_tokens=140_000, context_boundary_seq=4)
    assert history.get_compaction_state(conv)["last_context_tokens"] is None


def test_get_conversation_carries_the_boundary_the_assembler_reads(conv):
    from assistant import history

    history.save_message(conv, str(uuid.uuid4()), "user", "one")
    history.set_compaction(conv, "the gist", 7, False)
    row = history.get_conversation(conv)
    assert row["compaction_summary"] == "the gist"
    assert row["compaction_first_kept_seq"] == 7


def test_a_compacted_thread_assembles_head_gist_and_tail(conv):
    """End to end on real rows: the middle is gone, the head is verbatim, and the gist
    rides the first retained user turn rather than a synthetic message."""
    from assistant import assembly, delimiters, history

    for i in range(6):
        history.save_message(conv, str(uuid.uuid4()),
                             "user" if i % 2 == 0 else "assistant", f"row{i}")
    gist = delimiters.wrap_conversation_summary("earlier: agreed pricing")
    assert history.set_compaction(conv, gist, 4, False) is True

    class _P:
        context_window = 200_000

        def build_tool_turn(self, text, calls, results):
            return [{"role": "assistant", "content": text}]

    msgs = assembly.assemble_messages(_P(), conv)
    joined = " | ".join(str(m["content"]) for m in msgs)
    assert "row0" in joined and "row1" in joined      # head verbatim
    assert "row2" not in joined and "row3" not in joined  # middle gisted away
    assert "row4" in joined and "row5" in joined      # tail verbatim
    assert gist in joined
    # No synthetic turn: the gist rides row4's own user message.
    assert sum(1 for m in msgs if m["role"] == "user") == 2


def test_the_new_columns_have_the_declared_types(pg_db):
    from core.postgres import pg_fetchall

    rows = pg_fetchall(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_name = 'assistant_conversations' AND column_name IN "
        "('compaction_summary','compaction_first_kept_seq','untrusted_content_seen',"
        " 'last_context_tokens')"
    )
    by_name = {r["column_name"]: r for r in rows}
    assert set(by_name) == {"compaction_summary", "compaction_first_kept_seq",
                            "untrusted_content_seen", "last_context_tokens"}
    assert by_name["compaction_summary"]["data_type"] == "text"
    assert by_name["compaction_first_kept_seq"]["data_type"] == "integer"
    assert by_name["last_context_tokens"]["data_type"] == "integer"
    # NOT NULL DEFAULT FALSE: an existing conversation reads as untainted, and its rows
    # are still in context, so the in-context scan still covers it until it compacts.
    assert by_name["untrusted_content_seen"]["is_nullable"] == "NO"
    assert "false" in by_name["untrusted_content_seen"]["column_default"]
