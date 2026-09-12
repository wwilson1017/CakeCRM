"""Real-Postgres integration for Weekly Touches per rep (issue #146).

These are the claims a Recorder cannot check, because it agrees with whatever SQL it is
handed: that GROUP BY d.owner_id really puts every unowned deal in ONE bucket and the
LEFT JOIN really keeps it, that ROW_NUMBER() OVER (PARTITION BY d.owner_id) really caps
each rep separately rather than globally, that the window filter really runs BEFORE the
ranking, that LAST_TOUCH_SQL really admits an edit / an activity / a live note and really
excludes a deal that was only created, and that the closed/archived sweeps really hold on
both sides of the ratio.

Since #179 it also pins the rule that a touch counts while the deal is open, up to and
including the move into Won and never after: a deal won inside the window keeps that
week's touches and gains the win itself, while a note typed on an already-won deal, a win
outside the window, and a deal sitting in Won with no journaled win all count for nothing.

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


def _win(deal_id: int, *, days_ago: int | None = None) -> None:
    """A REAL move into Won, so the journal row is written by the code path production
    uses — there is no INSERT INTO deal_stage_events anywhere in this suite, on purpose.

    ``days_ago`` then backdates that row (the test_integration_crm_lifecycle_pg idiom),
    which is how a win is placed outside the rolling window without putting anything in
    the future. It rewrites every 'won' row for the deal, so call it with no ``days_ago``
    for the second win of a reopened-then-re-won deal.
    """
    from core.postgres import pg_execute
    from crm import service

    assert service.mark_deal_won(deal_id) is not None
    if days_ago is not None:
        pg_execute(
            "UPDATE deal_stage_events SET changed_at = now() - make_interval(days => %s) "
            "WHERE deal_id = %s AND new_stage = 'won'",
            (days_ago, deal_id),
        )


def _win_at(deal_id: int, instant) -> None:
    """As ``_win``, but pins the journal row to an EXACT instant so a test can sit a win
    on a window bound rather than merely near one."""
    from core.postgres import pg_execute
    from crm import service

    assert service.mark_deal_won(deal_id) is not None
    pg_execute(
        "UPDATE deal_stage_events SET changed_at = %s "
        "WHERE deal_id = %s AND new_stage = 'won'",
        (instant, deal_id),
    )


def _activity(deal_id: int, days_ago: int = 0) -> None:
    """One logged activity, dated off the DATABASE clock for the reason the
    activity/note test states: the module-level NOW is captured at import."""
    from core.postgres import pg_execute

    pg_execute(
        "INSERT INTO activity_log (deal_id, activity, note, created_at) "
        "VALUES (%s, 'call', 'rang', now() - make_interval(days => %s))",
        (deal_id, days_ago),
    )


def _won_at(deal_id: int):
    """The instant the journal says this deal most recently entered 'won'."""
    from core.postgres import pg_fetchone

    return pg_fetchone(
        "SELECT MAX(changed_at) AS at FROM deal_stage_events "
        "WHERE deal_id = %s AND new_stage = 'won'",
        (deal_id,),
    )["at"]


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


def test_lost_and_archived_deals_are_excluded_from_both_sides():
    """Lost is deliberately asymmetric with Won (#179): crm_bulk_move_deals can mark a
    whole column lost in one click, and a symmetric rule would mint that many touches."""
    from crm import service

    ada = _user("Ada")
    _deal("Open", owner=ada)
    _deal("Lost", owner=ada, stage="lost")
    _deal("Archived", owner=ada, archived=True)

    reps = _reps(service.get_weekly_touches())
    assert (reps["Ada"]["open_deals"], reps["Ada"]["touches"]) == (1, 1)
    assert [d["title"] for d in reps["Ada"]["deals"]] == ["Open"]


def test_a_won_deal_with_no_journaled_win_contributes_nothing():
    """Forward-only, no backfill. Three real cases land here: a deal created straight
    into 'won' (create_deal has no old stage to transition from, so it writes no event),
    the demo seed's raw-INSERTed won row, and anything won before the journal existed.

    The planted deal is TOUCHED, and that is load-bearing rather than incidental: its
    updated_at then sits inside the window, which is what makes this the mutation detector
    for LEAST(last_touch, won_at) — Postgres LEAST ignores NULL operands, so that shape
    would credit this deal with its updated_at. Verified both ways: with `touched=False`
    the deal's updated_at equals its created_at, the creation guard excludes it anyway, and
    the LEAST mutation goes UNDETECTED. Do not "tidy" that argument away.
    """
    from crm import service

    ada = _user("Ada")
    _deal("Open", owner=ada)
    _deal("Won, never journaled", owner=ada, stage="won")

    reps = _reps(service.get_weekly_touches())
    assert (reps["Ada"]["open_deals"], reps["Ada"]["touches"]) == (1, 1)
    assert [d["title"] for d in reps["Ada"]["deals"]] == ["Open"]


def test_touches_on_a_deal_survive_its_move_to_won_and_the_move_itself_counts():
    """The issue's worked example: noted Monday, edited Wednesday, won Thursday. By
    Friday every one of those touches used to be gone; now the deal counts once, dated
    at the win, and the rep's open_deals is 0 while their touches is 1."""
    from crm import service

    ada = _user("Ada")
    deal = _deal("Proposal to Won", owner=ada, touched=False)
    _activity(deal, days_ago=3)                      # Monday's note
    assert service.update_deal(deal, value=5000) is not None   # Wednesday's field edit
    _win(deal)                                       # Thursday's close

    out = service.get_weekly_touches()
    rep = _reps(out)["Ada"]
    assert (rep["open_deals"], rep["touches"]) == (0, 1)
    assert [d["title"] for d in rep["deals"]] == ["Proposal to Won"]
    assert rep["deals"][0]["stage"] == "won"
    # Dated at the win, not at the note and not at the edit.
    assert rep["deals"][0]["touched_at"] == _won_at(deal)
    assert (out["total_touches"], out["total_open_deals"]) == (1, 0)


def test_the_move_to_won_is_itself_a_touch_with_nothing_before_it():
    """Nothing noted, nothing edited — just the close. The worked-example test stages an
    activity AND an edit first, so it cannot prove this on its own."""
    from crm import service

    ada = _user("Ada")
    deal = _deal("Won outright", owner=ada, touched=False)
    _win(deal)

    rep = _reps(service.get_weekly_touches())["Ada"]
    assert (rep["open_deals"], rep["touches"]) == (0, 1)
    assert [d["title"] for d in rep["deals"]] == ["Won outright"]


def test_a_note_on_a_deal_already_won_is_not_a_touch():
    """Row four of the issue's table. The deal's instant froze at its win, so a note
    typed while it sits in Won moves nothing — and the win itself is long outside the
    window, so the deal is not on the board at all."""
    from crm import service

    ada = _user("Ada")
    won = _deal("Won last month", owner=ada, touched=False)
    _win(won, days_ago=20)
    _activity(won)                                   # a note, now, while sitting in Won
    _deal("Still open, untouched", owner=ada, touched=False)

    rep = _reps(service.get_weekly_touches())["Ada"]
    assert (rep["open_deals"], rep["touches"]) == (1, 0)
    assert rep["deals"] == []


def test_a_reopened_then_rewon_deal_counts_once_at_its_latest_win():
    """MAX over the 'won' events, so a deal re-won this week is credited this week and
    not in the window of the win it was reopened out of."""
    from core.postgres import pg_fetchone
    from crm import service

    ada = _user("Ada")
    deal = _deal("Won, reopened, re-won", owner=ada, touched=False)
    _win(deal, days_ago=20)
    assert service.update_deal_stage(deal, "negotiation") is not None
    _win(deal)

    assert pg_fetchone(
        "SELECT COUNT(*) AS c FROM deal_stage_events "
        "WHERE deal_id = %s AND new_stage = 'won'", (deal,),
    )["c"] == 2

    rep = _reps(service.get_weekly_touches())["Ada"]
    assert rep["touches"] == 1
    assert [d["title"] for d in rep["deals"]] == ["Won, reopened, re-won"]
    assert rep["deals"][0]["touched_at"] == _won_at(deal)

    # A window covering only the FIRST win credits nothing: MAX picked the latest.
    early = service.get_weekly_touch_detail(
        owner_id=ada,
        start=(NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        end=(NOW - timedelta(days=19)).strftime("%Y-%m-%d"),
    )
    assert early["rep"]["touches"] == 0
    assert early["deals"] == []


def test_open_deals_means_currently_open_and_touches_can_exceed_it():
    """The two are separate facts since #179, never a ratio — which is why open_deals
    carries its own OPEN_PREDICATE_D filter over the widened row set."""
    from crm import service

    ada = _user("Ada")
    for title in ("Closed one", "Closed two"):
        deal = _deal(title, owner=ada, touched=False)
        _activity(deal, days_ago=2)
        _win(deal)
    _deal("Still open", owner=ada, touched=False)

    out = service.get_weekly_touches()
    rep = _reps(out)["Ada"]
    assert (rep["open_deals"], rep["touches"]) == (1, 2)
    assert out["total_touches"] == 2 and out["total_open_deals"] == 1


def test_a_rep_whose_only_deal_was_won_this_week_still_shows_and_keeps_the_card_visible():
    """computed_deals counts the WIDENED row set. Scoped to open deals it would read 0
    here and the client would hide the card — hiding exactly the win it exists to
    credit. The zero-keys gate is untouched: a NULL count adds nothing either way."""
    from crm import service

    ada = _user("Ada")
    _win(_deal("Only deal", owner=ada, touch_count=3, touched=False))

    out = service.get_weekly_touches()
    assert "Ada" in _reps(out)
    rep = _reps(out)["Ada"]
    assert (rep["open_deals"], rep["touches"]) == (0, 1)
    assert out["computed_deals"] == 1

    bob = _user("Bob")
    _win(_deal("Keyless win", owner=bob, touch_count=None, touched=False))
    assert service.get_weekly_touches()["computed_deals"] == 1


def test_a_win_outside_the_window_never_creates_a_zero_row():
    """A removed user must not be resurrected as a permanent 0/0 row, so the roster's
    won branch carries the window's UPPER bound as well as its lower one."""
    from crm import service

    ada = _user("Ada")
    _win(_deal("Won ten days ago", owner=ada, touched=False), days_ago=10)
    assert "Ada" not in _reps(service.get_weekly_touches())

    bob = _user("Bob")
    _win(_deal("Won just now", owner=bob, touched=False))
    historical = service.get_weekly_touches(
        start=(NOW - timedelta(days=10)).strftime("%Y-%m-%d"),
        end=(NOW - timedelta(days=8)).strftime("%Y-%m-%d"),
    )
    assert "Bob" not in _reps(historical)


def test_the_window_bounds_are_inclusive_start_exclusive_end():
    """A win sitting exactly on window_start counts and one exactly on window_end does
    not. The outside-window cases cannot tell >= from > or < from <=."""
    from crm import service

    start_day = (NOW - timedelta(days=10)).strftime("%Y-%m-%d")
    end_day = (NOW - timedelta(days=8)).strftime("%Y-%m-%d")
    window_start = datetime.strptime(start_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    window_end = (
        datetime.strptime(end_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        + timedelta(days=1)
    )

    ada = _user("Ada")
    _win_at(_deal("At the start bound", owner=ada, touched=False), window_start)
    _win_at(_deal("At the end bound", owner=ada, touched=False), window_end)

    rep = _reps(service.get_weekly_touches(start=start_day, end=end_day))["Ada"]
    assert rep["touches"] == 1
    assert [d["title"] for d in rep["deals"]] == ["At the start bound"]


def test_the_card_and_the_detail_agree_about_a_won_deal():
    """Both surfaces read one SQL definition of a touched deal, so a won deal must land
    identically on the card and on the drill-down — same counts, same rows, same date."""
    from crm import service

    ada = _user("Ada")
    _deal("Open and touched", owner=ada)
    won = _deal("Worked then won", owner=ada, touched=False)
    _activity(won, days_ago=2)
    _win(won)

    card = _reps(service.get_weekly_touches())["Ada"]
    detail = service.get_weekly_touch_detail(owner_id=ada)

    assert (card["open_deals"], card["touches"]) == (1, 2)
    assert (detail["rep"]["open_deals"], detail["rep"]["touches"]) == (1, 2)
    assert {d["id"] for d in card["deals"]} == {d["id"] for d in detail["deals"]}
    card_won = next(d for d in card["deals"] if d["id"] == won)
    detail_won = next(d for d in detail["deals"] if d["id"] == won)
    assert card_won["touched_at"] == detail_won["touched_at"] == _won_at(won)


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
