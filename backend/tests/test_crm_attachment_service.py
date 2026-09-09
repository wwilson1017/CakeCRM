"""crm.attachment_service — hermetic (issue #57).

Split of responsibility, stated so it does not drift: THESE tests pin the pure rules
(filename normalization, magic-byte sniffing) and the SHAPE of the database work (which
statements run, in which order, on which cursor, and which error code comes out). They
do NOT pin what Postgres actually does with that SQL — the FK cascade, the TRUNCATE
sweep and the unique index live in test_crm_attachments_integration.py against a real
database, because a FakeConn agrees with whatever SQL you hand it.
"""

import pytest

from crm import attachment_service as svc

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
GIF = b"GIF89a" + b"\x00" * 10
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 4
PDF = b"%PDF-1.7" + b"\x00" * 8

# The metadata columns every row-returning statement selects, in order.
META_COLS = ["id", "note_id", "filename", "mime_type", "byte_size",
             "has_thumb", "created_at", "uploaded_by"]


# ── normalize_filename ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("photo.png", "photo.png"),
    ("  photo.png  ", "photo.png"),
    # Directory components are dropped, not escaped: a filename is a leaf.
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\rep\\shot.png", "shot.png"),
    ("a/b/c/deep.pdf", "deep.pdf"),
    # These two would break the quoted-string form of a Content-Disposition header.
    ('quo"te.png', "quote.png"),
    ("back\\slash.png", "slash.png"),
    ("line\nbreak.png", "linebreak.png"),
    ("tab\tsep.png", "tabsep.png"),
    # A NULL name is the case that used to crash the header encoder outright.
    (None, "attachment"),
    ("", "attachment"),
    ("   ", "attachment"),
    (".", "attachment"),
    ("..", "attachment"),
    ("/", "attachment"),
])
def test_normalize_filename(raw, expected):
    assert svc.normalize_filename(raw) == expected


def test_normalize_filename_caps_the_byte_length():
    out = svc.normalize_filename("x" * 500 + ".png")
    assert len(out.encode("utf-8")) <= svc.MAX_FILENAME_BYTES


def test_normalize_filename_truncates_on_a_character_boundary():
    """Cutting mid-character would emit a lone continuation byte, which is not valid
    UTF-8 and would break the RFC 5987 header the name is encoded into."""
    out = svc.normalize_filename("é" * 200)          # 2 bytes each
    assert len(out.encode("utf-8")) <= svc.MAX_FILENAME_BYTES
    assert out.encode("utf-8").decode("utf-8") == out
    assert set(out) == {"é"}


def test_normalize_filename_never_returns_empty():
    for raw in ("\x00\x01\x02", '"""', "\\\\\\", "\n\n"):
        assert svc.normalize_filename(raw) == "attachment"


# ── MIME resolution ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("data,expected", [
    (JPEG, "image/jpeg"),
    (PNG, "image/png"),
    (GIF, "image/gif"),
    (b"GIF87a" + b"\x00" * 10, "image/gif"),
    (WEBP, "image/webp"),
    (PDF, "application/pdf"),
])
def test_sniff_recognizes_the_allow_listed_types(data, expected):
    assert svc.sniff_mime(data[:svc.SNIFF_HEAD_BYTES]) == expected


@pytest.mark.parametrize("data", [
    b"<svg xmlns='http://www.w3.org/2000/svg'>",   # the stored-XSS case, twice-learned
    b"<!DOCTYPE html><html>",
    b"<?xml version='1.0'?>",
    b"GIF",                                        # truncated magic
    b"RIFF" + b"\x00" * 4 + b"WAVE",               # RIFF, but not a WebP
    b"",
    b"\x00\x01\x02\x03",
])
def test_unrecognized_bytes_store_as_inert_octet_stream(data):
    assert svc.sniff_mime(data[:svc.SNIFF_HEAD_BYTES]) is None
    assert svc.resolve_stored_mime(data[:svc.SNIFF_HEAD_BYTES]) == svc.FALLBACK_MIME


def test_the_client_declared_type_is_not_even_a_parameter():
    """The strongest form of 'never trust the declared type': a caller cannot pass it in,
    so trusting it by accident is unrepresentable rather than merely discouraged."""
    import inspect

    for fn in (svc.sniff_mime, svc.resolve_stored_mime):
        params = list(inspect.signature(fn).parameters)
        assert params == ["head"], f"{fn.__name__}{tuple(params)}"


