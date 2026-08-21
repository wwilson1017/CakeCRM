# Auto-issues loop — operator guide

CakeCRM drains its open-issue backlog with the **auto-issues loop**: an orchestrator
spawns one worker per eligible GitHub issue, and each worker takes its issue all the way
to a reviewed, tested, **ready-to-ship** pull request. A human merges every PR — the loop
never merges.

Two interchangeable orchestrators share the same state (labels, PR markers, `OUTCOME:`
vocabulary), so you can start a backlog with one and finish with the other:

- **`/auto-issues`** — headless workers, best for large unattended batches and runs that
  need a hard per-worker budget cap.
- **`/auto-issues-team`** — live Agent-Teams teammates you can watch and message
  mid-build; teammates whose plans touch the same files coordinate at plan time.

> The loop's engine lives in **user-level skills** (`~/.claude/skills/issue-worker/`,
> `~/.claude/commands/auto-issues*.md`), not in this repo. This document, the
> `.github/workflows/pr-review.yml` workflow, and `scripts/seed-labels.sh` are the
> repo-side wiring the loop expects.

## Conventions (source of truth: `CLAUDE.md`)

Issue eligibility, branch naming, PR target, and the human-merge rule are defined once in
**`CLAUDE.md` → "Issue Loop Conventions"**. In brief: work starts from a GitHub issue;
branch `feature/issue-N-<slug>` off `main`; PR back to `main`; human merge only;
eligibility is *open, unassigned, no `no-auto` label, and human-approved via the
`greenlit` label* (default-deny — an un-`greenlit` issue never enters the loop); respect
`Blocked by: #N` lines. See `CLAUDE.md` for the authoritative wording — it is not
duplicated here.

## Label vocabulary

`/auto-issues` auto-creates any missing labels at run start, and
`scripts/seed-labels.sh` reproduces/normalizes them on a fresh clone or fork (idempotent;
run it once to get canonical descriptions):

```bash
scripts/seed-labels.sh                 # targets the current repo (inferred via gh)
scripts/seed-labels.sh --repo owner/n  # targets an explicit repo
```

| Label | Meaning |
|-------|---------|
| `greenlit` | Human-approved for the loop (the loop only works greenlit issues). |
| `no-auto` | Opt this issue OUT of the loop (the human opt-out switch). |
| `complex` | Complex lane — a deep planner plans it before implementation. |
| `epic` | Epic lane — a human reviews the plan before any code is written. |
| `awaiting-answer` | Parked: waiting on a human answer to a clarifying question. |
| `awaiting-approval` | Parked: waiting on human approval of the plan. |
| `awaiting-question` | Parked at settle: a review finding needs a human decision. |
| `needs-settle` | PR open but not yet mergeable — re-queued for the settle lane. |
| `ready-to-ship` | Settled + verified — one click from merge (never auto-merged). |
| `needs-review` | Settle did not converge (standalone) — needs a human review. |
| `auto-failed` | Loop hit a technical dead-end after bounded retries. |
| `evidence-posted` | Verification evidence recorded on the PR. |
| `evidence-failed` | Verification found a bug that was not fixed this pass. |
| `reporter-greenlit` | Reporter approved the verification evidence. |
| `sync-intake` | Filed automatically by the cake_os sync bot — triage it at first-look. |

### Where `sync-intake` issues come from

The **cake_os sync bot** (`docs/SYNC.md`) files an issue whenever upstream CRM code changes,
so ports don't depend on somebody noticing. Those issues arrive **un-`greenlit` like any
other** — default-deny still holds, and the bot never labels its own work eligible. Most are
closed after a glance at the verdict in the title; the interesting ones get `greenlit` at
first-look and then run the ordinary pipeline below, with no special casing anywhere in the
loop. The port worker reads cake_os from the local clone, never from the issue body.

## Terminal outcomes

Every worker ends in exactly one state, printed on its last line as
`OUTCOME: <state> ...`:

- **READY** — PR opened, tests green, review clean, PR review settled (0 unresolved
  threads, required checks green, mergeable), evidence recorded, `ready-to-ship`
  applied. **Not merged.**
- **PARKED** — needs a human (a clarifying question or plan approval). Labeled +
  commented; resumes automatically once a human answers.
- **SETTLING** — PR open but not yet mergeable; remaining findings captured in a
  `<!-- auto-settle-todo -->` comment and labeled `needs-settle`. The next run resumes it
  with no human.
- **RETRY** — (complex/epic) the deep planner was unavailable; the issue stays eligible
  and the next run re-plans it fresh.
- **FAILED** — technical dead-end after bounded retries; a draft PR + `auto-failed`.
- **SKIPPED** — triage judged it non-actionable for one autonomous pass.

## The AI Code Review workflow

`.github/workflows/pr-review.yml` runs on every non-draft PR to `main` and posts one
advisory review comment (marker `<!-- ai-code-review-bot -->`). It is **optional**:

- With an `ANTHROPIC_API_KEY` Actions secret set, it calls the Anthropic Messages API and
  posts/updates the review comment. The model defaults to `claude-opus-4-8` and is
  overridable via the `AI_REVIEW_MODEL` repo variable.
- With no key (the default, and always on forks), the job is a **green no-op** — it posts
  nothing and never fails.
- It is **advisory** and **must not be configured as a required status check**: a
  transient AI outage should never block a merge.

> **Privacy note:** enabling the secret sends each PR's title, body, and diff to Anthropic
> for review. On a private pre-launch repo this may include unreleased source (and, in the
> worst case, secrets committed by mistake — before the gitleaks scan necessarily
> completes). Leave the key unset if that is a concern.

## Repo prerequisites (settings a PR cannot set for you)

The loop's "PR is 100% mergeable" gate assumes two repo settings that must be configured
once, by hand, in GitHub — they are not (and cannot be) set by a PR:

1. **Branch protection on `main`** — require conversation resolution and the CI status
   checks before merging. Add these **exact check contexts** (the CI job display names
   from `.github/workflows/ci.yml`):
   - `Backend (ruff + import check + tests)`
   - `Frontend (build + lint + test)`
   - `Secret scan (gitleaks)`

   Do **not** add `AI Code Review` as a required check (see above). Without conversation
   resolution + required CI checks, the loop's settle step has nothing to gate on.
2. **Optional `ANTHROPIC_API_KEY` Actions secret** — enables the AI Code Review workflow.
   The loop works fully without it.

> First-time contributors' fork PRs may require a maintainer to approve the workflow run
> before Actions execute; until approved, the checks show as pending rather than green.

## Bring-up sequence (do not skip steps)

1. `/auto-issues --dry-run` (or `/auto-issues-team --dry-run`) on the real backlog →
   triage report + queue print with **zero** GitHub mutations.
2. `/auto-issues --only <throwaway-issue>` → one worker, end-to-end to READY, on a
   disposable issue.
3. A small `--limit` open run; compare cost and outcomes.
4. Only then widen the batch (raise `--cap` for the team orchestrator).

This very repo's wiring (issue #11) was itself validated by the loop: issue #11 →
`/issue-worker` → this documentation's PR.
