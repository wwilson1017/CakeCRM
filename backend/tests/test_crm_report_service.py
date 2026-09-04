"""Hermetic tests for the Reports company rollup (issue #144).

Two things these pin that a behavioural test over canned rows cannot: the SQL TEXT of the
predicates that decide attribution (a canned row set passes whether or not the predicate
exists), and the exact ORDER BY term list of the two-source timeline (which is a silent
correctness bug when wrong, not a visible one).
"""

import re

import pytest

from crm import report_service


class Recorder:
    """Records every query and answers from a queue, in call order."""

    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.fetchone_queue: list = []
        self.fetchall_queue: list = []

    def fetchone(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def fetchall(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchall_queue.pop(0) if self.fetchall_queue else []

    def sql_containing(self, needle: str) -> str:
        for sql, _ in self.calls:
            if needle in sql:
                return sql
        raise AssertionError(f"no query contained {needle!r}; saw {[c[0] for c in self.calls]}")

    def params_for(self, needle: str) -> list:
        for sql, params in self.calls:
            if needle in sql:
                return params
        raise AssertionError(f"no query contained {needle!r}")

    def has_sql_containing(self, needle: str) -> bool:
        return any(needle in sql for sql, _ in self.calls)


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(report_service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(report_service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(
        report_service.field_service, "list_field_definitions", lambda entity_type: []
    )
    monkeypatch.setattr(
        report_service.field_service,
        "get_field_values_batch",
        lambda entity_type, ids: {},
    )
    monkeypatch.setattr(
        report_service.attachment_service, "list_for_notes", lambda ids: {}
    )
    return r


COMPANY = {"id": 7, "name": "Acme", "status": "active"}


def _prime_rollup(rec, contacts=None, deals=None, deal_acts=None, contact_acts=None, tasks=None):
    rec.fetchone_queue = [
        COMPANY,
        {"open_deal_count": 0, "open_deal_value": 0, "contact_count": 0},
    ]
    rec.fetchall_queue = [
        contacts if contacts is not None else [],
        deals if deals is not None else [],
    ]
    if deals:
        rec.fetchall_queue.append(deal_acts or [])
    if contacts:
        rec.fetchall_queue.append(contact_acts or [])
    if deals:
        rec.fetchall_queue.append(tasks or [])


# ── The 404 seam ────────────────────────────────────────────────────────────────────


def test_missing_company_returns_none_for_both_reads(rec):
    rec.fetchone_queue = [None]
    assert report_service.get_company_rollup(7) is None
    rec.fetchone_queue = [None]
    assert report_service.get_company_timeline(7) is None


# ── Attribution: the bug the first draft shipped ────────────────────────────────────


def test_contact_activity_bucket_tests_deal_id_is_null_absolutely(rec):
    """The contact bucket must exclude EVERY deal-bearing row, not just displayed deals.

    Testing membership of the displayed deal set instead ("deal_id <> ALL(deal_ids)") looks
    equivalent and is not: an ARCHIVED deal is absent from that set, so its activities
    reappear under the contact while archived history is switched off. Asserted on the SQL
    text because canned rows pass either way.
    """
    _prime_rollup(rec, contacts=[{"id": 3, "name": "Ada"}], deals=[])
    report_service.get_company_rollup(7)
    sql = rec.sql_containing("PARTITION BY a.contact_id")
    assert "a.deal_id IS NULL" in sql
    assert "ALL(" not in sql, "membership-of-displayed-set reintroduces the archived leak"


def test_timeline_reaches_a_deal_activity_only_through_its_deal(rec):
    """Same rule on the feed: an activity naming a deal must not be selected via a contact.

    If it were, an archived deal's activity would ride in on the contact branch and then be
    LABELLED with the archived deal's name — archived history displayed with the toggle off.
    """
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [[]]
    report_service.get_company_timeline(7)
    sql = rec.sql_containing("UNION ALL")
    activity_half = sql.split("UNION ALL", 1)[1]
    assert "a.deal_id IS NULL AND a.contact_id IN" in activity_half


def test_archived_deals_are_excluded_from_the_timeline_by_default(rec):
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [[]]
    report_service.get_company_timeline(7)
    sql = rec.sql_containing("UNION ALL")
    assert sql.count("archived_at IS NULL") == 2, "both note and activity deal sub-selects"
    assert "ch.archived = 0" in sql


def test_include_archived_widens_deals_and_notes_together(rec):
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [[]]
    report_service.get_company_timeline(7, include_archived=True)
    sql = rec.sql_containing("UNION ALL")
    assert "archived_at IS NULL" not in sql
    assert "ch.archived = 0" not in sql


# ── The total order ─────────────────────────────────────────────────────────────────


def test_timeline_order_is_total_across_both_sources(rec):
    """`source` is what makes the key unique across two independent SERIAL sequences.

    Pinned as the exact term list: dropping `source` leaves a clause the #58 scanner still
    accepts (it judges the final term's NAME, and `id` is allowlisted) while note #7 and
    activity #7 tie — one row on two pages, another skipped. This test fails deterministically
    without it; the scanner does not.
    """
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [[]]
    report_service.get_company_timeline(7)
    sql = rec.sql_containing("UNION ALL")
    order = re.search(r"ORDER BY (.+?) LIMIT", sql).group(1)
    terms = [t.strip().split()[0].split(".")[-1] for t in order.split(",")]
    assert terms == ["created_at", "source", "id"]


def test_rollup_child_reads_end_on_a_unique_tiebreaker(rec):
    _prime_rollup(rec)
    report_service.get_company_rollup(7)
    assert "ORDER BY name ASC, id ASC LIMIT" in rec.sql_containing("FROM contacts WHERE company_id = %s ORDER BY")
    assert "ORDER BY d.updated_at DESC, d.id DESC LIMIT" in rec.sql_containing("FROM deals d")


# ── Caps, probes and truncation flags ───────────────────────────────────────────────


def test_every_capped_read_asks_for_one_row_past_its_cap(rec):
    """Binding the bare cap makes every truncation flag permanently unreachable."""
    _prime_rollup(rec, contacts=[{"id": 3, "name": "Ada"}], deals=[{"id": 9, "value": 1}])
    report_service.get_company_rollup(7)
    assert rec.params_for("FROM contacts WHERE company_id = %s ORDER BY")[-1] == report_service.ROLLUP_CHILD_CAP + 1
    assert rec.params_for("FROM deals d")[-1] == report_service.ROLLUP_CHILD_CAP + 1
    assert rec.params_for("PARTITION BY a.deal_id")[-1] == report_service.ACTIVITY_PER_RECORD_CAP + 1
    assert rec.params_for("PARTITION BY a.contact_id")[-1] == report_service.ACTIVITY_PER_RECORD_CAP + 1
    assert rec.params_for("PARTITION BY tasks.deal_id")[-1] == report_service.TASKS_PER_DEAL_CAP + 1


@pytest.mark.parametrize("returned,expected", [(1, False), (2, True)])
def test_deal_truncation_flag_follows_the_probe_row(rec, monkeypatch, returned, expected):
    monkeypatch.setattr(report_service, "ROLLUP_CHILD_CAP", 1)
    _prime_rollup(rec, deals=[{"id": i, "value": 0} for i in range(returned)])
    result = report_service.get_company_rollup(7)
    assert result["deals_truncated"] is expected
    assert len(result["deals"]) == 1


def test_per_record_activity_cap_is_per_parent_not_global(rec, monkeypatch):
    """A busy deal must not starve a quiet sibling of its history."""
    monkeypatch.setattr(report_service, "ACTIVITY_PER_RECORD_CAP", 2)
    acts = [{"id": i, "deal_id": 9, "created_at": "t", "rn": i} for i in range(3)]
    acts += [{"id": 90, "deal_id": 10, "created_at": "t", "rn": 1}]
    _prime_rollup(rec, deals=[{"id": 9, "value": 0}, {"id": 10, "value": 0}], deal_acts=acts)
    result = report_service.get_company_rollup(7)
    busy, quiet = result["deals"]
    assert (len(busy["activities"]), busy["activities_truncated"]) == (2, True)
    assert (len(quiet["activities"]), quiet["activities_truncated"]) == (1, False)


def test_window_bookkeeping_never_reaches_the_client(rec):
    _prime_rollup(
        rec,
        deals=[{"id": 9, "value": 0}],
        deal_acts=[{"id": 1, "deal_id": 9, "created_at": "t", "rn": 1}],
    )
    result = report_service.get_company_rollup(7)
    assert "rn" not in result["deals"][0]["activities"][0]


def test_children_without_activity_still_carry_both_keys(rec):
    _prime_rollup(rec, contacts=[{"id": 3, "name": "Ada"}], deals=[{"id": 9, "value": 0}])
    result = report_service.get_company_rollup(7)
    for row in (result["contacts"][0], result["deals"][0]):
        assert row["activities"] == [] and row["activities_truncated"] is False
    assert result["deals"][0]["tasks"] == [] and result["deals"][0]["tasks_truncated"] is False
    assert result["deals"][0]["last_activity_at"] is None


def test_no_child_reads_fire_for_an_empty_company(rec):
    """An empty `= ANY('{}')` is a wasted round trip, so each child read is guarded."""
    _prime_rollup(rec)
    report_service.get_company_rollup(7)
    assert not rec.has_sql_containing("PARTITION BY a.deal_id")
    assert not rec.has_sql_containing("PARTITION BY a.contact_id")
    assert not rec.has_sql_containing("PARTITION BY tasks.deal_id")


# ── Headline numbers ────────────────────────────────────────────────────────────────


def test_summary_is_its_own_aggregate_over_the_full_tables(rec):
    """Reducing the capped lists instead lets the archive toggle move a headline number.

    Archived deals would compete for the same child window, so enabling MORE history could
    make the open-deal count go DOWN. This query is bounded by neither cap nor toggle.
    """
    rec.fetchone_queue = [COMPANY, {"open_deal_count": 4, "open_deal_value": 900, "contact_count": 2}]
    rec.fetchall_queue = [[], []]
    result = report_service.get_company_rollup(7, include_archived=True)
    sql = rec.sql_containing("open_deal_count")
    assert "archived_at IS NULL" in sql and "stage NOT IN" in sql
    assert "LIMIT" not in sql
    assert result["summary"]["open_deal_count"] == 4


def test_contact_count_excludes_archived_contacts_though_the_list_includes_them(rec):
    _prime_rollup(rec, contacts=[{"id": 3, "name": "Ada", "status": "archived"}])
    report_service.get_company_rollup(7)
    assert "status <> 'archived'" in rec.sql_containing("contact_count")
    assert "status" not in rec.sql_containing("FROM contacts WHERE company_id = %s ORDER BY name")


# ── Custom fields ───────────────────────────────────────────────────────────────────


def test_custom_fields_list_every_definition_including_unset_ones(rec, monkeypatch):
    """"Every field" includes the ones nobody filled in — a values-only read cannot say so."""
    monkeypatch.setattr(
        report_service.field_service,
        "list_field_definitions",
        lambda entity_type: [
            {"field_key": "region", "name": "Region", "field_type": "text"},
            {"field_key": "tier", "name": "Tier", "field_type": "select"},
        ],
    )
    monkeypatch.setattr(
        report_service.field_service,
        "get_field_values_batch",
        lambda entity_type, ids: {9: {"region": "South"}},
    )
    _prime_rollup(rec, deals=[{"id": 9, "value": 0}])
    result = report_service.get_company_rollup(7)
    assert result["deals"][0]["custom_fields"] == [
        {"field_key": "region", "name": "Region", "field_type": "text", "value": "South"},
        {"field_key": "tier", "name": "Tier", "field_type": "select", "value": None},
    ]


def test_custom_fields_cost_two_queries_per_entity_type_not_per_record(rec, monkeypatch):
    """This batching is what stops "Expand all" becoming ~150 HTTP requests."""
    calls: list[str] = []
    monkeypatch.setattr(
        report_service.field_service,
        "list_field_definitions",
        lambda entity_type: calls.append(f"defs:{entity_type}")
        or [{"field_key": "k", "name": "K", "field_type": "text"}],
    )
    monkeypatch.setattr(
        report_service.field_service,
        "get_field_values_batch",
        lambda entity_type, ids: calls.append(f"vals:{entity_type}") or {},
    )
    _prime_rollup(
        rec,
        contacts=[{"id": i, "name": "C"} for i in range(5)],
        deals=[{"id": 100 + i, "value": 0} for i in range(5)],
    )
    report_service.get_company_rollup(7)
    assert sorted(calls) == [
        "defs:company", "defs:contact", "defs:deal",
        "vals:company", "vals:contact", "vals:deal",
    ]


# ── Tasks ───────────────────────────────────────────────────────────────────────────


def test_deal_tasks_carry_the_not_dropped_predicate_and_are_open_only(rec):
    _prime_rollup(rec, deals=[{"id": 9, "value": 0}])
    report_service.get_company_rollup(7)
    sql = rec.sql_containing("PARTITION BY tasks.deal_id")
    assert "tasks.status != 'dropped'" in sql
    assert "tasks.completed = 0" in sql


# ── Timeline paging and hydration ───────────────────────────────────────────────────


def test_has_more_comes_from_the_probe_row_and_entries_are_trimmed(rec):
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [
        [{"id": i, "source": "note", "entity_type": "company", "entity_id": 7} for i in range(3)]
    ]
    page = report_service.get_company_timeline(7, limit=2)
    assert page["has_more"] is True and len(page["entries"]) == 2
    assert rec.params_for("UNION ALL")[-2:] == [3, 0]
    assert not rec.has_sql_containing("COUNT(")


def test_limit_is_clamped_to_the_documented_ceiling(rec):
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [[]]
    report_service.get_company_timeline(7, limit=10_000, offset=-5)
    assert rec.params_for("UNION ALL")[-2:] == [report_service.TIMELINE_MAX_LIMIT + 1, 0]


def test_hydration_is_scoped_to_the_ids_the_page_cites(rec):
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [
        [
            {"id": 1, "source": "note", "entity_type": "deal", "entity_id": 41},
            {"id": 2, "source": "note", "entity_type": "deal", "entity_id": 41},
        ],
        [{"id": 41, "title": "Q3", "archived_at": None}],
    ]
    report_service.get_company_timeline(7)
    assert rec.params_for("SELECT id, title, archived_at") == [[41]]
    assert not rec.has_sql_containing("SELECT id, name, status FROM contacts")


def test_a_source_that_moved_companies_is_labelled_not_raised(rec):
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [
        [{"id": 1, "source": "note", "entity_type": "deal", "entity_id": 999}],
        [],
    ]
    entry = report_service.get_company_timeline(7)["entries"][0]
    assert entry["source_name"] == "deal #999" and entry["source_archived"] is False


def test_archived_sources_are_marked_on_their_own_column(rec):
    """Deals archive on `archived_at`, contacts and companies on a `status` string."""
    rec.fetchone_queue = [COMPANY]
    rec.fetchall_queue = [
        [
            {"id": 1, "source": "note", "entity_type": "deal", "entity_id": 41},
            {"id": 2, "source": "note", "entity_type": "contact", "entity_id": 3},
        ],
        [{"id": 3, "name": "Ada", "status": "archived"}],
        [{"id": 41, "title": "Q3", "archived_at": "2026-02-01T00:00:00+00:00"}],
    ]
    entries = report_service.get_company_timeline(7, include_archived=True)["entries"]
    assert [(e["source_name"], e["source_archived"]) for e in entries] == [
        ("Q3", True),
        ("Ada", True),
    ]
