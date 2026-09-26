# CakeCRM brand assets

The mark is a flat slice, drawn once as SVG and rendered here at fixed sizes for places
that cannot take a vector (app-store listings, README badges, chat avatars, social cards).

| File | Use |
|---|---|
| `logo-mark.svg` | The source. Identical to `frontend/public/logo-mark.svg`, `frontend/public/favicon.svg`, `website/img/logo-mark.svg` and `website/favicon.svg` — edit one, copy to all five. |
| `logo-mark-16.png` … `logo-mark-64.png` | Favicon and toolbar sizes. |
| `logo-mark-128.png`, `logo-mark-256.png` | Avatars, README, docs. |
| `logo-mark-180.png` | Apple touch icon size. |
| `logo-mark-512.png`, `logo-mark-1024.png` | Store listings, social previews, print. |
| `readme/*-{light,dark}.png` | The README screenshots: the website captures framed on the light and dark page ground by `scripts/frame_readme_shots.py`. Regenerate after any re-shoot. |
| `lockup-light.png`, `lockup-dark.png` | Mark + "CakeCRM" wordmark (Montserrat 700) on the app's light and dark page colours, 2× resolution. |

Colours are the app's own tokens from `frontend/src/index.css`: card and raised for the
faces, the two golds for the top layer, maroon and accent-dark for the crust, and a
hairline maroon edge on the top face (`stroke-width` 0.5 with `vector-effect:
non-scaling-stroke`, so it stays one device pixel at any size).

The mark always sits to the **left** of the wordmark, never above it. Do not place it on
a red badge, and do not outline it. To regenerate the PNGs after editing the SVG, render
the SVG in a browser at each size (Chromium via Playwright is what produced these);
transparent background, no padding.
