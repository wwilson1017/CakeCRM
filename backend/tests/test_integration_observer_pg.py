"""Real-Postgres integration for the observer (#72 Phase 4).

Proves what a hermetic SQL-substring test structurally cannot: that the migration
applies and is applied ONCE, that the candidate predicate actually selects the right
conversations (including the assistant-row case, which is the whole reason quietness is
measured over every message), that the watermark advance is a real compare-and-set, and
that a REPLAY of equivalent content above a NEW watermark is deduped — cursor exclusion
alone would prove nothing about dedupe.

Marked ``integration`` and excluded from the default no-DB run. Admin DSN via
TEST_ADMIN_DSN; defaults to the local dev container.
"""

import os
import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")
MIGRATION = "20260914230644_observer_and_file_dreaming.sql"


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_obs_it_{os.getpid()}"
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
def clean(pg_db):
    from core.postgres import pg_execute

    pg_execute("DELETE FROM assistant_conversations")   # messages cascade
    pg_execute("DELETE FROM memory_facts")
    pg_execute("DELETE FROM tasks")


# ── helpers ──────────────────────────────────────────────────────────────────

def _conversation(watermark=None) -> str:
    from core.postgres import pg_execute

    cid = str(uuid.uuid4())
    pg_execute("INSERT INTO assistant_conversations (id, observed_through_seq) VALUES (%s, %s)",
               (cid, watermark))
    return cid


def _message(cid: str, seq: int, role: str, content: str, minutes_ago: float = 60.0) -> None:
    from core.postgres import pg_execute

    pg_execute(
        "INSERT INTO assistant_messages (id, conversation_id, role, content, seq, created_at) "
        "VALUES (%s, %s, %s, %s, %s, now() - make_interval(secs => %s))",
        (str(uuid.uuid4()), cid, role, content, seq, minutes_ago * 60),
    )


def _candidate_ids(quiet=10, min_rows=2, min_chars=200, limit=10):
    from assistant import history

    return [c["id"] for c in history.list_observer_candidates(quiet, min_rows, min_chars, limit)]


# ── the migration ────────────────────────────────────────────────────────────

def test_the_migration_applied_and_is_recorded_as_applied(pg_db):
    from core.postgres import pg_fetchone

    assert pg_fetchone("SELECT 1 AS ok FROM _migrations_applied WHERE filename = %s",
                       (MIGRATION,)) is not None


def test_a_rerun_of_the_runner_cannot_replay_the_backfill(pg_db):
    """The backfill keys on `observed_through_seq IS NULL`, which is ALSO what a
    conversation created after the migration looks like. Re-executing it would silently
    consume unobserved messages, so the once-only guarantee has to be real: the runner
    records every applied filename and skips it forever after."""
    from core import postgres
    from core.postgres import pg_fetchone

    cid = _conversation()                      # created AFTER the migration -> NULL
    _message(cid, 0, "user", "something the observer has not read yet")
    postgres.run_migrations()                  # the second boot

    row = pg_fetchone("SELECT observed_through_seq FROM assistant_conversations WHERE id = %s",
                      (cid,))
    assert row["observed_through_seq"] is None   # still eligible, not consumed


def test_the_new_columns_all_exist_with_their_defaults(pg_db):
    from core.postgres import pg_fetchall

    cols = {(r["table_name"], r["column_name"]): r for r in pg_fetchall(
        "SELECT table_name, column_name, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public'")}
    assert ("assistant_conversations", "observed_through_seq") in cols
    assert ("heartbeat_state", "last_observer_run_at") in cols
    for col in ("read_count", "last_read_at"):
        assert ("assistant_context_files", col) in cols
    for col in ("files_scored", "files_archived"):
        assert cols[("dreaming_runs", col)]["is_nullable"] == "NO"


# ── the candidate predicate ──────────────────────────────────────────────────

def test_a_settled_conversation_with_enough_new_user_rows_is_a_candidate(pg_db):
    cid = _conversation()
    _message(cid, 0, "user", "first thing")
    _message(cid, 1, "user", "second thing")
    assert _candidate_ids() == [cid]


def test_one_SHORT_new_user_row_is_not_enough(pg_db):
    cid = _conversation()
    _message(cid, 0, "user", "just one")
    assert _candidate_ids() == []


