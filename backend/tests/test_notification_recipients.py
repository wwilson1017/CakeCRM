"""Notification recipients — multi-user Phase B / B3 (issue #192).

`notifications.user_id` and `push_subscriptions.user_id` are nullable and NULL means
BROADCAST. That one sentence has four consequences, and each of them is a way to leak
somebody's notification if it is wrong, so each gets a test here:

  1. every bell read and dismissal is guarded by ``(user_id = %s OR user_id IS NULL)``
     — not just the list, which is the one people remember;
  2. a TARGETED web push reaches that seat's stamped subscriptions ONLY, while a
     BROADCAST reaches every stored subscription including unclaimed legacy ones (the
     migration deliberately claims nothing, so "unclaimed gets broadcasts and nothing
     else" is the behavior the whole no-claim decision rests on);
  3. the subscribe upsert RE-STAMPS ``user_id``, which is the self-heal that makes (2)
     converge without anybody clicking anything;
  4. a proactive nudge routes to the record's owner, and an UNOWNED record broadcasts.

Hermetic: pg helpers and pywebpush are mocked, no database.
"""

import sys
import types

import pytest

from notifications import delivery, service, subscriptions, vapid


class _Calls:
    """Records (flattened sql, params) for the pg helper it replaces."""

    def __init__(self, rowcount=1, rows=None, one=None):
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = rowcount
        self.rows = rows if rows is not None else []
        self.one = one

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.rowcount

    def fetchall(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.rows

    def fetchone(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.one

    @property
    def sql(self) -> str:
        assert len(self.calls) == 1, f"expected exactly one statement, got {len(self.calls)}"
        return self.calls[0][0]

    @property
    def params(self) -> tuple:
        assert len(self.calls) == 1, f"expected exactly one statement, got {len(self.calls)}"
        return self.calls[0][1]


GUARD = "(user_id = %s OR user_id IS NULL)"
ME, THEM = 7, 9


# ── 1. every bell surface is guarded, not just the list ──────────────────────

def test_list_is_scoped_to_me_or_broadcast(monkeypatch):
    c = _Calls()
    monkeypatch.setattr(service, "pg_fetchall", c.fetchall)
    service.list_notifications(user_id=ME, status="active", limit=5)
    assert GUARD in c.sql
    assert c.params[0] == ME


def test_list_all_statuses_is_still_scoped(monkeypatch):
    """?status=all widens the STATUS filter, never the recipient one."""
    c = _Calls()
    monkeypatch.setattr(service, "pg_fetchall", c.fetchall)
    service.list_notifications(user_id=ME, status="all")
    assert GUARD in c.sql
    assert c.params[0] == ME


def test_badge_count_is_scoped(monkeypatch):
    """The badge is a separate query from the list (#6: the list is capped). An
    unscoped count would show a seat a number it cannot explain by opening the bell."""
    c = _Calls(one={"n": 3})
    monkeypatch.setattr(service, "pg_fetchone", c.fetchone)
    assert service.get_active_count(user_id=ME) == 3
    assert GUARD in c.sql
    assert c.params == (ME,)


def test_dismiss_is_scoped(monkeypatch):
    c = _Calls(rowcount=1)
    monkeypatch.setattr(service, "pg_execute", c.execute)
    service.dismiss_notification("n1", user_id=ME)
    assert GUARD in c.sql
    assert c.params == ("n1", ME)


def test_dismissing_someone_elses_reports_not_found_not_forbidden(monkeypatch):
    """The guard makes the UPDATE match zero rows; the service must then answer the
    same way it does for a genuinely missing id, so the endpoint is not an oracle for
    what exists on another seat."""
    monkeypatch.setattr(service, "pg_execute", _Calls(rowcount=0).execute)
    out = service.dismiss_notification("theirs", user_id=ME)
    assert "error" in out and "not found" in out["error"]


def test_dismiss_all_is_scoped(monkeypatch):
    c = _Calls(rowcount=4)
    monkeypatch.setattr(service, "pg_execute", c.execute)
    assert service.dismiss_all(user_id=ME) == {"ok": True, "dismissed": 4}
    assert GUARD in c.sql
    assert c.params == (ME,)


def test_readers_refuse_to_run_without_a_seat():
    """user_id is keyword-only and required on every reader/dismisser. There is no safe
    default — None would silently answer 'broadcasts only' — so forgetting it must be a
    TypeError at the call site, not a wrong answer at runtime."""
    for call in (lambda: service.list_notifications(),
                 lambda: service.get_active_count(),
                 lambda: service.dismiss_notification("n1"),
                 lambda: service.dismiss_all()):
        with pytest.raises(TypeError):
            call()


def test_create_stores_the_recipient(monkeypatch):
    c = _Calls()
    monkeypatch.setattr(service, "pg_execute", c.execute)
    service.create_notification("t", "m", [], notification_id="nid", user_id=ME)
    assert "user_id" in c.sql
    assert c.params[-1] == ME


def test_create_defaults_to_broadcast(monkeypatch):
    """The install-level senders (digest, heartbeat-failure, the unattributed
    background notify_user turn) pass no recipient and must stay install-wide."""
    c = _Calls()
    monkeypatch.setattr(service, "pg_execute", c.execute)
    service.create_notification("t", "m", [])
    assert c.params[-1] is None


# ── 2 + 3. push subscriptions: stamping, self-heal, targeted fan-out ─────────

def test_subscribe_restamps_the_owner_on_conflict(monkeypatch):
    """THE self-heal. Without `user_id = EXCLUDED.user_id` an endpoint keeps whichever
    seat first registered it, so a handed-down or legacy browser would go on receiving
    the previous occupant's targeted notifications forever."""
    c = _Calls()
    monkeypatch.setattr(subscriptions, "pg_execute", c.execute)
    subscriptions.save_subscription("https://push.example/a", "p", "a", ME, "UA")
    assert "ON CONFLICT (endpoint) DO UPDATE" in c.sql
    assert "user_id = EXCLUDED.user_id" in c.sql
    assert c.params[-1] == ME


def test_targeted_subscription_query_excludes_unclaimed(monkeypatch):
    """An unstamped endpoint's owner is UNKNOWN, so it must never match a targeted
    send — this is why the migration can safely leave legacy rows unclaimed."""
    c = _Calls(rows=[])
    monkeypatch.setattr(subscriptions, "pg_fetchall", c.fetchall)
    subscriptions.list_subscriptions(user_id=ME)
    assert "WHERE user_id = %s" in c.sql
    assert "IS NULL" not in c.sql
    assert c.params == (ME,)


def test_broadcast_subscription_query_has_no_filter(monkeypatch):
    c = _Calls(rows=[])
    monkeypatch.setattr(subscriptions, "pg_fetchall", c.fetchall)
    subscriptions.list_subscriptions()
    assert "WHERE" not in c.sql
    assert c.params == ()


def test_unsubscribe_route_scope_cannot_remove_another_seats_endpoint(monkeypatch):
    """The endpoint arrives in the request body, so the authenticated route scopes the
    delete. 'Mine or unclaimed' keeps a legacy endpoint removable by whoever is at that
    browser while stopping A from unsubscribing B's devices."""
    c = _Calls(rowcount=1)
    monkeypatch.setattr(subscriptions, "pg_execute", c.execute)
    subscriptions.remove_subscription("https://push.example/a", ME)
    assert GUARD in c.sql
    assert c.params == ("https://push.example/a", ME)


def test_internal_prune_still_deletes_by_endpoint_alone(monkeypatch):
    """The 404/410 prune in delivery has no seat in hand and a dead endpoint must go
    whoever owns it."""
    c = _Calls(rowcount=1)
    monkeypatch.setattr(subscriptions, "pg_execute", c.execute)
    subscriptions.remove_subscription("https://push.example/a")
    assert "user_id" not in c.sql
    assert c.params == ("https://push.example/a",)


# ── delivery fan-out by target ───────────────────────────────────────────────

_SUB = {"endpoint": "https://push.example/abc", "p256dh": "p", "auth": "a", "user_agent": ""}


@pytest.fixture
def delivered(monkeypatch):
    """Capture the recipient the row was written with and the one push fanned out to."""
    seen: dict = {"push_target": "unset"}
    monkeypatch.setattr(delivery.service, "create_notification",
                        lambda t, m, ch=None, notification_id=None, user_id=None:
                        seen.update(row_user_id=user_id) or "nid-1")
    monkeypatch.setattr(delivery.service, "update_channels", lambda *a, **k: None)
    monkeypatch.setattr(vapid, "get_vapid_keys", lambda: ("pub", "priv"))
    monkeypatch.setattr(vapid, "get_vapid_claims", lambda: {"sub": "mailto:a@b.c"})

    def _list(user_id=None):
        seen["push_target"] = user_id
        return [_SUB]

    monkeypatch.setattr(subscriptions, "list_subscriptions", _list)

    mod = types.ModuleType("pywebpush")
    mod.webpush = lambda **kw: None
    mod.WebPushException = type("WebPushException", (Exception,), {})
    monkeypatch.setitem(sys.modules, "pywebpush", mod)
    return seen


def test_targeted_delivery_routes_row_and_push(delivered):
    delivery.deliver_notification("Hi", "there", user_id=ME)
    assert delivered["row_user_id"] == ME
    assert delivered["push_target"] == ME


def test_broadcast_delivery_keeps_the_row_and_fan_out_install_wide(delivered):
    delivery.deliver_notification("Hi", "there")
    assert delivered["row_user_id"] is None
    assert delivered["push_target"] is None      # None = every stored subscription


def test_delivery_target_is_keyword_only():
    """A positional third argument would be `channels_sent` to the eye and a recipient
    to the code. Keyword-only makes every routed call say `user_id=` out loud."""
    with pytest.raises(TypeError):
        delivery.deliver_notification("Hi", "there", ME)


# ── 4. nudges route to the record's owner ────────────────────────────────────

def _nudge_env(monkeypatch, candidates, active=(THEM,)):
    """Drive _maybe_send_nudges with a claimed sweep, claimed nudges and a seat roster."""
    from proactive import service as ps

    monkeypatch.setattr(ps, "collect_nudge_candidates", lambda: candidates)
    monkeypatch.setattr(ps, "pg_execute", lambda sql, params=(): 1)       # sweep claimed
    monkeypatch.setattr(ps, "pg_fetchone", lambda sql, params=(): {"id": 1})  # nudge claimed
    monkeypatch.setattr(ps, "_active_owner_ids", lambda: set(active))
    out = []
    import notifications.delivery as d
    monkeypatch.setattr(d, "deliver_notification",
                        lambda title, message, user_id=None: out.append(user_id) or {"ok": True})
    return ps, out


def _cand(entity_id, owner_id):
    from proactive import service as ps
    return {"entity_type": "deal", "entity_id": entity_id, "owner_id": owner_id,
            "kind": ps.KIND_STALE_DEAL, "title": "Deal going cold", "message": "m"}


def test_owned_nudge_goes_to_its_owner(monkeypatch):
    from datetime import datetime, timezone
    ps, out = _nudge_env(monkeypatch, [_cand(1, THEM)])
    ps._maybe_send_nudges(datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc))
    assert out == [THEM]


def test_unowned_nudge_broadcasts(monkeypatch):
    """An unassigned stale deal is everyone's problem — and sending it to nobody would
    be the worst of the three options."""
    from datetime import datetime, timezone
    ps, out = _nudge_env(monkeypatch, [_cand(1, None)])
    ps._maybe_send_nudges(datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc))
    assert out == [None]


