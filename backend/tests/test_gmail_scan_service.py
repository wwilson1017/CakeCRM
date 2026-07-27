"""Hermetic tests for the Gmail touch-scan heartbeat job (issue #17).

No DB, no network: gmail.client.call_gmail, gmail.store, the pg helpers, get_connection
(via the fake_conn fixture), touch_count_service.schedule_recompute, and alerts are all
monkeypatched. These prove the branch logic + the SQL statement sequence; real ON CONFLICT
idempotency + the CRM-reset compatibility live in test_gmail_scan_integration.py.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from gmail_scan import service as gs


def _raise(*_a, **_k):
    raise AssertionError("must not be called")


@pytest.fixture(autouse=True)
def _reset_inflight():
    """The single-in-flight-worker guard (gs._inflight) is module state that survives across
    tests in the same process. A test that leaves a still-blocked worker parked there would
    push the NEXT test down the 'prior pass still hung' branch (turning an expected 'ok' into
    'error'), so reset — and release/join — it after every test."""
    yield
    worker = getattr(gs, "_inflight", None)
    gs._inflight = None
    if worker is not None and worker.is_alive():
        worker.join(timeout=1)


@pytest.fixture
def connected(monkeypatch):
    """Gmail connected; own mailbox is me@own.com."""
    monkeypatch.setattr(gs.store, "is_connected", lambda *a, **k: True)
    monkeypatch.setattr(gs.store, "get_row", lambda *a, **k: {"email": "me@own.com"})


# ── run_scan_if_due gating ───────────────────────────────────────────────────

def test_not_connected_is_silent_noop(monkeypatch):
    monkeypatch.setattr(gs.store, "is_connected", lambda *a, **k: False)
    monkeypatch.setattr(gs, "pg_execute", _raise)       # no DB write when disconnected
    monkeypatch.setattr(gs, "call_gmail", _raise)
    assert gs.run_scan_if_due() is None


def test_is_connected_exception_treated_as_disconnected(monkeypatch):
    monkeypatch.setattr(gs.store, "is_connected", _raise)
    monkeypatch.setattr(gs, "pg_execute", _raise)
    monkeypatch.setattr(gs, "call_gmail", _raise)
    assert gs.run_scan_if_due() is None


def test_throttled_when_claim_lost(monkeypatch, connected):
    monkeypatch.setattr(gs, "pg_execute", lambda *a, **k: 0)   # claim lost / not due
    monkeypatch.setattr(gs, "call_gmail", _raise)              # must not scan
    assert gs.run_scan_if_due() is None


def test_claim_sql_is_atomic_due_guard(monkeypatch, connected):
    captured = {}
    monkeypatch.setattr(gs.settings, "gmail_scan_interval_minutes", 15)

    def fake_pg(sql, params=()):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return 0   # lose the claim so the scan short-circuits

    monkeypatch.setattr(gs, "pg_execute", fake_pg)
    monkeypatch.setattr(gs, "call_gmail", _raise)
    gs.run_scan_if_due()
    assert "make_interval(mins => %s)" in captured["sql"]
    assert "last_scan_at IS NULL" in captured["sql"]
    assert captured["params"] == (15,)


def test_fetch_uses_approved_op_and_window(monkeypatch, connected):
    monkeypatch.setattr(gs.settings, "gmail_scan_interval_minutes", 15)
    monkeypatch.setattr(gs, "pg_execute", lambda *a, **k: 1)   # claim + record both ok
    captured = {}

    def fake_call(op, **kwargs):
        captured["op"] = op
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(gs, "call_gmail", fake_call)
    out = gs.run_scan_if_due()
    assert captured["op"] is gs.ops.list_messages_op        # ONLY the allow-listed op
    assert captured["kwargs"] == {"query": "in:inbox newer_than:2d", "max_results": 50}
    assert out["status"] == "ok" and out["seen"] == 0


def test_window_widens_with_long_interval(monkeypatch):
    monkeypatch.setattr(gs.settings, "gmail_scan_interval_minutes", 4320)   # 3 days
    assert gs._window_days() == 4


def test_gmail_error_records_error_state_never_raises(monkeypatch, connected):
    calls = []
    monkeypatch.setattr(gs, "pg_execute", lambda sql, params=(): calls.append((sql, params)) or 1)

    def boom(*a, **k):
        raise RuntimeError("gmail is down")

    monkeypatch.setattr(gs, "call_gmail", boom)
    out = gs.run_scan_if_due()
    assert out == {"status": "error"}
    # last statement is the error-state record, carrying 'error' + the message.
    record_sql, record_params = calls[-1]
    assert "last_status = %s" in record_sql
    assert record_params[0] == "error" and "gmail is down" in record_params[1]


# ── _process_message branches (via fake_conn) ───────────────────────────────

def test_first_sight_match_logs_email_touch(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs,
                     fetchone_results=[("m1",), (7,), (99,)],  # claim, contact, activity id
                     fetchall_results=[[(3,)]])                # exactly one open deal
    msg = {"id": "m1", "from": "Dana <dana@client.com>", "subject": "Hi"}
    out = gs._process_message(msg, "me@own.com")
    assert out == {"outcome": "logged", "deal_id": 3}
    stmts = [s for s, _ in conn.executed]
    assert "INSERT INTO gmail_scanned_messages" in stmts[0] and "ON CONFLICT (message_id) DO NOTHING" in stmts[0]
    assert "lower(email) = %s" in stmts[1]
    assert "stage NOT IN ('won', 'lost')" in stmts[2] and "LIMIT 2" in stmts[2]
    assert "INSERT INTO activity_log" in stmts[3] and "'email'" in stmts[3]
    assert "outcome = 'logged'" in stmts[4]
    # activity insert params: (note, contact_id, deal_id, occurred_at)
    activity_params = conn.executed[3][1]
    assert activity_params[0] == "Inbound email from dana@client.com: Hi"
    assert activity_params[1] == 7 and activity_params[2] == 3


def test_duplicate_message_short_circuits(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs, fetchone_results=[None])   # claim hit ON CONFLICT
    out = gs._process_message({"id": "m1", "from": "a@b.com"}, "me@own.com")
    assert out == {"outcome": "duplicate"}
    assert len(conn.executed) == 1                              # nothing after the claim


def test_sender_match_is_case_insensitive(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs,
                     fetchone_results=[("m1",), (7,), (99,)],
                     fetchall_results=[[(3,)]])
    gs._process_message({"id": "m1", "from": "Dana <Dana@Client.COM>", "subject": "x"}, "me@own.com")
    assert conn.executed[0][1][1] == "dana@client.com"          # claim stores lowercased
    assert conn.executed[1][1] == ("dana@client.com",)          # contact lookup lowercased


def test_zero_open_deals_logs_contact_only(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs,
                     fetchone_results=[("m1",), (7,), (99,)],
                     fetchall_results=[[]])                      # no open deals
    out = gs._process_message({"id": "m1", "from": "a@b.com", "subject": "x"}, "me@own.com")
    assert out == {"outcome": "logged", "deal_id": None}
    assert conn.executed[3][1][2] is None                       # activity deal_id NULL


def test_multiple_open_deals_logs_contact_only(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs,
                     fetchone_results=[("m1",), (7,), (99,)],
                     fetchall_results=[[(3,), (4,)]])            # ambiguous → no fabrication
    out = gs._process_message({"id": "m1", "from": "a@b.com", "subject": "x"}, "me@own.com")
    assert out == {"outcome": "logged", "deal_id": None}
    assert conn.executed[3][1][2] is None


def test_unmatched_sender_bumps_no_activity(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs,
                     fetchone_results=[("m1",), None, (1, None)])  # claim, no contact, upsert→count 1
    out = gs._process_message({"id": "m1", "from": "new@guy.com"}, "me@own.com")
    assert out["outcome"] == "unmatched" and out["alert_email"] is None
    stmts = [s for s, _ in conn.executed]
    assert not any("INSERT INTO activity_log" in s for s in stmts)
    assert "gmail_unmatched_correspondents" in stmts[2] and "outcome = 'unmatched'" in stmts[3]


def test_unmatched_threshold_crossing_flags_alert(monkeypatch, fake_conn):
    fake_conn(monkeypatch, gs, fetchone_results=[("m1",), None, (3, None)])
    out = gs._process_message({"id": "m1", "from": "new@guy.com"}, "me@own.com")
    assert out["alert_email"] == "new@guy.com" and out["alert_count"] == 3


def test_unmatched_already_alerted_never_reflags(monkeypatch, fake_conn):
    already = datetime(2026, 7, 1, tzinfo=timezone.utc)
    fake_conn(monkeypatch, gs, fetchone_results=[("m1",), None, (9, already)])
    out = gs._process_message({"id": "m1", "from": "new@guy.com"}, "me@own.com")
    assert out["alert_email"] is None                            # fires at most once


def test_self_mail_claimed_and_skipped(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs, fetchone_results=[("m1",)])
    out = gs._process_message({"id": "m1", "from": "Me <me@own.com>"}, "me@own.com")
    assert out == {"outcome": "skipped_self"}
    assert len(conn.executed) == 1
    assert conn.executed[0][1][2] == "skipped_self"             # claimed with the skip outcome


def test_invalid_sender_claimed_and_skipped(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, gs, fetchone_results=[("m1",)])
    out = gs._process_message({"id": "m1", "from": "Undisclosed recipients:;"}, "me@own.com")
    assert out == {"outcome": "skipped_invalid"}
    assert len(conn.executed) == 1


def test_missing_message_id_raises(monkeypatch, fake_conn):
    fake_conn(monkeypatch, gs, fetchone_results=[])
    with pytest.raises(ValueError):
        gs._process_message({"from": "a@b.com"}, "me@own.com")   # never claim without an id


# ── _parse_occurred_at (F9: naive datetimes must not raise) ──────────────────

def test_parse_occurred_at_fresh_date():
    now = datetime.now(timezone.utc)
    header = (now - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    assert gs._parse_occurred_at(header) is not None


def test_parse_occurred_at_zoneless_normalized_not_raised(monkeypatch):
    # -0000 / no explicit zone → parsedate returns naive; must normalize, not TypeError.
    now = datetime.now(timezone.utc)
    header = (now - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S -0000")
    result = gs._parse_occurred_at(header)
    assert result is not None and result.tzinfo is not None


def test_parse_occurred_at_garbage_and_out_of_window_return_none():
    assert gs._parse_occurred_at("not a date") is None
    assert gs._parse_occurred_at("") is None
    assert gs._parse_occurred_at("Mon, 01 Jan 1990 00:00:00 +0000") is None      # too old
    assert gs._parse_occurred_at("Mon, 01 Jan 2099 00:00:00 +0000") is None      # far future


# ── _run_scan post-commit side effects ───────────────────────────────────────

def test_recompute_fired_once_per_deal_post_commit(monkeypatch, connected):
    monkeypatch.setattr(gs, "pg_execute", lambda *a, **k: 1)
    msgs = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
    monkeypatch.setattr(gs, "call_gmail", lambda *a, **k: msgs)
    outcomes = {
        "a": {"outcome": "logged", "deal_id": 3},
        "b": {"outcome": "logged", "deal_id": 3},    # same deal → one recompute
        "c": {"outcome": "logged", "deal_id": None},  # contact-only → no recompute
        "d": {"outcome": "duplicate"},
    }
    monkeypatch.setattr(gs, "_process_message", lambda msg, own: outcomes[msg["id"]])
    recomputed = []
    monkeypatch.setattr(gs.touch_count_service, "schedule_recompute",
                        lambda deal_id, *a, **k: recomputed.append(deal_id) or True)
    out = gs.run_scan_if_due()
    assert recomputed == [3]                          # recompute fired once (a+b same deal; c has none)
    assert out["logged"] == 3 and out["new"] == 3     # a, b, c all logged; d was a duplicate


def test_scan_abandons_pass_when_gmail_hangs(monkeypatch, connected):
    # A hung list call must not park the job forever: the wall-clock deadline abandons
    # the pass (records error, frees the slot) so the next tick retries. (F7 / lead ask.)
    import time
    monkeypatch.setattr(gs, "_SCAN_HTTP_DEADLINE", 0.05)
    monkeypatch.setattr(gs, "pg_execute", lambda *a, **k: 1)

    def hang(*a, **k):
        time.sleep(0.4)          # exceeds the deadline; the leaked worker exits promptly
        return []

    monkeypatch.setattr(gs, "call_gmail", hang)
    out = gs.run_scan_if_due()
    assert out == {"status": "error"}


def test_second_pass_reuses_single_in_flight_worker(monkeypatch):
    # Codex P2: t.join(timeout) can't kill a hung request (gmail/client.py has no transport
    # timeout), so a fresh worker per timed-out pass would leak one thread+socket per interval.
    # The guard keeps a SINGLE in-flight worker: a second pass must REUSE it, never spawn a
    # second. Block the worker on an Event (not sleep) so the liveness check is deterministic;
    # assert on identity (immune to other tests' leaked daemons), not a live-thread count.
    monkeypatch.setattr(gs, "_SCAN_HTTP_DEADLINE", 0.05)
    release = threading.Event()
    monkeypatch.setattr(gs, "call_gmail", lambda *a, **k: release.wait() or [])
    try:
        with pytest.raises(TimeoutError):
            gs._list_recent_inbox()                 # pass 1 spawns a worker that hangs
        first = gs._inflight
        assert first is not None and first.is_alive()   # the single leaked worker
        with pytest.raises(TimeoutError):
            gs._list_recent_inbox()                 # pass 2 while it's still in flight
        assert gs._inflight is first                # SAME worker — none spawned
    finally:
        release.set()                               # let the worker exit promptly
        if gs._inflight is not None:
            gs._inflight.join(timeout=1)


def test_per_message_error_is_isolated(monkeypatch, connected):
    monkeypatch.setattr(gs, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(gs, "call_gmail", lambda *a, **k: [{"id": "a"}, {"id": "b"}])

    def proc(msg, own):
        if msg["id"] == "a":
            raise RuntimeError("bad row")
        return {"outcome": "unmatched", "alert_email": None}

    monkeypatch.setattr(gs, "_process_message", proc)
    monkeypatch.setattr(gs.touch_count_service, "schedule_recompute", lambda *a, **k: True)
    out = gs.run_scan_if_due()
    assert out["status"] == "ok" and out["errors"] == 1 and out["new"] == 1   # partial → still ok


def test_all_messages_failing_records_error_status(monkeypatch, connected):
    # A pass where EVERY message fails is systemic → persisted status 'error', not 'ok'.
    calls = []
    monkeypatch.setattr(gs, "pg_execute", lambda sql, params=(): calls.append((sql, params)) or 1)
    monkeypatch.setattr(gs, "call_gmail", lambda *a, **k: [{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr(gs, "_process_message",
                        lambda msg, own: (_ for _ in ()).throw(RuntimeError("boom")))
    out = gs.run_scan_if_due()
    assert out["status"] == "error" and out["errors"] == 2
    assert calls[-1][1][0] == "error"                 # last record UPDATE carries 'error' status


# ── _fire_unmatched_alert ────────────────────────────────────────────────────

def test_alert_created_then_stamped(monkeypatch):
    order = []                                        # pin the create-BEFORE-stamp guarantee
    created = {}
    monkeypatch.setattr(gs.alerts, "create_alert",
                        lambda **k: order.append("create") or created.update(k) or {"ok": True})
    stamped = {}
    monkeypatch.setattr(gs, "pg_execute",
                        lambda sql, params=(): order.append("stamp")
                        or stamped.update(sql=" ".join(sql.split()), params=params) or 1)
    gs._fire_unmatched_alert("new@guy.com", 3)
    assert order == ["create", "stamp"]               # create FIRST so a stamp failure only retries
    assert created["source"] == "gmail_touch_scan" and created["source_id"] == "new@guy.com"
    assert "alerted_at = now()" in stamped["sql"] and "alerted_at IS NULL" in stamped["sql"]
    assert stamped["params"] == ("new@guy.com",)


def test_alert_failure_skips_stamp_and_never_raises(monkeypatch):
    def boom(**k):
        raise RuntimeError("db down")

    monkeypatch.setattr(gs.alerts, "create_alert", boom)
    monkeypatch.setattr(gs, "pg_execute", _raise)     # stamp must not run
    gs._fire_unmatched_alert("new@guy.com", 3)         # must not raise
