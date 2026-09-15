"""Real-Postgres integration for the owner filters and tool attribution (issue #190).

The hermetic suite proves the tool layer PASSES the right arguments. It cannot prove the
SQL those arguments produce is valid or selects the right rows, and every predicate this
issue adds is assembled by string interpolation into an f-string query — `IS NULL` vs
`= %s`, in five shared WHERE builders and two analytics reads, one of which has a second
COUNT query that must filter identically to its page. A wrong predicate there passes
every mock and fails on the first real row.

Marked ``integration``; same throwaway-database pattern as the sibling `_pg` suites.
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_identity_{os.getpid()}"
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


@pytest.fixture
def seats(pg_db):
    """Two real users, so an owner filter has something to be wrong about."""
    from core.postgres import pg_execute
    from users import service as users_service

    pg_execute("TRUNCATE users RESTART IDENTITY CASCADE")
    rep = users_service.create_user("rep@example.com", "Rep", "pw-rep-12345", role="member")
    boss = users_service.create_user("boss@example.com", "Boss", "pw-boss-12345", role="admin")
    return {"rep": dict(rep), "boss": dict(boss)}


@pytest.fixture(autouse=True)
def _clean(pg_db):
    from core.postgres import pg_execute
    pg_execute(
        "TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
        "crm_chatter_attachments, crm_field_definitions, crm_field_values, "
        "crm_field_provenance, deal_stage_events, proactive_nudges, "
        "deal_ai_touch_evidence RESTART IDENTITY"
    )
    yield


def _tools(user):
    from crm import tools
    return tools.get_crm_tools(user=user)[1]


# ── Attribution lands in the database ──────────────────────────────────────────

def test_assistant_activity_and_note_carry_the_talking_seat(seats):
    from core.postgres import pg_fetchone

    ex = _tools(seats["rep"])
    contact = ex["crm_create_contact"](name="Acme Bob")
    ex["crm_log_activity"](activity="call", note="rang them", contact_id=contact["id"])
    ex["crm_add_note"](entity_type="contact", entity_id=contact["id"], message="left a message")

    assert pg_fetchone("SELECT actor_id FROM activity_log ORDER BY id DESC LIMIT 1")["actor_id"] \
        == seats["rep"]["id"]
    assert pg_fetchone(
        "SELECT author_id FROM crm_chatter ORDER BY id DESC LIMIT 1"
    )["author_id"] == seats["rep"]["id"]


def test_unattended_turn_writes_null_not_a_guess(seats):
    from core.postgres import pg_fetchone

    ex = _tools(None)
    contact = ex["crm_create_contact"](name="Nobody's Contact")
    ex["crm_log_activity"](activity="email", contact_id=contact["id"])

    assert pg_fetchone("SELECT owner_id FROM contacts WHERE id = %s", (contact["id"],))["owner_id"] is None
    assert pg_fetchone("SELECT actor_id FROM activity_log ORDER BY id DESC LIMIT 1")["actor_id"] is None


@pytest.mark.parametrize("tool,kwargs,table", [
    ("crm_create_contact", {"name": "Owned Contact"}, "contacts"),
    ("crm_create_company", {"name": "Owned Company"}, "companies"),
    ("crm_create_deal", {"title": "Owned Deal"}, "deals"),
    ("crm_create_task", {"title": "Owned Task"}, "tasks"),
])
def test_interactive_creates_land_the_requesting_seat(seats, tool, kwargs, table):
    from core.postgres import pg_fetchone

    row = _tools(seats["rep"])[tool](**kwargs)
    stored = pg_fetchone(f"SELECT owner_id FROM {table} WHERE id = %s", (row["id"],))
    assert stored["owner_id"] == seats["rep"]["id"]


# ── The owner predicate is valid SQL and selects the right rows ────────────────

@pytest.fixture
def three_contacts(seats):
    """One contact each for rep, boss, and nobody — the three buckets a filter must split."""
    from crm import service
    return {
        "rep": service.create_contact(name="Acme Rep Contact", owner_id=seats["rep"]["id"]),
        "boss": service.create_contact(name="Acme Boss Contact", owner_id=seats["boss"]["id"]),
        "none": service.create_contact(name="Acme Orphan Contact"),
    }


def test_contact_owner_filter_splits_the_three_buckets(seats, three_contacts):
    ex = _tools(seats["rep"])
    assert {c["name"] for c in ex["crm_find_contact"](query="Acme")["contacts"]} == {
        "Acme Rep Contact", "Acme Boss Contact", "Acme Orphan Contact",
    }
    assert [c["name"] for c in ex["crm_find_contact"](query="Acme", owner="me")["contacts"]] \
        == ["Acme Rep Contact"]
    assert [c["name"] for c in ex["crm_find_contact"](query="Acme", owner="unassigned")["contacts"]] \
        == ["Acme Orphan Contact"]
    assert [c["name"] for c in
            ex["crm_find_contact"](query="Acme", owner="boss@example.com")["contacts"]] \
        == ["Acme Boss Contact"]


def test_company_and_task_owner_filters_run(seats):
    from crm import service

    service.create_company(name="Acme Rep Co", owner_id=seats["rep"]["id"])
    service.create_company(name="Acme Orphan Co")
    service.create_task(title="Rep task", owner_id=seats["rep"]["id"])
    service.create_task(title="Orphan task")

    ex = _tools(seats["rep"])
    assert [c["name"] for c in ex["crm_search_companies"](query="Acme", owner="me")["companies"]] \
        == ["Acme Rep Co"]
    assert [c["name"] for c in
            ex["crm_search_companies"](query="Acme", owner="unassigned")["companies"]] \
        == ["Acme Orphan Co"]
    assert [t["title"] for t in ex["crm_list_tasks"](owner="me")["tasks"]] == ["Rep task"]
    assert [t["title"] for t in ex["crm_list_tasks"](owner="unassigned")["tasks"]] == ["Orphan task"]


def test_counts_pagination_stays_in_step_with_the_filtered_page(seats, three_contacts):
    # The shared builder exists so a filter cannot reach the page query without also
    # reaching the COUNT. Assert the pair agrees, which is the property that would break
    # silently: a filtered list beside an unfiltered total does not error, it lies.
    from crm import service

    for owner_id, expected in (
        (seats["rep"]["id"], 1), (service.UNASSIGNED, 1), (None, 3),
    ):
        rows = service.search_contacts("Acme", owner_id=owner_id)
        assert len(rows) == expected
        assert service.count_search_contacts("Acme", owner_id=owner_id) == expected


# ── Analytics: the owner column and its filter, page and COUNT ─────────────────

def _stale_deal(title, owner_id=None):
    from core.postgres import pg_execute
    from crm import service

    deal = service.create_deal(title=title, stage="qualified", owner_id=owner_id)
    # Backdate every recency source LAST_TOUCH_SQL reads, so the deal is genuinely stale.
    pg_execute("UPDATE deals SET updated_at = now() - interval '90 days' WHERE id = %s",
               (deal["id"],))
    return deal


def test_stale_deals_carry_owner_id_and_filter_on_it(seats):
    ex = _tools(seats["rep"])
    _stale_deal("Rep stale deal", seats["rep"]["id"])
    _stale_deal("Orphan stale deal")

    everyone = ex["crm_get_stale_deals"]()
    assert {d["title"] for d in everyone["deals"]} == {"Rep stale deal", "Orphan stale deal"}
    # The routing signal B3 reads, present whether or not the caller filtered.
    by_title = {d["title"]: d["owner_id"] for d in everyone["deals"]}
    assert by_title == {"Rep stale deal": seats["rep"]["id"], "Orphan stale deal": None}

    assert [d["title"] for d in ex["crm_get_stale_deals"](owner="me")["deals"]] == ["Rep stale deal"]
    assert [d["title"] for d in ex["crm_get_stale_deals"](owner="unassigned")["deals"]] \
        == ["Orphan stale deal"]


def test_stale_deal_truncation_count_filters_with_the_page(seats):
    # total_stale is only computed when the page was truncated, and it is a SECOND query
    # with its own copy of the predicate — the exact shape that drifts. Force truncation
    # with limit=1 and check the total describes the same slice as the rows.
    from crm import analytics_service, service

    for i in range(3):
        _stale_deal(f"Rep stale {i}", seats["rep"]["id"])
    for i in range(2):
        _stale_deal(f"Orphan stale {i}")

    assert analytics_service.get_stale_deals(limit=1)["total_stale"] == 5
    assert analytics_service.get_stale_deals(
        limit=1, owner_id=seats["rep"]["id"])["total_stale"] == 3
    assert analytics_service.get_stale_deals(
        limit=1, owner_id=service.UNASSIGNED)["total_stale"] == 2


def test_contact_staleness_carries_owner_id_and_filters_on_it(seats, three_contacts):
    ex = _tools(seats["rep"])
    everyone = ex["crm_get_contact_staleness"](stale_days=1)
    # Nobody has ever been contacted, so all three are stale with a null last-contact.
    assert {c["name"]: c["owner_id"] for c in everyone["contacts"]} == {
        "Acme Rep Contact": seats["rep"]["id"],
        "Acme Boss Contact": seats["boss"]["id"],
        "Acme Orphan Contact": None,
    }
    assert [c["name"] for c in ex["crm_get_contact_staleness"](stale_days=1, owner="me")["contacts"]] \
        == ["Acme Rep Contact"]
    assert [c["name"] for c in
            ex["crm_get_contact_staleness"](stale_days=1, owner="unassigned")["contacts"]] \
        == ["Acme Orphan Contact"]
