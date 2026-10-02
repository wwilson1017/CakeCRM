"""Chatter @-mentions (issue #235) — hermetic.

Covers the four seams: request validation (StrictInt at the boundary, range/dedupe/cap
in the service), the in-transaction mention diff and who it hands back to notify, the
router scheduling delivery post-commit, and the notification itself — targeted,
linked to the record, never to the actor. Real-Postgres behaviour (the FK cascade, the
TRUNCATE sweep, ON CONFLICT) lives in test_crm_chatter_mentions_integration.py.
"""

import pytest
from conftest import FAKE_ADMIN, fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import chatter_service, links, mention_notify
from crm.router import router as crm_router
from notifications import delivery

# ── _clean_mentions ───────────────────────────────────────────────────────────


def test_clean_mentions_none_means_not_supplied():
    assert chatter_service._clean_mentions(None) is None


def test_clean_mentions_dedupes_and_keeps_order():
    assert chatter_service._clean_mentions([3, 1, 3, 2]) == [3, 1, 2]


@pytest.mark.parametrize("bad", [[True], [0], [-4], ["3"], [1.0], "3", 3, {"a": 1}])
def test_clean_mentions_rejects_anything_but_positive_ints(bad):
    # bool is an int subclass: without the explicit check `true` would mention user 1.
    with pytest.raises(ValueError):
        chatter_service._clean_mentions(bad)


def test_clean_mentions_caps_the_count():
    ok = list(range(1, chatter_service.MAX_MENTIONS + 1))
    assert chatter_service._clean_mentions(ok) == ok
    with pytest.raises(ValueError, match="Too many mentions"):
        chatter_service._clean_mentions(ok + [999])


# ── post_note / edit_note: the in-transaction diff ────────────────────────────

NOTE_COLS = ["id", "entity_type", "entity_id", "message"]


def _stmts(conn):
    return [s for s, _ in conn.executed]


def test_post_note_without_mentions_touches_no_mention_table(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, chatter_service,
                     fetchone_results=[(1,), (5, "deal", 3, "hi")])
    conn.description = NOTE_COLS
    note, recipients = chatter_service.post_note("deal", 3, "hi", author_id=1)
    assert recipients == [] and note["mentions"] == []
    assert not any("crm_chatter_mentions" in s for s in _stmts(conn))


def test_post_note_stores_active_mentions_in_the_same_txn_and_skips_the_author(
        monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, chatter_service,
        fetchone_results=[(1,), (5, "deal", 3, "@Ada @Me hi")],
        # stored set (empty) → INSERT RETURNING (both active) → read-back
        fetchall_results=[[], [(7,), (1,)], [(7, "Ada"), (1, "Me")]],
    )
    conn.description = NOTE_COLS
    note, recipients = chatter_service.post_note(
        "deal", 3, "@Ada @Me hi", author_id=1, mentions=[7, 1])
    stmts = _stmts(conn)
    lock = stmts.index("SELECT 1 FROM deals WHERE id = %s FOR UPDATE")
    insert = next(i for i, s in enumerate(stmts) if "INSERT INTO crm_chatter_mentions" in s)
    assert lock < insert  # one transaction, target locked first
    insert_sql = stmts[insert]
    # Only ACTIVE seats can be newly mentioned, the name is the SERVER's, and a
    # duplicate is a no-op rather than a 500.
    assert "u.is_active" in insert_sql
    assert "COALESCE(NULLIF(btrim(u.name), ''), u.email)" in insert_sql
    assert "ON CONFLICT (note_id, user_id) DO NOTHING RETURNING user_id" in insert_sql
    assert recipients == [7]  # the author's own mention is stored but not notified
    assert note["mentions"] == [{"user_id": 7, "name": "Ada"}, {"user_id": 1, "name": "Me"}]


def test_post_note_drops_an_inactive_or_unknown_id_without_failing_the_note(
        monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, chatter_service,
        fetchone_results=[(1,), (5, "deal", 3, "hi")],
        fetchall_results=[[], [(7,)], [(7, "Ada")]],  # 8 was not active → not RETURNed
    )
    conn.description = NOTE_COLS
    _, recipients = chatter_service.post_note("deal", 3, "hi", author_id=1, mentions=[7, 8])
    assert recipients == [7]