def test_one_LONG_new_user_row_is_enough_on_its_own(pg_db):
    """Without the character floor a user who types one substantial message and stops is
    never observed at all — which is most of what the observer exists to catch."""
    cid = _conversation()
    _message(cid, 0, "user", "Dana at Acme said she would send the quote by Friday. " * 6)
    assert _candidate_ids() == [cid]


def test_the_last_row_of_a_budget_truncated_batch_is_offered_again(pg_db):
    """The transcript budget processes an oversized segment across successive runs. With
    a row-only threshold the final leftover row would strand until another message
    arrived, or until the stale guard dropped it unread 14 days later."""
    from assistant import history
    from memory import observer

    cid = _conversation()
    big = "y" * observer.MAX_ROW_CHARS
    for seq in range(10):
        _message(cid, seq, "user", big)

    # The budget really does truncate this segment, so the boundary is not the last row.
    _, through = observer.build_transcript(history.user_rows_since(cid, -1, 200))
    assert through is not None and through < 9

    # Walk the watermark to the point where exactly ONE long row is left unobserved.
    history.advance_observed_seq(cid, 8)
    assert _candidate_ids() == [cid]


def test_a_conversation_still_being_typed_in_is_not_a_candidate(pg_db):
    cid = _conversation()
    _message(cid, 0, "user", "first thing", minutes_ago=60)
    _message(cid, 1, "user", "second thing", minutes_ago=2)
    assert _candidate_ids() == []


def test_a_newer_assistant_row_keeps_the_conversation_out(pg_db):
    """THE reason quietness is measured over every message. Both user rows are old, so a
    user-row-only check would call this settled — but the assistant answered two minutes
    ago, so the turn is still in flight and no commitment in it has settled."""
    cid = _conversation()
    _message(cid, 0, "user", "first thing", minutes_ago=60)
    _message(cid, 1, "user", "second thing", minutes_ago=30)
    _message(cid, 2, "assistant", "still replying", minutes_ago=2)
    assert _candidate_ids() == []


def test_rows_at_or_below_the_watermark_do_not_count(pg_db):
    cid = _conversation(watermark=1)
    _message(cid, 0, "user", "already observed")
    _message(cid, 1, "user", "also already observed")
    _message(cid, 2, "user", "new, but only one")
    assert _candidate_ids() == []
    _message(cid, 3, "user", "now there are two new ones")
    assert _candidate_ids() == [cid]


def test_characters_below_the_watermark_do_not_count_either(pg_db):
    """The char sum carries the same FILTER as the row count — otherwise a long observed
    history would keep re-qualifying a conversation with nothing new in it."""
    cid = _conversation(watermark=0)
    _message(cid, 0, "user", "an extremely long already-observed message. " * 20)
    _message(cid, 1, "user", "hi")
    assert _candidate_ids() == []


def test_a_null_watermark_counts_every_user_row(pg_db):
    cid = _conversation(watermark=None)
    _message(cid, 0, "user", "first thing")
    _message(cid, 1, "user", "second thing")
    from assistant import history

    rows = history.list_observer_candidates(10, 2, 200, 10)
    assert rows[0]["observed_through_seq"] == -1     # COALESCEd for the caller


def test_a_conversation_with_no_messages_is_never_a_candidate(pg_db):
    _conversation()
    assert _candidate_ids() == []


def test_candidates_come_back_oldest_activity_first(pg_db):
    old = _conversation()
    _message(old, 0, "user", "old one")
    _message(old, 1, "user", "old two", minutes_ago=600)
    new = _conversation()
    _message(new, 0, "user", "new one")
    _message(new, 1, "user", "new two", minutes_ago=30)
    assert _candidate_ids() == [old, new]


def test_the_limit_is_honoured(pg_db):
    for _ in range(3):
        cid = _conversation()
        _message(cid, 0, "user", "a")
        _message(cid, 1, "user", "b")
    assert len(_candidate_ids(limit=2)) == 2


# ── user_rows_since / advance_observed_seq ───────────────────────────────────

def test_user_rows_since_never_returns_an_assistant_row(pg_db):
    from assistant import history

    cid = _conversation()
    _message(cid, 0, "user", "typed by a human")
    _message(cid, 1, "assistant", "QUOTED FROM AN EMAIL")
    _message(cid, 2, "user", "also typed by a human")
    rows = history.user_rows_since(cid, -1, 50)
    assert [r["content"] for r in rows] == ["typed by a human", "also typed by a human"]
    assert [r["seq"] for r in rows] == [0, 2]


