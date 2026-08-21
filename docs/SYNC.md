# cake_os → CakeCRM sync

CakeCRM's CRM features are ported from the CAKE OS CRM, which is an actively developed
system. This document describes the **sync bot** that keeps the two from drifting: how an
upstream merge becomes an intake issue here, what may and may not cross the boundary, and
how to actually perform a port.

Two halves, in two repos:

| Half | Lives in | Status |
|---|---|---|
| **Sender** — watches `master`, fires a dispatch on a qualifying merge | cake_os | **Not built yet.** §6 is its spec. |
| **Receiver** — validates, classifies, files an intake issue | CakeCRM (this repo) | Built (issue #23). |

The receiver shipped first, deliberately. Until the sender exists nothing fires it, and
nothing breaks — the workflow simply never runs.

---

## 1. The two guarantees

**Never a direct push.** The bot opens issues. Ports become ordinary pull requests through
the ordinary review path, and a human merges them, exactly as `CLAUDE.md` requires. This is
structural, not a promise: no component in the pipeline holds a push-capable credential
(§6.2).

**No upstream text, ever.** CakeCRM is going public and its history is permanent, while
cake_os is a private repo full of company internals. So the wire format carries **metadata
only** — no diff text, no PR title, no PR body, no commit message, no author — and the
rendered issue carries **no cake_os path either**.

That last point is the subtle one. A path looks safe but is only *prefix*-constrained:
everything after `backend/apps/crm/` is free text chosen by whoever named the file upstream.
It could carry a customer or staff name, or forge the dedupe marker. So the intake issue
renders **CakeCRM's own counterpart path**, and only when that file already exists in this
tree — a name already public here, disclosing nothing new. Everything else becomes a count.

Enforced three ways:

- **Structurally** — the payload schema (§3) has no field that can hold prose, out-of-scope
  paths are dropped before rendering, and validation errors name fields rather than values,
  so nothing reaches a runner log verbatim.
- **Contractually** — the allowlist in §3 is the binding contract for the sender.
- **By test** — `backend/tests/test_sync_intake.py` feeds sentinel-bearing paths and asserts
  no sentinel survives into the rendered body. The ledger guard additionally asserts
  `SYNC_LEDGER.md` parses to schema and carries no email addresses or diff fences. It
  deliberately does **not** encode staff-name patterns: a denylist would publish the very
  PII it protects.

---

## 2. How it flows

```
cake_os merge to master (touches a watched path)
        │
        │  workflow_dispatch — metadata only
        ▼
CakeCRM .github/workflows/sync-intake.yml
        │  validate → classify → dedupe
        ▼
"sync-intake" issue, NOT greenlit
        │
        │  human first-look (/auto-issues-first-look)
        ▼
greenlit → the ordinary /auto-issues pipeline ports it
        │  (reads cake_os from the local clone, never from the issue)
        ▼
Port PR → review → human merge → adds its SYNC_LEDGER.md row
```

**Nothing in this repo translates anything.** Translation is judgment work, and the existing
`issue-worker` pipeline already does it with plan → implement → review → settle. An intake
issue is just an issue.

### Watched paths

| cake_os | CakeCRM counterpart root |
|---|---|
| `backend/apps/crm/` | `backend/crm/` |
| `frontend/src/apps/crm/` | `frontend/src/crm/` |
| `frontend/src/shared/dnd/` | `frontend/src/shared/dnd/` |

### Classification (deterministic; source of truth is `scripts/sync_intake.py`)

Per file, first match wins:

| # | Rule | Class |
|---|---|---|
| 1 | not under a watched root | dropped — never rendered |
| 2 | one of the three exact internal-only paths | `internal` |
| 3 | `*.md` | `docs` |
| 4 | under `frontend/src/shared/dnd/` | `dnd` |
| 5 | otherwise | `code` |

Internal-only is **exact paths**, not basename matching:
`backend/apps/crm/import_service.py` (Odoo-bound), `backend/apps/crm/lead_import_service.py`
(generic CSV, but imports `apps.dimm` and `apps.todo_gtd`), and
`backend/apps/crm/tools/lead_import_tools.py`.

The payload verdict is the most actionable class present:

| Verdict | Meaning |
|---|---|
| `crm-code` | CRM code changed — worth a look. |
| `shared-dnd-only` | Only `shared/dnd/` changed. |
| `internal-paths-only` | Only excluded paths. Nothing to port. |
| `docs-only` | Only Markdown under a watched path. Nothing to port. |
| `no-watched-files` | Nothing in scope. Filed for the audit trail. |

**Verdicts are factual, not portability judgments** — and that wording is deliberate.
Portability is not decidable from a path: an Odoo-only edit inside `router.py` classifies as
`code`, and a generic improvement inside `import_service.py` classifies as `internal`. The
verdict tells you whether to *look*, never whether to *port*.

`shared-dnd-only` deserves its own note: that module has **13 non-CRM consumers** in cake_os
against one CRM consumer — CRM is 1 of 14. A dnd-only change is far more likely platform
work, so it does not read as a CRM signal. (The consumers are named nowhere in this repo on
purpose: upstream app names describe the business, and this repo is public.)

Every merge files an issue whatever the verdict — even `no-watched-files`. Silence would be
ambiguous (did the bot fire, or crash?), and the issue is both the dedupe anchor and the
audit trail. At the measured rate (§5) that is roughly one closable issue a month. If it ever
becomes a burden, gate filing on `verdict == crm-code` — a one-line change.

### Dedupe

Each issue body opens with `<!-- sync-source-sha: <full 40-char sha> -->`. Before filing, the
workflow lists `sync-intake` issues (all states, paginated) and skips if the marker is
present. The full SHA is used so a short-SHA lookalike cannot collide.

This is **best-effort, deliberately not serialized**. One merge fires one dispatch, so a
concurrent double-file is theoretical — whereas adding a `concurrency:` group would risk
*dropping* a distinct intake, since only one run may sit pending. The worse failure is the
one that loses data.

`SYNC_LEDGER.md` is *not* consulted at runtime: its rows carry a short SHA, so the check
would be inexact, and it would turn a reviewed document into a second database.

**Two residual risks, named rather than papered over.** Both assume an attacker already
holds dispatch access — i.e. the sender token leaked, or a CakeCRM collaborator went bad —
and neither can leak data or push code; the ceiling is issue-tracker noise.

- *Spam.* Since the payload is only syntax-checked, a fresh fabricated SHA per call defeats
  dedupe and files unlimited intake issues. Accepted: the blast radius is triage time, and
  the fix (an HMAC field, or a rate cap) buys little against a threat model where the token
  is already compromised.
- *Suppression.* The marker binds to the SHA alone, so pre-filing an intake for a SHA that a
  future merge will carry would cause the real dispatch to be skipped as a duplicate. This
  needs the attacker to predict a commit SHA in advance, which is not practical — noted for
  completeness. Binding the marker to a hash of SHA + verdict + counts would close it if the
  precondition ever became realistic.

---

## 3. Payload contract (the binding allowlist)

Four `workflow_dispatch` inputs. GitHub allows 25 inputs totalling 65,535 characters; four is
not an accident, since every added field is a new place prose could hide.

| Input | Type | Validation |
|---|---|---|
| `source_sha` | string | exactly 40 hex characters; normalized to lowercase |
| `source_pr` | string | `[1-9][0-9]{0,6}` — no zero, no leading zeros |
| `merged_at` | string | ISO-8601 with a UTC offset; re-emitted canonically |
| `files` | string | JSON array of `{path, additions, deletions, status}` |

Per entry:

- `path` — relative, no `..` segment, characters limited to `[A-Za-z0-9._/-]`, ≤400 characters
  total and ≤255 per component. The charset bars whitespace, control characters, and anything
  with Markdown or HTML meaning; the per-component bound exists because the classifier `stat`s
  each mapped path, and an over-long single name would otherwise raise `ENAMETOOLONG` instead
  of a clean rejection. Paths outside the watched roots are **accepted and dropped**, not
  rejected: the sender may legitimately post its whole changed-file list.
- `additions` / `deletions` — non-negative integers. Checked with `type(v) is int`, not
  `isinstance`, because `bool` subclasses `int` and `True` would otherwise pass as a count.
- `status` — one of GitHub's fixed values (`added`, `modified`, `removed`, `renamed`,
  `copied`, `changed`, `unchanged`). Validating against a closed enum is what stops `status`
  becoming a free-text field.
