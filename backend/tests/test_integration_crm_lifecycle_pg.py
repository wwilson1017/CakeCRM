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
        "TRUNCATE companies, contacts, deals, activity_log, todos, crm_chatter, "
        "crm_chatter_attachments, crm_chatter_mentions, crm_field_definitions, crm_field_values, "
        "crm_field_provenance, deal_stage_events, proactive_nudges, "
        "deal_ai_touch_evidence RESTART IDENTITY"
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


def test_every_move_into_and_out_of_won_is_journaled_on_both_write_paths(pg_db):
    """The invariant Weekly Touches (#179) derives a win instant from: a deal whose stage
    is 'won' got there through _write_deal_update or bulk_move_deals, and both journal the
    transition in the same transaction, in both directions. create_deal is the known
    exception — it has no old stage to leave — and is exactly why a deal created straight
    into 'won' has no win instant and contributes nothing to the weekly numbers."""
    from core.postgres import pg_fetchall, pg_fetchone
    from crm import service

    single = service.create_deal("Single path", stage="negotiation")
    service.mark_deal_won(single["id"])
    service.update_deal_stage(single["id"], "negotiation")
    service.mark_deal_won(single["id"])
    assert [
        (e["old_stage"], e["new_stage"]) for e in pg_fetchall(
            "SELECT old_stage, new_stage FROM deal_stage_events WHERE deal_id = %s "
            "ORDER BY id", (single["id"],),
        )
    ] == [("negotiation", "won"), ("won", "negotiation"), ("negotiation", "won")]
    # MAX over the 'won' rows is the LATEST win, which is what TOUCH_AT_SQL reads.
    newest_won = pg_fetchone(
        "SELECT MAX(changed_at) AS at FROM deal_stage_events "
        "WHERE deal_id = %s AND new_stage = 'won'", (single["id"],),
    )["at"]
    last_row = pg_fetchone(
        "SELECT changed_at FROM deal_stage_events WHERE deal_id = %s "
        "ORDER BY id DESC LIMIT 1", (single["id"],),
    )["changed_at"]
    assert newest_won == last_row

    bulk = service.create_deal("Bulk path", stage="proposal")
    assert service.bulk_move_deals([bulk["id"]], "won")["updated"] == 1
    assert service.bulk_move_deals([bulk["id"]], "negotiation")["updated"] == 1
    assert service.bulk_move_deals([bulk["id"]], "won")["updated"] == 1
    assert [
        (e["old_stage"], e["new_stage"]) for e in pg_fetchall(
            "SELECT old_stage, new_stage FROM deal_stage_events WHERE deal_id = %s "
            "ORDER BY id", (bulk["id"],),
        )
    ] == [("proposal", "won"), ("won", "negotiation"), ("negotiation", "won")]

    created_won = service.create_deal("Born won", stage="won")
    assert pg_fetchone(
        "SELECT COUNT(*) AS c FROM deal_stage_events WHERE deal_id = %s",
        (created_won["id"],),
    )["c"] == 0


# ── archive ───────────────────────────────────────────────────────────────────

def test_archiving_removes_a_deal_from_every_read_at_once(pg_db):
    from core.postgres import pg_execute
    from crm import analytics_service, service

    contact = service.create_contact("Ana")
    keep = service.create_deal("Keep", contact_id=contact["id"], value=100, stage="qualified")
    junk = service.create_deal("Junk", contact_id=contact["id"], value=99999, stage="qualified")

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

    # Issue #83: the board's opt-in recovery view. Real SQL proof that the hole is
    # exactly one query wide — the archived deal comes back as a CARD, while every
    # value the board reports is byte-identical to the live-only board's. Keyed by
    # stage because the stage_summary query carries no ORDER BY.
    live = service.get_pipeline()
    widened = service.get_pipeline(include_archived=True)
    assert sorted(d["id"] for d in widened["deals"]) == sorted([keep["id"], junk["id"]])
    assert next(d for d in widened["deals"] if d["id"] == junk["id"])["archived_at"] is not None
    assert widened["total_pipeline_value"] == live["total_pipeline_value"] == 100
    by_stage = {s["stage"]: (s["count"], s["total_value"]) for s in widened["stage_summary"]}
    assert by_stage == {s["stage"]: (s["count"], s["total_value"]) for s in live["stage_summary"]}
    assert by_stage["qualified"] == (1, 100)  # the 99999 archived deal is invisible to money

    # The stage filter still binds once the predicate is dropped from the WHERE.
    assert sorted(d["id"] for d in
                  service.get_pipeline(stage="qualified", include_archived=True)["deals"]) \
        == sorted([keep["id"], junk["id"]])

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
    service.create_todo("Follow up", deal_id=source["id"])
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
    assert [t["id"] for t in service.list_todos(deal_id=source["id"])] == []
    assert len(service.list_todos(deal_id=target["id"])) == 1

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
    assert row["has_open_todo"] is False
    assert row["contact_name"] == "Ana"

    # Any touch resets it — that is the whole point of the shared last-touch rule.
    service.log_activity("call", deal_id=old["id"])
    assert analytics_service.get_stale_deals(stale_days=14)["deals"] == []


