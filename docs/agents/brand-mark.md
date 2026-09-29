# Brand mark

> Topic doc split out of `AGENTS.md` (Brand mark section). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> matching Product Rules bullet — find its topic doc through the index at the top of
> `AGENTS.md`.

- **The brand mark is a flat vector slice, `frontend/public/logo-mark.svg`** (a 100×100
  viewBox wedge seen three-quarter-on: card/raised faces, the two gold tokens as the top
  layer, maroon + accent-dark red as the crust — chosen so nothing sinks into the dark
  theme's ground — and a hairline maroon `vector-effect: non-scaling-stroke` on the top face
  (`stroke-width` 0.5 — one CSS px is TWO device px on retina), since a cream top on the
  cream page otherwise has no edge). Byte-identical copies serve as `frontend/public/favicon.svg`
  and `docs/brand/logo-mark.svg`; the explainer site (#163/#164) carries two more under
  `website/`. It replaced the 🍰 emoji at every site (`CrmLayout` nav fallback, `LoginPage`,
  `SetupPage`, the README heading). It descends from the sponsoring bakery's logo slice —
  an earlier cut used a pixel crop of that logo, unusable above ~64 px — and is deliberately
  NOT named after the dessert: that word is on the genericization denylist, filenames
  included. Edit the SVG, and every copy must be re-copied (they are not built), and the
  fixed-size PNG exports in `docs/brand/` (16–1024 px + light/dark lockups, rendered in
  Chromium, transparent, unpadded; `docs/brand/README.md` says how) regenerated with it.
  `frontend/public/icon-{192,512}.png` are the todo PWA's **maskable** icons the manifest in
  `crm/todo_pwa.py` has referenced since #70 without the files existing: the mark at 70% on
  the card colour, so a masked launcher's safe zone never clips it. The mark always sits
  LEFT of the "CakeCRM" wordmark, never above it, and never on a badge.
