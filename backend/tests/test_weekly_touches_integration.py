"""Real-Postgres integration for Weekly Touches per rep (issue #146).

These are the claims a Recorder cannot check, because it agrees with whatever SQL it is
handed: that GROUP BY d.owner_id really puts every unowned deal in ONE bucket and the
LEFT JOIN really keeps it, that ROW_NUMBER() OVER (PARTITION BY d.owner_id) really caps
each rep separately rather than globally, that the window filter really runs BEFORE the
ranking, that LAST_TOUCH_SQL really admits an edit / an activity / a live note and really
excludes a deal that was only created, and that the closed/archived sweeps really hold on
both sides of the ratio.

The hermetic suite pins the shaper and the SQL's shape; this pins its meaning.

Marked ``integration`` and excluded from the default no-DB run. Fixture data is fresh and
fictional.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")

NOW = datetime.now(timezone.utc)

# A custom range the drill-down can be asked for by DAY, the same way the card takes one.
RANGE_START = (NOW - timedelta(days=3)).strftime("%Y-%m-%d")
RANGE_END = (NOW + timedelta(days=1)).strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_touches_{os.getpid()}"
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
    from core.postgres import get_connection, pg_execute
    from crm import service

    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=True)
    pg_execute("DELETE FROM users")
    yield


def _user(name: str) -> int:
    """A real row: `deals.owner_id` is a real FK, so a synthetic id cannot stand in."""
    from core.postgres import pg_fetchone

    return pg_fetchone(
        "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, 'x') RETURNING id",
        (f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.test", name),
    )["id"]


def _deal(
    title: str,
    *,
    owner: int | None = None,
    stage: str = "proposal",
    touched: bool = True,
    touch_count: int | None = 1,
    archived: bool = False,
) -> int:
    """One deal, optionally 'touched'.

    `touched` bumps `updated_at` past `created_at` — which is exactly what makes it a
    touch, and leaving them equal is exactly what makes a merely-CREATED deal not one.
    """
    from core.postgres import pg_fetchone

    row = pg_fetchone(
        "INSERT INTO deals (title, stage, value, ai_touch_count, archived_at) "
        "VALUES (%s, %s, 1000, %s, %s) RETURNING id",
        (title, stage, touch_count, NOW if archived else None),
    )
    deal_id = row["id"]
    from core.postgres import pg_execute

    # owner_id set separately so the INSERT stays identical for the unowned case.
    pg_execute("UPDATE deals SET owner_id = %s WHERE id = %s", (owner, deal_id))
    if touched:
        # A real edit: created an hour ago, updated since. Backdating `created_at` is how
        # that gets modelled without putting `updated_at` in the FUTURE — the card's
        # default window is `[now()-7d, now())`, exclusive at the top, so a touch stamped
        # even a second ahead falls outside the very window under test.
        #
        # Writing the module-level NOW here instead (the first attempt) was worse: it is
        # captured at import and is therefore OLDER than this row, leaving
        # updated_at < created_at. Such a deal still counts, because the predicate is `<>`
        # — but then nothing in the suite proves that an edit AFTER creation is what a
        # touch means, which is the whole claim.
        pg_execute(
            "UPDATE deals SET created_at = created_at - interval '1 hour' WHERE id = %s",
            (deal_id,),
        )
    else:
        # A deal that was only ever created: updated_at == created_at.
        pg_execute("UPDATE deals SET created_at = updated_at WHERE id = %s", (deal_id,))
    return deal_id


def _reps(payload) -> dict:
    return {r["name"]: r for r in payload["reps"]}


def test_unowned_deals_are_one_bucket_named_unassigned_not_an_exclusion():
    from crm import service

    ada = _user("Ada")
    _deal("Ada one", owner=ada)
    _deal("Nobody one")
    _deal("Nobody two")

    out = service.get_weekly_touches()
    reps = _reps(out)

    assert set(reps) == {"Ada", "Unassigned"}
    assert reps["Unassigned"]["open_deals"] == 2      # ONE bucket, not two rows
    assert reps["Unassigned"]["touches"] == 2
    # The totals are the buckets, which is only true while the unowned deals are counted.
    assert out["total_open_deals"] == 3
    assert out["total_touches"] == 3
    assert out["reps"][-1]["name"] == "Unassigned"    # and it sinks last


def test_a_rep_with_open_deals_but_no_touches_still_gets_a_row():
    from crm import service

    _user("Sam")
    sam = _user("SamTwo")
    _deal("Untouched", owner=sam, touched=False)

    reps = _reps(service.get_weekly_touches())
    assert reps["SamTwo"]["open_deals"] == 1
    assert reps["SamTwo"]["touches"] == 0
    assert reps["SamTwo"]["deals"] == []
    # A user with no deals at all is not a rep here — there is nothing to be accountable for.
    assert "Sam" not in reps


def test_creating_a_deal_is_not_a_touch():
    from crm import service

    ada = _user("Ada")
    _deal("Only created", owner=ada, touched=False)

    reps = _reps(service.get_weekly_touches())
    assert (reps["Ada"]["open_deals"], reps["Ada"]["touches"]) == (1, 0)


def test_an_activity_and_a_live_note_each_count_as_a_touch():
    from core.postgres import pg_execute
    from crm import service

    ada = _user("Ada")
    by_activity = _deal("By activity", owner=ada, touched=False)
    by_note = _deal("By note", owner=ada, touched=False)
    by_archived_note = _deal("By archived note", owner=ada, touched=False)

    # The DATABASE clock, not the module-level NOW: NOW is captured at import and is
    # therefore older than these rows, so LAST_TOUCH_SQL's GREATEST would pick the deal's
    # own updated_at instead and the event would not read as a touch at all.
    pg_execute(
        "INSERT INTO activity_log (deal_id, activity, note, created_at) "
        "VALUES (%s, 'call', 'rang', now())", (by_activity,),
    )
    pg_execute(
        "INSERT INTO crm_chatter (entity_type, entity_id, message, archived, created_at) "
        "VALUES ('deal', %s, 'spoke', 0, now())", (by_note,),
    )
    # An archived note is not a live one, so LAST_TOUCH_SQL must not see it.
    pg_execute(
        "INSERT INTO crm_chatter (entity_type, entity_id, message, archived, created_at) "
        "VALUES ('deal', %s, 'retracted', 1, now())", (by_archived_note,),
    )

    reps = _reps(service.get_weekly_touches())
    titles = {d["title"] for d in reps["Ada"]["deals"]}
    assert titles == {"By activity", "By note"}
    assert reps["Ada"]["touches"] == 2
    assert reps["Ada"]["open_deals"] == 3


def test_closed_and_archived_deals_are_excluded_from_both_sides():
    from crm import service

    ada = _user("Ada")
    _deal("Open", owner=ada)
    _deal("Won", owner=ada, stage="won")
    _deal("Lost", owner=ada, stage="lost")
    _deal("Archived", owner=ada, archived=True)

    reps = _reps(service.get_weekly_touches())
    assert (reps["Ada"]["open_deals"], reps["Ada"]["touches"]) == (1, 1)
    assert [d["title"] for d in reps["Ada"]["deals"]] == ["Open"]


def test_the_cap_is_per_rep_and_ranks_only_touched_deals():
    """A global LIMIT would starve the second rep entirely; capping before the window
    filter would return fewer than the limit for a rep carrying untouched deals."""
    from crm import service

    ada = _user("Ada")
    bob = _user("Bob")
    limit = service.WEEKLY_TOUCHES_LIMIT

    # Ada: more touched deals than the cap, plus untouched ones that must not consume a slot.
    for i in range(limit + 3):
        _deal(f"Ada {i}", owner=ada, touch_count=i + 1)
    for i in range(5):
        _deal(f"Ada untouched {i}", owner=ada, touched=False)
    # Bob: exactly one, which a global cap spent on Ada would hide.
    _deal("Bob only", owner=bob, touch_count=99)

    reps = _reps(service.get_weekly_touches())

    assert len(reps["Ada"]["deals"]) == limit
    assert reps["Ada"]["touches"] == limit + 3          # the headline is not capped
    assert [d["title"] for d in reps["Bob"]["deals"]] == ["Bob only"]
    # Ranked by count desc, so the cap keeps the most-touched, not an arbitrary ten.
    assert reps["Ada"]["deals"][0]["touch_count"] == limit + 3
    # No untouched deal survived the filter into the ranking.
    assert all("untouched" not in d["title"] for d in reps["Ada"]["deals"])


def test_the_cap_holds_for_the_unassigned_partition_too():
    """PARTITION BY treats every NULL owner as ONE partition, so the cap must apply to
    the unowned bucket exactly as it does to a person's. This is the one claim in the
    per-rep cap that only a real database can settle — a Recorder returns whatever rows
    it was queued, whatever the window function says."""
    from crm import service

    limit = service.WEEKLY_TOUCHES_LIMIT
    for i in range(limit + 5):
        _deal(f"Nobody {i}", touch_count=i + 1)

    rep = _reps(service.get_weekly_touches())["Unassigned"]

    assert rep["touches"] == limit + 5          # the headline counts them all
    assert len(rep["deals"]) == limit           # the rows are capped, not one big partition
    assert rep["deals"][0]["touch_count"] == limit + 5   # and ranked, not arbitrary


def test_computed_deals_counts_every_open_deal_not_just_the_touched_ones():
    """The zero-keys gate must stay window-independent, or a quiet week reads as a
    missing provider and the card disappears from a fully configured install."""
    from crm import service

    ada = _user("Ada")
    _deal("Touched", owner=ada, touch_count=3)
    _deal("Quiet", owner=ada, touched=False, touch_count=5)
    _deal("No estimate", owner=ada, touched=False, touch_count=None)

    out = service.get_weekly_touches()
    assert out["total_touches"] == 1        # only one deal was touched in the window
    assert out["computed_deals"] == 2       # but two carry a count, window or not
    assert out["total_open_deals"] == 3


def test_no_provider_means_no_computed_deals_but_real_touch_numbers():
    from crm import service

    ada = _user("Ada")
    _deal("Touched", owner=ada, touch_count=None)

    out = service.get_weekly_touches()
    assert out["computed_deals"] == 0       # the card hides
    assert (out["total_touches"], out["total_open_deals"]) == (1, 1)   # honest anyway


def test_detail_returns_one_bucket_in_full():
    from crm import service

    ada = _user("Ada")
    bob = _user("Bob")
    limit = service.WEEKLY_TOUCHES_LIMIT
    for i in range(limit + 4):
        _deal(f"Ada {i}", owner=ada)
    _deal("Bob only", owner=bob)

    out = service.get_weekly_touch_detail(owner_id=ada)

    assert out["rep"]["name"] == "Ada"
    assert out["rep"]["touches"] == limit + 4
    assert len(out["deals"]) == limit + 4                 # uncapped, unlike the card
    assert all(d["owner_id"] == ada for d in out["deals"])


def test_detail_for_the_unassigned_bucket_selects_exactly_the_unowned_deals():
    from crm import service

    ada = _user("Ada")
    _deal("Ada one", owner=ada)
    _deal("Nobody one")
    _deal("Nobody two")

    out = service.get_weekly_touch_detail(owner_id=None)

    assert out["rep"] == {"user_id": None, "name": "Unassigned", "open_deals": 2, "touches": 2}
    assert {d["title"] for d in out["deals"]} == {"Nobody one", "Nobody two"}


def test_detail_for_a_real_rep_with_nothing_open_is_a_zero_row():
    from crm import service

    sam = _user("Sam")
    out = service.get_weekly_touch_detail(owner_id=sam)

    assert out["rep"] == {"user_id": sam, "name": "Sam", "open_deals": 0, "touches": 0}
    assert out["deals"] == []


def test_detail_for_an_unknown_user_is_none():
    from crm import service

    assert service.get_weekly_touch_detail(owner_id=987654) is None


def test_the_card_and_the_detail_agree_about_one_rep():
    """The two surfaces share both query builders AND the window resolver; this is the
    claim that matters. Neither is given an explicit range, so both resolve the rolling
    default — which is exactly how the card's link reaches this function."""
    from crm import service

    ada = _user("Ada")
    for i in range(3):
        _deal(f"Ada {i}", owner=ada)
    _deal("Ada quiet", owner=ada, touched=False)

    card = _reps(service.get_weekly_touches())["Ada"]
    detail = service.get_weekly_touch_detail(owner_id=ada)

    assert (detail["rep"]["touches"], detail["rep"]["open_deals"]) == (
        card["touches"], card["open_deals"]
    )
    assert {d["id"] for d in detail["deals"]} == {d["id"] for d in card["deals"]}


