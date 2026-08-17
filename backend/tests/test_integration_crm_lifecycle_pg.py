"""Real-Postgres integration for the deal lifecycle + sales intelligence (issue #22).

These queries lean on Postgres features a mock cannot validate — array construction
and ``cardinality`` in the gap scan, ``ARRAY_AGG``/``HAVING`` in duplicate detection,
``make_interval``, ``GREATEST`` NULL semantics, ``NULLS FIRST`` ordering, and a real FK
inside the CRM-reset TRUNCATE. The hermetic suites pin the SQL shape; this one proves
the SQL actually runs and returns the right rows.

Marked ``integration`` and excluded from the default no-DB run (see pytest.ini). Same
throwaway-database pattern as test_integration_crm_pg.py.
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_lifecycle_{os.getpid()}"
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
def _clean(pg_db):
    from core.postgres import pg_execute
    pg_execute(
        "TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
        "crm_field_definitions, crm_field_values, crm_field_provenance, "
        "deal_stage_events RESTART IDENTITY"
    )
    yield


# ── Migration ─────────────────────────────────────────────────────────────────

def test_migrations_added_the_lifecycle_columns_and_stage_log(pg_db):
    from core.postgres import pg_fetchall

    cols = {r["column_name"] for r in pg_fetchall(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'deals'")}
    assert {"lost_reason", "archived_at"} <= cols

    event_cols = {r["column_name"]: r["is_nullable"] for r in pg_fetchall(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'deal_stage_events'")}
    assert {"id", "deal_id", "old_stage", "new_stage", "changed_at"} <= set(event_cols)
    assert event_cols["deal_id"] == "NO"


def test_migrations_are_idempotent(pg_db):
    """Every migration re-runs on each boot; a second apply must be a no-op."""
    from core import postgres
    postgres.run_migrations()


# ── Stage history ─────────────────────────────────────────────────────────────

def test_stage_moves_are_logged_and_plain_edits_are_not(pg_db):
    from core.postgres import pg_fetchall
    from crm import service

    deal = service.create_deal("Renewal", stage="lead")
    service.update_deal_stage(deal["id"], "qualified")
    service.update_deal(deal["id"], value=1200)          # no stage change
    service.update_deal(deal["id"], stage="proposal")

    events = pg_fetchall(
        "SELECT old_stage, new_stage FROM deal_stage_events WHERE deal_id = %s "
        "ORDER BY id", (deal["id"],))
    assert [(e["old_stage"], e["new_stage"]) for e in events] == [
        ("lead", "qualified"), ("qualified", "proposal")]


def test_stage_events_cascade_when_a_deal_row_goes_away(pg_db):
    from core.postgres import pg_execute, pg_fetchone
    from crm import service

    deal = service.create_deal("Temp", stage="lead")
    service.update_deal_stage(deal["id"], "won")
    pg_execute("DELETE FROM deals WHERE id = %s", (deal["id"],))
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deal_stage_events")["c"] == 0


# ── won / lost ────────────────────────────────────────────────────────────────

def test_mark_lost_records_reason_and_note_then_reopening_clears_it(pg_db):
    from crm import chatter_service, service

    deal = service.create_deal("Big one", stage="negotiation", probability=60)
    lost = service.mark_deal_lost(deal["id"], lost_reason="chose a competitor")
    assert lost["stage"] == "lost" and lost["probability"] == 0
    assert lost["lost_reason"] == "chose a competitor"
    notes = chatter_service.get_chatter("deal", deal["id"])
    assert any("chose a competitor" in n["message"] for n in notes)

    # Reopened: the reason must not survive into the timeline or win/loss reads.
    reopened = service.update_deal_stage(deal["id"], "negotiation")
    assert reopened["lost_reason"] == ""

    won = service.mark_deal_won(deal["id"])
    assert won["stage"] == "won" and won["probability"] == 100 and won["lost_reason"] == ""


# ── archive ───────────────────────────────────────────────────────────────────

def test_archiving_removes_a_deal_from_every_read_at_once(pg_db):
    from core.postgres import pg_execute
    from crm import analytics_service, service

    contact = service.create_contact("Ana")
    keep = service.create_deal("Keep", contact_id=contact["id"], value=100, stage="lead")
    junk = service.create_deal("Junk", contact_id=contact["id"], value=99999, stage="lead")

    service.archive_deal(junk["id"])

    board = service.get_pipeline()
    assert [d["id"] for d in board["deals"]] == [keep["id"]]
    assert board["total_pipeline_value"] == 100

    dash = service.get_dashboard_stats()
    assert dash["total_pipeline_value"] == 100
    assert [d["id"] for d in dash["top_deals"]] == [keep["id"]]

    assert service.get_analytics()["win_loss"]["open_deals"] == 1
    assert [d["id"] for d in service.list_deals()] == [keep["id"]]
    assert [d["id"] for d in service.search_deals(search="")] == [keep["id"]]
    assert [d["id"] for d in service.get_contact_detail(contact["id"])["deals"]] == [keep["id"]]

    # An archived deal is not "going stale" — it is put away. Age both so only the
    # live one can qualify.
    pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => 40)")
    stale = analytics_service.get_stale_deals(stale_days=14)
    assert [d["id"] for d in stale["deals"]] == [keep["id"]]
    assert stale["total_stale"] == 1

    # ...but the archived deal itself is still readable, so it can be restored.
    assert service.get_deal(junk["id"])["archived_at"] is not None
    assert not service.is_crm_empty()

    restored = service.archive_deal(junk["id"], archived=False)
    assert restored["archived_at"] is None
    assert len(service.get_pipeline()["deals"]) == 2


def test_repeat_archive_keeps_the_original_timestamp(pg_db):
    from crm import service

    deal = service.create_deal("Junk")
    first = service.archive_deal(deal["id"])["archived_at"]
    again = service.archive_deal(deal["id"])["archived_at"]
    assert first == again


# ── merge ─────────────────────────────────────────────────────────────────────

def test_merge_moves_history_gap_fills_fields_and_archives_the_source(pg_db, monkeypatch):
    from crm import chatter_service, field_service, service, touch_count_service

    monkeypatch.setattr(touch_count_service, "schedule_recompute", lambda *a, **k: True)

    contact = service.create_contact("Ana")
    target = service.create_deal("Kept deal", contact_id=contact["id"], value=500)
    source = service.create_deal("Dupe deal", contact_id=contact["id"], value=700)

    service.log_activity("call", note="talked", deal_id=source["id"])
    service.create_task("Follow up", deal_id=source["id"])
    chatter_service.add_note("deal", source["id"], "source note")

    region = field_service.create_field_definition(
        {"entity_type": "deal", "name": "Region", "field_type": "text"})
    tier = field_service.create_field_definition(
        {"entity_type": "deal", "name": "Tier", "field_type": "text"})
    field_service.set_field_values("deal", target["id"], {str(tier["id"]): "gold"}, "u")
    field_service.set_field_values(
        "deal", source["id"],
        {str(region["id"]): "north", str(tier["id"]): "bronze"}, "u")

    merged = service.merge_deals(target["id"], source["id"])

    assert merged["id"] == target["id"]
    assert merged["value"] == 500 and merged["title"] == "Kept deal"  # untouched

    acts = service.get_activity_log(deal_id=target["id"])
    assert len(acts) == 1 and acts[0]["note"] == "talked"
    assert [t["id"] for t in service.list_tasks(deal_id=source["id"])] == []
    assert len(service.list_tasks(deal_id=target["id"])) == 1

    messages = [n["message"] for n in chatter_service.get_chatter("deal", target["id"])]
    assert any(m.startswith(f"[Merged from deal #{source['id']}] source note") for m in messages)
    assert any(f'Merged deal #{source["id"]}' in m for m in messages)
    # The source keeps its own thread — the merge is restorable.
    assert [n["message"] for n in chatter_service.get_chatter("deal", source["id"])] == ["source note"]

    values = {r["field_key"]: r["value"]
              for r in field_service.get_field_values("deal", target["id"]) if r["value"]}
    assert values == {"region": "north", "tier": "gold"}  # gap filled, target's own wins

    assert service.get_deal(source["id"])["archived_at"] is not None
    assert [d["id"] for d in service.get_pipeline()["deals"]] == [target["id"]]


def test_merge_rejects_a_missing_or_self_target(pg_db):
    from crm import service

    deal = service.create_deal("Only")
    with pytest.raises(ValueError, match="into itself"):
        service.merge_deals(deal["id"], deal["id"])
    with pytest.raises(ValueError, match="Deal not found"):
        service.merge_deals(deal["id"], 424242)


# ── search ────────────────────────────────────────────────────────────────────

def test_search_deals_matches_related_names_and_filters_custom_fields(pg_db):
    from crm import field_service, service

    company = service.create_company("Northwind")
    contact = service.create_contact("Ana Ruiz", company_id=company["id"])
    a = service.create_deal("Spring order", contact_id=contact["id"],
                            company_id=company["id"], value=100)
    b = service.create_deal("Autumn order", value=900)

    assert [d["id"] for d in service.search_deals(search="Northwind")] == [a["id"]]
    assert [d["id"] for d in service.search_deals(search="Ana")] == [a["id"]]
    assert {d["id"] for d in service.search_deals(search="order")} == {a["id"], b["id"]}
    assert [d["id"] for d in service.search_deals(search="order", sort_by="value",
                                                  sort_dir="desc")] == [b["id"], a["id"]]

    region = field_service.create_field_definition(
        {"entity_type": "deal", "name": "Region", "field_type": "text"})
    field_service.set_field_values("deal", a["id"], {str(region["id"]): "North"}, "u")

    hits = service.search_deals(custom_field_filters={"region": "north"})  # case-insensitive
    assert [d["id"] for d in hits] == [a["id"]]
    assert hits[0]["custom_fields"] == {"region": "North"}
    assert service.search_deals(custom_field_filters={"region": "sou"}) == []  # not substring


# ── intelligence reads ────────────────────────────────────────────────────────

def test_stale_deals_reports_days_and_skips_fresh_or_closed_ones(pg_db):
    from core.postgres import pg_execute
    from crm import analytics_service, service

    contact = service.create_contact("Ana")
    old = service.create_deal("Old", contact_id=contact["id"], value=100)
    service.create_deal("Fresh", contact_id=contact["id"], value=200)
    closed = service.create_deal("Closed", contact_id=contact["id"])
    service.mark_deal_won(closed["id"])

    pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => 40), "
               "created_at = now() - make_interval(days => 60) WHERE id = %s", (old["id"],))
    pg_execute("UPDATE deal_stage_events SET changed_at = now() - make_interval(days => 30) "
               "WHERE deal_id = %s", (old["id"],))

    out = analytics_service.get_stale_deals(stale_days=14)
    assert [d["id"] for d in out["deals"]] == [old["id"]]
    assert out["total_stale"] == 1
    row = out["deals"][0]
    assert row["days_since_touch"] >= 39
    assert row["days_in_stage"] >= 59          # no stage events on it -> created_at
    assert row["has_open_task"] is False
    assert row["contact_name"] == "Ana"

    # Any touch resets it — that is the whole point of the shared last-touch rule.
    service.log_activity("call", deal_id=old["id"])
    assert analytics_service.get_stale_deals(stale_days=14)["deals"] == []


def test_stale_deals_flags_a_deal_that_already_has_a_follow_up(pg_db):
    from core.postgres import pg_execute
    from crm import analytics_service, service

    deal = service.create_deal("Old")
    service.create_task("Chase it", deal_id=deal["id"])
    pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => 40) WHERE id = %s",
               (deal["id"],))
    assert analytics_service.get_stale_deals(stale_days=14)["deals"][0]["has_open_task"] is True


def test_contact_staleness_ranks_never_contacted_first(pg_db):
    from core.postgres import pg_execute
    from crm import analytics_service, service

    never = service.create_contact("Never Touched")
    old = service.create_contact("Long Ago")
    recent = service.create_contact("Just Called")
    archived = service.create_contact("Parked", status="archived")

    service.log_activity("call", contact_id=old["id"])
    pg_execute("UPDATE activity_log SET created_at = now() - make_interval(days => 90) "
               "WHERE contact_id = %s", (old["id"],))
    service.log_activity("call", contact_id=recent["id"])
    service.log_activity("call", contact_id=archived["id"])
    pg_execute("UPDATE activity_log SET created_at = now() - make_interval(days => 90) "
               "WHERE contact_id = %s", (archived["id"],))

    out = analytics_service.get_contact_staleness(stale_days=30)
    ids = [c["id"] for c in out["contacts"]]
    assert ids == [never["id"], old["id"]]          # recent excluded, archived excluded
    assert out["contacts"][0]["days_since_contact"] is None
    assert out["contacts"][1]["days_since_contact"] >= 89


def test_contact_staleness_counts_open_deals_and_sees_notes_as_contact(pg_db):
    from core.postgres import pg_execute
    from crm import analytics_service, chatter_service, service

    ana = service.create_contact("Ana")
    service.create_deal("Open", contact_id=ana["id"])
    closed = service.create_deal("Closed", contact_id=ana["id"])
    service.mark_deal_won(closed["id"])
    assert analytics_service.get_contact_staleness(stale_days=1)["contacts"][0]["open_deals"] == 1

    # A note counts as a touch even with no activity_log row (GREATEST ignores NULLs).
    chatter_service.add_note("contact", ana["id"], "spoke at the counter")
    assert analytics_service.get_contact_staleness(stale_days=1)["contacts"] == []
    pg_execute("UPDATE crm_chatter SET created_at = now() - make_interval(days => 90) "
               "WHERE entity_type = 'contact'")
    assert len(analytics_service.get_contact_staleness(stale_days=30)["contacts"]) == 1


def test_find_duplicates_groups_exact_matches_only(pg_db):
    from crm import analytics_service, service

    a = service.create_contact("Ana Ruiz", email="Ana@Example.com")
    b = service.create_contact("A. Ruiz", email="  ana@example.com ")
    service.create_contact("Someone Else", email="else@example.com")
    service.create_company("Northwind", domain="northwind.test")
    service.create_company("Northwind Ltd", domain="Northwind.test")

    contact_groups = analytics_service.find_duplicate_contacts()
    email_group = next(g for g in contact_groups if g["match_on"] == "email")
    assert email_group["value"] == "ana@example.com"
    assert [r["id"] for r in email_group["records"]] == [a["id"], b["id"]]
    assert {r["label"] for r in email_group["records"]} == {"Ana Ruiz", "A. Ruiz"}

    company_groups = analytics_service.find_duplicate_companies()
    assert [g["match_on"] for g in company_groups] == ["domain"]
    assert company_groups[0]["count"] == 2

    everything = analytics_service.find_duplicates()
    assert everything["groups_returned"] >= 2


def test_duplicate_deals_need_the_same_contact_and_ignore_archived(pg_db):
    from crm import analytics_service, service

    ana = service.create_contact("Ana")
    bob = service.create_contact("Bob")
    d1 = service.create_deal("Q1 renewal", contact_id=ana["id"])
    d2 = service.create_deal("q1 renewal", contact_id=ana["id"])
    service.create_deal("Q1 renewal", contact_id=bob["id"])   # different contact

    groups = analytics_service.find_duplicate_deals()
    assert len(groups) == 1
    assert [r["id"] for r in groups[0]["records"]] == [d1["id"], d2["id"]]

    service.archive_deal(d2["id"])
    assert analytics_service.find_duplicate_deals() == []


def test_scan_gaps_lists_missing_fields_worst_first(pg_db):
    from crm import analytics_service, service

    worst = service.create_contact("No Details")                       # 4 gaps
    company = service.create_company("Acme", domain="acme.test",
                                     industry="retail", phone="123")   # no gaps
    partial = service.create_contact("Some Details", email="a@b.test", phone="1",
                                     title="Buyer", company_id=company["id"])
    assert partial

    out = analytics_service.scan_gaps(entity_type="contact")
    assert [c["id"] for c in out["contacts"]] == [worst["id"]]
    assert set(out["contacts"][0]["missing_fields"]) == {
        "email", "phone", "company_link", "title"}
    assert out["gaps_returned"] == 1

    deals = analytics_service.scan_gaps(entity_type="deal")
    service.create_deal("Vague")
    deals = analytics_service.scan_gaps(entity_type="deal")
    assert set(deals["deals"][0]["missing_fields"]) == {
        "value", "expected_close_date", "contact_link"}


def test_scan_gaps_surfaces_unconfirmed_assistant_writes(pg_db):
    from crm import analytics_service, provenance_service, service

    contact = service.create_contact("Ana", email="a@b.test", phone="1", title="Buyer")
    provenance_service.record_fields("contact", contact["id"], {"phone": "1"})
    out = analytics_service.scan_gaps()
    assert [r["field_name"] for r in out["unverified_fields"]] == ["phone"]

    provenance_service.confirm("contact", contact["id"], "phone")
    assert analytics_service.scan_gaps()["unverified_fields"] == []


# ── company chatter + reset sweep ─────────────────────────────────────────────

def test_company_notes_round_trip_and_are_cleaned_on_delete(pg_db):
    from core.postgres import pg_fetchone
    from crm import chatter_service, service

    company = service.create_company("Northwind")
    chatter_service.add_note("company", company["id"], "met at the counter")
    assert [n["message"] for n in chatter_service.get_chatter("company", company["id"])] == [
        "met at the counter"]

    with pytest.raises(ValueError, match="No company with id"):
        chatter_service.add_note("company", 999999, "orphan")

    service.delete_company(company["id"])
    assert pg_fetchone(
        "SELECT COUNT(*) AS c FROM crm_chatter WHERE entity_type = 'company'")["c"] == 0


def test_clear_all_truncates_through_the_stage_event_foreign_key(pg_db):
    """deal_stage_events is the only CRM table with a real FK to deals — Postgres
    refuses to truncate a referenced table unless the referencing one is in the same
    statement, so a missing entry here breaks the entire CRM reset."""
    from core.postgres import pg_fetchone
    from crm import service

    deal = service.create_deal("Doomed")
    service.update_deal_stage(deal["id"], "won")
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deal_stage_events")["c"] == 1

    service.clear_all()
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deal_stage_events")["c"] == 0
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deals")["c"] == 0


def test_merge_refuses_an_archived_deal_end_to_end(pg_db):
    from crm import service

    contact = service.create_contact("Ana")
    target = service.create_deal("Kept", contact_id=contact["id"])
    source = service.create_deal("Dupe", contact_id=contact["id"])
    service.archive_deal(target["id"])

    with pytest.raises(ValueError, match="archived"):
        service.merge_deals(target["id"], source["id"])
    # Nothing moved — the refusal happens before any write.
    assert service.get_deal(source["id"])["archived_at"] is None

    service.archive_deal(target["id"], archived=False)
    assert service.merge_deals(target["id"], source["id"])["id"] == target["id"]


def test_boolean_custom_field_filters_actually_match(pg_db):
    """Boolean values are stored '1'/'0'; a raw str(True) -> 'True' matched nothing, so
    every boolean filter silently returned an empty list."""
    from crm import field_service, service

    keyacct = field_service.create_field_definition(
        {"entity_type": "deal", "name": "Key account", "field_type": "boolean"})
    yes = service.create_deal("Big customer")
    no = service.create_deal("Small customer")
    field_service.set_field_values("deal", yes["id"], {str(keyacct["id"]): "1"}, "u")
    field_service.set_field_values("deal", no["id"], {str(keyacct["id"]): "0"}, "u")

    assert [d["id"] for d in service.search_deals(
        custom_field_filters={"key_account": True})] == [yes["id"]]
    assert [d["id"] for d in service.search_deals(
        custom_field_filters={"key_account": False})] == [no["id"]]
    # The string forms the model may also send still work.
    assert [d["id"] for d in service.search_deals(
        custom_field_filters={"key_account": "1"})] == [yes["id"]]


def test_scan_gaps_finds_company_gaps(pg_db):
    """The crm_scan_gaps tool advertises company scanning; prove the branch runs and
    returns the right labels against real Postgres."""
    from crm import analytics_service, service

    bare = service.create_company("Bare Co")
    service.create_company("Complete Co", domain="complete.test",
                           industry="retail", phone="555-0100")
    partial = service.create_company("Partial Co", domain="partial.test")

    out = analytics_service.scan_gaps(entity_type="company")
    by_id = {c["id"]: set(c["missing_fields"]) for c in out["companies"]}
    assert by_id == {
        bare["id"]: {"domain", "industry", "phone"},
        partial["id"]: {"industry", "phone"},
    }
    # Worst-first: three gaps before two.
    assert out["companies"][0]["id"] == bare["id"]
    assert out["companies"][0]["label"] == "Bare Co"


def test_company_provenance_is_unwritable(pg_db):
    """Pins WHY delete_company has no crm_field_provenance cleanup while delete_contact
    does: a company provenance row cannot exist. A reviewer reasonably reads the
    asymmetry as a leak, so make the invariant executable — if companies ever become a
    valid provenance entity this fails, and the delete has to be added with it."""
    from core.postgres import pg_fetchone
    from crm import provenance_service, service

    assert provenance_service.VALID_ENTITY_TYPES == {"deal", "contact"}
    company = service.create_company("Northwind")
    with pytest.raises(ValueError, match="Invalid entity_type"):
        provenance_service.record("company", company["id"], "phone", "555-0100", "assistant")
    service.delete_company(company["id"])
    assert pg_fetchone(
        "SELECT COUNT(*) AS c FROM crm_field_provenance WHERE entity_type = 'company'")["c"] == 0


def test_scan_gaps_never_claims_company_provenance(pg_db):
    """scan_gaps(entity_type='company') must return an empty unverified_fields rather
    than a query that can only ever be empty."""
    from crm import analytics_service, service

    service.create_company("Bare Co")
    assert analytics_service.scan_gaps(entity_type="company")["unverified_fields"] == []


def test_merge_fills_a_cleared_target_field_but_not_a_set_one(pg_db, monkeypatch):
    """Clearing a custom field stores value='' rather than deleting the row, so
    "the target left it blank" is usually an EXISTING empty row — a plain
    ON CONFLICT DO NOTHING would skip exactly the case gap-fill exists for."""
    from crm import field_service, service, touch_count_service

    monkeypatch.setattr(touch_count_service, "schedule_recompute", lambda *a, **k: True)
    contact = service.create_contact("Ana")
    target = service.create_deal("Kept", contact_id=contact["id"])
    source = service.create_deal("Dupe", contact_id=contact["id"])

    cleared = field_service.create_field_definition(
        {"entity_type": "deal", "name": "Region", "field_type": "text"})
    kept = field_service.create_field_definition(
        {"entity_type": "deal", "name": "Tier", "field_type": "text"})
    field_service.set_field_values(
        "deal", target["id"], {str(cleared["id"]): "", str(kept["id"]): "gold"}, "u")
    field_service.set_field_values(
        "deal", source["id"], {str(cleared["id"]): "north", str(kept["id"]): "bronze"}, "u")

    service.merge_deals(target["id"], source["id"])

    values = {r["field_key"]: r["value"]
              for r in field_service.get_field_values("deal", target["id"])}
    assert values == {"region": "north", "tier": "gold"}


def test_archived_deals_stop_surfacing_unconfirmed_fields(pg_db):
    """An archived deal disappears from every other read; its unconfirmed fields must
    go with it, or merge_deals' archived source keeps asking to verify a dead deal."""
    from crm import analytics_service, provenance_service, service

    live = service.create_deal("Live", value=100)
    doomed = service.create_deal("Doomed", value=200)
    # record() directly: record_fields re-reads the live row and skips when the
    # snapshot differs, and a float value column never string-matches "100".
    provenance_service.record("deal", live["id"], "value", "100", "assistant")
    provenance_service.record("deal", doomed["id"], "value", "200", "assistant")
    assert len(analytics_service.scan_gaps()["unverified_fields"]) == 2

    service.archive_deal(doomed["id"])
    remaining = analytics_service.scan_gaps()["unverified_fields"]
    assert [r["entity_id"] for r in remaining] == [live["id"]]