- The array must be non-empty, ≤500 entries, with no duplicate paths.

**Not in the contract, and never will be:** PR title, PR body, commit message, author,
branch name, diff text, or `previous_filename`. A rename is conveyed by its `status`; the
porting worker reads the real repo anyway, so nothing is lost.

**This is syntax validation only.** The receiver cannot prove the SHA, PR number, timestamp
and counts actually belong together — that would require authenticated cake_os access. The
payload is a *notification*, and every downstream step re-derives truth from the real repo.

---

## 4. Porting: the playbook

An intake issue tells you *that* something changed. This section is *how* to bring it over.

**Read the source from the local cake_os clone (`~/ai/cake_os`), never from the issue.**
Fetch first — a stale clone has produced confidently wrong ports before.

### Both sides are PostgreSQL

Worth stating plainly because the opposite was believed for a while: cake_os's CRM is
**Postgres**, not SQLite. There is no `backend/apps/crm/db.py`; the service files use the
same `core.postgres` helpers CakeCRM does, with `%s` placeholders.

This matters because `docs/solutions/database-issues/cakecrm-sqlite-to-postgres-crm-port.md`
is about **chatty's** SQLite `crm_lite` (issue #3). For a cake_os port:

- **Do not apply** its `?`→`%s`, `cursor.lastrowid`, `LIKE`→`ILIKE`, or threading-lock items.
  There is no SQLite here.