def test_svg_is_not_in_the_inline_image_allow_list():
    assert "image/svg+xml" not in svc.INLINE_IMAGE_MIMES


# ── create_attachment: local rejections, before any database work ─────────────

def test_empty_file_rejected_without_touching_the_database(monkeypatch):
    called = []
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: called.append(a))
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(1, data=b"", filename="x.png")
    assert e.value.code == "file_empty"
    assert not called


def test_oversize_file_rejected_without_touching_the_database(monkeypatch):
    called = []
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: called.append(a))
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(1, data=b"x" * (svc.MAX_ATTACHMENT_BYTES + 1), filename="x.png")
    assert e.value.code == "file_too_large"
    assert not called


def test_nonpositive_note_id_is_a_not_found(monkeypatch):
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: pytest.fail("no DB call expected"))
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(0, data=PNG, filename="x.png")
    assert e.value.code == "note_not_found"


# ── create_attachment: the preflight ──────────────────────────────────────────

def _preflight_queue(monkeypatch, rows):
    """Queue pg_fetchone results for the unlocked preflight, in call order."""
    queue = list(rows)
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: queue.pop(0) if queue else None)
    return queue


def test_preflight_refuses_a_missing_note_before_generating_a_thumbnail(monkeypatch):
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: pytest.fail("decoded too early"))
    _preflight_queue(monkeypatch, [None])
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(7, data=PNG, filename="x.png")
    assert e.value.code == "note_not_found"


def test_preflight_refuses_an_archived_note_before_generating_a_thumbnail(monkeypatch):
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: pytest.fail("decoded too early"))
    _preflight_queue(monkeypatch, [{"archived": 1}])
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(7, data=PNG, filename="x.png")
    assert e.value.code == "note_archived"


def test_preflight_refuses_a_full_note_before_generating_a_thumbnail(monkeypatch):
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: pytest.fail("decoded too early"))
    _preflight_queue(monkeypatch, [
        {"archived": 0}, None, {"n": svc.MAX_ATTACHMENTS_PER_NOTE},
    ])
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(7, data=PNG, filename="x.png")
    assert e.value.code == "limit_exceeded"


def test_a_retry_of_already_attached_bytes_short_circuits_without_decoding(monkeypatch):
    """Idempotency without re-paying for the thumbnail. A client retry after a lost
    response re-sends the same 10 MB, and decoding it again buys nothing."""
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: pytest.fail("decoded on a retry"))
    existing = {"id": 4, "note_id": 7, "filename": "x.png"}
    _preflight_queue(monkeypatch, [{"archived": 0}, existing])
    assert svc.create_attachment(7, data=PNG, filename="x.png") == existing


# ── create_attachment: the locked transaction ─────────────────────────────────

def _ready_preflight(monkeypatch):
    """Preflight that finds a live, non-full note with no matching row."""
    _preflight_queue(monkeypatch, [{"archived": 0}, None, {"n": 0}])


def test_happy_path_locks_the_note_then_inserts_in_one_transaction(monkeypatch, fake_conn):
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: (b"thumbbytes", "image/webp"))
    conn = fake_conn(
        monkeypatch, svc,
        fetchone_results=[(0,), None, (0,), (11, 7, "x.png", "image/png", 16, True, "t", 1)],
        description=META_COLS,
    )
    out = svc.create_attachment(7, data=PNG, filename="x.png", uploaded_by=1)

    stmts = [s for s, _ in conn.executed]
    assert stmts[0] == "SELECT archived FROM crm_chatter WHERE id = %s FOR UPDATE"
    assert "INSERT INTO crm_chatter_attachments" in stmts[-1]
    # ONE transaction, ONE cursor — the lock and the insert cannot be split apart.
    assert conn.entries == 1
    assert len(set(conn.executed_by)) == 1
    assert out["id"] == 11


def test_the_insert_returns_metadata_columns_not_the_blob(monkeypatch, fake_conn):
    """`RETURNING *` would make Postgres read back the multi-MB row we just wrote purely
    so the service could discard it."""
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: None)
    conn = fake_conn(
        monkeypatch, svc,
        fetchone_results=[(0,), None, (0,), (11, 7, "x.png", "image/png", 16, False, "t", None)],
        description=META_COLS,
    )
    out = svc.create_attachment(7, data=PNG, filename="x.png")
    insert = next(s for s, _ in conn.executed if "INSERT INTO" in s)
    assert "RETURNING *" not in insert
    assert "data" not in out and "thumb_data" not in out and "sha256" not in out