def test_a_custom_range_really_bounds_the_detail_list():
    """A touch inside the default window but outside the requested range separates
    "honoured start/end" from "ignored them and resolved the default".

    The card's default window is the rolling last 7 days; the range asked for here is the
    last 3. A deal touched 5 days ago therefore sits INSIDE the default and OUTSIDE the
    range, so the card counts it and the ranged drill-down must not. A touch 30 days old
    would have been excluded by both and proved nothing.
    """
    from core.postgres import pg_execute
    from crm import service

    ada = _user("Ada")
    recent = _deal("Touched today", owner=ada)
    five_days = _deal("Touched five days ago", owner=ada)
    pg_execute(
        "UPDATE deals SET created_at = created_at - interval '30 days', "
        "                 updated_at = now() - interval '5 days' WHERE id = %s",
        (five_days,),
    )

    # The card's default window is 7 days wide, so it sees BOTH.
    card = _reps(service.get_weekly_touches())["Ada"]
    assert {d["id"] for d in card["deals"]} == {recent, five_days}
    assert card["touches"] == 2

    out = service.get_weekly_touch_detail(owner_id=ada, start=RANGE_START, end=RANGE_END)

    assert {d["id"] for d in out["deals"]} == {recent}
    assert out["rep"]["touches"] == 1
    assert out["rep"]["open_deals"] == 2      # still their whole book, window or not


