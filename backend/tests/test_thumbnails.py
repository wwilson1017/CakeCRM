"""core.thumbnails — the pure decode/shrink/encode pipeline (issue #57).

Runs against the REAL Pillow, on images generated in-test. Mocking Pillow here would
test nothing: every claim this module makes is about what Pillow actually does with a
particular header (draft-decoding, EXIF transposition, the memory ceilings), and a mock
would agree with whatever the test asserted.

The decompression-bomb tests are the load-bearing ones. They must never build a real
oversized image — allocating a 20 000 x 20 000 buffer to prove we refuse to allocate it
would be self-defeating — so they craft the HEADER only and assert the refusal comes
from the size fields, before any pixel buffer exists.
"""

import io
import struct
import zlib

import pytest

from core import thumbnails


def _png(width: int, height: int, mode: str = "RGB", color=(200, 40, 40)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def _jpeg(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    # A gradient rather than a flat fill: a solid colour compresses to almost nothing,
    # which would make the byte-cap ladder trivially succeed at its first step and hide a
    # broken ladder.
    im = Image.new("RGB", (width, height))
    im.putdata([((x * 7) % 256, (y * 11) % 256, (x + y) % 256)
                for y in range(height) for x in range(width)])
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _png_header_claiming(width: int, height: int) -> bytes:
    """A real, tiny PNG whose IHDR has been rewritten to DECLARE a huge size.

    Pillow answers `.size` from the IHDR, which is exactly the point: the ceiling check
    must fire from the header, before a pixel buffer exists. Building a genuine image of
    this size in CI is the allocation the module exists to prevent, so the file stays
    tiny and only its header lies. The IHDR CRC is recomputed because Pillow validates it
    on critical chunks and would otherwise reject the file for the wrong reason — the
    test would still "pass" while proving nothing.
    """
    data = bytearray(_png(4, 4))
    # 8-byte signature, then the IHDR chunk: 4-byte length, 4-byte type, then the payload
    # (width, height, ...). So width is at 16 and height at 20.
    struct.pack_into(">II", data, 16, width, height)
    ihdr = bytes(data[12:29])                      # type + 13-byte payload
    struct.pack_into(">I", data, 29, zlib.crc32(ihdr) & 0xFFFFFFFF)
    return bytes(data)


def _jpeg_header_claiming(width: int, height: int) -> bytes:
    """A real, tiny JPEG whose SOF0 frame header DECLARES a huge size.

    The PNG twin above only ever exercises the UNDRAFTABLE branch, which left the
    module's main real-world path — a 12-48 MP photo straight off a phone — with no test
    distinguishing "classified draftable, higher ceiling applied" from "misclassified,
    wrong ceiling applied". Since `draft()` is what makes the higher ceiling safe, getting
    that classification backwards is precisely the bug worth catching.
    """
    data = bytearray(_jpeg(8, 8))
    # Walk the marker segments to SOF0 (0xFFC0): its payload is
    # [precision:1][height:2][width:2], so height sits 1 byte past the segment length.
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xC0:
            struct.pack_into(">HH", data, i + 5, height, width)
            return bytes(data)
        seg_len = struct.unpack_from(">H", data, i + 2)[0]
        i += 2 + seg_len
    raise AssertionError("no SOF0 marker in the generated JPEG")


# ── Happy path ────────────────────────────────────────────────────────────────

def test_generate_returns_bytes_and_an_allowed_mime():
    out = thumbnails.generate(_jpeg(640, 480), thumb_size=320, thumb_max_bytes=28_000)
    assert out is not None
    data, mime = out
    assert isinstance(data, bytes) and data
    assert mime in thumbnails.THUMB_MIMES
    assert len(data) <= 28_000


def test_generate_bounds_the_longest_edge_and_keeps_aspect_ratio():
    from PIL import Image

    out = thumbnails.generate(_jpeg(800, 400), thumb_size=320, thumb_max_bytes=28_000)
    assert out is not None
    im = Image.open(io.BytesIO(out[0]))
    assert max(im.size) == 320
    # 2:1 in, 2:1 out — the square crop the blueprint offered was deliberately not ported,
    # because CSS object-fit does the cropping into whatever slot the layout gives it.
    assert im.size == (320, 160)


def test_generate_never_upscales_a_small_source():
    from PIL import Image

    out = thumbnails.generate(_png(64, 48), thumb_size=320, thumb_max_bytes=28_000)
    assert out is not None
    assert Image.open(io.BytesIO(out[0])).size == (64, 48)


def test_generate_flattens_alpha_onto_white():
    from PIL import Image

    out = thumbnails.generate(
        _png(64, 48, mode="RGBA", color=(255, 0, 0, 0)), thumb_size=320, thumb_max_bytes=28_000
    )
    assert out is not None
    im = Image.open(io.BytesIO(out[0]))
    # Both output encodings are opaque; a fully transparent source composites to white.
    assert im.mode in ("RGB", "L")


# ── Decompression-bomb defence (header-only; nothing large is ever allocated) ──

def test_refuses_an_undraftable_source_over_the_pixel_ceiling():
    # 4000 x 4000 = 16 MP: over MAX_UNDRAFTABLE_PIXELS (8 MP), under both edge ceilings,
    # so this isolates the PIXEL check rather than the edge check.
    assert thumbnails.generate(
        _png_header_claiming(4000, 4000), thumb_size=320, thumb_max_bytes=28_000
    ) is None


def test_refuses_an_undraftable_source_over_the_edge_ceiling():
    # 7000 x 1000 = 7 MP: UNDER the pixel ceiling, over MAX_UNDRAFTABLE_EDGE (6000). A
    # pixel-count check alone would let this through, and one enormous dimension is its
    # own allocation problem.
    assert thumbnails.generate(
        _png_header_claiming(7000, 1000), thumb_size=320, thumb_max_bytes=28_000
    ) is None


def test_the_undraftable_ceiling_is_lower_than_the_draftable_one():
    """The two-tier ceiling is the module's whole memory-safety claim, so pin it.

    A 4000 x 4000 PNG is refused while the SAME dimensions would be inside the draftable
    ceiling — because `draft()` is a no-op outside JPEG/MPO, so a PNG that size really is
    decoded at full resolution.
    """
    assert thumbnails.MAX_UNDRAFTABLE_PIXELS < thumbnails.MAX_SOURCE_PIXELS
    assert thumbnails.MAX_UNDRAFTABLE_EDGE < thumbnails.MAX_SOURCE_EDGE
    dims = (4000, 4000)
    assert dims[0] * dims[1] > thumbnails.MAX_UNDRAFTABLE_PIXELS
    assert dims[0] * dims[1] < thumbnails.MAX_SOURCE_PIXELS


def test_a_real_image_just_under_the_undraftable_ceiling_is_accepted():
    """The refusal must be a CEILING, not a blanket rejection of every PNG.

    Uses an injected low ceiling rather than a real 8 MP allocation: the boundary logic is
    what is under test, and building a genuine 8 MP RGBA image in CI to prove it would
    allocate the ~32 MB the module is designed to avoid.
    """
    out = thumbnails.generate(
        _png(200, 100), thumb_size=64, thumb_max_bytes=28_000,
        max_undraftable_pixels=20_001, max_undraftable_edge=201,
    )
    assert out is not None
    # One pixel over the same injected ceiling is refused — same source shape, opposite
    # verdict, so the test cannot pass by accident.
    assert thumbnails.generate(
        _png(200, 100), thumb_size=64, thumb_max_bytes=28_000,
        max_undraftable_pixels=19_999, max_undraftable_edge=201,
    ) is None


# ── Encoding ladders ──────────────────────────────────────────────────────────

def test_falls_back_to_jpeg_when_webp_is_unavailable(monkeypatch):
    from PIL import features

    monkeypatch.setattr(features, "check", lambda name: False if name == "webp" else True)
    out = thumbnails.generate(_jpeg(640, 480), thumb_size=320, thumb_max_bytes=28_000)
    assert out is not None
    assert out[1] == thumbnails.THUMB_MIME_JPEG


def test_falls_back_to_jpeg_when_a_webp_save_raises(monkeypatch):
    """A Pillow build can advertise webp and still raise on save. Losing the thumbnail
    entirely for that would be worse than a slightly larger JPEG."""
    from PIL import Image

    real_save = Image.Image.save

    def flaky_save(self, fp, fmt=None, **kw):
        if fmt == "WEBP":
            raise OSError("encoder error")
        return real_save(self, fp, fmt, **kw)

    monkeypatch.setattr(Image.Image, "save", flaky_save)
    out = thumbnails.generate(_jpeg(640, 480), thumb_size=320, thumb_max_bytes=28_000)
    assert out is not None
    assert out[1] == thumbnails.THUMB_MIME_JPEG


def test_returns_none_when_no_quality_step_fits_the_cap():
    """An unencodable-within-cap source is a PERMANENT property of those bytes, which is
    what lets the caller settle it rather than retry forever."""
    assert thumbnails.generate(_jpeg(640, 480), thumb_size=320, thumb_max_bytes=10) is None


def test_every_quality_step_is_a_real_drop():
    """Clamping the low end would make the last encodes identical and silently discard
    thumbnails a lower quality would have fitted."""
    for steps in (thumbnails.WEBP_QUALITY_STEPS, thumbnails.JPEG_QUALITY_STEPS):
        assert list(steps) == sorted(steps, reverse=True)
        assert len(set(steps)) == len(steps)


def test_generate_raises_on_undecodable_bytes():
    """PURE and MAY RAISE: `None` means 'refused', an exception means 'something else'.
    Collapsing the two would cost the caller the distinction its wrapper is built on."""
    with pytest.raises(Exception):
        thumbnails.generate(b"not an image at all", thumb_size=320, thumb_max_bytes=28_000)


def test_one_process_wide_decode_budget():
    assert thumbnails.decode_slots._initial_value == thumbnails.MAX_CONCURRENT_DECODES


# ── The draftable (JPEG) branch — the main real-world path ────────────────────

def test_a_jpeg_over_the_undraftable_ceiling_is_still_accepted():
    """The two-tier ceiling has to actually be two tiers.

    9 MP is OVER MAX_UNDRAFTABLE_PIXELS (8 MP) and well under MAX_SOURCE_PIXELS (50 MP).
    A JPEG that size is a perfectly ordinary phone photo and must NOT be refused — if
    JPEG ever stopped being classified draftable, this is what would catch it, where every
    other test in this file would stay green.
    """
    assert 3000 * 3000 > thumbnails.MAX_UNDRAFTABLE_PIXELS
    assert 3000 * 3000 < thumbnails.MAX_SOURCE_PIXELS
    out = thumbnails.generate(_jpeg_header_claiming(3000, 3000),
                              thumb_size=320, thumb_max_bytes=28_000)
    assert out is not None
    assert out[1] in thumbnails.THUMB_MIMES


def test_a_jpeg_over_the_draftable_ceiling_is_refused():
    # 60 MP is over MAX_SOURCE_PIXELS even for a draftable format.
    assert thumbnails.generate(
        _jpeg_header_claiming(10_000, 6_000), thumb_size=320, thumb_max_bytes=28_000
    ) is None


def test_a_jpeg_over_the_draftable_edge_ceiling_is_refused():
    # 13 MP: under the pixel ceiling, over MAX_SOURCE_EDGE (12 000).
    assert thumbnails.generate(
        _jpeg_header_claiming(13_000, 1_000), thumb_size=320, thumb_max_bytes=28_000
    ) is None


def test_the_same_declared_size_is_refused_as_a_png_and_accepted_as_a_jpeg():
    """The clearest statement of the invariant: identical dimensions, opposite verdicts,
    decided ONLY by whether Pillow can draft-decode the format."""
    png = thumbnails.generate(_png_header_claiming(3000, 3000),
                              thumb_size=320, thumb_max_bytes=28_000)
    jpeg = thumbnails.generate(_jpeg_header_claiming(3000, 3000),
                               thumb_size=320, thumb_max_bytes=28_000)
    assert png is None            # undraftable: refused by the lower ceiling
    assert jpeg is not None       # draftable: cleared the higher one and thumbnailed


# ── Every allow-listed image type actually round-trips ───────────────────────

@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_each_supported_source_format_produces_a_thumbnail(fmt):
    """attachment_service thumbnails four types; only two were exercised end to end.

    A Pillow quirk specific to GIF (palette/multi-frame) or WebP (lossy vs lossless,
    alpha) would otherwise surface first in production.
    """
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (30, 90, 160)).save(buf, fmt)
    out = thumbnails.generate(buf.getvalue(), thumb_size=320, thumb_max_bytes=28_000)
    assert out is not None, fmt
    assert out[1] in thumbnails.THUMB_MIMES
