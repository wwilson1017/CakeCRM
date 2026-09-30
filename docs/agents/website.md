# Website (mycakecrm.com)

> Topic doc split out of `AGENTS.md` (Website section). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **The explainer site lives in `website/` and is part of the open-source repo** (#163).
  One static `index.html` + `style.css`, no build step, no framework — copied from the
  Chatty `website/` pattern. `.github/workflows/deploy-website.yml` FTP-pushes that
  directory to pair.com on every push to `main` touching `website/**` (plus
  `workflow_dispatch`), using the operator-created `PAIR_FTP_HOST`/`PAIR_FTP_USER`/
  `PAIR_FTP_PASSWORD` secrets. That third-party action is **pinned to a commit SHA with
  the version in a trailing comment**, never to its `v4.3.5` tag: it is handed all three
  production FTP credentials, and a tag is movable, so a retagged or compromised upstream
  would run replacement code against the live host on the very next website push. Re-pin
  the SHA and update the comment together when upgrading it. The theme tokens in
  `style.css` are a LITERAL copy of the light `--color-ck-*` values in
  `frontend/src/index.css` (the site cannot import the app's CSS) — when the app palette
  changes, re-copy them; light only by decision. Fonts are the same `@fontsource` woff2
  files the app ships, self-hosted under `website/fonts/` so the page makes no
  third-party request — and self-hosting IS redistribution, so the SIL Open Font License
  1.1 text plus both upstream copyright notices ride beside them in
  `website/fonts/OFL.txt`, pointed at from the `@font-face` block. Be precise about why
  that file is needed, because the review finding that prompted it overstated the case:
  each woff2 DOES embed a copyright (sfnt `name` nameID 0) and a license URL (nameID 14)
  — what it carries is no license *text* (no nameID 13) and a zero-length WOFF2
  extended-metadata block, and OFL 1.1 condition 2 wants the license itself to travel
  with the copy. So `OFL.txt` is the only full notice shipped with them: adding a font
  family here means adding its notice there in the same commit.
  **Screenshots come ONLY from the demo instance after a reset to the fictional sample
  data** (`POST /api/crm/clear-all` → `/load-sample-data`), shot with Playwright at
  1280×800 light at 2× with the launcher folded (WebP at native 2× width, quality 82; PNG
  fallback at 1× and palettised) — dashboard, pipeline board, a deal card over the board,
  the Todos Today and Inbox tabs, and a deal with the Baker drawer, that last one at
  1600×1000 so the drawer sits beside the sheet instead of over it; the hero is a mark +
  wordmark lockup (see **Brand mark**), not a screenshot, and the theme of the copy is
  simplicity. **The README uses the same captures, framed**: `scripts/frame_readme_shots.py`
  puts each WebP master in a browser window on the app's own page ground, light and dark,
  and writes 1× PNGs to `docs/brand/readme/`, which the README picks between with
  `<picture>`; re-run it whenever the site images are re-shot, and re-copy its colour
  constants when the palette changes. The Contacts frame is README-only, so
  `website/img/contacts.webp` is a master the site never displays; it lives beside the
  others so one directory holds every source. The genericization guard scans
  text, not pixels, so a real prospect name in a PNG would be invisible to CI and
  permanent in history. Strip the example-data banner and the bell badge before shooting;
  the Baker shot uses a deal-scoped quick action, because an install-wide question can
  surface memory facts from pre-reset data. Demo credentials never enter the repo.
  **The copy is a claim about the app and is re-read against it whenever the shots are
  re-taken** — the nav word is Todos, closed stages are hidden on the board by default,
  filters save as team views, Reports is a tab, the key goes in under AI Setup, and one
  Telegram bot serves a link per seat; a sentence the app no longer backs comes out.
  **Marketing copy about safety is a claim about the code and is held to it.** The
  assistant ships three tool modes (`AssistantPanelBody.MODES`: Read / Ask / Auto), Ask
  is the default, `engine` executes a write immediately under `tool_mode == "power"` —
  the drawer's **Auto** — and since #180 Ask itself auto-approves the ROUTINE tier
  (`registry.is_routine_write`: creating and updating records, logging activity), keeping
  the card for deletes, archives, merges, bulk writes, drafts, notifications and memory.
  So the page says everyday edits go straight in under Ask, that removals, notifications
  and anything leaving the app wait for approval, and that Auto is something you turn on
  yourself. "Every write asks first" was false twice over — for Auto, and for a routine
  write in Ask — and a review caught the second after the first was fixed. Any future
  safety sentence owes the same check against `frontend/src/assistant/` (the Ask
  tooltip is the shipped wording) and `backend/assistant/{engine,confirm_tier}.py`.
