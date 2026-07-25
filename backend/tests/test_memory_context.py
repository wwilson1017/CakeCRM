"""memory/context.py — building the per-turn 'Long-term memory' block.

The load-bearing behaviors: matches surface first, backfill fills to the limit,
ONLY matches count as retrievals (backfill is filler), None/blank text uses backfill
only, and any service failure degrades to an empty block (never raises).
"""

import pytest

from memory import context


@pytest.fixture
def svc(monkeypatch):
    """Install stub search_facts/query_facts/track_retrieval_for and record tracked ids."""
    state = {"search": [], "backfill": [], "tracked": None, "search_calls": 0, "last_query": None}

    def fake_search(query, limit=20, track_retrieval=True, **kw):
        state["search_calls"] += 1
        state["last_query"] = query
        return list(state["search"])

    def fake_query(limit=50, track_retrieval=True, **kw):
        return list(state["backfill"])

    def fake_track(ids):
        state["tracked"] = list(ids)

    monkeypatch.setattr(context.service, "search_facts", fake_search)
    monkeypatch.setattr(context.service, "query_facts", fake_query)
    monkeypatch.setattr(context.service, "track_retrieval_for", fake_track)
    return state


def _fact(fid, subject="Dana", predicate="works at", object_="Acme", memory_type=None, valid_from="2026-07-01"):
    return {"id": fid, "subject": subject, "predicate": predicate, "object": object_,
            "memory_type": memory_type, "valid_from": valid_from}


def test_renders_matches_inside_a_nonce_fence(svc):
    svc["search"] = [_fact(1, memory_type="person")]
    out = context.build_memory_context("tell me about Dana at Acme")
    assert "<recorded_memory id=" in out and "</recorded_memory id=" in out
    assert "- [person] Dana — works at — Acme (since 2026-07-01)" in out


def test_matches_first_then_backfill_deduped(svc):
    svc["search"] = [_fact(1, subject="A")]
    svc["backfill"] = [_fact(1, subject="A"), _fact(2, subject="B")]  # id=1 duplicates the match
    out = context.build_memory_context("something about A")
    # id 1 appears once (dedup), id 2 backfilled
    assert out.count("— works at —") == 2


def test_only_matches_are_tracked_not_backfill(svc):
    svc["search"] = [_fact(1)]
    svc["backfill"] = [_fact(2), _fact(3)]
    context.build_memory_context("find one")
    assert svc["tracked"] == [1]   # backfill ids 2,3 are NOT tracked


def test_none_user_text_uses_backfill_only(svc):
    svc["backfill"] = [_fact(5)]
    out = context.build_memory_context(None)
    assert svc["search_calls"] == 0
    assert "Dana" in out
    assert svc["tracked"] == []    # nothing matched → nothing tracked


def test_no_facts_returns_empty(svc):
    out = context.build_memory_context("anything")
    assert out == ""


def test_limit_is_respected(svc):
    svc["backfill"] = [_fact(i) for i in range(50)]
    out = context.build_memory_context(None)
    assert out.count("- ") == context.MEMORY_CONTEXT_FACT_LIMIT


def test_long_user_text_is_sliced_before_tokenizing(svc):
    svc["search"] = [_fact(1)]
    # Should not raise / hang on a huge input; the cap bounds tokenization.
    context.build_memory_context("word " * 5000)
    assert svc["tracked"] == [1]


def test_service_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(context.service, "search_facts", boom)
    monkeypatch.setattr(context.service, "query_facts", boom)
    assert context.build_memory_context("x") == ""


def test_search_receives_bounded_raw_user_text(svc):
    # Tokenization now lives in service.search_facts; the builder just passes the
    # (length-bounded) raw user text through.
    svc["search"] = [_fact(1)]
    context.build_memory_context("tell me about Dana at Acme corp")
    assert svc["last_query"] == "tell me about Dana at Acme corp"


def test_long_user_text_bounded_before_search(svc):
    svc["search"] = [_fact(1)]
    context.build_memory_context("word " * 5000)
    assert len(svc["last_query"]) <= context._USER_TEXT_CAP