def test_a_deactivated_owners_nudge_broadcasts_rather_than_vanishing(monkeypatch):
    """Deactivation keeps the user row and every owner_id, so a departed rep's stale
    deals keep their owner forever. Addressing that id would hide the nudge from every
    seat that can actually act on the record."""
    from datetime import datetime, timezone
    ps, out = _nudge_env(monkeypatch, [_cand(1, THEM)], active=())   # THEM deactivated
    ps._maybe_send_nudges(datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc))
    assert out == [None]


def test_a_roster_read_failure_degrades_to_broadcast(monkeypatch):
    """The safe direction: a nudge is about a shared CRM record, so the worst case of
    guessing wrong is that the team sees what one rep would have."""
    from proactive import service as ps
    from users import service as users_service

    def boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(users_service, "list_users", boom)
    assert ps._active_owner_ids() == set()


def test_the_roster_is_read_once_per_sweep_not_once_per_nudge(monkeypatch):
    """A per-candidate lookup would put a query inside a loop capped only by the nudge
    limit, on the scheduler thread."""
    from datetime import datetime, timezone

    from proactive import service as ps

    calls = []
    monkeypatch.setattr(ps, "collect_nudge_candidates",
                        lambda: [_cand(1, THEM), _cand(2, THEM), _cand(3, None)])
    monkeypatch.setattr(ps, "pg_execute", lambda sql, params=(): 1)
    monkeypatch.setattr(ps, "pg_fetchone", lambda sql, params=(): {"id": 1})
    monkeypatch.setattr(ps, "_active_owner_ids", lambda: calls.append(1) or {THEM})
    monkeypatch.setattr(ps.settings, "proactive_max_nudges_per_run", 5)
    import notifications.delivery as d
    monkeypatch.setattr(d, "deliver_notification", lambda t, m, user_id=None: {"ok": True})
    ps._maybe_send_nudges(datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc))
    assert calls == [1]


