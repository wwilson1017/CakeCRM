# Cross-branch gitleaks failures under parallel runs

## Context

CI's secret-scan job checks out with `fetch-depth: 0` and runs `gitleaks git .`. gitleaks'
git mode defaults to scanning **all fetched refs**, not the PR's own history — so with
several feature branches in flight (an `/auto-issues-team` run), a single false positive
on ANY unmerged branch turns `Secret scan (gitleaks)` red on EVERY open PR at once.

Observed 2026-08-21: a `generic-api-key` false positive on `feature/issue-60-...`
(`backend/users/router.py` — an ordinary `user_id, body.new_password` argument line,
entropy 3.68) failed the scan on PR #88, whose diff touched no such file.

## Diagnosis (two commands, before reading your own diff)

```bash
git branch -a --contains <the-commit-gitleaks-reported>   # whose branch is it?
git merge-base --is-ancestor <commit> HEAD && echo mine || echo "not my history"
```

Measured on PR #88: scoped to the PR branch → 6 commits, 0 leaks; unscoped → 189
commits, 1 leak, belonging to a concurrent teammate's branch.

## Fixes (both landed 2026-08-21)

1. **Structural (the cure)** — scope the scan to the PR's own history: PR #88 added
   `--log-opts="HEAD"` to the gitleaks invocation (closes #89). The checked-out merge
   ref's history covers the PR branch + main; unrelated sibling refs stop mattering.
2. **At source (the immediate unblock)** — neutralize the false positive on its own
   branch with a `.gitleaksignore` fingerprint (`commit:file:rule:line`). Prefer this
   over a `.gitleaks.toml` `[[allowlists]]` keyed on commit+path, which exempts every
   future REAL finding in that file/commit. Note an ignore file only protects the branch
   that carries it; the `--log-opts` scoping is what protects siblings.

## Validation trap

gitleaks (like most scanners) allowlists canonical documentation examples — planting
`AKIAIOSFODNN7EXAMPLE` proves nothing. Validate any scanner change with a randomly
generated credential: unscoped run finds it + the FP; scoped run finds only what's in
the PR's own history. A negative control that cannot fail is not a control.

## Related

- History-pinned findings cannot be fixed by editing the tip — the finding stays on the
  original commit forever. Fingerprint it or (where policy allows, not here) rewrite.
- When `Secret scan (gitleaks)` reddens a PR, check WHOSE commit the finding pins
  (`git branch -a --contains <sha>`) before reading your own diff — under
  `fetch-depth: 0` one branch's finding used to fail every open PR at once.
- When validating any secret-scanner change, plant a RANDOMLY GENERATED credential,
  never a canonical doc example: scanners allowlist known examples
  (`AKIAIOSFODNN7EXAMPLE` proves nothing), and a negative control that cannot fail is
  not a control.
