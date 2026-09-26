"""Frame demo screenshots for the README.

Takes the raw 2x captures the website uses (light theme, launcher folded) and puts
each one in a browser window with a soft shadow on the app's own page ground, in a
light and a dark variant, so the README reads right under either GitHub theme.
Output is a 1x palettised PNG per variant under docs/brand/readme/.

    python scripts/frame_readme_shots.py website/img docs/brand/readme dashboard pipeline ...

Colours are the light/dark `--color-ck-*` tokens from frontend/src/index.css; re-copy
them here when the palette changes (the same rule website/style.css follows).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

GROUND = {"light": (0xF1, 0xF1, 0xE8), "dark": (0x1C, 0x1B, 0x19)}   # ck-bg
CHROME = {"light": (0xE6, 0xE4, 0xDB), "dark": (0x2A, 0x28, 0x26)}   # title bar (dark: ck-card)
LINE = {"light": (0xD1, 0xD5, 0xDB), "dark": (0x4A, 0x48, 0x45)}     # ck-line-strong
DOTS = {"light": (0xB8, 0xB5, 0xAA), "dark": (0x5C, 0x59, 0x54)}
SHADOW = {"light": (40, 30, 20, 70), "dark": (0, 0, 0, 150)}

S = 2  # captures are 2x; the CSS-px constants below are scaled by S
PAD, RADIUS, BAR, BORDER = 56, 12, 36, 1


def _rounded_mask(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), r, fill=255)
    return m


def frame(shot: Image.Image, theme: str) -> Image.Image:
    shot = shot.convert("RGB")
    w, h = shot.size
    bar = BAR * S
    win = Image.new("RGB", (w, h + bar), CHROME[theme])
    d = ImageDraw.Draw(win)
    for x in (18, 36, 54):
        d.ellipse((x * S - 5 * S, bar // 2 - 5 * S, x * S + 5 * S, bar // 2 + 5 * S), fill=DOTS[theme])
    d.line((0, bar - 1, w, bar - 1), fill=LINE[theme], width=S)
    win.paste(shot, (0, bar))

    r = RADIUS * S
    bordered = Image.new("RGB", (w + 2 * BORDER * S, win.height + 2 * BORDER * S), LINE[theme])
    bordered.paste(win, (BORDER * S, BORDER * S), _rounded_mask(win.size, r - BORDER * S))
    bordered.putalpha(_rounded_mask(bordered.size, r))

    pad = PAD * S
    canvas = Image.new("RGBA", (bordered.width + 2 * pad, bordered.height + 2 * pad), GROUND[theme] + (255,))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (pad, pad + 14 * S, pad + bordered.width, pad + 14 * S + bordered.height), r, fill=SHADOW[theme])
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * S)))
    canvas.alpha_composite(bordered, (pad, pad))
    return canvas.convert("RGB")


def main(src: Path, out: Path, names: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        shot = Image.open(src / f"{name}.webp")  # the 2x master; the PNG beside it is 1x
        for theme in ("light", "dark"):
            img = frame(shot, theme)
            img = img.resize((img.width // S, img.height // S), Image.LANCZOS)
            target = out / f"{name}-{theme}.png"
            img.convert("P", palette=Image.ADAPTIVE, colors=256).save(target, optimize=True)
            print(target, img.size, target.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:])
