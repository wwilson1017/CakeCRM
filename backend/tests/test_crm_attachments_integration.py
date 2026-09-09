"""Real-Postgres integration for chatter note attachments (issue #57).

These are the claims a FakeConn cannot check, because it agrees with whatever SQL it is
handed: that the migration applies, that the FK cascade really deletes attachments when
their note goes, that BOTH `_truncate_all` variants execute against a schema where this
table is referenced (Postgres refuses to truncate a referenced table without its child,
so a missing entry is a hard error, not a silent leak), that a reused SERIAL id inherits
nothing, and that the unique index makes a retry idempotent.

Marked ``integration`` and excluded from the default no-DB run. Fixture data is fresh and
fictional — the blueprint's own fixtures carry real internals and none of them are copied.
"""

import hashlib
import io
import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


def _png(width=48, height=32) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    im = Image.new("RGB", (width, height))
    im.putdata([((x * 5) % 256, (y * 9) % 256, (x + y) % 256)
                for y in range(height) for x in range(width)])
    im.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_att_{os.getpid()}"
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
    # A FRESH database matters: the migration runner records applied filenames, so
    # re-applying this new migration against a dirty one would silently skip it.
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
    from core.postgres import get_connection
    from crm import service

    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=True)
    yield


def _contact(name="Robin Vale", email="robin@example.test") -> int:
    from crm import service

    return service.create_contact(name=name, email=email)["id"]


def _note(entity_type, entity_id, message="Called the vendor back about the sample order."):
    from crm import chatter_service

    return chatter_service.add_note(entity_type, entity_id, message)["id"]


# ── The migration itself ──────────────────────────────────────────────────────

def test_migration_created_the_table_with_its_constraints(pg_db):
    from core.postgres import pg_fetchall, pg_fetchone

    cols = {r["column_name"]: r for r in pg_fetchall(
        "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
        "WHERE table_name = 'crm_chatter_attachments'"
    )}
    assert {"id", "note_id", "filename", "mime_type", "byte_size", "sha256",
            "data", "thumb_data", "thumb_mime", "uploaded_by", "created_at"} <= set(cols)
    assert cols["data"]["data_type"] == "bytea"
    assert cols["filename"]["is_nullable"] == "NO"

    # The FK is the whole lifecycle design — without CASCADE, delete_contact leaks rows.
    # Read from pg_catalog rather than information_schema: the latter needs a LIKE with a
    # literal '%', which psycopg2 parses as a parameter placeholder.
    fk = pg_fetchone(
        """SELECT confdeltype FROM pg_constraint
           WHERE conrelid = 'crm_chatter_attachments'::regclass
             AND contype = 'f' AND confrelid = 'crm_chatter'::regclass"""
    )
    assert fk and fk["confdeltype"] == "c", "the crm_chatter FK must be ON DELETE CASCADE"
    # ...and the users FK must NOT cascade: deleting a user retires their account, it does
    # not destroy the files they uploaded to shared records.
    user_fk = pg_fetchone(
        """SELECT confdeltype FROM pg_constraint
           WHERE conrelid = 'crm_chatter_attachments'::regclass
             AND contype = 'f' AND confrelid = 'users'::regclass"""
    )
    assert user_fk and user_fk["confdeltype"] == "n", "uploaded_by must be ON DELETE SET NULL"


def test_the_thumb_pair_check_rejects_a_half_set_pair(pg_db):
    from core.postgres import get_connection

    note_id = _note("contact", _contact())
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_connection() as conn:
            conn.cursor().execute(
                """INSERT INTO crm_chatter_attachments
                       (note_id, filename, mime_type, byte_size, sha256, data, thumb_data)
                   VALUES (%s, 'x.png', 'image/png', 3, %s, %s, %s)""",
                (note_id, "b" * 64, b"abc", b"thumb"),          # thumb_mime missing
            )


def test_the_thumb_pair_check_rejects_an_unknown_thumb_mime(pg_db):
    from core.postgres import get_connection

    note_id = _note("contact", _contact())
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_connection() as conn:
            conn.cursor().execute(
                """INSERT INTO crm_chatter_attachments
                       (note_id, filename, mime_type, byte_size, sha256, data,
                        thumb_data, thumb_mime)
                   VALUES (%s, 'x.png', 'image/png', 3, %s, %s, %s, 'image/svg+xml')""",
                (note_id, "c" * 64, b"abc", b"thumb"),
            )


# ── Round trip ────────────────────────────────────────────────────────────────