def test_edit_note_omitted_mentions_preserves_the_stored_set(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, chatter_service,
                     fetchone_results=[(1,), (5, "deal", 3, "x")],
                     fetchall_results=[[(7, "Ada")]])
    conn.description = NOTE_COLS
    note, recipients = chatter_service.edit_note(5, "x", mentions=None, actor_id=1)
    assert recipients == []
    assert note["mentions"] == [{"user_id": 7, "name": "Ada"}]
    assert not any("DELETE FROM crm_chatter_mentions" in s or "INSERT INTO crm_chatter_mentions" in s
                   for s in _stmts(conn))


def test_edit_note_list_is_the_new_set_and_only_additions_are_told(monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, chatter_service,
        fetchone_results=[(1,), (5, "deal", 3, "x")],
        # stored {7, 8} → wanted [8, 9, 2]: remove 7, add 9 and 2 (2 is the editor)
        fetchall_results=[[(7,), (8,)], [(9,), (2,)], [(8, "Bo"), (9, "Cy"), (2, "Me")]],
    )
    conn.description = NOTE_COLS
    _, recipients = chatter_service.edit_note(5, "x", mentions=[8, 9, 2], actor_id=2)
    stmts = _stmts(conn)
    assert stmts[0] == "SELECT 1 FROM crm_chatter WHERE id = %s FOR UPDATE"
    delete_params = next(p for s, p in conn.executed if "DELETE FROM crm_chatter_mentions" in s)
    assert delete_params == (5, [7])
    insert_params = next(p for s, p in conn.executed if "INSERT INTO crm_chatter_mentions" in s)
    assert insert_params == (5, [9, 2])  # 8 was already there: not re-inserted, not re-told
    assert recipients == [9]


def test_edit_note_empty_list_clears_every_mention(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, chatter_service,
                     fetchone_results=[(1,), (5, "deal", 3, "x")],
                     fetchall_results=[[(7,)], []])
    conn.description = NOTE_COLS
    note, recipients = chatter_service.edit_note(5, "x", mentions=[], actor_id=1)
    assert recipients == [] and note["mentions"] == []
    assert any("DELETE FROM crm_chatter_mentions" in s for s in _stmts(conn))


def test_edit_note_missing_note_returns_none(monkeypatch, fake_conn):
    fake_conn(monkeypatch, chatter_service, fetchone_results=[None])
    assert chatter_service.edit_note(999, "x", mentions=[3]) == (None, [])


def test_add_note_the_assistant_path_carries_no_mentions(monkeypatch):
    """crm_add_note calls add_note, which has no mentions parameter at all — so no agent
    turn, attended or unattended, can make a mention notification fire."""
    import inspect

    assert "mentions" not in inspect.signature(chatter_service.add_note).parameters


# ── The router: validation at the boundary, delivery post-commit ──────────────


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


@pytest.fixture
def notified(monkeypatch):
    calls = []
    monkeypatch.setattr(mention_notify, "notify_mentions",
                        lambda note, recipients, actor: calls.append((note, recipients, actor)))
    return calls


def test_post_schedules_one_delivery_task_for_the_recipients(client, notified, monkeypatch):
    seen = {}

    def _post(t, i, m, author_id=None, mentions=None):
        seen.update(author_id=author_id, mentions=mentions)
        return {"id": 9, "entity_type": t, "entity_id": i, "message": m, "mentions": []}, [7]

    monkeypatch.setattr(chatter_service, "post_note", _post)
    r = client.post("/api/crm/chatter/deal/3/note", json={"message": "@Ada hi", "mentions": [7]})
    assert r.status_code == 200
    assert seen == {"author_id": FAKE_ADMIN["id"], "mentions": [7]}
    assert len(notified) == 1
    note, recipients, actor = notified[0]
    assert note["id"] == 9 and recipients == [7] and actor["id"] == FAKE_ADMIN["id"]
    assert "newly_mentioned" not in r.json()  # recipients never leak into the response