def test_stale_deals_flags_a_deal_that_already_has_a_follow_up(pg_db):
    from core.postgres import pg_execute
    from crm import analytics_service, service

    deal = service.create_deal("Old")
    service.create_todo("Chase it", deal_id=deal["id"])
    pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => 40) WHERE id = %s",
               (deal["id"],))
    assert analytics_service.get_stale_deals(stale_days=14)["deals"][0]["has_open_todo"] is True


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
    # The snapshot must equal the LIVE value or the row is stale and correctly hidden
    # (scan_gaps filters staleness now) — value is a float column, so stringify it.
    provenance_service.record("deal", live["id"], "value", str(live["value"]), "assistant")
    provenance_service.record("deal", doomed["id"], "value", str(doomed["value"]), "assistant")
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


def test_todos_follow_an_archived_deal_out_of_view_but_history_does_not(pg_db):
    from crm import service

    deal = service.create_deal("Junk")
    service.create_todo("Chase junk", deal_id=deal["id"])
    standalone = service.create_todo("Unrelated errand")
    service.log_activity("call", note="talked", deal_id=deal["id"])

    service.archive_deal(deal["id"])
    assert [t["id"] for t in service.list_todos()] == [standalone["id"]]
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


def test_every_todo_surface_hides_an_archived_deals_todos(pg_db):
    """list_todos was fixed first; the dashboard counts and the contact page read the
    same todos and were still counting them — and crm_dashboard is background-callable,
    so the heartbeat kept nagging about the todo the archive was meant to silence."""
    from crm import service

    contact = service.create_contact("Ana")
    deal = service.create_deal("Junk", contact_id=contact["id"])
    service.create_todo("Chase junk", deal_id=deal["id"], contact_id=contact["id"],
                        due_date="2020-01-01")
    service.create_todo("Real errand", contact_id=contact["id"], due_date="2020-01-01")

    before = service.get_dashboard_stats()
    assert before["overdue_todos"] == 2 and before["pending_todos"] == 2

    service.archive_deal(deal["id"])
    after = service.get_dashboard_stats()
    assert after["overdue_todos"] == 1 and after["pending_todos"] == 1
    assert [t["title"] for t in service.list_todos()] == ["Real errand"]
    assert [t["title"] for t in
            service.get_contact_detail(contact["id"])["todos"]] == ["Real errand"]


def test_search_still_carries_the_lost_reason_for_a_quarter_review(pg_db):
    from crm import service, tools

    deal = service.create_deal("Big one", stage="negotiation")
    service.mark_deal_lost(deal["id"], lost_reason="chose a competitor")
    row = tools.crm_search_deals(stage="lost")["deals"][0]
    assert row["lost_reason"] == "chose a competitor"
    assert row["updated_at"]        # the only recency signal a search row carries


def test_reopening_a_lost_deal_stops_asking_you_to_verify_the_reason(pg_db):
    """The concrete stale-provenance case this PR creates: mark_deal_lost badges the
    reason it wrote, then reopening the deal blanks lost_reason — leaving a provenance
    row pointing at a value the record no longer has. scan_gaps must not offer it."""
    from crm import analytics_service, service, tools

    deal = service.create_deal("Big one", stage="negotiation")
    # Through the TOOL, not the service: provenance is recorded in the assistant's
    # executors by design — a human edit via the router deliberately mints no badge.
    tools.crm_mark_deal_lost(deal["id"], lost_reason="chose a competitor")
    unverified = analytics_service.scan_gaps(entity_type="deal")["unverified_fields"]
    assert "lost_reason" in [r["field_name"] for r in unverified]

    service.update_deal_stage(deal["id"], "negotiation")   # clears lost_reason
    assert service.get_deal(deal["id"])["lost_reason"] == ""
    after = analytics_service.scan_gaps(entity_type="deal")["unverified_fields"]
    assert "lost_reason" not in [r["field_name"] for r in after]


def test_a_human_edit_retires_the_badge_in_scan_gaps(pg_db):
    """Same rule for the ordinary case — get_provenance already hides a snapshot the
    human has moved past; scan_gaps must agree with it rather than have its own idea
    of what 'unverified' means."""
    from crm import analytics_service, provenance_service, service

    contact = service.create_contact("Ana", phone="555-0100")
    provenance_service.record("contact", contact["id"], "phone", "555-0100", "assistant")
    assert [r["field_name"] for r in
            analytics_service.scan_gaps(entity_type="contact")["unverified_fields"]] == ["phone"]

    service.update_contact(contact["id"], phone="555-0199")   # human overwrote it
    assert analytics_service.scan_gaps(entity_type="contact")["unverified_fields"] == []
    # ...and the two surfaces agree, which is the actual invariant.
    assert provenance_service.get_provenance("contact", contact["id"]) == []