def test_advance_observed_seq_is_a_real_compare_and_set(pg_db):
    from assistant import history
    from core.postgres import pg_fetchone

    cid = _conversation()
    assert history.advance_observed_seq(cid, 5) is True
    assert history.advance_observed_seq(cid, 3) is False      # never rewinds
    assert history.advance_observed_seq(cid, 5) is False      # not strictly forward
    assert history.advance_observed_seq(cid, 6) is True
    assert pg_fetchone("SELECT observed_through_seq FROM assistant_conversations WHERE id = %s",
                       (cid,))["observed_through_seq"] == 6


def test_advancing_does_not_reorder_the_users_conversation_list(pg_db):
    from assistant import history
    from core.postgres import pg_fetchone

    cid = _conversation()
    before = pg_fetchone("SELECT updated_at FROM assistant_conversations WHERE id = %s",
                         (cid,))["updated_at"]
    history.advance_observed_seq(cid, 5)
    assert pg_fetchone("SELECT updated_at FROM assistant_conversations WHERE id = %s",
                       (cid,))["updated_at"] == before


# ── find_live_facts_by_key ───────────────────────────────────────────────────

def test_the_key_lookup_finds_an_exact_match_behind_many_partial_ones(pg_db):
    """A bounded ILIKE prefetch would order by confidence and cap at five, so the exact
    match could be pushed out — and the observer would then supersede or duplicate the
    wrong fact."""
    from memory import service

    for i in range(12):
        service.add_fact(f"Dana Chen {i}", "works at something", f"Acme {i}",
                         created_by="observer")
    service.add_fact("Dana", "works at", "Acme", created_by="assistant")

    matches = service.find_live_facts_by_key("Dana", "works at")
    assert [m["object"] for m in matches] == ["Acme"]
    assert matches[0]["created_by"] == "assistant"


def test_the_key_lookup_treats_like_wildcards_as_literal_text(pg_db):
    """`%` and `_` are LIKE wildcards and query_facts escapes neither, so a subject
    containing one would match half the table."""
    from memory import service

    service.add_fact("100% margin", "is", "the goal", created_by="observer")
    service.add_fact("Dana", "is", "a buyer", created_by="observer")
    assert [m["subject"] for m in service.find_live_facts_by_key("100% margin", "is")] == ["100% margin"]
    assert service.find_live_facts_by_key("%", "is") == []
    assert service.find_live_facts_by_key("_ana", "is") == []


def test_the_key_lookup_is_case_and_whitespace_insensitive(pg_db):
    from memory import service

    service.add_fact("Dana Chen", "works at", "Acme", created_by="observer")
    assert len(service.find_live_facts_by_key("  dana   CHEN ", "WORKS at")) == 1


def test_the_key_lookup_ignores_invalidated_and_archived_facts(pg_db):
    from core.postgres import pg_execute
    from memory import service

    added = service.add_fact("Dana", "works at", "Acme", created_by="observer")
    service.invalidate_fact(added["id"])
    assert service.find_live_facts_by_key("Dana", "works at") == []

    fresh = service.add_fact("Dana", "works at", "Beta", created_by="observer")
    pg_execute("UPDATE memory_facts SET archived_at = now() WHERE id = %s", (fresh["id"],))
    assert service.find_live_facts_by_key("Dana", "works at") == []


# ── end to end, with a scripted provider ─────────────────────────────────────

class ScriptedProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def stream_turn(self, messages, tools, system_prompt):
        self.calls += 1
        yield {"type": "text", "text": self.payload}
        yield {"type": "_turn_complete", "stop_reason": "stop"}


REPLY = (
    '{"facts": [{"subject": "Dana", "predicate": "works at", "object": "Acme", '
    '"memory_type": "person", "confidence": 1.0}], '
    '"commitments": [{"title": "Chase the Acme quote", "due_date": null}]}'
)


def _observe_once(cid, provider, tracked=None):
    from assistant import history
    from memory import observer

    conv = {"id": cid, "observed_through_seq": None}
    row = history.list_observer_candidates(10, 2, 200, 10)
    conv["observed_through_seq"] = next((c["observed_through_seq"] for c in row if c["id"] == cid), -1)
    return observer.observe_conversation(conv, provider, tracked if tracked is not None else {})