def test_the_stored_row_carries_the_sniffed_type_the_size_and_the_digest(monkeypatch, fake_conn):
    import hashlib

    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: None)
    conn = fake_conn(
        monkeypatch, svc,
        fetchone_results=[(0,), None, (0,), (11, 7, "x.png", "image/png", 16, False, "t", None)],
        description=META_COLS,
    )
    svc.create_attachment(7, data=PNG, filename="../x.png", uploaded_by=3)
    params = next(p for s, p in conn.executed if "INSERT INTO" in s)
    note_id, filename, mime, size, sha, data, thumb_data, thumb_mime, uploader = params
    assert (note_id, filename, mime, size) == (7, "x.png", "image/png", len(PNG))
    assert sha == hashlib.sha256(PNG).hexdigest()
    assert data == PNG and (thumb_data, thumb_mime) == (None, None)
    assert uploader == 3


def test_a_note_archived_between_preflight_and_the_lock_is_still_refused(monkeypatch, fake_conn):
    """The preflight is an optimization; the locked block is the authority. Without this
    re-check, the preflight's own race would be the bug."""
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: None)
    conn = fake_conn(monkeypatch, svc, fetchone_results=[(1,)], description=META_COLS)
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(7, data=PNG, filename="x.png")
    assert e.value.code == "note_archived"
    assert not any("INSERT INTO" in s for s, _ in conn.executed)


def test_a_note_deleted_between_preflight_and_the_lock_is_still_refused(monkeypatch, fake_conn):
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: None)
    conn = fake_conn(monkeypatch, svc, fetchone_results=[None], description=META_COLS)
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(7, data=PNG, filename="x.png")
    assert e.value.code == "note_not_found"
    assert not any("INSERT INTO" in s for s, _ in conn.executed)


def test_under_the_lock_idempotency_is_checked_before_the_cap(monkeypatch, fake_conn):
    """Order is the whole point: a lost response on the TENTH attachment must still be
    retryable. Checking the cap first would make that row permanently unrecoverable."""
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: None)
    conn = fake_conn(
        monkeypatch, svc,
        fetchone_results=[(0,), (11, 7, "x.png", "image/png", 16, False, "t", None)],
        description=META_COLS,
    )
    out = svc.create_attachment(7, data=PNG, filename="x.png")
    stmts = [s for s, _ in conn.executed]
    assert "sha256 = %s" in stmts[1]
    # The COUNT never ran, so a full note cannot refuse a retry of bytes it already holds.
    assert not any("COUNT(*)" in s for s in stmts)
    assert not any("INSERT INTO" in s for s in stmts)
    assert out["id"] == 11


def test_the_cap_is_enforced_under_the_lock(monkeypatch, fake_conn):
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: None)
    conn = fake_conn(
        monkeypatch, svc,
        fetchone_results=[(0,), None, (svc.MAX_ATTACHMENTS_PER_NOTE,)],
        description=META_COLS,
    )
    with pytest.raises(svc.AttachmentError) as e:
        svc.create_attachment(7, data=PNG, filename="x.png")
    assert e.value.code == "limit_exceeded"
    assert not any("INSERT INTO" in s for s, _ in conn.executed)


# ── Thumbnail generation ──────────────────────────────────────────────────────

def test_only_allow_listed_image_types_are_thumbnailed(monkeypatch, fake_conn):
    seen = []
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: seen.append(d) or None)
    for data in (PDF, b"\x00\x01\x02\x03" + b"\x00" * 12):
        _ready_preflight(monkeypatch)
        fake_conn(
            monkeypatch, svc,
            fetchone_results=[(0,), None, (0,), (1, 7, "f", "x", 16, False, "t", None)],
            description=META_COLS,
        )
        svc.create_attachment(7, data=data, filename="f")
    assert seen == [], "a non-image must never reach the decoder"