def test_a_candidate_missing_its_owner_is_an_error_not_a_broadcast(monkeypatch):
    """`cand["owner_id"]` is indexed on purpose. If a producer ever stopped carrying the
    column, `.get()` would quietly widen one rep's nudges into install-wide ones — the
    exact leak this issue exists to close — so the contract fails loudly instead."""
    from datetime import datetime, timezone
    bad = _cand(1, None)
    del bad["owner_id"]
    ps, out = _nudge_env(monkeypatch, [bad])
    with pytest.raises(KeyError):
        ps._maybe_send_nudges(datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc))
    assert out == []


def test_candidates_carry_the_owner_from_analytics(monkeypatch):
    """#190 already SELECTs owner_id in both readers; this pins that the nudge builder
    reads it through rather than dropping it."""
    from crm import analytics_service
    from proactive import service as ps

    monkeypatch.setattr(ps.settings, "proactive_max_nudges_per_run", 3)
    monkeypatch.setattr(analytics_service, "get_stale_deals", lambda limit: {"deals": [
        {"id": 2, "title": "Neglected", "value": 2.0, "stage": "lead",
         "days_since_touch": 40, "has_open_task": False, "owner_id": THEM},
    ]})
    monkeypatch.setattr(analytics_service, "get_contact_staleness", lambda limit: {"contacts": [
        {"id": 5, "name": "Quiet", "open_deals": 1, "days_since_contact": 45,
         "owner_id": None},
    ]})
    cands = ps.collect_nudge_candidates()
    assert [(c["entity_type"], c["owner_id"]) for c in cands] == [("deal", THEM), ("contact", None)]


def test_the_digest_stays_a_broadcast(monkeypatch):
    """#98 Decision 4a: per-owner value comes from routing the NUDGES, not from
    multiplying one team-wide pipeline digest by the seat count."""
    from datetime import datetime, timezone

    from proactive import service as ps

    monkeypatch.setattr(ps, "pg_execute", lambda sql, params=(): 1)
    monkeypatch.setattr(ps, "collect_digest", lambda: {"open_deals": 1})
    monkeypatch.setattr(ps, "format_digest", lambda s: ("Daily pipeline digest", "body"))
    monkeypatch.setattr(ps, "_maybe_enhance_digest", lambda s: False)
    out = []
    import notifications.delivery as d
    monkeypatch.setattr(d, "deliver_notification",
                        lambda title, message, user_id=None: out.append(user_id) or {"ok": True})
    ps._maybe_send_digest(datetime(2026, 9, 17, 23, 0, tzinfo=timezone.utc))
    assert out == [None]