@pytest.fixture
def real_model(monkeypatch):
    """Run the real observe_conversation, bypassing only the event-loop bridge."""
    from memory import observer

    def call(provider, prompt):
        provider.calls += 1
        call.prompts.append(prompt)
        return provider.payload
    call.prompts = []
    monkeypatch.setattr(observer, "_call_model", call)
    return call


def test_one_settled_conversation_yields_a_fact_and_an_inbox_task(pg_db, real_model):
    from core.postgres import pg_fetchone
    from memory import service

    cid = _conversation()
    _message(cid, 0, "user", "Dana works at Acme and owes us a quote")
    _message(cid, 1, "user", "she said she would send it by Friday")

    out = _observe_once(cid, ScriptedProvider(REPLY))
    assert out["facts_added"] == 1 and out["tasks_added"] == 1

    fact = service.find_live_facts_by_key("Dana", "works at")[0]
    assert fact["created_by"] == "observer"
    assert fact["source"] == f"conversation:{cid}"
    assert fact["confidence"] == 0.9

    task = pg_fetchone("SELECT title, status, source, owner_id, description, completed FROM tasks")
    assert task["status"] == "inbox" and task["source"] == "agent"
    assert task["owner_id"] is None and task["completed"] == 0
    assert f"conversation:{cid}" in task["description"]

    assert pg_fetchone("SELECT observed_through_seq FROM assistant_conversations WHERE id = %s",
                       (cid,))["observed_through_seq"] == 1


def test_equivalent_content_replayed_above_a_new_watermark_is_deduped(pg_db, real_model):
    """A second run writing nothing could just mean the cursor excluded the rows. This
    replays EQUIVALENT content as brand-new messages, so the cursor offers them again and
    only the dedupe can stop the duplicate."""
    from core.postgres import pg_fetchone
    from memory import service

    cid = _conversation()
    _message(cid, 0, "user", "Dana works at Acme and owes us a quote")
    _message(cid, 1, "user", "she said she would send it by Friday")
    provider = ScriptedProvider(REPLY)
    _observe_once(cid, provider)

    _message(cid, 2, "user", "just to confirm, Dana is still at Acme")
    _message(cid, 3, "user", "and that quote is still coming")
    out = _observe_once(cid, provider)

    assert provider.calls == 2                       # the model really was asked again
    assert out["facts_added"] == 0 and out["facts_superseded"] == 0
    assert out["tasks_added"] == 0
    assert pg_fetchone("SELECT count(*) AS n FROM memory_facts")["n"] == 1
    assert pg_fetchone("SELECT count(*) AS n FROM tasks")["n"] == 1
    assert len(service.find_live_facts_by_key("Dana", "works at")) == 1


def test_a_changed_object_supersedes_only_the_observers_own_fact(pg_db, real_model):
    from core.postgres import pg_fetchone
    from memory import service

    cid = _conversation()
    _message(cid, 0, "user", "Dana works at Acme and owes us a quote")
    _message(cid, 1, "user", "she said she would send it by Friday")
    _observe_once(cid, ScriptedProvider(REPLY))

    moved = REPLY.replace('"object": "Acme"', '"object": "Beta Industries"')
    _message(cid, 2, "user", "Dana has moved over to Beta Industries")
    _message(cid, 3, "user", "starting next month")
    out = _observe_once(cid, ScriptedProvider(moved))

    assert out["facts_superseded"] == 1
    live = service.find_live_facts_by_key("Dana", "works at")
    assert [f["object"] for f in live] == ["Beta Industries"]
    assert pg_fetchone("SELECT count(*) AS n FROM memory_facts")["n"] == 2   # the old row is kept


def test_an_explicit_fact_survives_a_conflicting_observation(pg_db, real_model):
    from memory import service

    service.add_fact("Dana", "works at", "Acme", created_by="assistant", confidence=1.0)
    cid = _conversation()
    _message(cid, 0, "user", "Dana has moved over to Beta Industries")
    _message(cid, 1, "user", "starting next month")
    moved = REPLY.replace('"object": "Acme"', '"object": "Beta Industries"')
    out = _observe_once(cid, ScriptedProvider(moved))

    assert out["facts_added"] == 0 and out["facts_superseded"] == 0
    live = service.find_live_facts_by_key("Dana", "works at")
    assert [(f["object"], f["created_by"]) for f in live] == [("Acme", "assistant")]