def test_a_failed_thumbnail_still_stores_the_attachment(monkeypatch, fake_conn):
    """NULL thumb is a legal terminal state — the UI renders a download-only chip. Losing
    the whole attachment because the decoder refused it would be the wrong trade."""
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(svc, "_build_thumbnail", lambda d: None)
    conn = fake_conn(
        monkeypatch, svc,
        fetchone_results=[(0,), None, (0,), (11, 7, "x.png", "image/png", 16, False, "t", None)],
        description=META_COLS,
    )
    out = svc.create_attachment(7, data=PNG, filename="x.png")
    params = next(p for s, p in conn.executed if "INSERT INTO" in s)
    assert params[6] is None and params[7] is None
    assert out["has_thumb"] is False


def test_build_thumbnail_never_raises_when_the_pipeline_does(monkeypatch):
    """Totality lives in the wrapper: `generate` is pure and may raise, and an upload must
    not 500 because one image was undecodable."""
    def boom(*a, **k):
        raise ValueError("undecodable")

    monkeypatch.setattr(svc.thumbnails, "generate", boom)
    assert svc._build_thumbnail(PNG) is None


def test_build_thumbnail_releases_its_decode_slot_on_failure(monkeypatch):
    """A leaked slot is worse than a lost thumbnail: it permanently shrinks the ONE
    process-wide budget until a restart."""
    def boom(*a, **k):
        raise ValueError("undecodable")

    monkeypatch.setattr(svc.thumbnails, "generate", boom)
    for _ in range(svc.thumbnails.MAX_CONCURRENT_DECODES + 2):
        assert svc._build_thumbnail(PNG) is None
    # Every slot is free again — acquiring all of them must succeed immediately.
    acquired = [svc.thumbnails.decode_slots.acquire(blocking=False)
                for _ in range(svc.thumbnails.MAX_CONCURRENT_DECODES)]
    for got in acquired:
        if got:
            svc.thumbnails.decode_slots.release()
    assert all(acquired)


def test_no_decode_slot_degrades_to_no_thumbnail(monkeypatch):
    monkeypatch.setattr(svc.thumbnails.decode_slots, "acquire", lambda **k: False)
    monkeypatch.setattr(svc.thumbnails, "generate", lambda *a, **k: pytest.fail("no slot held"))
    assert svc._build_thumbnail(PNG) is None


# ── Reads ─────────────────────────────────────────────────────────────────────

def test_list_for_notes_with_no_ids_never_touches_the_database(monkeypatch):
    monkeypatch.setattr(svc, "pg_fetchall", lambda *a, **k: pytest.fail("queried for nothing"))
    assert svc.list_for_notes([]) == {}
    # A list that reduces to nothing usable is the same case — `IN ()` is a syntax error.
    assert svc.list_for_notes([0, -3]) == {}


def test_list_for_notes_groups_by_note_and_orders_explicitly(monkeypatch):
    seen = {}

    def fake(sql, params=()):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = params
        return [
            {"id": 1, "note_id": 5}, {"id": 2, "note_id": 5}, {"id": 3, "note_id": 9},
        ]

    monkeypatch.setattr(svc, "pg_fetchall", fake)
    out = svc.list_for_notes([5, 9])
    assert [a["id"] for a in out[5]] == [1, 2]
    assert [a["id"] for a in out[9]] == [3]
    assert "ORDER BY created_at, id" in seen["sql"]
    assert seen["params"] == ([5, 9],)


def test_list_for_notes_never_selects_the_blobs(monkeypatch):
    """This embeds into every chatter response, including the assistant's crm_get_chatter
    payload. Selecting `data` here would put megabytes into a list response."""
    seen = {}
    def capture(sql, params=()):
        seen["sql"] = sql
        return []

    monkeypatch.setattr(svc, "pg_fetchall", capture)
    svc.list_for_notes([1])
    sql = seen["sql"]
    assert "thumb_data IS NOT NULL" in sql        # the boolean, not the bytes
    assert "data," not in sql.replace("thumb_data", "")


def test_get_meta_reads_no_blob_so_a_304_can_be_answered_cheaply(monkeypatch):
    seen = {}
    def capture(sql, params=()):
        seen["sql"] = sql
        return None

    monkeypatch.setattr(svc, "pg_fetchone", capture)
    svc.get_meta(3)
    sql = " ".join(seen["sql"].split())
    assert "sha256" in sql and "thumb_mime" in sql
    assert "thumb_data IS NOT NULL" in sql
    # Neither blob column is selected — that is the whole point of the fast path.
    assert "SELECT id, note_id, filename, mime_type, byte_size, sha256, thumb_mime," in sql


