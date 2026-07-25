# CakeCRM PR evidence: direct boot, not the cake_os harness

**Problem.** The `auto-issues-pr-evidence` skill is hardwired to cake_os — `~/ai/cake_os`
worktrees, `~/ai/cake-os-secrets/.env`, the cake_os Postgres bring-up, the
`disable_side_effects` guard (a cake_os feature CakeCRM doesn't have), and the
`cake-os-bot` reporter. On a CakeCRM PR it fails at every path. Four workers hit this
independently on the 2026-07-25 team run (#5, #8, #14, #16); the fix below produced real
evidence on all seven PRs that ran it.

**Solution — boot CakeCRM directly (env vars only, no `backend/.env`):**

```bash
# 1. Fresh throwaway DB on the shared container (psql may be absent — use the venv's psycopg2):
#    role cake/cake_dev, NOT .env.example's cakecrm. FRESH is mandatory: the migration
#    runner records applied filenames, so a dirty/shared DB silently skips edited schema.
docker exec cake_os-postgres-1 psql -U cake -c 'CREATE DATABASE cakecrm_ev<N>'

# 2. Build the SPA once, then one uvicorn serves API + UI single-origin (no vite proxy):
cd frontend && npm run build && cd ../backend
DATABASE_URL=postgresql://cake:cake_dev@localhost:5432/cakecrm_ev<N> \
AUTH_PASSWORD=<pw> \
.venv/bin/python -m uvicorn main:app --port <slot-port>   # python3.12; migrations self-apply
```

- Verify with a FRESH browser subagent: `playwright-core` + `channel:'chrome'` (system
  Chrome, no download). Capture the migration log, live API responses, and UI renders.
- `POST /api/login` returns `access_token` (not `token`); the login route rate-limits
  ~10/5min — an exploratory verifier can lock itself out.
- The `gs://cake-pr-evidence` bucket DOES work for uploads (`pr-36`, `pr-42`) — it's the
  cake_os *scaffolding* that doesn't transfer, not the bucket.
- Never `docker compose up` CakeCRM's own compose on this machine (it maps 5432:5432 and
  collides with the shared container; never `docker compose down` the shared one).
- Clean up: kill the uvicorn, `DROP DATABASE cakecrm_ev<N>`.

**Evidence-partial for AI-gated / external-OAuth surfaces.** Provider keys are in-app,
encrypted, and unavailable to workers by design; the same applies to real bot tokens
(Telegram) and OAuth apps (Gmail). So:

1. Prove the keyless half LIVE: boot + migrations, zero-key graceful degradation (a core
   product rule — this half is real contract, not filler), non-breakage of touched pages,
   keyless lifecycles. Where the feature displays AI-produced state, seed it directly in
   the DB and verify the render/lifecycle.
2. Caveat the true AI/OAuth path in the evidence comment — "Not tested: no provider
   key/credentials headless by design" — and @-mention the human for the manual pass.
3. Label `ready-to-ship` + `evidence-posted`, end `OUTCOME: READY reason=evidence-partial`.

Blanket `evidence-infra-skip` under-verifies: half the feature's contract is still
provable. Skip only when the live boot itself genuinely fails.

**Origin:** 2026-07-25 `/auto-issues-team` run (PRs #39–#47); supersedes the harness half
of the older evidence notes in `.claude/coach-lessons.md` (the boot half there still
holds and points here).
