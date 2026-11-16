"""Heartbeat's set queries and deterministic, bounded model message."""

from crm import service
from heartbeat import dedupe


def test_current_items_runs_three_uncapped_queries_on_the_shared_predicates(monkeypatch):
    calls = []

    def fetch(sql, params):
        calls.append((sql, params))
        return [{"id": len(calls), "label": f"Record {len(calls)}"}]

    monkeypatch.setattr(dedupe, "pg_fetchall", fetch)
    assert dedupe.current_items() == {("todo", 1): "Record 1", ("deal", 2): "Record 2", ("contact", 3): "Record 3"}
    assert len(calls) == 3
    assert all("LIMIT" not in sql.upper() for sql, _ in calls)
    assert service.LIVE_TODO_PREDICATE in calls[0][0]
    assert service.NOT_DROPPED_TODO in calls[0][0]
    assert service.LAST_TOUCH_SQL in calls[1][0]
    assert service.OPEN_PREDICATE_D in calls[1][0]
    assert service.LIVE_PREDICATE_D in calls[1][0]
    assert "ct.status = 'active'" in calls[2][0]


def test_announced_reads_only_the_heartbeat_kind(monkeypatch):
    calls = []
    monkeypatch.setattr(dedupe, "pg_fetchall", lambda sql, params: calls.append((sql, params)) or [
        {"entity_type": "todo", "entity_id": 5}])
    assert dedupe.announced() == {("todo", 5)}
    assert len(calls) == 1 and calls[0][1] == ("heartbeat",)
    assert "WHERE kind = %s" in calls[0][0]


def test_save_sends_parallel_arrays_in_one_statement(monkeypatch):
    calls = []
    monkeypatch.setattr(dedupe, "pg_execute", lambda sql, params: calls.append((sql, params)))
    dedupe.save([("todo", 5), ("deal", 7)])
    assert len(calls) == 1
    assert calls[0][1] == (["todo", "deal"], [5, 7], "heartbeat", "heartbeat")
    dedupe.save([])
    assert calls[1][1] == ([], [], "heartbeat", "heartbeat")


def test_describe_groups_by_type_caps_the_list_and_never_writes_a_hash_id():
    records = {("deal", n): f"Example {n}" for n in range(12, 0, -1)}
    records[("todo", 3)] = "Call\n  tomorrow " + "x" * 90
    text = dedupe.describe(records)
    assert "Deals with no recent activity (12 new)" in text
    assert "and 2 more" in text
    assert "todo 3: Call tomorrow" in text
    assert "deal 10:" in text and "deal 11:" not in text
    assert "#" not in text
    assert len(text.splitlines()) == 2
    assert len(text.splitlines()[0].split("todo 3: ")[1]) == 80
