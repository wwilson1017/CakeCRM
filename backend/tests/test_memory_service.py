"""memory/service.py — facts CRUD + FTS, hermetic (pg helpers monkeypatched).

Captures the SQL + params each call issues so the query shape (parameterized FTS,
archived/expired filters, throttle predicate) is pinned without a database.
"""

import pytest

from memory import service


class _Recorder:
    """Records (sql, params) for pg_execute/pg_fetchone/pg_fetchall and returns
    canned results. SQL is whitespace-normalized for easy substring assertions."""

    def __init__(self, fetchone=None, fetchall=None, rowcount=1):
        self.calls: list[tuple[str, object]] = []
        self._fetchone = fetchone
        self._fetchall = fetchall if fetchall is not None else []
        self._rowcount = rowcount

    def _norm(self, sql):
        return " ".join(sql.split())

    def execute(self, sql, params=()):
        self.calls.append((self._norm(sql), params))
        return self._rowcount

    def fetchone(self, sql, params=()):
        self.calls.append((self._norm(sql), params))
        return self._fetchone

    def fetchall(self, sql, params=()):
        self.calls.append((self._norm(sql), params))
        return list(self._fetchall)

    def last_sql(self):
        return self.calls[-1][0]

    def last_params(self):
        return self.calls[-1][1]


@pytest.fixture
def rec(monkeypatch):
    def _install(fetchone=None, fetchall=None, rowcount=1):
        r = _Recorder(fetchone=fetchone, fetchall=fetchall, rowcount=rowcount)
        monkeypatch.setattr(service, "pg_execute", r.execute)
        monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
        monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
        return r
    return _install


# ── add_fact ──────────────────────────────────────────────────────────────────

def test_add_fact_inserts_and_shapes_result(rec):
    r = rec(fetchone={"id": 7, "subject": "Dana", "predicate": "works at",
                      "object": "Acme", "valid_from": "2026-07-24", "memory_type": "person"})
    out = service.add_fact("Dana", "works at", "Acme", memory_type="person")
    assert out["ok"] is True and out["id"] == 7
    assert "INSERT INTO memory_facts" in r.last_sql()
    assert "COALESCE(%s::date, CURRENT_DATE)" in r.last_sql()


def test_add_fact_rejects_blank_fields_before_db(rec):
    r = rec(fetchone={"id": 1})
    assert service.add_fact("   ", "p", "o") == {"error": "subject is required"}
    assert service.add_fact("s", "", "o") == {"error": "predicate is required"}
    assert service.add_fact("s", "p", "   ") == {"error": "object is required"}
    assert r.calls == []  # never reached the DB


def test_add_fact_single_lines_and_caps_fields(rec):
    r = rec(fetchone={"id": 1})
    service.add_fact("multi\n  line   subject", "p", "x" * 999)
    subj, obj = r.last_params()[0], r.last_params()[2]
    assert subj == "multi line subject"        # whitespace collapsed
    assert len(obj) == 500                       # capped


def test_add_fact_clamps_confidence_and_stores_valid_type(rec):
    r = rec(fetchone={"id": 1})
    service.add_fact("s", "p", "o", confidence=5.0, memory_type="person")
    params = r.last_params()
    assert params[6] == 1.0            # confidence clamped to 1.0
    assert params[7] == "person"       # valid memory_type stored


def test_add_fact_rejects_invalid_memory_type_before_db(rec):
    r = rec(fetchone={"id": 1})
    out = service.add_fact("s", "p", "o", memory_type="decison")  # typo
    assert "error" in out and "memory_type must be one of" in out["error"]
    assert r.calls == []   # rejected before INSERT — a typo can't silently strip tier-1


def test_add_fact_rejects_malformed_valid_from(rec):
    r = rec(fetchone={"id": 1})
    assert "error" in service.add_fact("s", "p", "o", valid_from="not-a-date")
    assert r.calls == []


# ── query_facts ─────────────────────────────────────────────────────────────────

def test_query_facts_excludes_archived_and_expired_by_default(rec):
    r = rec(fetchall=[])
    service.query_facts()
    assert "archived_at IS NULL" in r.last_sql()
    assert "valid_to IS NULL" in r.last_sql()


def test_query_facts_include_archived_and_expired_lift_filters(rec):
    r = rec(fetchall=[])
    service.query_facts(include_archived=True, include_expired=True)
    assert "archived_at IS NULL" not in r.last_sql()
    assert "valid_to IS NULL" not in r.last_sql()


def test_query_facts_as_of_window(rec):
    r = rec(fetchall=[])
    service.query_facts(as_of="2026-01-01")
    sql = r.last_sql()
    assert "valid_from <= %s::date" in sql and "valid_to IS NULL OR valid_to >= %s::date" in sql


def test_query_facts_partial_match_filters(rec):
    r = rec(fetchall=[])
    service.query_facts(subject="dana", predicate="works")
    sql = r.last_sql()
    assert "subject ILIKE" in sql and "predicate ILIKE" in sql
    assert "dana" in r.last_params() and "works" in r.last_params()


def test_query_facts_tracks_retrieval_of_results(rec):
    r = rec(fetchall=[{"id": 3}, {"id": 4}])
    service.query_facts()
    # last call is the tracking UPDATE
    assert "retrieval_count = retrieval_count + 1" in r.last_sql()
    assert r.last_params() == ([3, 4],)