# ── Bulk stage moves (#55) ────────────────────────────────────────────────────

def test_bulk_move_end_to_end(pg_db):
    """The set-based path against real Postgres: `= ANY`, the grouped UPDATEs, and the
    `unnest` multi-row audit INSERT are all constructs a mock cannot validate."""
    from core.postgres import pg_fetchall
    from crm import service

    fresh = service.create_deal("Fresh", stage="lead")
    reopened = service.mark_deal_lost(service.create_deal("Reopened", stage="negotiation")["id"],
                                     lost_reason="budget")
    already = service.create_deal("Already there", stage="qualified")
    archived = service.create_deal("Archived", stage="lead")
    service.archive_deal(archived["id"])

    result = service.bulk_move_deals(
        [fresh["id"], reopened["id"], already["id"], archived["id"], 999_999], "qualified")

    assert result["ok"] is True
    assert result["updated"] == 2
    assert result["updated_ids"] == [fresh["id"], reopened["id"]]
    # Errors arrive in REQUEST order, so the archived deal (listed 4th) precedes the
    # bogus id (listed 5th) — the operator reads them back in the order they selected.
    assert result["errors"] == [
        f"Cannot change the stage of archived deal #{archived['id']} — restore it first",
        "Deal 999999 not found",
    ]

    rows = {r["id"]: r for r in pg_fetchall(
        "SELECT id, stage, lost_reason, archived_at FROM deals")}
    assert rows[fresh["id"]]["stage"] == "qualified"
    assert rows[reopened["id"]]["stage"] == "qualified"
    assert rows[reopened["id"]]["lost_reason"] == "", "leaving 'lost' clears the reason"
    assert rows[already["id"]]["stage"] == "qualified"
    assert rows[archived["id"]]["stage"] == "lead", "the archived deal was left alone"

    events = [(e["deal_id"], e["old_stage"], e["new_stage"]) for e in pg_fetchall(
        "SELECT deal_id, old_stage, new_stage FROM deal_stage_events "
        "WHERE old_stage <> new_stage AND new_stage = 'qualified' ORDER BY id")]
    assert (fresh["id"], "lead", "qualified") in events
    assert (reopened["id"], "lost", "qualified") in events
    assert not any(e[0] in (already["id"], archived["id"]) for e in events)


def test_bulk_move_into_won_settles_probability(pg_db):
    from core.postgres import pg_fetchall
    from crm import service

    a = service.create_deal("A", stage="lead", probability=30)
    b = service.create_deal("B", stage="proposal", probability=70)
    assert service.bulk_move_deals([a["id"], b["id"]], "won")["updated"] == 2

    probs = {r["id"]: r["probability"] for r in pg_fetchall("SELECT id, probability FROM deals")}
    assert probs[a["id"]] == 100 and probs[b["id"]] == 100


def test_bulk_move_leaves_updated_at_alone_for_a_same_stage_deal(pg_db):
    """`updated_at` is read as a touch by LAST_TOUCH_SQL, so a no-op move must not
    reset the deal's staleness clock."""
    from core.postgres import pg_fetchone
    from crm import service

    deal = service.create_deal("Parked", stage="qualified")
    before = pg_fetchone("SELECT updated_at FROM deals WHERE id = %s", (deal["id"],))["updated_at"]
    service.bulk_move_deals([deal["id"]], "qualified")
    after = pg_fetchone("SELECT updated_at FROM deals WHERE id = %s", (deal["id"],))["updated_at"]
    assert before == after


def test_bulk_and_single_deal_paths_cannot_drift(pg_db):
    """The #1323 pin. Twin deals in identical states, one moved through
    update_deal_stage and one through bulk_move_deals, must end up byte-identical —
    that is the whole point of sharing _classify_deal_update."""
    from core.postgres import pg_fetchall, pg_fetchone
    from crm import service

    def snapshot(deal_id):
        row = pg_fetchone(
            "SELECT stage, probability, lost_reason FROM deals WHERE id = %s", (deal_id,))
        events = [(e["old_stage"], e["new_stage"]) for e in pg_fetchall(
            "SELECT old_stage, new_stage FROM deal_stage_events WHERE deal_id = %s ORDER BY id",
            (deal_id,))]
        return dict(row), events

    for stage_from, target, lost_reason in (
        ("lead", "qualified", None),      # plain open move
        ("lead", "won", None),            # closing transition settles probability
        ("lead", "lost", None),           # the other closing transition
        ("lost", "negotiation", "budget"),  # reopening clears the stale reason
        # A no-op move: both paths must agree it changes no column and logs no event.
        # NOT the #96 guard — snapshot() never reads updated_at, so this row passes with
        # or without the fix. test_same_stage_move_touches_nothing_on_either_path is the
        # guard; this row only pins that the two paths still classify a no-op alike.
        ("qualified", "qualified", None),
    ):
        def make(name):
            deal = service.create_deal(name, stage="negotiation", probability=45)
            if stage_from == "lost":
                service.mark_deal_lost(deal["id"], lost_reason=lost_reason)
            else:
                service.update_deal_stage(deal["id"], stage_from)
            return deal["id"]

        single_id, bulk_id = make(f"single {stage_from}->{target}"), make(f"bulk {stage_from}->{target}")
        service.update_deal_stage(single_id, target)
        service.bulk_move_deals([bulk_id], target)

        single_row, single_events = snapshot(single_id)
        bulk_row, bulk_events = snapshot(bulk_id)
        assert single_row == bulk_row, f"{stage_from}->{target} columns drifted"
        assert single_events == bulk_events, f"{stage_from}->{target} stage log drifted"