- **Do still apply** its CakeCRM-side items, which are about this repo's conventions rather
  than the source engine: the `new Date(iso + 'Z')` → "Invalid Date" trap, `DOUBLE PRECISION`
  vs `REAL` for money, `exclude_unset` partial-update null handling, enum coercion in the
  service layer, real pagination totals, and `run_in_threadpool` for bulk loops.

### What actually makes a port judgment work, not a patch-apply

1. **Tenancy has to be stripped.** cake_os threads `user_email`/`owner_email` through roughly
   a quarter of its CRM functions; CakeCRM is single-user, so those parameters have nowhere to
   land. This is structural, not cosmetic.
2. **The module topology does not line up.** ~20 cake_os service modules collapse into
   CakeCRM's single `backend/crm/service.py` (plus a handful of siblings). "Same filename
   exists" is a *candidate*, never a destination — which is exactly how the intake issue words
   it.
3. **Other apps leak in.** `apps.todo_gtd` and `apps.dimm` appear across several CRM modules.
   CakeCRM has no equivalent, so those call sites need removing or adapting on every port.
4. **Some CakeCRM ports deliberately fixed upstream bugs.** A faithful re-apply can silently
   reintroduce one. Check the target file's comments before overwriting behavior.
5. **Upstream has tests, and they do not come across as-is.** cake_os's CRM is covered by ~22
   test files that import `apps.crm`; they assume its tenancy and module layout. Port the
   *intent*, and write the test against CakeCRM's shape.
6. **Scrub before you commit.** Upstream source contains company-domain email literals and
   staff names, including in test fixtures. Nothing from a port may carry them —
   `backend/tests/test_prompt_genericization.py` enforces this for model-facing text, but the
   rest is on you.

### Finishing a port

Run the normal gates (`ruff check .` → import check → `pytest -q` from `backend/`;
`npm ci` → `npm run build` → `npm run lint` → `npm test` from `frontend/`), add a
`SYNC_LEDGER.md` row, update `CLAUDE.md`'s Source Map if the architecture moved, and open a PR.
Close the intake issue with it.