def test_query_facts_can_skip_tracking(rec):
    r = rec(fetchall=[{"id": 3}])
    service.query_facts(track_retrieval=False)
    assert all("retrieval_count" not in sql for sql, _ in r.calls)


# ── search_facts (FTS) ──────────────────────────────────────────────────────────

def test_search_blank_query_hits_no_db(rec):
    r = rec(fetchall=[{"id": 1}])
    assert service.search_facts("   ") == []
    assert r.calls == []


def test_search_uses_to_tsquery_simple_with_or_terms(rec):
    r = rec(fetchall=[])
    service.search_facts("dana acme")
    sql = r.last_sql()
    assert "to_tsquery('simple', %s)" in sql
    assert "websearch_to_tsquery" not in sql
    assert "search_tsv @@ q" in sql
    assert "valid_to IS NULL AND archived_at IS NULL" in sql
    assert r.last_params()[0] == "dana | acme"   # OR-of-keywords, bound param


def test_or_tsquery_distinct_unicode_terms_len2():
    assert service._or_tsquery("Dana DANA Acme") == "dana | acme"   # deduped
    assert service._or_tsquery("Li US IT") == "li | us | it"        # len-2 entities kept
    assert service._or_tsquery("José") == "josé"                     # unicode word chars
    assert service._or_tsquery("&|!()") == ""                        # no usable terms


def test_search_all_punctuation_hits_no_db(rec):
    r = rec(fetchall=[{"id": 1}])
    assert service.search_facts("&|!()") == []   # tokenizes to nothing → no DB call
    assert r.calls == []


def test_search_query_is_length_capped(rec):
    r = rec(fetchall=[])
    service.search_facts("x" * 5000)
    assert len(r.last_params()[0]) == service._QUERY_MAX


def test_search_tracks_matches(rec):
    r = rec(fetchall=[{"id": 9}])
    service.search_facts("acme")
    assert "retrieval_count = retrieval_count + 1" in r.last_sql()
    assert r.last_params() == ([9],)


# ── invalidate_fact ─────────────────────────────────────────────────────────────

def test_invalidate_fact_returns_db_valid_to(rec):
    r = rec(fetchone={"id": 5, "valid_to": "2026-07-24"})
    out = service.invalidate_fact(5)
    assert out == {"id": 5, "valid_to": "2026-07-24", "ok": True}
    assert "RETURNING id, valid_to" in r.last_sql()


def test_invalidate_fact_bad_id(rec):
    r = rec(fetchone=None)
    assert service.invalidate_fact("abc") == {"error": "fact_id must be an integer"}
    assert r.calls == []


def test_invalidate_fact_not_found(rec):
    rec(fetchone=None)
    # The structured `not_found` flag (issue #72) lets the REST layer answer 404 rather
    # than 400 without matching on the message wording.
    assert service.invalidate_fact(999) == {"error": "Fact 999 not found", "not_found": True}


# ── track_retrieval_for ─────────────────────────────────────────────────────────

def test_track_retrieval_throttle_and_array(rec):
    r = rec()
    service.track_retrieval_for([1, 2, 3])
    sql = r.last_sql()
    assert "id = ANY(%s)" in sql
    assert "interval '1 hour'" in sql
    assert r.last_params() == ([1, 2, 3],)


def test_track_retrieval_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(service, "pg_execute", boom)
    # Must swallow — a tracking failure can't break a chat turn.
    service.track_retrieval_for([1])


def test_track_retrieval_empty_is_noop(rec):
    r = rec()
    service.track_retrieval_for([])
    assert r.calls == []


def test_add_fact_rejects_malformed_date(rec):
    r = rec(fetchone={"id": 1})
    assert service.add_fact("s", "p", "o", valid_from="not-a-date") == {
        "error": "valid_from must be a date in YYYY-MM-DD format"}
    assert r.calls == []   # rejected before any DB call


def test_add_fact_accepts_iso_date(rec):
    rec(fetchone={"id": 1, "subject": "s", "predicate": "p", "object": "o",
                  "valid_from": "2026-01-02", "memory_type": None})
    assert service.add_fact("s", "p", "o", valid_from="2026-01-02")["ok"] is True


def test_invalidate_fact_rejects_malformed_date(rec):
    r = rec(fetchone={"id": 1, "valid_to": "x"})
    assert service.invalidate_fact(5, valid_to="nope") == {
        "error": "valid_to must be a date in YYYY-MM-DD format"}
    assert r.calls == []


def test_or_tsquery_drops_function_words_with_fallback():
    # Function words are dropped so conversational queries don't OR-match facts on
    # filler (which would keep every fact perpetually "used" and defeat archival).
    assert service._or_tsquery("what do we know about Dana") == "know | dana"
    assert service._or_tsquery("works at Acme") == "works | acme"
    # An all-filler query falls back to the raw tokens so bare lookups still work.
    assert service._or_tsquery("is at") == "is | at"
    # Name/acronym homographs are NOT stoplisted, so they stay searchable as entities.
    assert service._or_tsquery("Li US IT") == "li | us | it"