def test_post_with_no_recipients_schedules_nothing(client, notified, monkeypatch):
    monkeypatch.setattr(chatter_service, "post_note",
                        lambda *a, **k: ({"id": 9, "message": "hi"}, []))
    assert client.post("/api/crm/chatter/deal/3/note", json={"message": "hi"}).status_code == 200
    assert notified == []


@pytest.mark.parametrize("bad", [[True], ["7"], [1.5], "7"])
def test_mentions_must_be_strict_ints_at_the_boundary(client, notified, bad):
    r = client.post("/api/crm/chatter/deal/3/note", json={"message": "hi", "mentions": bad})
    assert r.status_code == 422
    r = client.patch("/api/crm/chatter/note/5", json={"message": "hi", "mentions": bad})
    assert r.status_code == 422


def test_patch_omitted_and_null_both_preserve_and_a_list_is_passed_through(
        client, notified, monkeypatch):
    seen = []

    def _edit(nid, m, mentions=None, actor_id=None):
        seen.append((mentions, actor_id))
        return {"id": nid, "entity_type": "contact", "entity_id": 4, "message": m}, (
            [8] if mentions else [])

    monkeypatch.setattr(chatter_service, "edit_note", _edit)
    client.patch("/api/crm/chatter/note/5", json={"message": "a"})
    client.patch("/api/crm/chatter/note/5", json={"message": "a", "mentions": None})
    client.patch("/api/crm/chatter/note/5", json={"message": "a", "mentions": [8]})
    client.patch("/api/crm/chatter/note/5", json={"message": "a", "mentions": []})
    assert seen == [(None, 1), (None, 1), ([8], 1), ([], 1)]
    assert [r for _, r, _ in notified] == [[8]]


def test_a_bad_mention_id_is_a_400_from_the_real_service(client, notified):
    # 0 passes StrictInt but not the service's positive-id rule; raised before any DB call.
    r = client.post("/api/crm/chatter/deal/3/note", json={"message": "hi", "mentions": [0]})
    assert r.status_code == 400


# ── notify_mentions: targeted, linked, never the actor ────────────────────────


@pytest.fixture
def delivered(monkeypatch):
    calls = []

    def _deliver(title, message, *, user_id=None, link=None):
        calls.append({"title": title, "message": message, "user_id": user_id, "link": link})
        return {"ok": True}

    monkeypatch.setattr(delivery, "deliver_notification", _deliver)
    return calls


def test_one_targeted_delivery_per_recipient_linking_to_the_deal(monkeypatch, delivered):
    monkeypatch.setattr(mention_notify, "pg_fetchone", lambda sql, p: {"name": "Acme renewal"})
    note = {"entity_type": "deal", "entity_id": 12, "message": "can you  take\nthis call?"}
    sent = mention_notify.notify_mentions(note, [7, 8], {"id": 1, "name": "Robin", "email": "r@x"})
    assert sent == 2
    assert [c["user_id"] for c in delivered] == [7, 8]
    assert all(c["link"] == "/crm/pipeline?deal=12" for c in delivered)
    assert delivered[0]["title"] == "Robin mentioned you on Deal — Acme renewal"
    assert delivered[0]["message"] == "can you take this call?"


@pytest.mark.parametrize("entity_type,path", [
    ("contact", "/crm/contacts/4"), ("company", "/crm/companies/4"),
])
def test_contact_and_company_link_to_their_detail_route(monkeypatch, delivered, entity_type, path):
    monkeypatch.setattr(mention_notify, "pg_fetchone", lambda sql, p: {"name": "X"})
    mention_notify.notify_mentions(
        {"entity_type": entity_type, "entity_id": 4, "message": "m"}, [7], {"id": 1})
    assert delivered[0]["link"] == path


def test_the_actor_is_never_notified_even_if_handed_in(monkeypatch, delivered):
    monkeypatch.setattr(mention_notify, "pg_fetchone", lambda sql, p: None)
    mention_notify.notify_mentions({"entity_type": "deal", "entity_id": 3, "message": "m"},
                                   [1, 7], {"id": 1})
    assert [c["user_id"] for c in delivered] == [7]