def test_same_stage_move_touches_nothing_on_either_path(pg_db):
    """The parity test issue #96 asks for, and the one nothing covered before it.

    `LAST_TOUCH_SQL` reads `deals.updated_at` as a touch, so a redundant stage write must
    not move it — otherwise the deal silently drops out of `get_stale_deals` and the
    heartbeat's nudges for a full window with nothing changed. `bulk_move_deals` skipped
    the no-op from the start; `update_deal_stage` bumped it until #96. Both paths are
    asserted here so the fix cannot regress on one of them alone.
    """
    from core.postgres import pg_fetchone
    from crm import service

    def snapshot(deal_id):
        return (
            pg_fetchone("SELECT updated_at FROM deals WHERE id = %s", (deal_id,))["updated_at"],
            pg_fetchone("SELECT count(*) AS n FROM deal_stage_events WHERE deal_id = %s",
                        (deal_id,))["n"],
        )

    single = service.create_deal("Single parked", stage="qualified")["id"]
    bulk = service.create_deal("Bulk parked", stage="qualified")["id"]
    before_single, before_bulk = snapshot(single), snapshot(bulk)

    assert service.update_deal_stage(single, "qualified")["stage"] == "qualified"
    assert service.bulk_move_deals([bulk], "qualified")["ok"] is True

    assert snapshot(single) == before_single, "single-deal path reset the staleness clock"
    assert snapshot(bulk) == before_bulk, "bulk path reset the staleness clock"

    # And the guard is not a blanket refusal to write: a real move still moves.
    service.update_deal_stage(single, "proposal")
    assert snapshot(single) != before_single


def test_an_unchanged_full_form_save_is_not_a_touch(pg_db):
    """`PUT /api/crm/deals/{id}` resaving an untouched form — the second caller #96 names.

    Deliberately exercises every writable type together, because the no-op test lives in
    Postgres: TEXT (title/notes/currency), DOUBLE PRECISION (value), INTEGER
    (probability), a date carried as TEXT (expected_close_date), and nullable FKs
    (contact_id/company_id/owner_id, where `=` would swallow NULL and `IS DISTINCT FROM`
    does not). Comparing these in Python instead of SQL is what would go wrong quietly.

    This also pins the accepted consequence: `DealForm` always PUTs the standard fields
    and then writes changed custom fields separately, and `set_field_values` never touches
    the parent row — so a custom-field-only save no longer bumps `deals.updated_at`.
    Custom-field edits are not deal touches, uniformly (the detail page's
    `CustomFieldsSection` never made them one either).
    """
    from core.postgres import pg_fetchone
    from crm import service

    contact = service.create_contact("Form Tester", email="form@example.test")
    deal = service.create_deal(
        "Form deal", stage="proposal", value=1234.56, probability=40,
        expected_close_date="2026-09-01", notes="as discussed", contact_id=contact["id"],
    )
    form = {
        "title": "Form deal", "stage": "proposal", "value": 1234.56, "probability": 40,
        "expected_close_date": "2026-09-01", "notes": "as discussed", "currency": "USD",
        "contact_id": contact["id"], "company_id": None, "owner_id": None,
    }

    def updated_at():
        return pg_fetchone("SELECT updated_at FROM deals WHERE id = %s", (deal["id"],))["updated_at"]

    before = updated_at()
    assert service.update_deal(deal["id"], **form)["id"] == deal["id"]
    assert updated_at() == before, "an unchanged form save reset the staleness clock"

    # One changed field in the same shape still writes — the guard skips no-ops, not edits.
    service.update_deal(deal["id"], **{**form, "value": 2000.0})
    assert updated_at() != before