def test_upload_list_and_read_back_the_exact_bytes(pg_db):
    from core import thumbnails
    from crm import attachment_service as svc, chatter_service

    data = _png()
    note_id = _note("contact", _contact())
    meta = svc.create_attachment(note_id, data=data, filename="site-photo.png")

    assert meta["mime_type"] == "image/png"
    assert meta["byte_size"] == len(data)
    assert meta["has_thumb"] is True
    assert "data" not in meta and "sha256" not in meta

    # The embed is what the REST route and the agent tool both read.
    notes = chatter_service.get_chatter("contact", 1)
    assert [a["id"] for a in notes[0]["attachments"]] == [meta["id"]]

    assert svc.get_file(meta["id"])["data"] == data
    thumb = svc.get_thumb(meta["id"])
    assert thumb["thumb_mime"] in thumbnails.THUMB_MIMES
    assert 0 < len(thumb["thumb_data"]) <= svc.THUMB_MAX_BYTES
    assert svc.get_meta(meta["id"])["sha256"] == hashlib.sha256(data).hexdigest()


def test_a_non_image_stores_without_a_thumbnail(pg_db):
    from crm import attachment_service as svc

    note_id = _note("contact", _contact())
    meta = svc.create_attachment(note_id, data=b"%PDF-1.7\nnot really", filename="quote.pdf")
    assert meta["mime_type"] == "application/pdf"
    assert meta["has_thumb"] is False
    assert svc.get_thumb(meta["id"]) is None


def test_an_unrecognized_body_is_stored_as_inert_octet_stream(pg_db):
    from crm import attachment_service as svc

    note_id = _note("contact", _contact())
    meta = svc.create_attachment(
        note_id, data=b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>",
        filename="logo.svg",
    )
    assert meta["mime_type"] == "application/octet-stream"


# ── Idempotency and the cap ───────────────────────────────────────────────────

def test_the_same_bytes_twice_produce_one_row(pg_db):
    from core.postgres import pg_fetchone
    from crm import attachment_service as svc

    data = _png()
    note_id = _note("contact", _contact())
    first = svc.create_attachment(note_id, data=data, filename="a.png")
    second = svc.create_attachment(note_id, data=data, filename="a.png")
    assert second["id"] == first["id"]
    assert pg_fetchone(
        "SELECT COUNT(*) AS n FROM crm_chatter_attachments WHERE note_id = %s", (note_id,)
    )["n"] == 1


def test_the_per_note_cap_is_enforced_and_a_retry_still_works_at_the_cap(pg_db):
    from crm import attachment_service as svc

    note_id = _note("contact", _contact())
    last = None
    for i in range(svc.MAX_ATTACHMENTS_PER_NOTE):
        last = svc.create_attachment(note_id, data=_png(48 + i, 32), filename=f"p{i}.png")

    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(note_id, data=_png(200, 100), filename="over.png")
    assert e.value.code == "limit_exceeded"

    # Idempotency BEFORE the cap: a lost response on the tenth attachment must still be
    # retryable, or that row is permanently unrecoverable.
    again = svc.create_attachment(
        note_id, data=_png(48 + svc.MAX_ATTACHMENTS_PER_NOTE - 1, 32),
        filename="p9.png",
    )
    assert again["id"] == last["id"]


def test_an_archived_note_refuses_new_attachments_but_keeps_the_ones_it_has(pg_db):
    from crm import attachment_service as svc, chatter_service

    contact_id = _contact()
    note_id = _note("contact", contact_id)
    meta = svc.create_attachment(note_id, data=_png(), filename="before.png")

    chatter_service.archive_note(note_id)
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(note_id, data=_png(60, 40), filename="after.png")
    assert e.value.code == "note_archived"

    # The write gate must NOT be reused as a read gate: archiving hides a note, it does
    # not destroy what is on it.
    notes = chatter_service.get_chatter("contact", contact_id, include_archived=True)
    assert [a["id"] for a in notes[0]["attachments"]] == [meta["id"]]
    assert svc.get_file(meta["id"])["data"]


def test_attaching_to_a_missing_note_is_refused(pg_db):
    from crm import attachment_service as svc

    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(999_999, data=_png(), filename="x.png")
    assert e.value.code == "note_not_found"


# ── Lifecycle: the reason this table got a real FK ────────────────────────────

def test_deleting_the_note_cascades_its_attachments(pg_db):
    from core.postgres import get_connection, pg_fetchone
    from crm import attachment_service as svc

    note_id = _note("contact", _contact())
    svc.create_attachment(note_id, data=_png(), filename="x.png")
    with get_connection() as conn:
        conn.cursor().execute("DELETE FROM crm_chatter WHERE id = %s", (note_id,))
    assert pg_fetchone(
        "SELECT COUNT(*) AS n FROM crm_chatter_attachments WHERE note_id = %s", (note_id,)
    )["n"] == 0