def test_get_file_converts_the_memoryview_to_bytes(monkeypatch):
    """psycopg2 hands BYTEA back as a memoryview; Response wants bytes."""
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: {
        "data": memoryview(b"abc"), "mime_type": "image/png", "filename": "x", "sha256": "s",
    })
    assert svc.get_file(1)["data"] == b"abc"


def test_get_thumb_reports_a_missing_thumbnail_as_absent(monkeypatch):
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: {
        "thumb_data": None, "thumb_mime": None, "sha256": "s",
    })
    assert svc.get_thumb(1) is None


def test_get_thumb_converts_the_memoryview_to_bytes(monkeypatch):
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: {
        "thumb_data": memoryview(b"tt"), "thumb_mime": "image/webp", "sha256": "s",
    })
    assert svc.get_thumb(1)["thumb_data"] == b"tt"


def test_reads_return_none_for_a_missing_row(monkeypatch):
    monkeypatch.setattr(svc, "pg_fetchone", lambda *a, **k: None)
    assert svc.get_file(1) is None and svc.get_thumb(1) is None and svc.get_meta(1) is None


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_locks_the_parent_note_before_deleting(monkeypatch, fake_conn):
    """Parent-then-child, the same order create_attachment takes — so the two cannot
    deadlock, and a delete cannot interleave with an upload's cap count."""
    conn = fake_conn(monkeypatch, svc, fetchone_results=[(7,), (1,), (3,)])
    assert svc.delete_attachment(3) is True
    stmts = [s for s, _ in conn.executed]
    assert stmts[0] == "SELECT note_id FROM crm_chatter_attachments WHERE id = %s"
    assert stmts[1] == "SELECT 1 FROM crm_chatter WHERE id = %s FOR UPDATE"
    assert "DELETE FROM crm_chatter_attachments" in stmts[2]
    # The lock is worthless in a different transaction from the delete it guards.
    assert conn.entries == 1
    assert len(set(conn.executed_by)) == 1
    assert next(p for s, p in conn.executed if "FOR UPDATE" in s) == (7,)


def test_delete_of_a_missing_row_takes_no_lock_and_reports_false(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, svc, fetchone_results=[None])
    assert svc.delete_attachment(3) is False
    assert not any("FOR UPDATE" in s for s, _ in conn.executed)
    assert not any("DELETE" in s for s, _ in conn.executed)


def test_delete_reports_false_when_the_row_vanished_under_the_lock(monkeypatch, fake_conn):
    # Two fetches, not three: the FOR UPDATE's result is never read, so the queue is
    # (note_id lookup, DELETE ... RETURNING).
    fake_conn(monkeypatch, svc, fetchone_results=[(7,), None])
    assert svc.delete_attachment(3) is False


# ── Cross-cutting invariants ──────────────────────────────────────────────────

def test_every_error_code_is_one_the_router_maps():
    """Codes and statuses are two halves of one contract: `_ATTACHMENT_STATUS[e.code]`
    raises KeyError for an unmapped code, which escapes the router's except block as a 500.

    The code set is read from the SOURCE — every `AttachmentError("...")` literal in the
    service — not hand-typed here. A hand-typed set is exactly as stale as the router's
    table, so it would agree with it forever and never catch the drift it names.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(svc))
    raised = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AttachmentError"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert raised, "found no AttachmentError raise sites — the scan is broken, not the code"

    from crm.router import _ATTACHMENT_STATUS

    assert raised <= set(_ATTACHMENT_STATUS), (
        f"unmapped error code(s) would 500: {sorted(raised - set(_ATTACHMENT_STATUS))}"
    )


def test_the_upload_cap_matches_the_assistant_upload_precedent():
    from assistant import uploads

    assert svc.MAX_ATTACHMENT_BYTES == uploads.MAX_FILE_SIZE


def test_the_thumb_mimes_the_service_allows_are_the_ones_the_encoder_emits():
    """The migration's CHECK hard-codes the same two values; if the encoder ever gained a
    third, an insert would fail at runtime rather than here."""
    from core import thumbnails

    assert thumbnails.THUMB_MIMES == ("image/webp", "image/jpeg")