def test_closing_a_deal_settles_probability_on_every_path(pg_db):
    from crm import service

    dragged = service.create_deal("Dragged to won", stage="negotiation", probability=30)
    assert service.update_deal_stage(dragged["id"], "won")["probability"] == 100

    lost = service.create_deal("Dragged to lost", stage="proposal", probability=45)
    assert service.update_deal(lost["id"], stage="lost")["probability"] == 0

    # Even an explicit probability loses to the close — see _write_deal_update.
    explicit = service.create_deal("Explicit", stage="proposal", probability=45)
    assert service.update_deal(explicit["id"], stage="won", probability=80)["probability"] == 100
    # ...but editing probability on an already-closed deal is still the caller's call.
    assert service.update_deal(explicit["id"], probability=60)["probability"] == 60


def test_custom_field_filter_key_matching_is_case_insensitive(pg_db):
    """Stored keys are slugified lowercase; a model echoing the display name
    ("Region") must still find the field rather than silently get zero rows."""
    from crm import field_service, service

    region = field_service.create_field_definition(
        {"entity_type": "deal", "name": "Region", "field_type": "text"})
    deal = service.create_deal("Northern order")
    field_service.set_field_values("deal", deal["id"], {str(region["id"]): "North"}, "u")

    for key in ("region", "Region", "REGION"):
        assert [d["id"] for d in service.search_deals(
            custom_field_filters={key: "north"})] == [deal["id"]], key


