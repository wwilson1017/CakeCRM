"""Shared image thumbnail pipeline — decode, shrink, encode (issue #57).

Ported from ``cake_os/backend/core/thumbnails.py``, adapted from base64-in/base64-out
to bytes-in/bytes-out because CakeCRM stores attachment originals as ``bytea`` rather
than base64 TEXT. The blueprint's ``crop_square`` mode is NOT ported: its one consumer
was an equipment card where the stored pixels were the rendered pixels, and here CSS
``object-fit: cover`` does the crop, so cropping server-side too would discard framing
the layout never asked to lose.

**Leaf module by design.** Stdlib only at import time; Pillow is imported LAZILY inside
the functions that need it, the same discipline ``assistant/uploads.py`` applies to
pdfplumber/python-docx/openpyxl — CI imports the app with no ``DATABASE_URL`` and a
minimal install must not break. Nothing here imports from a feature package.

**This module is PURE and MAY RAISE.** Totality belongs to the caller's wrapper — see
``crm.attachment_service._build_thumbnail``, which is contracted to return ``None``
rather than raise. The split is deliberate: ``None`` from ``generate`` means "these
bytes are permanently unusable" (refused by a ceiling, or unencodable within the cap),
while an exception means something else went wrong and the wrapper decides.

The operation ORDER below is load-bearing for memory safety, not stylistic: reject
oversized sources from the header, let the JPEG decoder downscale during decode,
transpose only when there is actually an orientation tag, shrink to thumbnail scale,
and only THEN flatten alpha. Flattening a 50 MP image to RGBA first allocates ~200 MB
per call, and catching ``MemoryError`` does not save a container from the OOM killer.
"""

import io
import logging
import threading

logger = logging.getLogger(__name__)


# --- Encoding ---------------------------------------------------------------

# Two REAL ladders. WebP is tried first and is ~30% smaller than JPEG at equal quality;
# JPEG exists for a Pillow build without libwebp. Every step must be a genuine quality
# drop: clamping the low end (e.g. max(q, 60)) makes the last encodes identical and
# discards thumbnails that would have fitted.
WEBP_QUALITY_STEPS = (72, 58, 45, 35)
JPEG_QUALITY_STEPS = (72, 58, 45)

THUMB_MIME_WEBP = "image/webp"
THUMB_MIME_JPEG = "image/jpeg"
# The only values the consumer's CHECK constraint allows and the only ones a frontend
# will render. Named here so the migration and the service agree with one source.
THUMB_MIMES = (THUMB_MIME_WEBP, THUMB_MIME_JPEG)


# --- Source ceilings --------------------------------------------------------

# Refuse to DECODE anything larger than this. Checked from the header, before any pixel
# buffer is allocated: a decompression bomb must be rejected, not caught. 50 MP passes
# every phone camera.
MAX_SOURCE_PIXELS = 50_000_000
MAX_SOURCE_EDGE = 12_000

# ...but those two limits are only safe for formats Pillow can DRAFT-decode. `draft()`
# is a documented no-op outside JPEG/MPO, and `ImageOps.exif_transpose` calls `load()`
# unconditionally — so a 50 MP PNG is decoded at FULL resolution (~200 MB) before
# `thumbnail()` ever shrinks it, which is exactly the allocation this module claims to
# avoid. Undraftable formats therefore get their own, much lower ceiling: 8 MP is a
# ~24-32 MB decode (RGB/RGBA), still far above any real photo. Keep these in step with
# each other — they ARE the memory-safety boundary.
DRAFT_CAPABLE_FORMATS = ("JPEG", "MPO")
MAX_UNDRAFTABLE_PIXELS = 8_000_000
MAX_UNDRAFTABLE_EDGE = 6_000


# --- The ONE process-wide decode budget -------------------------------------

# Peak memory is per-CALL, but the risk is CONCURRENT calls: thumbnail work runs in
# FastAPI's threadpool, so an 8 MP undraftable source at ~32 MB decoded is fine once and
# 1 GB+ forty times over. This bounds how many decodes can be in flight at once.
#
# **The per-slot cost is NOT just the decoded pixel buffer.** At this module's ceilings,
# one in-flight call can hold simultaneously:
#
#   the caller's source bytes (bounded upstream by the 10 MB upload cap)   ~10 MB
#   the decoded pixel buffer (MAX_UNDRAFTABLE_PIXELS at RGBA)              ~32 MB
#   exif_transpose's SECOND full-size image, when an orientation tag is    ~32 MB
#     present — it returns a new image, so both live at once, and it runs
#     BEFORE thumbnail() shrinks anything (see `generate` below)
#   post-shrink resize/encode intermediates                            negligible
#                                                                        ---------
#                                                                          ~74 MB
#
# So MAX_CONCURRENT_DECODES x that is **~150 MB** for the decode step. Treat that as an
# ESTIMATE OF THIS STEP, not as a process ceiling: it does not count the source bytes
# held by uploads *waiting* for a slot, non-image uploads (which never take one), or
# concurrent attachment downloads. The honest process bound is the per-request 10 MB cap
# times however many requests the threadpool is serving. Raising either the upload cap or
# the slot count multiplies this, so re-do the arithmetic before touching either.
#
# **This is the ONE Pillow-decode budget for the whole process** — a second semaphore
# elsewhere would silently double peak memory. The bound is PER PROCESS, which is the
# whole service today because the deploy runs `gunicorn --workers 1`. Re-do this
# arithmetic if the worker count ever rises.
MAX_CONCURRENT_DECODES = 2
DECODE_TIMEOUT_SECONDS = 5
decode_slots = threading.BoundedSemaphore(MAX_CONCURRENT_DECODES)