def test_a_fractional_value_that_rounds_to_the_stored_one_is_not_a_touch(pg_db):
    """Postgres coerces on ASSIGNMENT but promotes on COMPARISON, so `probability=40.1`
    into an INTEGER column stores 40 (no change) while a bare `IS DISTINCT FROM 40.1`
    would call it distinct and bump `updated_at` anyway. `_DEAL_COLUMN_TYPES` casts the
    comparison to the destination type to close that. Reachable because the assistant's
    tool arguments are not runtime schema-validated — `update_deal` clamps probability
    into range but does not make it an int.
    """
    from core.postgres import pg_fetchone
    from crm import service

    deal = service.create_deal("Fractional", stage="proposal", probability=40)

    def row():
        return pg_fetchone("SELECT updated_at, probability FROM deals WHERE id = %s",
                           (deal["id"],))

    before = row()
    service.update_deal(deal["id"], probability=40.1)   # stores 40 — nothing changed
    assert row() == before, "a fractional no-op reset the staleness clock"

    # Rounding that lands on a DIFFERENT value is a real edit and must still write.
    service.update_deal(deal["id"], probability=40.6)   # stores 41
    after = row()
    assert after["probability"] == 41 and after["updated_at"] != before["updated_at"]


def test_the_declared_column_types_match_the_real_deals_schema(pg_db):
    """`_DEAL_COLUMN_TYPES` is hand-written, so it can drift off the schema — a column
    silently retyped would get the wrong cast and either reopen the no-op hole or start
    raising. Coverage drift is already structural (`update_deal`'s allowlist is derived
    from the map), so what is left to check is that each declared type is the REAL one.
    Read from `information_schema` rather than from the constant under test.
    """
    from core.postgres import pg_fetchall
    from crm import service

    # Scoped to the active schema: another visible schema owning a `deals` table would
    # otherwise merge into this dict and validate the wrong columns.
    actual = {r["column_name"]: r["data_type"] for r in pg_fetchall(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'deals' AND table_schema = current_schema()")}
    expected = {"text": "text", "float8": "double precision", "int": "integer"}

    missing = service._DEAL_COLUMN_TYPES.keys() - actual.keys()
    assert not missing, f"declared columns absent from the deals table: {sorted(missing)}"
    wrong = {c: (declared, actual[c])
             for c, declared in service._DEAL_COLUMN_TYPES.items()
             if actual[c] != expected[declared]}
    assert not wrong, f"declared type does not match the schema: {wrong}"


def test_an_untyped_value_for_a_text_column_still_writes(pg_db):
    """Regression guard. Postgres accepts an I/O conversion into TEXT on ASSIGNMENT but has
    no operator for it in COMPARISON, so an uncast `title IS DISTINCT FROM 12345` raises
    `operator does not exist: text = integer` on a write that worked before this change.
    `crm_update_deal` forwards raw, unvalidated LLM arguments, so a bare number for a text
    field is reachable — and the resulting psycopg2 error escapes as the registry's generic
    "please try again", looping the model on a permanent condition.
    """
    from core.postgres import pg_fetchone
    from crm import service

    deal = service.create_deal("12345", stage="proposal", expected_close_date="2026-09-01")

    def row():
        return pg_fetchone("SELECT updated_at, title FROM deals WHERE id = %s", (deal["id"],))

    before = row()
    # Numerically equal to the stored text: must be recognised as a no-op, not raise.
    service.update_deal(deal["id"], title=12345)
    assert row() == before, "an untyped no-op for a TEXT column reset the staleness clock"

    # A different number is a real edit and must still land as text.
    service.update_deal(deal["id"], title=999)
    after = row()
    assert after["title"] == "999" and after["updated_at"] != before["updated_at"]


def test_re_marking_a_deal_lost_with_the_same_reason_is_not_a_touch(pg_db):
    """The third column-map shape reaching `_write_deal_update`, after `{stage}` and the
    full form: `{stage, probability, lost_reason}`. Worth its own case because
    `lost_reason` is the one column `_classify_deal_update` injects on its own, so a bug
    specific to that combination would miss both other tests.

    The chatter note is deliberately still appended on the second call — a note is a real
    event that `LAST_TOUCH_SQL` reads in its own right, so this asserts the DEAL row is
    untouched, not that the whole call became a no-op.
    """
    from core.postgres import pg_fetchone
    from crm import service

    deal = service.create_deal("Doomed", stage="negotiation")
    service.mark_deal_lost(deal["id"], lost_reason="chose a competitor")

    def row():
        return pg_fetchone(
            "SELECT updated_at, stage, probability, lost_reason FROM deals WHERE id = %s",
            (deal["id"],),
        )

    before = row()
    assert before["stage"] == "lost" and before["probability"] == 0

    service.mark_deal_lost(deal["id"], lost_reason="chose a competitor")
    assert row() == before, "re-marking lost with the same reason reset the staleness clock"

    # A DIFFERENT reason is a real edit and must still land.
    service.mark_deal_lost(deal["id"], lost_reason="budget cut")
    after = row()
    assert after["lost_reason"] == "budget cut" and after["updated_at"] != before["updated_at"]


def test_concurrent_bulk_moves_over_overlapping_ids_do_not_deadlock(pg_db):
    """The locking claim, exercised rather than asserted.

    `bulk_move_deals` locks `ORDER BY id ... FOR UPDATE` specifically so two batches over
    overlapping deals can't deadlock. Two threads here submit OVERLAPPING id sets in
    OPPOSITE request order — the classic deadlock setup — and the ascending lock order is
    the only reason it holds. A regression that drops the ORDER BY fails here with a
    psycopg2 DeadlockDetected, which no hermetic test can catch.
    """
    import threading

    from crm import service

    ids = [service.create_deal(f"Conc {i}", stage="lead")["id"] for i in range(30)]
    failures: list[str] = []

    def hammer(target: str, order: list[int]):
        try:
            for _ in range(5):
                service.bulk_move_deals(order, target)
        except Exception as e:  # a deadlock surfaces here, not as a bad row
            failures.append(f"{target}: {type(e).__name__}: {e}")

    t1 = threading.Thread(target=hammer, args=("qualified", ids[:22]))
    t2 = threading.Thread(target=hammer, args=("proposal", list(reversed(ids[8:]))))
    t1.start(), t2.start()
    t1.join(), t2.join()

    assert failures == []

    from core.postgres import pg_fetchall
    stages = {r["stage"] for r in pg_fetchall("SELECT stage FROM deals")}
    assert stages <= {"lead", "qualified", "proposal"}, "a deal landed in a stage nobody set"
    # Every logged transition must be a real one — a no-op must never mint an event row.
    noops = pg_fetchall(
        "SELECT deal_id FROM deal_stage_events WHERE old_stage = new_stage")
    assert noops == []


# ── Touch-count evidence snapshot (issue #56) ─────────────────────────────────

def test_migration_created_touch_evidence_table_without_an_inbound_fk(pg_db):
    """FK-less by design: Postgres refuses to TRUNCATE a referenced table unless the
    referencing table rides the SAME statement, and that statement's exact text is pinned
    by tests in a hot, often sibling-owned file. (The autouse fixture's TRUNCATE above
    would already be failing if this table held an inbound FK — assert it explicitly so
    the reason is recorded, not just implied.)"""
    from core.postgres import pg_fetchall

    cols = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in pg_fetchall(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'deal_ai_touch_evidence'")}
    assert cols["deal_id"][0] == "integer"
    assert cols["verdicts"] == ("jsonb", "NO")
    assert "computed_at" in cols

    fks = pg_fetchall(
        "SELECT constraint_type FROM information_schema.table_constraints "
        "WHERE table_name = 'deal_ai_touch_evidence'")
    kinds = {r["constraint_type"] for r in fks}
    assert "FOREIGN KEY" not in kinds
    assert "PRIMARY KEY" in kinds          # one snapshot row per deal


def _seed_deal(title="Evidence deal", stage="qualified"):
    from crm import service
    return service.create_deal(title=title, stage=stage)["id"]


def test_store_touch_count_writes_count_and_snapshot_atomically(pg_db):
    from core.postgres import pg_fetchone
    from crm import touch_count_service as tcs

    deal_id = _seed_deal()
    deal = {"ai_touch_count_at": None, "ai_touch_evidence_count": None}
    payload = {"v": 1, "count": 2, "watermark": "2026-01-03T00:00:00+00:00",
               "evidence_count": 2,
               "items": [{"source": "note", "source_id": 1, "touch": True, "reason": ""},
                         {"source": "activity", "source_id": 1, "touch": True, "reason": ""}],
               "skipped": []}
    assert tcs._store_touch_count(
        deal_id, 2, "2026-01-03T00:00:00+00:00", 2, False, deal, payload) == 2

    row = pg_fetchone("SELECT ai_touch_count, ai_touch_evidence_count FROM deals WHERE id = %s",
                      (deal_id,))
    snap = pg_fetchone("SELECT verdicts FROM deal_ai_touch_evidence WHERE deal_id = %s",
                       (deal_id,))
    assert row["ai_touch_count"] == 2 and row["ai_touch_evidence_count"] == 2
    assert snap["verdicts"]["count"] == 2 and len(snap["verdicts"]["items"]) == 2


def test_store_touch_count_losing_the_cas_leaves_no_orphaned_explanation(pg_db):
    """A repair that loses the compare-and-swap must not overwrite the explanation that
    belongs to the count that DID win."""
    from core.postgres import pg_fetchone
    from crm import touch_count_service as tcs

    deal_id = _seed_deal()
    winner = {"v": 1, "count": 1, "watermark": "2026-01-03T00:00:00+00:00",
              "evidence_count": 1, "items": [], "skipped": []}
    tcs._store_touch_count(deal_id, 1, "2026-01-03T00:00:00+00:00", 1, False,
                           {"ai_touch_count_at": None, "ai_touch_evidence_count": None}, winner)

    # A force_write whose CAS keys describe a snapshot that has since moved on.
    stale_deal = {"ai_touch_count_at": "2020-01-01T00:00:00+00:00",
                  "ai_touch_evidence_count": 99}
    loser = {"v": 1, "count": 42, "watermark": "2026-02-01T00:00:00+00:00",
             "evidence_count": 7, "items": [], "skipped": []}
    assert tcs._store_touch_count(
        deal_id, 42, "2026-02-01T00:00:00+00:00", 7, True, stale_deal, loser) is None

    row = pg_fetchone("SELECT ai_touch_count FROM deals WHERE id = %s", (deal_id,))
    snap = pg_fetchone("SELECT verdicts FROM deal_ai_touch_evidence WHERE deal_id = %s",
                       (deal_id,))
    assert row["ai_touch_count"] == 1            # count untouched
    assert snap["verdicts"]["count"] == 1        # explanation untouched


def test_store_touch_count_fallback_drops_the_stale_snapshot(pg_db):
    from core.postgres import pg_fetchone
    from crm import touch_count_service as tcs

    deal_id = _seed_deal()
    payload = {"v": 1, "count": 1, "watermark": "2026-01-03T00:00:00+00:00",
               "evidence_count": 1, "items": [], "skipped": []}
    tcs._store_touch_count(deal_id, 1, "2026-01-03T00:00:00+00:00", 1, False,
                           {"ai_touch_count_at": None, "ai_touch_evidence_count": None}, payload)
    # A later reply whose verdicts failed validation: count only, snapshot removed.
    assert tcs._store_touch_count(
        deal_id, 5, "2026-01-04T00:00:00+00:00", 2, False,
        {"ai_touch_count_at": "2026-01-03T00:00:00+00:00", "ai_touch_evidence_count": 1},
        None) == 5
    assert pg_fetchone("SELECT ai_touch_count FROM deals WHERE id = %s",
                       (deal_id,))["ai_touch_count"] == 5
    assert pg_fetchone("SELECT 1 AS x FROM deal_ai_touch_evidence WHERE deal_id = %s",
                       (deal_id,)) is None


def test_truncate_sweep_includes_touch_evidence_and_blocks_id_reuse(pg_db):
    """Nothing cascades an FK-less table, so a missed sweep would let a deal that reuses a
    truncated SERIAL id inherit a deleted deal's explanation."""
    from core.postgres import pg_execute, pg_fetchone
    from crm import service, touch_count_service as tcs

    deal_id = _seed_deal()
    tcs._store_touch_count(deal_id, 3, "2026-01-03T00:00:00+00:00", 1, False,
                           {"ai_touch_count_at": None, "ai_touch_evidence_count": None},
                           {"v": 1, "count": 3, "watermark": "2026-01-03T00:00:00+00:00",
                            "evidence_count": 1, "items": [], "skipped": []})
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deal_ai_touch_evidence")["c"] == 1

    service.clear_all()
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deal_ai_touch_evidence")["c"] == 0

    # RESTART IDENTITY hands the same id to the next deal — it must inherit nothing.
    reused_id = _seed_deal(title="Fresh deal")
    assert reused_id == deal_id
    out = tcs.get_touch_evidence(reused_id)
    assert out["verdict_state"] == "none" and out["counted"] is None and out["events"] == []
    pg_execute("SELECT 1")          # connection still usable after the sweep


def test_get_touch_evidence_round_trip_and_reconciliation(pg_db):
    """End-to-end over real rows: verdicts render against live notes/activities, a new note
    makes the explanation stale, archiving a judged note makes it superseded, and a stage
    change shows up as a deterministic never-counted row."""
    from crm import chatter_service, service, touch_count_service as tcs

    deal_id = _seed_deal()
    note = chatter_service.add_note("deal", deal_id, "Called the buyer, wants a sample")
    activity = service.log_activity("email", note="replied on pricing", deal_id=deal_id)

    _deal, chatter, activities, _snap, _stage, _trunc = tcs._load_evidence(deal_id)
    entries, skipped = tcs.build_evidence_entries(_deal, chatter, activities)
    watermark = tcs.evidence_watermark(_deal, chatter, activities)
    evidence_count = len(chatter) + len(activities)
    verdicts = [{"touch": e["source_id"] == note["id"] and e["source"] == "note",
                 "reason": "" if e["source"] == "note" else "bulk mail, no reply"}
                for e in entries]
    payload = tcs._build_payload(1, watermark, evidence_count, entries, verdicts, skipped)
    assert tcs._store_touch_count(deal_id, 1, watermark, evidence_count, False,
                                  {"ai_touch_count_at": None,
                                   "ai_touch_evidence_count": None}, payload) == 1

    out = tcs.get_touch_evidence(deal_id)
    assert out["verdict_state"] == "current" and out["counted"] == 1
    by_source = {(e["source"], e["source_id"]): e for e in out["events"]}
    assert by_source[("note", note["id"])]["state"] == "touch"
    assert by_source[("activity", activity["id"])]["state"] == "not_touch"
    assert by_source[("activity", activity["id"])]["reason"] == "bulk mail, no reply"

    # A stage change adds a deterministic row that needs no AI at all.
    service.update_deal_stage(deal_id, "proposal")
    stage_rows = [e for e in tcs.get_touch_evidence(deal_id)["events"]
                  if e["source"] == "stage_move"]
    assert len(stage_rows) == 1 and stage_rows[0]["state"] == "stage_move"

    # A new note moves the evidence past what was judged.
    chatter_service.add_note("deal", deal_id, "Follow-up call booked")
    assert tcs.get_touch_evidence(deal_id)["verdict_state"] == "stale"

    # Editing the judged note is caught by the stored line digest, even though the edit
    # moves neither created_at nor the row count.
    chatter_service.update_note(note["id"], "Completely different wording now")
    edited = tcs.get_touch_evidence(deal_id)
    note_row = next(e for e in edited["events"]
                    if e["source"] == "note" and e["source_id"] == note["id"])
    assert note_row["state"] == "edited_since"
    assert edited["verdict_state"] in ("stale", "superseded")

    # Archiving the judged note removes the counted row → the sum can no longer be shown.
    chatter_service.archive_note(note["id"])
    after = tcs.get_touch_evidence(deal_id)
    assert after["verdict_state"] == "superseded"
    assert ("note", note["id"]) not in {(e["source"], e["source_id"]) for e in after["events"]}


# ── Top deals ranking (issue #240) ────────────────────────────────────────────

def test_top_deals_rank_qualified_deals_by_the_weighted_score(pg_db):
    """Leads are left out, and the order is max(value,0) × (0.4 + 0.6 × p): value-dominant,
    so $500K@20% (260K) beats $150K@99% (148.5K) where plain value × probability would not,
    yet a LOWER value can still win on probability — $600K@99% (596.4K) over $1M@20% (520K)
    — which is why the ranking must run before the LIMIT rather than on a value prefix."""
    from core.postgres import pg_execute
    from crm import service

    service.create_deal("Moonshot lead", value=5_000_000, probability=99, stage="lead")
    big = service.create_deal("Big", value=1_000_000, probability=20, stage="qualified")
    likely = service.create_deal("Likely", value=600_000, probability=99, stage="negotiation")
    mid = service.create_deal("Mid", value=500_000, probability=20, stage="proposal")
    small = service.create_deal("Small", value=150_000, probability=99, stage="proposal")
    # create_deal clamps probability; a row written elsewhere may not, so the SCORE clamps
    # too — unclamped, 120K@150% would score 156K and jump Small's 148.5K.
    over = service.create_deal("Over", value=120_000, probability=100, stage="proposal")
    pg_execute("UPDATE deals SET probability = 150 WHERE id = %s", (over["id"],))
    service.create_deal("Zero", value=0, probability=100, stage="negotiation")
    service.create_deal("Won", value=9_000_000, probability=100, stage="won")

    top = service.get_dashboard_stats()["top_deals"]
    assert [d["id"] for d in top] == [likely["id"], big["id"], mid["id"], small["id"], over["id"]]
    # Same wire shape: full rows with both link labels (#123).
    assert {"contact_name", "company_name", "stage", "value"} <= set(top[0])


def test_top_deals_break_score_ties_by_recency_then_id(pg_db):
    from core.postgres import pg_execute
    from crm import service

    a = service.create_deal("A", value=1000, probability=50, stage="qualified")
    b = service.create_deal("B", value=1000, probability=50, stage="qualified")
    c = service.create_deal("C", value=1000, probability=50, stage="qualified")
    pg_execute("UPDATE deals SET updated_at = now() - interval '1 day'")
    pg_execute("UPDATE deals SET updated_at = now() WHERE id = %s", (a["id"],))

    top = service.get_dashboard_stats()["top_deals"]
    assert [d["id"] for d in top] == [a["id"], c["id"], b["id"]]


def test_top_deals_floor_a_negative_value_or_probability(pg_db):
    """Neither column carries a CHECK, and the REST value field has no lower bound, so the
    score clamps both from below. Unclamped, 100K@-50% (10K) would sink under 50K@0% (20K),
    and a -$1M deal would sort under a $0 one instead of tying with it at zero."""
    from core.postgres import pg_execute
    from crm import service

    neg_prob = service.create_deal("NegProb", value=100_000, probability=0, stage="qualified")
    pg_execute("UPDATE deals SET probability = -50 WHERE id = %s", (neg_prob["id"],))
    floor = service.create_deal("Floor", value=50_000, probability=0, stage="qualified")
    zero = service.create_deal("Zero", value=0, probability=0, stage="qualified")
    neg_val = service.create_deal("NegVal", value=-1_000_000, probability=100, stage="qualified")
    pg_execute("UPDATE deals SET updated_at = now() - interval '1 day' WHERE id = %s", (zero["id"],))

    top = service.get_dashboard_stats()["top_deals"]
    assert [d["id"] for d in top] == [neg_prob["id"], floor["id"], neg_val["id"], zero["id"]]