def test_duplicate_group_labels_are_always_strings(pg_db):
    """Grouping contacts by NAME labels each record with its email — a blank one must
    come back as '' rather than None (dict.get's default never fires on a present
    key holding None)."""
    from crm import analytics_service, service

    service.create_contact("Ana Ruiz")            # no email at all
    service.create_contact("Ana Ruiz", email="ana@example.test")

    name_groups = [g for g in analytics_service.find_duplicate_contacts()
                   if g["match_on"] == "name"]
    assert len(name_groups) == 1
    labels = [r["label"] for r in name_groups[0]["records"]]
    assert all(isinstance(label, str) for label in labels)
    assert "" in labels


def test_archive_restore_keeps_the_deal_stale(pg_db):
    """Archiving must not reset the staleness clock — a bookkeeping round-trip would
    otherwise erase the deal from every stale/nudge surface until a real touch."""
    from core.postgres import pg_execute
    from crm import analytics_service, service

    deal = service.create_deal("Cold one")
    pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => 60) "
               "WHERE id = %s", (deal["id"],))
    assert analytics_service.get_stale_deals(stale_days=14)["count"] == 1

    service.archive_deal(deal["id"])
    service.archive_deal(deal["id"], archived=False)
    assert [d["id"] for d in analytics_service.get_stale_deals(
        stale_days=14)["deals"]] == [deal["id"]]