def test_delete_contact_takes_the_attachments_with_it(pg_db):
    """delete_contact has NO attachment-specific code — the cascade is what covers it, so
    this is the test standing in for the line that would otherwise be there."""
    from core.postgres import pg_fetchone
    from crm import attachment_service as svc, service

    contact_id = _contact()
    note_id = _note("contact", contact_id)
    svc.create_attachment(note_id, data=_png(), filename="x.png")

    assert service.delete_contact(contact_id) is True
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter_attachments")["n"] == 0


def test_delete_company_takes_the_attachments_with_it(pg_db):
    from core.postgres import pg_fetchone
    from crm import attachment_service as svc, service

    company_id = service.create_company(name="Northwind Supply")["id"]
    note_id = _note("company", company_id)
    svc.create_attachment(note_id, data=_png(), filename="x.png")

    assert service.delete_company(company_id) is True
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter_attachments")["n"] == 0


def test_delete_attachment_removes_only_that_row(pg_db):
    from core.postgres import pg_fetchone
    from crm import attachment_service as svc

    note_id = _note("contact", _contact())
    keep = svc.create_attachment(note_id, data=_png(48, 32), filename="keep.png")
    drop = svc.create_attachment(note_id, data=_png(60, 40), filename="drop.png")

    assert svc.delete_attachment(drop["id"]) is True
    assert svc.delete_attachment(drop["id"]) is False          # already gone
    assert svc.get_file(drop["id"]) is None
    assert svc.get_file(keep["id"]) is not None
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter WHERE id = %s", (note_id,))["n"] == 1


# ── The CRM reset (a missing TRUNCATE entry is a hard error, not a leak) ──────

@pytest.mark.parametrize("include_definitions", [False, True])
def test_both_truncate_variants_execute_against_the_real_schema(pg_db, include_definitions):
    """Postgres REFUSES to truncate a referenced table without its child in the same
    statement. So if crm_chatter_attachments were dropped from either string, this raises
    — the failure mode is a broken CRM reset, not a silent orphan."""
    from core.postgres import get_connection, pg_fetchone
    from crm import attachment_service as svc, service

    note_id = _note("contact", _contact())
    svc.create_attachment(note_id, data=_png(), filename="x.png")

    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=include_definitions)
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter_attachments")["n"] == 0


def test_a_note_reusing_a_truncated_id_inherits_no_attachments(pg_db):
    """The ID-reuse orphan this repo has been bitten by before: RESTART IDENTITY hands the
    same SERIAL id to the next row, so anything left behind reattaches itself."""
    from core.postgres import get_connection
    from crm import attachment_service as svc, chatter_service, service

    contact_id = _contact()
    old_note = _note("contact", contact_id)
    svc.create_attachment(old_note, data=_png(), filename="ghost.png")

    with get_connection() as conn:
        service._truncate_all(conn.cursor())

    new_contact = _contact("Alex Marsh", "alex@example.test")
    new_note = _note("contact", new_contact, "Fresh thread on a reused id.")
    assert new_note == old_note                                  # the id really was reused
    notes = chatter_service.get_chatter("contact", new_contact)
    assert notes[0]["attachments"] == []


# ── merge_deals (#57 added a claim about it; this is the claim) ───────────────

def test_merging_a_deal_copies_note_text_without_duplicating_attachments(pg_db):
    """The copies carry message text only, and the source keeps its own files.

    merge_deals copies notes onto the target and ARCHIVES the source rather than deleting
    it, so duplicating multi-MB blobs would double the storage for a second view of the
    same files. Both halves are asserted: nothing new on the target, everything still
    reachable on the source.
    """
    from core.postgres import pg_fetchone
    from crm import attachment_service as svc, chatter_service, service

    source = service.create_deal(title="Bulk order — spring", stage="lead")["id"]
    target = service.create_deal(title="Bulk order — consolidated", stage="lead")["id"]
    note_id = _note("deal", source, "Photo of the damaged pallet.")
    attachment = svc.create_attachment(note_id, data=_png(), filename="pallet.png")

    service.merge_deals(target, source)

    # The copied note landed and has NO attachments. (merge_deals also writes its own
    # "Merged deal #N into this deal" housekeeping note, so pick the copy by its marker
    # rather than assuming the target has exactly one note.)
    copied = [n for n in chatter_service.get_chatter("deal", target)
              if n["message"].startswith(f"[Merged from deal #{source}]")]
    assert len(copied) == 1
    assert copied[0]["message"].endswith("Photo of the damaged pallet.")
    assert copied[0]["attachments"] == []

    # Exactly one attachment row exists in the whole install — nothing was duplicated.
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter_attachments")["n"] == 1

    # And it is still reachable through the archived source deal's original note.
    original = chatter_service.get_chatter("deal", source)
    assert [a["id"] for a in original[0]["attachments"]] == [attachment["id"]]
    assert svc.get_file(attachment["id"])["data"]