def test_a_deal_touched_after_the_card_loaded_is_still_on_the_drill_down():
    """The regression Stage 4 found, and the reason the window is RE-RESOLVED rather than
    forwarded as frozen instants.

    Membership is "this deal's CURRENT most recent touch falls in the window", so freezing
    the bounds does not freeze the answer: a touch made after the card rendered moves the
    deal PAST a frozen upper bound and deletes it from the page the user just clicked
    through to. Measured before the fix — the card said 2, the page said "1 of 2".
    """
    from core.postgres import pg_execute
    from crm import service

    ada = _user("Ada")
    worked = _deal("Deal A", owner=ada)
    _deal("Deal B", owner=ada)

    card = _reps(service.get_weekly_touches())["Ada"]
    assert card["touches"] == 2

    # The ordinary next thing a rep does after reading the dashboard.
    pg_execute(
        "INSERT INTO activity_log (deal_id, activity, note, created_at) "
        "VALUES (%s, 'call', 'rang them', now())", (worked,),
    )

    out = service.get_weekly_touch_detail(owner_id=ada)

    assert out["rep"]["touches"] == 2
    assert worked in {d["id"] for d in out["deals"]}


def test_working_a_deal_from_the_drill_down_does_not_remove_it_from_the_list():
    """The same defect from inside the page: the sheet's stage change calls reload(), and
    under a frozen window the deal the user had just moved dropped out of its own list."""
    from crm import service

    ada = _user("Ada")
    worked = _deal("Deal A", owner=ada)

    before = service.get_weekly_touch_detail(owner_id=ada)
    assert worked in {d["id"] for d in before["deals"]}

    service.update_deal_stage(worked, "negotiation")

    after = service.get_weekly_touch_detail(owner_id=ada)
    assert after["rep"]["touches"] == 1
    assert worked in {d["id"] for d in after["deals"]}