def test_tasks_follow_an_archived_deal_out_of_view_but_history_does_not(pg_db):
    from crm import service

    deal = service.create_deal("Junk")
    service.create_task("Chase junk", deal_id=deal["id"])
    standalone = service.create_task("Unrelated errand")
    service.log_activity("call", note="talked", deal_id=deal["id"])

    service.archive_deal(deal["id"])
    assert [t["id"] for t in service.list_tasks()] == [standalone["id"]]
    # History is still readable — you need it to decide whether to restore.
    assert len(service.get_activity_log(deal_id=deal["id"])) == 1
    assert len(service.get_activity_log()) == 1


def test_a_stage_change_on_an_archived_deal_is_refused_end_to_end(pg_db):
    from crm import service

    deal = service.create_deal("Parked", stage="proposal")
    service.archive_deal(deal["id"])
    with pytest.raises(ValueError, match="restore it first"):
        service.mark_deal_won(deal["id"])
    # Non-stage edits still work, and restoring re-enables the close.
    assert service.update_deal(deal["id"], notes="tidy")["notes"] == "tidy"
    service.archive_deal(deal["id"], archived=False)
    assert service.mark_deal_won(deal["id"])["stage"] == "won"


def test_search_is_the_way_back_from_an_archive(pg_db):
    from crm import service

    keep = service.create_deal("Live one")
    gone = service.create_deal("Archived one")
    service.archive_deal(gone["id"])

    assert [d["id"] for d in service.search_deals(search="one")] == [keep["id"]]
    found = service.search_deals(search="one", include_archived=True)
    assert {d["id"] for d in found} == {keep["id"], gone["id"]}


def test_undated_deals_sort_last_by_close_date(pg_db):
    from crm import service

    undated = service.create_deal("No date")
    soon = service.create_deal("Soon", expected_close_date="2026-09-01")
    later = service.create_deal("Later", expected_close_date="2027-01-15")
    ordered = service.search_deals(sort_by="expected_close_date", sort_dir="asc")
    assert [d["id"] for d in ordered] == [soon["id"], later["id"], undated["id"]]


def test_creating_a_deal_already_closed_settles_its_probability(pg_db):
    from crm import service

    won = service.create_deal("Won on arrival", stage="won", probability=0)
    lost = service.create_deal("Lost on arrival", stage="lost", probability=90)
    assert won["probability"] == 100 and lost["probability"] == 0
