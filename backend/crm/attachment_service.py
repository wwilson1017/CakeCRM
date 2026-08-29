"""CRM — file and image attachments on chatter notes (issue #57).

Ported from ``cake_os/backend/apps/chatter/`` and collapsed to CakeCRM's shape. The
blueprint is a four-app platform: a ``Surface`` registry over four chatter tables, a
mid-flight migration onto a shared "rail", a cross-app ``can_view`` authorization
registry, and GCS object storage behind V4 signed URLs. CakeCRM has ONE chatter table,
no per-object ACLs (CLAUDE.md: ownership is not access control) and no object store, so
all of that collapses to: one table, one FK, bytes in Postgres, and an authenticated
endpoint that hands the browser the bytes.

Bytes live in ``crm_chatter_attachments.data`` — see the migration header for the full
deploy-target argument the issue's gate asked for. The short version: Railway's app
filesystem is ephemeral except the required volume at ``/app/backend/data``, so files
WOULD have survived, but one store means one transaction, one ``pg_dump`` and a CRM
reset that cannot leak bytes.

All validation lives HERE rather than in the router, the same rule ``chatter_service``
follows, so any future caller shares one source of truth. Failures raise
``AttachmentError`` carrying a machine-readable ``code``; the router maps codes to
statuses and never matches on message text.

Two things are deliberately strict:

* **The stored MIME type is derived from magic bytes and the client's declared type is
  never consulted** — it is not even a parameter, so trusting it is unrepresentable.
  Only four image types and PDF keep a real type; everything else, SVG included, is
  stored and served as ``application/octet-stream``. These bytes come back from the
  app's own origin where a stored XSS reaches the session token.
* **The filename is normalized once, on the way in**, and stored NOT NULL. It is echoed
  into a ``Content-Disposition`` header, so a path-bearing, control-character-bearing or
  unbounded name is a response-header problem, not a cosmetic one.
"""

import hashlib
import logging
import re
import unicodedata

from core import thumbnails
from core.postgres import get_connection, pg_fetchall, pg_fetchone, row_to_dict

logger = logging.getLogger(__name__)

# Mirrors assistant/uploads.MAX_FILE_SIZE. Deliberately tighter than the blueprint's
# 20 MB: cake_os streams from GCS, while core/postgres.py has no streaming primitive, so
# every full-size read materializes in the single gunicorn worker.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_NOTE = 10

# Thumbnail geometry, from the blueprint gallery this pipeline is ported from.
THUMB_SIZE = 320
THUMB_MAX_BYTES = 28_000

# Enough for every signature below; the WebP check needs bytes 8..12.
SNIFF_HEAD_BYTES = 16
FALLBACK_MIME = "application/octet-stream"

# The only types stored under their real name AND offered a thumbnail. An ALLOW-list, not
# the blueprint's deny-list: a deny-list has to enumerate every dangerous type forever,
# and this repo has already been bitten twice by the SVG case on the branding logo
# (served-as-png never renders, and real SVG from an app origin is a stored-XSS surface).
INLINE_IMAGE_MIMES = ("image/jpeg", "image/png", "image/gif", "image/webp")

MAX_FILENAME_BYTES = 120
_DEFAULT_FILENAME = "attachment"
# Strip C0/C1 control characters and the two quoting characters that would break a
# Content-Disposition header's quoted-string form.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f"\\]')


