---
title: Standing up CI/DCO/secret-scan on CakeCRM, and adapting cake_os-shaped automation to a main-based repo
date: 2026-07-23
category: workflow-issues
module: .github/workflows, issue-worker/review-super/pr skills
tags: [ci, github-actions, gitleaks, dco, ruff, auto-issues, review-super]
problem_type: convention
---

## Context

Issue #10 added the first CI/repo-hygiene infrastructure to CakeCRM (`wwilson1017/CakeCRM`,
default branch `main`), a repo seeded from the cake_os/Chatty product shell. The auto-issues
pipeline (issue-worker → review-super → `/pr` → settle → evidence) is built for
`tncheesecake/cake_os` on `master` with its own PR bots, so running it verbatim on a fresh
`main`-based personal repo misfires in several silent ways. Verified: PR #27, all 3 CI jobs green.

## Guidance

**CI workflow (`.github/workflows/ci.yml`), three jobs, on PRs to `main`:**
- backend: `ruff check .` → a no-DB import check → `python -m pytest -q` (run from `backend/`).
  The import check works with no `DATABASE_URL` because the Postgres pool inits in the FastAPI
  `lifespan` handler, not at import — so CI needs no Postgres service.
- frontend: `npm ci` → `npm run build` → `npm run lint`.
- secret-scan: the **pinned gitleaks binary** (SHA-256 verified) run as `gitleaks git .`, NOT
  `gitleaks/gitleaks-action@v2`.

**Pin ruff config from the sibling repo.** The seed was authored against cake_os's
`select = ["F","E","W","I"]`, `ignore = ["E501"]`. Rely on ruff 0.16 *defaults* and it flags the
seed (B008 FastAPI-`Depends`, BLE001, I001, PIE810). Port `backend/ruff.toml`; the only seed
churn is auto-fixable `I001` import ordering.

**DCO, not CLA.** Document `git commit -s` in CONTRIBUTING.md; sign off every commit with the
repo's identity; leave the DCO *App* install + required-status-checks as human-only admin steps
noted in the PR body. Do NOT add a blocking CI DCO job — it would red-flag the automation's own
unsigned PRs.

**Adapting the cake_os-shaped skills to `main`:**
- Base ref: pass `origin/main` explicitly to review-super; `gh pr create --base main`. `master`
  doesn't exist, and local `main` is stale pre-seed so three-dot `main...HEAD` balloons.
- Swap hardcoded `owner:"tncheesecake", name:"cake_os"` → `wwilson1017/CakeCRM` and `base master`
  → `main` in every settle `gh api`/graphql call.
- Before waiting on the settle/evidence reviewers, check they exist: CakeCRM has no
  `chatgpt-codex-connector` or "AI Code Review" check, so the review-thread poll is a no-op and
  the real gate is simply the three CI jobs going green.

## What didn't work
- Local default `python3` is 3.9.6 — can't resolve `fastapi==0.135.3` (needs ≥3.10). Rebuild the
  venv on `python3.12` (matches CI's `setup-python` pin).
- `gitleaks/gitleaks-action@v2` is Node-20-based (deprecated) + org-license-gated; abandoned for
  the binary.
- Fresh worktree had no git identity → first commit got a `user@Host.localdomain` author;
  fixed with `git config user.email <gh-noreply>` + `git commit --amend --reset-author -s`.

## Why This Matters
A CI gate that is red on day-one code you didn't author, or a settle step that polls the wrong
repo and reports a false "0 threads → clean", both erode trust in the whole pipeline. Verifying
each gate locally against untouched HEAD first, and treating the live Actions run as the real
"does it work" evidence for an infra PR, keeps the automation honest.

## When to Apply
Any auto-issues worker on CakeCRM (or another non-cake_os, `main`-based repo), and any task that
adds or edits `.github/workflows` CI gates.