---

## 5. Expected volume

Measured over the 92 days before this shipped: **67 qualifying merges ≈ 5.1/week** — 19
backend-CRM-only, 20 frontend-CRM-only, 26 mixed, 2 dnd-only. Most are small. Expect to close
most intakes unread after a glance at the verdict, and to greenlight the few that matter.

---

## 6. Spec for the cake_os sender (its companion issue)

Everything below is what the cake_os side must do. It is written to be pasted into that
issue.

### 6.1 Trigger

On `push` to `master` (i.e. after a merge) whose changed files touch
`backend/apps/crm/`, `frontend/src/apps/crm/`, or `frontend/src/shared/dnd/`, dispatch to
CakeCRM:

```
POST /repos/wwilson1017/CakeCRM/actions/workflows/sync-intake.yml/dispatches
{
  "ref": "main",
  "inputs": {
    "source_sha": "<40-hex merge commit sha>",
    "source_pr":  "<pr number>",
    "merged_at":  "<ISO-8601 UTC>",
    "files":      "<JSON array, see §3>"
  }
}
```

`ref` **must** be `main`. The receiver refuses anything else: a dispatch names the ref, and
GitHub takes both the workflow and the classifier script from that ref.

Send only files under the three watched roots. The receiver drops the rest anyway, and
filtering at the source keeps the payload comfortably inside the 65,535-character limit.

### 6.2 Credential — the part that matters

A **fine-grained PAT**, stored on cake_os as `CAKECRM_SYNC_TOKEN`:

- Resource owner: `wwilson1017`
- Repository access: **only** `CakeCRM`
- Permissions: **Actions → Read and write**, and nothing else

That single permission is what makes the "never a direct push" guarantee structural:

| Endpoint | Required permission |
|---|---|
| `workflow_dispatch` (what we use) | **Actions: write** |
| `repository_dispatch` (rejected) | **Contents: write** — i.e. push-capable |
| Create an issue | **Issues: write** |

`repository_dispatch` was rejected precisely because its token would be push-capable against
a `main` that is not currently branch-protected. A `workflow_dispatch` token cannot push,
cannot open a PR, and cannot even create an issue — the intake issue is authored by
`github-actions[bot]` using the *receiving* workflow's own `github.token`.

Being honest about the residual: *Actions: write* is repository-wide Actions authority. It
can also enable or disable workflows and dispatch any other dispatchable workflow in
CakeCRM. CakeCRM has no other dispatchable workflow, so the blast radius is small — but it is
not literally "may fire only this one workflow."

Fine-grained PATs expire within a year. Rotation is a recurring chore; the sender should fail
loudly, not silently, when the token stops working.

### 6.3 Failure behavior

A dispatch that is rejected (4xx) means the payload violated §3. The sender should surface
that as a failed check on the cake_os side — never retry blindly, and never fall back to
sending more data than the contract allows.

---

## 7. Operational notes

- `sync-intake.yml` only becomes dispatchable once it is on CakeCRM's **default branch**;
  that is a GitHub rule for `workflow_dispatch`, not a choice.
- The workflow **self-provisions** its `sync-intake` label (`gh label create --force`), so a
  fresh clone or fork works without anyone having run `scripts/seed-labels.sh` first.
- The repo's `default_workflow_permissions` is `read`, which is why the workflow declares
  `permissions: { contents: read, issues: write }` explicitly. Removing that block breaks it.
- To exercise the receiver by hand without the sender:

  ```bash
  gh workflow run sync-intake.yml --ref main \
    -f source_sha=<40-hex> -f source_pr=1234 -f merged_at=2026-08-20T11:25:15Z \
    -f files='[{"path":"backend/apps/crm/chatter_service.py","additions":3,"deletions":1,"status":"modified"}]'
  ```

  Firing the same SHA twice is the dedupe test.