class AttachmentError(ValueError):
    """A user-facing attachment problem, carrying a stable machine-readable code.

    The router switches on ``code`` — never on the message — so reworded copy can never
    silently change an HTTP status.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_filename(raw: str | None) -> str:
    """The name to store: never empty, never a path, never unbounded.

    Directory components are dropped rather than escaped (a filename is a leaf, and
    ``../`` in one has no legitimate meaning), control characters and quoting characters
    are removed, and the result is truncated on a UTF-8 BYTE boundary because that is
    what a header length actually costs. NFC normalization keeps a macOS upload and its
    Linux twin comparing equal.
    """
    name = unicodedata.normalize("NFC", raw or "")
    # Both separators, because a Windows client sends backslashes.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_FILENAME_CHARS.sub("", name).strip()
    if not name or name in (".", ".."):
        return _DEFAULT_FILENAME
    encoded = name.encode("utf-8")
    if len(encoded) > MAX_FILENAME_BYTES:
        # errors="ignore" drops a multi-byte character the cut lands inside, rather than
        # emitting a lone continuation byte.
        name = encoded[:MAX_FILENAME_BYTES].decode("utf-8", errors="ignore").strip()
    return name or _DEFAULT_FILENAME


def sniff_mime(head: bytes) -> str | None:
    """The MIME type these leading bytes actually are, or None if unrecognized.

    Pure and total. Deliberately takes no "declared type" argument: a caller cannot pass
    the client's claim in, so it cannot be trusted by accident.
    """
    if not head:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    # RIFF container; the form type at offset 8 is what makes it a WebP rather than a WAV.
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def resolve_stored_mime(head: bytes) -> str:
    """The type to STORE: a recognized allow-listed type, else octet-stream."""
    sniffed = sniff_mime(head)
    if sniffed in INLINE_IMAGE_MIMES or sniffed == "application/pdf":
        return sniffed
    return FALLBACK_MIME


def _build_thumbnail(data: bytes) -> tuple[bytes, str] | None:
    """Total wrapper around the pure pipeline: returns a pair or None, never raises.

    ``core.thumbnails.generate`` is pure and may raise; totality belongs here. Both a
    permanent refusal (a decompression-bomb ceiling, an unencodable source) and a
    transient one (no decode slot within the timeout) collapse to None, because this
    table records no attempt marker: the thumbnail is generated once, at upload, and a
    NULL is a legal terminal state the UI renders as a download-only chip. A read-path
    healer with a ``thumb_attempted_at`` stamp is the upgrade path if transient losses
    ever matter — see the migration header.
    """
    if not thumbnails.decode_slots.acquire(timeout=thumbnails.DECODE_TIMEOUT_SECONDS):
        logger.warning("chatter attachment thumbnail: no decode slot within timeout")
        return None
    try:
        return thumbnails.generate(
            data, thumb_size=THUMB_SIZE, thumb_max_bytes=THUMB_MAX_BYTES
        )
    except Exception:
        logger.warning("chatter attachment thumbnail generation failed", exc_info=True)
        return None
    finally:
        thumbnails.decode_slots.release()


# The metadata a caller may see. `data`, `thumb_data` and `sha256` are deliberately
# absent: the list embeds into every chatter response (and therefore into the assistant's
# crm_get_chatter payload), and returning multi-MB blobs there would be both a memory
# problem and a leak of bytes nobody asked for.
_META_COLUMNS = (
    "id, note_id, filename, mime_type, byte_size, "
    "(thumb_data IS NOT NULL) AS has_thumb, created_at, uploaded_by"
)


def create_attachment(
    note_id: int,
    *,
    data: bytes,
    filename: str | None,
    uploaded_by: int | None = None,
) -> dict:
    """Attach ``data`` to an existing note. Returns the new (or existing) metadata row.

    Ordering is load-bearing:

    1. Cheap local rejections, before anything is read from the database.
    2. An UNLOCKED preflight — note exists and is live, these exact bytes are not
       already attached, the note is not full. This is an optimization only: without it
       an authenticated caller could spend a decode slot and ~74 MB per request on a
       note id that does not exist, and a retry would re-decode 10 MB to discover a row
       it already has.
    3. Thumbnail generation, OUTSIDE any transaction — Pillow must never run while
       holding a row lock.
    4. ONE transaction that re-checks everything authoritatively, because the preflight
       can race. `SELECT ... FOR UPDATE` on the parent note serializes concurrent
       uploads to the same note, which is what makes the per-note cap a real limit
       rather than a suggestion.

    Idempotency is checked BEFORE the cap in the locked block as well: a retry of bytes
    that are already attached must succeed even when the note is full, or a lost response
    on the tenth attachment becomes permanently unrecoverable.
    """
    if not data:
        raise AttachmentError("file_empty", "That file is empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(
            "file_too_large",
            f"Attachments are limited to {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.",
        )
    if not isinstance(note_id, int) or note_id <= 0:
        raise AttachmentError("note_not_found", "That note no longer exists.")

    name = normalize_filename(filename)
    mime = resolve_stored_mime(data[:SNIFF_HEAD_BYTES])
    sha = hashlib.sha256(data).hexdigest()

    existing = _preflight(note_id, sha)
    if existing is not None:
        return existing

    thumb = _build_thumbnail(data) if mime in INLINE_IMAGE_MIMES else None
    thumb_data, thumb_mime = thumb if thumb else (None, None)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT archived FROM crm_chatter WHERE id = %s FOR UPDATE", (note_id,))
        row = cur.fetchone()
        if row is None:
            raise AttachmentError("note_not_found", "That note no longer exists.")
        if row[0]:
            raise AttachmentError("note_archived", "That note no longer accepts attachments.")

        # Idempotency before the cap, as above.
        cur.execute(
            f"SELECT {_META_COLUMNS} FROM crm_chatter_attachments "
            "WHERE note_id = %s AND sha256 = %s",
            (note_id, sha),
        )
        found = cur.fetchone()
        if found is not None:
            return row_to_dict(cur, found)

        cur.execute(
            "SELECT COUNT(*) FROM crm_chatter_attachments WHERE note_id = %s", (note_id,)
        )
        if cur.fetchone()[0] >= MAX_ATTACHMENTS_PER_NOTE:
            raise AttachmentError(
                "limit_exceeded",
                f"A note can hold {MAX_ATTACHMENTS_PER_NOTE} attachments.",
            )

        # RETURNING the metadata columns only — `RETURNING *` would re-read the blob we
        # just wrote purely to discard it.
        cur.execute(
            f"""INSERT INTO crm_chatter_attachments
                    (note_id, filename, mime_type, byte_size, sha256, data,
                     thumb_data, thumb_mime, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_META_COLUMNS}""",
            (note_id, name, mime, len(data), sha, data, thumb_data, thumb_mime, uploaded_by),
        )
        return row_to_dict(cur, cur.fetchone())


def _preflight(note_id: int, sha: str) -> dict | None:
    """Unlocked pre-checks. Returns an already-attached row, or None to continue.

    Raises the same errors the locked block would, just earlier and without spending a
    decode slot first. Every rejection here is re-made authoritatively under the lock —
    this exists to make abuse cheap to refuse, not to decide anything.
    """
    note = pg_fetchone("SELECT archived FROM crm_chatter WHERE id = %s", (note_id,))
    if note is None:
        raise AttachmentError("note_not_found", "That note no longer exists.")
    if note["archived"]:
        raise AttachmentError("note_archived", "That note no longer accepts attachments.")

    found = pg_fetchone(
        f"SELECT {_META_COLUMNS} FROM crm_chatter_attachments "
        "WHERE note_id = %s AND sha256 = %s",
        (note_id, sha),
    )
    if found is not None:
        return found

    row = pg_fetchone(
        "SELECT COUNT(*) AS n FROM crm_chatter_attachments WHERE note_id = %s", (note_id,)
    )
    if row and row["n"] >= MAX_ATTACHMENTS_PER_NOTE:
        raise AttachmentError(
            "limit_exceeded", f"A note can hold {MAX_ATTACHMENTS_PER_NOTE} attachments."
        )
    return None


def list_for_notes(note_ids: list[int]) -> dict[int, list[dict]]:
    """Attachment metadata for many notes at once, grouped by note id.

    Returns ``{}`` for an empty input WITHOUT touching the database — an empty thread is
    the common case, and a naive ``IN ()`` is a syntax error. Order is explicit
    (``created_at, id``) so a query-plan change can never silently reshuffle the tiles.
    """
    ids = [int(n) for n in note_ids if isinstance(n, int) and n > 0]
    if not ids:
        return {}
    rows = pg_fetchall(
        f"""SELECT {_META_COLUMNS} FROM crm_chatter_attachments
            WHERE note_id = ANY(%s)
            ORDER BY created_at, id""",
        (ids,),
    )
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["note_id"], []).append(row)
    return grouped


def get_meta(attachment_id: int) -> dict | None:
    """Everything the serving routes need EXCEPT the blobs — the 304 fast path.

    Carries ``sha256`` (the ETag) and ``thumb_mime``, so a revalidation request can be
    answered without ever reading ``data`` or ``thumb_data``.
    """
    return pg_fetchone(
        """SELECT id, note_id, filename, mime_type, byte_size, sha256, thumb_mime,
                  (thumb_data IS NOT NULL) AS has_thumb
           FROM crm_chatter_attachments WHERE id = %s""",
        (attachment_id,),
    )


def get_file(attachment_id: int) -> dict | None:
    """The original bytes plus the headers they are served with."""
    row = pg_fetchone(
        "SELECT data, mime_type, filename, sha256 FROM crm_chatter_attachments WHERE id = %s",
        (attachment_id,),
    )
    if row is None:
        return None
    # psycopg2 hands BYTEA back as a memoryview; Response wants bytes.
    row["data"] = bytes(row["data"])
    return row


def get_thumb(attachment_id: int) -> dict | None:
    """The thumbnail bytes, or None when the row is gone or has no thumbnail."""
    row = pg_fetchone(
        "SELECT thumb_data, thumb_mime, sha256 FROM crm_chatter_attachments WHERE id = %s",
        (attachment_id,),
    )
    if row is None or row["thumb_data"] is None:
        return None
    row["thumb_data"] = bytes(row["thumb_data"])
    return row


def delete_attachment(attachment_id: int) -> bool:
    """Hard-delete one attachment. False when it was already gone.

    Takes the PARENT note's lock first, the same parent-then-child order every other
    writer here uses, so it cannot deadlock against ``create_attachment`` and cannot
    interleave with one: without it a concurrent upload could count a row that is being
    deleted toward the cap, or dedupe onto it.

    A hard delete rather than the blueprint's void-with-reason: this repo already
    hard-deletes chatter when its entity is deleted, has no audit chain to record a
    reason into, and with the bytes in the row a delete is the only thing that actually
    reclaims the space. Any member may delete, matching #60's model — an uploader-only
    gate would contradict a CRM where any member can already edit any record.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT note_id FROM crm_chatter_attachments WHERE id = %s", (attachment_id,)
        )
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute("SELECT 1 FROM crm_chatter WHERE id = %s FOR UPDATE", (row[0],))
        cur.execute(
            "DELETE FROM crm_chatter_attachments WHERE id = %s RETURNING id", (attachment_id,)
        )
        return cur.fetchone() is not None