def test_an_unreadable_record_name_degrades_to_its_number(monkeypatch, delivered):
    def _boom(sql, p):
        raise RuntimeError("db down")

    monkeypatch.setattr(mention_notify, "pg_fetchone", _boom)
    mention_notify.notify_mentions({"entity_type": "deal", "entity_id": 3, "message": "m"},
                                   [7], {"id": 1, "email": "r@x"})
    assert delivered[0]["title"] == "r@x mentioned you on Deal #3"


def test_one_failed_recipient_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setattr(mention_notify, "pg_fetchone", lambda sql, p: None)
    seen = []

    def _deliver(title, message, *, user_id=None, link=None):
        seen.append(user_id)
        if user_id == 7:
            raise RuntimeError("push exploded")

    monkeypatch.setattr(delivery, "deliver_notification", _deliver)
    sent = mention_notify.notify_mentions(
        {"entity_type": "deal", "entity_id": 3, "message": "m"}, [7, 8], {"id": 1})
    assert seen == [7, 8] and sent == 1


def test_a_long_note_is_excerpted():
    out = mention_notify._excerpt("x" * 500)
    assert len(out) == mention_notify.EXCERPT_LEN and out.endswith("…")


# ── Delivery: the link is validated, then reaches every channel ───────────────


@pytest.mark.parametrize("link,ok", [
    ("/crm/pipeline?deal=3", True),
    ("/crm/contacts/4", True),
    ("//evil.example/x", False),
    ("/\\evil.example", False),
    ("https://evil.example", False),
    ("javascript:alert(1)", False),
    ("/crm/a b", False),
    ("/crm/\nx", False),
    ("", False),
    (None, False),
    ("/" + "a" * 600, False),
])
def test_clean_link(link, ok):
    assert delivery._clean_link(link) == (link if ok else None)


def test_push_payload_clicks_through_to_the_link():
    import json

    assert json.loads(delivery._build_payload("t", "m", "n", "/crm/contacts/4"))["url"] == \
        "/crm/contacts/4"
    assert json.loads(delivery._build_payload("t", "m", "n"))["url"] == "/crm"


def test_the_row_gets_the_cleaned_link_and_telegram_gets_the_app_url(monkeypatch):
    rows, texts = [], []
    monkeypatch.setattr(delivery.service, "create_notification",
                        lambda t, m, ch=None, notification_id=None, user_id=None, link=None:
                        rows.append(link) or "nid")
    monkeypatch.setattr(delivery.service, "update_channels", lambda *a, **k: None)
    monkeypatch.setattr(delivery, "_send_web_push", lambda *a, **k: False)
    import telegram.service as tg

    monkeypatch.setattr(tg, "notify_user_telegram", lambda uid, text: texts.append(text) or True)
    monkeypatch.setattr(links, "app_url", lambda p: "https://crm.example" + p)

    delivery.deliver_notification("T", "M", user_id=7, link="/crm/contacts/4")
    delivery.deliver_notification("T", "M", user_id=7, link="//evil.example")
    assert rows == ["/crm/contacts/4", None]
    assert texts == ["T\nM\nhttps://crm.example/crm/contacts/4", "T\nM"]


def test_record_path():
    assert links.record_path("deal", 3) == "/crm/pipeline?deal=3"
    assert links.record_path("contact", 3) == "/crm/contacts/3"
    assert links.record_path("company", 3) == "/crm/companies/3"
    with pytest.raises(KeyError):
        links.record_path("invoice", 3)


def test_the_frontend_mention_cap_agrees_with_the_server():
    """The composer caps what it sends at its own MAX_MENTIONS; the server refuses more
    than chatter_service.MAX_MENTIONS. Read the TypeScript source rather than restating
    the number, the test_crm_deal_links precedent — two hardcoded copies prove nothing."""
    import re
    from pathlib import Path

    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "crm"
          / "chatterMentions.ts").read_text(encoding="utf-8")
    match = re.search(r"export const MAX_MENTIONS = (\d+);", ts)
    assert match, "MAX_MENTIONS not found in chatterMentions.ts"
    assert int(match.group(1)) == chatter_service.MAX_MENTIONS