def _flatten(im, image_cls):
    """Composite any alpha onto white; leave RGB/L alone.

    Called only AFTER the image has been shrunk, so the RGBA copy and the canvas are a
    few hundred KB rather than a few hundred MB.
    """
    if im.mode in ("RGB", "L"):
        return im.convert("RGB")
    rgba = im.convert("RGBA")
    flat = image_cls.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    return flat


def _encode_ladder(im, fmt, steps, max_bytes, **save_kwargs):
    """First encoding at or under the byte cap, else None."""
    for quality in steps:
        buf = io.BytesIO()
        im.save(buf, fmt, quality=quality, **save_kwargs)
        out = buf.getvalue()
        if len(out) <= max_bytes:
            return out
    return None


def _encode(im, max_bytes):
    """``(bytes, mime)`` for the smallest acceptable encoding, or None."""
    try:
        from PIL import features  # lazy: optional dependency
        webp_available = features.check("webp")
    except Exception:
        webp_available = False

    if webp_available:
        try:
            data = _encode_ladder(im, "WEBP", WEBP_QUALITY_STEPS, max_bytes, method=4)
            if data:
                return data, THUMB_MIME_WEBP
            # A WebP ladder that ran but never fitted still falls through to JPEG. WebP
            # is *usually* smaller, but "strictly smaller" is not a property of either
            # codec — it depends on the image — so giving up here would drop thumbnails
            # that JPEG could have encoded within the cap.
        except Exception:
            # Availability is not the only way WebP fails; a build can advertise the
            # feature and still raise on save. Fall through rather than lose the
            # thumbnail entirely.
            logger.warning("WebP thumbnail encode failed; falling back to JPEG", exc_info=True)

    data = _encode_ladder(im, "JPEG", JPEG_QUALITY_STEPS, max_bytes, optimize=True)
    return (data, THUMB_MIME_JPEG) if data else None


def _exif_orientation(im):
    """The EXIF orientation tag, or None. Reads the already-parsed header only."""
    try:
        exif = im.getexif()
        return exif.get(0x0112) if exif else None
    except Exception:
        # A malformed EXIF block must not cost us the thumbnail; treat it as absent.
        return None


def generate(
    data: bytes,
    *,
    thumb_size: int,
    thumb_max_bytes: int,
    max_source_pixels: int = MAX_SOURCE_PIXELS,
    max_source_edge: int = MAX_SOURCE_EDGE,
    max_undraftable_pixels: int = MAX_UNDRAFTABLE_PIXELS,
    max_undraftable_edge: int = MAX_UNDRAFTABLE_EDGE,
) -> tuple[bytes, str] | None:
    """Original bytes in, ``(thumb_bytes, mime)`` out, or ``None``.

    ``None`` means the source was REFUSED (a ceiling rejected it from the header) or
    could not be encoded within the cap — a permanent property of these bytes, so a
    caller may record it as settled. Any other problem raises; the caller's wrapper
    decides what that means.

    The result preserves the source aspect ratio with ``thumb_size`` as the longest
    edge. CSS ``object-fit: cover`` does the crop into whatever slot the layout gives
    it, so cropping to a square here as well would discard framing twice.

    Does NOT acquire ``decode_slots`` — the caller's wrapper holds it, so a wrapper
    handling several images can take the budget once rather than per image.
    """
    from PIL import Image, ImageOps  # lazy: optional dependency

    im = Image.open(io.BytesIO(data))          # header parse only; no pixels yet
    width, height = im.size
    # `im.format` is known from the header, so the ceiling is chosen before a single
    # pixel is allocated.
    draftable = im.format in DRAFT_CAPABLE_FORMATS
    max_pixels = max_source_pixels if draftable else max_undraftable_pixels
    max_edge = max_source_edge if draftable else max_undraftable_edge
    if (width <= 0 or height <= 0
            or width * height > max_pixels
            or max(width, height) > max_edge):
        return None

    # JPEG-only DCT-scaled decode: a 48 MP JPEG decodes at ~1/8 scale and never allocates
    # full resolution. A no-op for other formats — which is precisely why they are capped
    # lower above.
    im.draft("RGB", (thumb_size * 2, thumb_size * 2))

    # Only transpose when there is actually an orientation to apply. exif_transpose()
    # calls load() and allocates a SECOND full-size image unconditionally, so calling it
    # blindly doubles peak memory for the overwhelmingly common case of a photo with no
    # orientation tag (and every PNG).
    if _exif_orientation(im) not in (None, 1):
        im = ImageOps.exif_transpose(im)       # phone photos carry rotation in EXIF

    im.thumbnail((thumb_size * 4, thumb_size * 4), Image.Resampling.LANCZOS)
    im = _flatten(im, Image)
    # `thumbnail` is in-place, bounds the LONGEST edge, and never upscales — so a source
    # already smaller than the target keeps its own size rather than being blown up into
    # a blurry, larger payload.
    im.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)

    return _encode(im, thumb_max_bytes)
