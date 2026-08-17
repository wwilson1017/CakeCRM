"""Sales-intelligence reads (issue #22) — SQL shape, clamping, and payload contract.

Hermetic: ``crm.analytics_service``'s pg helpers are monkeypatched, so these pin the
query SHAPE and the pure shaping logic. The queries themselves run against a real
Postgres in ``test_integration_crm_lifecycle_pg.py`` — several of them use
window/array/interval features that a mock cannot validate.

These functions are the ones the proactive heartbeat will call unattended, so the
things asserted hardest here are the ones a background job gets wrong quietly:
bounds on every model-supplied number, and never counting an archived or closed deal.
"""

import pytest

from crm import analytics_service as az
from tests.test_crm_service import Recorder


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(az, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(az, "pg_fetchall", r.fetchall)
    return r


# ── bounds ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [0, -5, 10_000, None, "twenty", 1.5e9])
def test_every_limit_is_clamped(bad):
    """The LLM writes these numbers. An absurd or non-numeric value must bound to
    something sane rather than reach the database."""
    out = az._bounded(bad, default=20)
    assert isinstance(out, int) and 1 <= out <= az.MAX_LIMIT


def test_stale_days_and_limit_are_clamped_on_the_query(rec):
    rec.fetchall_queue = [[]]
    rec.fetchone_queue = [{"cnt": 0}]
    az.get_stale_deals(stale_days=99999, limit=99999)
    params = rec.params_for("FROM deals d")
    assert params == [365, az.MAX_LIMIT]


# ── stale deals ───────────────────────────────────────────────────────────────

def test_stale_deals_excludes_closed_and_archived(rec):
    rec.fetchall_queue = [[]]
    rec.fetchone_queue = [{"cnt": 0}]
    az.get_stale_deals()
    sql = rec.sql_containing("FROM deals d")
    assert "d.stage NOT IN ('won', 'lost')" in sql
    assert "d.archived_at IS NULL" in sql


def test_stale_deals_shares_the_last_touch_definition_with_the_dashboard(rec):
    """The dashboard's stale COUNT and this list must agree on what 'touched' means —
    they read the same SQL constant, and this pins that they still do."""
    from crm.service import LAST_TOUCH_SQL

    rec.fetchall_queue = [[]]
    rec.fetchone_queue = [{"cnt": 0}]
    az.get_stale_deals()
    assert " ".join(LAST_TOUCH_SQL.split()) in rec.sql_containing("FROM deals d")


def test_stale_deals_reports_days_in_stage_and_open_task(rec):
    rec.fetchall_queue = [[]]
    rec.fetchone_queue = [{"cnt": 0}]
    az.get_stale_deals()
    sql = rec.sql_containing("FROM deals d")
    assert "deal_stage_events" in sql and "d.created_at" in sql  # with a fallback
    assert "AS has_open_task" in sql


def test_stale_total_is_counted_before_the_limit(rec):
    """A truncated list must never understate the problem."""
    rec.fetchall_queue = [[{"id": 1}, {"id": 2}]]
    rec.fetchone_queue = [{"cnt": 57}]
    out = az.get_stale_deals(limit=2)
    assert out["count"] == 2 and out["total_stale"] == 57


# ── contact staleness ────────────────────────────────────────────────────────

def test_contact_staleness_puts_never_contacted_first(rec):
    rec.fetchall_queue = [[]]
    az.get_contact_staleness()
    sql = rec.sql_containing("FROM contacts ct")
    assert "lt.touched_at IS NULL OR" in sql          # never-contacted are included
    assert "ORDER BY lt.touched_at ASC NULLS FIRST" in sql


def test_contact_staleness_only_considers_active_contacts(rec):
    rec.fetchall_queue = [[]]
    az.get_contact_staleness()
    assert "ct.status = 'active'" in rec.sql_containing("FROM contacts ct")


def test_contact_staleness_open_deal_count_ignores_archived(rec):
    rec.fetchall_queue = [[]]
    az.get_contact_staleness()
    sql = rec.sql_containing("AS open_deals")
    assert "d.archived_at IS NULL" in sql and "d.stage NOT IN ('won', 'lost')" in sql


# ── duplicates ───────────────────────────────────────────────────────────────

def test_duplicate_matching_is_normalized_exact_not_fuzzy(rec):
    """A false positive invites the assistant to merge two genuinely distinct
    records. The bar is 'same string typed twice'."""
    rec.fetchall_queue = [[], [], []]
    az.find_duplicate_contacts()
    sql = rec.sql_containing("FROM contacts")
    assert "lower(btrim(email))" in sql
    assert "ILIKE" not in sql and "similarity" not in sql
    assert "HAVING COUNT(*) > 1" in sql


def test_duplicate_contacts_skip_blank_keys(rec):
    rec.fetchall_queue = [[], [], []]
    az.find_duplicate_contacts()
    assert "btrim(email) <> ''" in rec.sql_containing("lower(btrim(email))")


def test_duplicate_deals_require_the_same_contact(rec):
    """Title alone is a false-positive machine: the same title across ten customers
    is not a duplicate."""
    rec.fetchall_queue = [[], []]
    az.find_duplicate_deals()
    sql = rec.sql_containing("FROM deals")
    assert "GROUP BY lower(btrim(title)), contact_id" in sql
    assert "contact_id IS NOT NULL" in sql
    assert "archived_at IS NULL" in sql


def test_duplicate_groups_are_shaped_with_labelled_records(rec, monkeypatch):
    rec.fetchall_queue = [
        [{"match_value": "a@b.com", "count": 2, "ids": [3, 9]}],  # by email
        [],                                                        # by name
        [{"id": 3, "label": "Ana"}, {"id": 9, "label": "Ana R"}],  # label lookup
    ]
    groups = az.find_duplicate_contacts()
    assert groups == [{
        "match_on": "email", "value": "a@b.com", "count": 2,
        "records": [{"id": 3, "label": "Ana"}, {"id": 9, "label": "Ana R"}],
    }]


def test_find_duplicates_rejects_an_unknown_entity_type(rec):
    assert "error" in az.find_duplicates(entity_type="invoice")


def test_find_duplicates_all_covers_every_entity(rec):
    rec.fetchall_queue = [[] for _ in range(12)]
    out = az.find_duplicates()
    assert set(out) == {"contacts", "companies", "deals", "total_groups"}


# ── gap scan ─────────────────────────────────────────────────────────────────

def test_scan_gaps_orders_worst_first_and_names_the_missing_fields(rec):
    rec.fetchall_queue = [[], [], [], []]
    az.scan_gaps()
    sql = rec.sql_containing("FROM contacts")
    assert "AS missing_fields" in sql
    assert "ORDER BY cardinality(missing_fields) DESC" in sql


def test_scan_gaps_ignores_closed_and_archived_deals(rec):
    rec.fetchall_queue = [[], [], [], []]
    az.scan_gaps()
    sql = rec.sql_containing("FROM deals")
    assert "archived_at IS NULL" in sql and "stage NOT IN ('won', 'lost')" in sql


def test_scan_gaps_surfaces_unconfirmed_assistant_writes(rec):
    """The values most worth a second look are the ones the assistant wrote and
    nobody has confirmed — nothing else exposes those to the model."""
    rec.fetchall_queue = [[], [], [], [{"entity_type": "deal", "field_name": "value"}]]
    out = az.scan_gaps()
    sql = rec.sql_containing("crm_field_provenance")
    assert "confirmed_at IS NULL" in sql
    assert out["unverified_fields"][0]["field_name"] == "value"


def test_scan_gaps_total_excludes_the_provenance_list(rec):
    rec.fetchall_queue = [[{"id": 1}], [{"id": 2}], [], [{"x": 1}, {"x": 2}]]
    assert az.scan_gaps()["total_gaps"] == 2


def test_scan_gaps_rejects_an_unknown_entity_type(rec):
    assert "error" in az.scan_gaps(entity_type="invoice")


def test_scan_gaps_single_entity_scans_only_that_entity(rec):
    rec.fetchall_queue = [[], []]
    out = az.scan_gaps(entity_type="deal")
    assert "deals" in out and "contacts" not in out and "companies" not in out
    assert rec.params_for("crm_field_provenance")[0] == ["deal"]


def test_scan_gaps_company_branch_checks_domain_industry_and_phone(rec):
    """The crm_scan_gaps tool advertises company gap scanning, so the branch needs a
    check of its own — the contact/deal tests above never touch it."""
    rec.fetchall_queue = [[], []]
    out = az.scan_gaps(entity_type="company")
    sql = rec.sql_containing("FROM companies")
    for label, column in (("domain", "domain"), ("industry", "industry"), ("phone", "phone")):
        assert f"ARRAY['{label}']" in sql and f"btrim({column}) = ''" in sql
    assert "status = 'active'" in sql          # archived companies aren't neglected
    assert "companies" in out and "contacts" not in out and "deals" not in out


def test_find_duplicate_companies_matches_domain_then_name(rec):
    """Covered hermetically, not only by the integration suite — CI runs
    `pytest -m 'not integration'`, so an integration-only test guards nothing in CI."""
    rec.fetchall_queue = [[], [], []]
    az.find_duplicate_companies()
    domain_sql = rec.sql_containing("lower(btrim(domain))")
    assert "btrim(domain) <> ''" in domain_sql and "HAVING COUNT(*) > 1" in domain_sql
    name_sql = rec.sql_containing("lower(btrim(name))")
    assert "FROM companies" in name_sql and "btrim(name) <> ''" in name_sql


def test_predicates_are_imported_not_retyped():
    """The live/open predicates have ONE definition (crm.service). A local copy here is
    how a sweep site silently gets left behind when the definition changes."""
    import inspect

    from crm import service
    src = inspect.getsource(az)
    assert az.LIVE_PREDICATE is service.LIVE_PREDICATE
    assert az.OPEN_PREDICATE is service.OPEN_PREDICATE
    assert "archived_at IS NULL" not in src
    assert "stage NOT IN" not in src