def test_the_prompt_carries_the_message_dates_and_both_fences(pg_db, real_model):
    from crm import gtd_service

    gtd_service.create_todo("An existing open todo", status="inbox", source="agent")
    cid = _conversation()
    _message(cid, 0, "user", "Dana works at Acme and owes us a quote")
    _message(cid, 1, "user", "she said she would send it by Friday")
    tracked = {t.casefold(): t for t in gtd_service.list_open_task_titles(30, 30)}
    _observe_once(cid, ScriptedProvider(REPLY), tracked)

    prompt = real_model.prompts[0]
    assert 'source="conversation"' in prompt and 'source="tracked_tasks"' in prompt
    assert "An existing open todo" in prompt
    assert "Dana works at Acme" in prompt
    # The row shape here is what row_to_dict really returns (an ISO string), so this is
    # the assertion that would have caught the dead date/stale-cutoff path.
    assert "unknown date" not in prompt
    import re
    assert re.search(r"USER \[\d{4}-\d{2}-\d{2}\]: Dana works at Acme", prompt)


def test_an_open_task_from_outside_the_prompt_list_still_blocks_a_duplicate(pg_db, real_model):
    """The capped prompt list cannot see it; the existence query can."""
    from core.postgres import pg_fetchone
    from crm import gtd_service

    gtd_service.create_todo("Chase the Acme quote", status="next_action", source="ui")
    cid = _conversation()
    _message(cid, 0, "user", "Dana works at Acme and owes us a quote")
    _message(cid, 1, "user", "she said she would send it by Friday")
    out = _observe_once(cid, ScriptedProvider(REPLY), {})    # deliberately EMPTY list

    assert out["tasks_added"] == 0
    assert pg_fetchone("SELECT count(*) AS n FROM tasks")["n"] == 1


# ── the title dedupe, against the real regex (#198 review) ──────────────────

def test_an_open_task_with_odd_internal_spacing_still_blocks_a_duplicate(pg_db):
    """`validate_title` only strips the ends, so a human's "Call  Bob" is STORED with two
    spaces while the observer always proposes a collapsed title. Normalizing only the
    needle would make the check less permissive, not more, and this function would
    create the duplicate it exists to prevent."""
    from crm import gtd_service

    gtd_service.create_todo("Call  Bob   about   the   quote", status="inbox", source="ui")
    assert gtd_service.open_task_with_title_exists("Call Bob about the quote") is True
    assert gtd_service.open_task_with_title_exists("call bob ABOUT the quote") is True
    assert gtd_service.open_task_with_title_exists("  Call Bob about the quote  ") is True
    # ...but it must still be an EXACT title match, not a fuzzy one.
    assert gtd_service.open_task_with_title_exists("Call Bob") is False


def test_a_finished_task_does_not_block_a_new_one(pg_db):
    from crm import gtd_service

    todo = gtd_service.create_todo("Chase the Acme quote", status="inbox", source="agent")
    assert gtd_service.open_task_with_title_exists("Chase the Acme quote") is True
    gtd_service.update_todo(todo["id"], {"status": "done"})
    assert gtd_service.open_task_with_title_exists("Chase the Acme quote") is False


def test_the_observed_day_follows_the_configured_timezone(pg_db, real_model, monkeypatch):
    """End to end through the real column type: a message written in the evening in a
    zone west of UTC must be labelled with the USER's day, not the session's."""
    import re

    monkeypatch.setenv("TIMEZONE", "America/Los_Angeles")
    from core.localtime import today_local

    cid = _conversation()
    _message(cid, 0, "user", "Dana works at Acme and owes us a quote", minutes_ago=30)
    _message(cid, 1, "user", "she said she would send it by tomorrow", minutes_ago=20)
    _observe_once(cid, ScriptedProvider(REPLY))

    prompt = real_model.prompts[0]
    today = today_local().isoformat()
    assert f"Today's date: {today}" in prompt
    days = set(re.findall(r"USER \[(\d{4}-\d{2}-\d{2})\]:", prompt))
    assert days == {today}, f"transcript days {days} disagree with today {today}"
