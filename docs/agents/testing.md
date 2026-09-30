# Tests, guard tests and CI

> Topic doc split out of `AGENTS.md` (Tests and CI). `AGENTS.md` keeps the enforceable
> rules; this file is the full record, moved verbatim. Add new notes here, not in `AGENTS.md`.

## Backend tests

- **Backend tests** live in `backend/tests/` (config in `backend/pytest.ini`,
  `asyncio_mode = auto`). The default `pytest` run is **hermetic** — pg helpers and
  provider SDKs are mocked, encryption runs against a per-test key — so the CI gate
  needs no database. Tests that need a real PostgreSQL are marked
  `@pytest.mark.integration` and deselected by default (`addopts = -m "not
  integration"`); run them with `pytest -m integration` and a reachable
  `TEST_ADMIN_DSN`. No `skip`/`xfail`/`# noqa`/`eslint-disable` — fix root causes.
  A repo-wide **guard** test — one that sweeps the tree and asserts a property
  (`test_route_authz`, `test_gmail_guard`, `test_prompt_genericization`,
  `test_query_determinism`, `test_help_library`) — must itself be falsifiable, because a sweep that quietly
  stops matching is a permanent green, and a permanent green is worse than no guard
  since it reads as coverage. So a new one owes two things beyond the property itself:
  a self-test pinning its detector on synthetic input (both a case it must flag and a
  case it must not), and an assertion that it still reached the real code — per module
  or per registered item, never one repo-wide total, which the largest package satisfies
  on its own. `test_query_determinism` and `test_prompt_genericization` carry both
  (`test_the_guard_actually_catches_a_leak` is the latter's detector self-test);
  `test_gmail_guard`'s source sweep currently has neither and is worth hardening the
  next time it is touched.

## Frontend tests

- **Frontend tests** are **vitest** (`npm test` → `vitest run`), landed with #73. Config
  is a STANDALONE `frontend/vitest.config.ts` — vitest reads it *instead of*
  `vite.config.ts`, so the production build config stays untouched and tests skip the
  react/tailwind plugin pipeline. Tests are **co-located** with the code
  (`src/**/*.{test,spec}.{ts,tsx}`), not in a separate tree. The default environment is
  `node`; a DOM test opts in with a per-file `// @vitest-environment jsdom` docblock —
  add them that way, never by flipping the default. Components are rendered with plain
  `react-dom/client` `createRoot` + React's `act`; there is deliberately **no
  `@testing-library`** dependency (`vitest` + `jsdom` are the only test devDeps). Three
  fail-closed guards are load-bearing and must not be deleted as "defaults":
  `passWithNoTests: false`, `allowOnly: false`, and `expect.requireAssertions: true`
  (the last is NOT a vitest default) — a runner that reports "0 tests / exit 0" is a
  permanent silent green, which is worse than no runner because it looks like coverage.
  `env: { TZ: 'America/Chicago' }` pins the timezone so local and CI agree — deliberately
  **not** UTC: `crm/pipelineFilters.ts` derives `ymd` from local-date getters because
  `toISOString()` drifts a day in US evening time, and under a UTC runner that distinction
  disappears, so a test pinning it could never fail.

## Genericization guard (`test_prompt_genericization.py`)

- The rule itself (no company internals, genericized ports) is a hard line in `AGENTS.md`.
  Enforced by `backend/tests/test_prompt_genericization.py` (#22, widened repo-wide in
  #90), which scans **two** surfaces against **two** denylists, split by what a token
  IS rather than by which file holds it. `_FORBIDDEN` (= `_COMPANY + _VERTICAL +
  _BLUEPRINT`) covers the **model-facing payload** — the assembled system prompt,
  every tool name/description/schema, the heartbeat prompt, the UI starter chips, and —
  since #143 — every `backend/help/content/` topic, which becomes payload the moment a
  help tool returns it, so blueprint names are banned there as they are everywhere else a
  provider can see. The
  narrower `_REPO_FORBIDDEN` (= `_COMPANY + _VERTICAL`) covers **every committed text
  file**, enumerated by `git ls-files`, so the scope is a file CLASS: a new `docs/`,
  `scripts/` or `.github/` file is guarded the moment it is *staged* — there is no
  directory list to remember to update, which is the whole point (the gap #90 closed
  let a hardcoded upstream org URL and six real upstream directory names reach CI
  green). `_BLUEPRINT` (`cake_os`, `casey`, `cake_crm_`) is the deliberate asymmetry: banned from
  the payload — a shipped product must not name what it was ported from — but
  legitimate in committed prose, since the Source Map and every port comment cite the
  blueprint by name. Deliberate exemptions live in `_REPO_ALLOW` as path → **{pattern:
  exact expected count}** + a written reason, and the count is the whole point: a
  file-keyed exemption would repeat the mistake the gitleaks bullet under "CI" below
  already records — it "exempts every finding in that file, including a
  real one" — and AGENTS.md is the most-edited file in the repo, so an unbounded
  exemption *here* would be the widest hole of all. Entries exist ONLY for text that
  must talk *about* the denylist: this rule and a sibling guard's own literals. A count that stops matching reality
  fails CI **in both directions** — a stale or inflated allowance is caught as surely
  as a new occurrence — so turning the guard down takes a visible edit to that list
  rather than a bumped number. Scrub the file instead whenever scrubbing is possible.
  **No file is exempt, the guard included** — it holds a counted allowance for its own
  denylist literals like everything else, so the one file with the most licence to
  carry these strings is not also the one place nobody is watching. Surfaces beyond
  plain file *content* are covered because they leak just as permanently: every
  committed **filename** (for a compressed container like a .xlsx, whose bytes no
  decoder can read, that is the only surface there is), **invisible characters** (a
  name pasted out of Word or a PDF can carry a soft hyphen or zero-width space inside
  it and match nothing while reading perfectly — stripped from paths as well as
  bodies), and text in encodings a naive reader drops. On that last: a NUL-byte "is
  this binary?" probe silently skips **UTF-16**, exactly the shape a spreadsheet or
  CSV export of real customer names arrives in, while BOM-less UTF-16 of ASCII content
  is byte-wise *valid UTF-8* and so decodes "successfully" into NUL-interleaved mush
  that matches nothing. The decoder therefore never gives up, and covers the whole
  family in **one** move instead of guessing an encoding: it takes the BOM'd reading
  (UTF-32 tested before UTF-16, which share a two-byte prefix) or falls back UTF-8 →
  latin-1, and for anything NUL-bearing it *additionally* scans the bytes with the
  NULs removed. That one extra reading catches every fixed-width encoding of ASCII at
  once — UTF-16 and UTF-32, either byte order, BOM or none — plus a plain ASCII name
  sitting inside an otherwise-binary blob. Guessing instead meant a NUL-density
  heuristic, and that had a hole: most real binaries are NUL-dense too. Boundaries are
  `(?<![0-9a-z])`, **not
  `\b`** — `\b` counts `_` as a word character, so a token went invisible the moment an
  underscore followed it (`cake_os\b` misses `cake_os_prompt`; the company abbreviation
  vanished the same way inside `<abbrev>_internal`), which are precisely the shapes
  these names take in identifiers, filenames and env vars (six such bypasses were
  measured, and this very bullet tripped the guard by naming one). The scan
  reads the working tree, **not history**: tokens committed before a scrub stay in the
  log. `test_sync_intake.py` consumes `_REPO_FORBIDDEN` **by name** for the same
  reason: it used to hand-copy the blueprint regex in a `pattern != …` exclusion, so
  widening that pattern turned the exclusion into a no-op and failed that test — loudly
  and fail-closed, but on a file nothing was wrong with. Naming the class instead means
  both of #88's scans inherit every future widening automatically. It deliberately
  narrows what they scan (blueprint tokens are legitimate outside the payload) and
  keeps the rendered-issue-body scan, which is coverage no file scan can provide.

## CI

- **CI** (`.github/workflows/ci.yml`) runs on every PR to `main` and on `push` to
  `main`, in three jobs: **backend** (`ruff check .` → import check → `python -m
  pytest -q`, from `backend/`), **frontend** (`npm ci` → `npm run build` → `npm run
  lint` → `npm test`), and **secret-scan** (gitleaks). The scan runs `gitleaks git .` over FULL history, so
  a false positive stays found forever once committed and cannot be fixed by editing
  the tip. `.gitleaksignore` records verified-false findings by exact
  `commit:file:rule:line` fingerprint, with the reasoning written down. Use that file,
  NOT a `.gitleaks.toml` allowlist keyed on commit+path — the latter exempts every
  finding in that file in that commit, including a real one. Validate any new entry
  with a **randomly generated** token, never a canonical doc example
  (`AKIAIOSFODNN7EXAMPLE` and friends are allowlisted by default and prove nothing).
  The alternative is a force-push, which this repo does not do. Backend lint config is `backend/ruff.toml`
  (select `F,E,W,I`; `E501` ignored); dev/CI tooling is pinned in
  `backend/requirements-dev.txt`; tests live in `backend/tests/`. The import check
  imports the app with no `DATABASE_URL` (the Postgres pool inits in the lifespan
  handler), so CI needs no database.

- **AI Code Review** (`.github/workflows/pr-review.yml`) is an **optional, advisory**
  check on PRs to `main`: it posts a review comment (marker `<!-- ai-code-review-bot -->`)
  when an `ANTHROPIC_API_KEY` Actions secret is set (model overridable via the
  `AI_REVIEW_MODEL` repo variable), and is a **green no-op** otherwise (forks, keyless
  repos). It must **not** be configured as a required check. It uses only the GitHub API
  (no checkout) and `on: pull_request` (never `pull_request_target`).

