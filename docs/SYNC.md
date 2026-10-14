# Porting from cake_os

CakeCRM's CRM features are ported from the CAKE OS CRM, which is still actively developed.
Upstream changes are brought over by hand, as ordinary issues. This is how to do a port;
`SYNC_LEDGER.md` records what has already crossed.

**Read the source from the local cake_os clone (`~/ai/cake_os`), never from an issue body.**
Fetch first — a stale clone has produced confidently wrong ports before.

## Both sides are PostgreSQL

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

## What actually makes a port judgment work, not a patch-apply

1. **Tenancy has to be stripped.** cake_os threads `user_email`/`owner_email` through roughly
   a quarter of its CRM functions; CakeCRM is single-user, so those parameters have nowhere to
   land. This is structural, not cosmetic.
2. **The module topology does not line up.** ~20 cake_os service modules collapse into
   CakeCRM's single `backend/crm/service.py` (plus a handful of siblings). "Same filename
   exists" is a *candidate*, never a destination.
3. **Other apps leak in.** `apps.todo_gtd` and `apps.dimm` appear across several CRM modules,
   so those call sites need removing or adapting on every port. Do **not** assume there is
   nothing to map onto: CakeCRM landed a todo/GTD todo mode of its own in #70, so a
   `todo_gtd` call site may now have a real counterpart here rather than needing deletion.
   Check the current tree before stripping.
4. **Some CakeCRM ports deliberately fixed upstream bugs.** A faithful re-apply can silently
   reintroduce one. Check the target file's comments before overwriting behavior.
5. **Upstream has tests, and they do not come across as-is.** cake_os's CRM is covered by ~22
   test files that import `apps.crm`; they assume its tenancy and module layout. Port the
   *intent*, and write the test against CakeCRM's shape.
6. **Scrub before you commit.** Upstream source contains company-domain email literals and
   staff names, including in test fixtures. Nothing from a port may carry them —
   `backend/tests/test_prompt_genericization.py` enforces this for model-facing text, but the
   rest is on you.

## Finishing a port

Run the normal gates (`ruff check .` → import check → `pytest -q` from `backend/`;
`npm ci` → `npm run build` → `npm run lint` → `npm test` from `frontend/`), add a
`SYNC_LEDGER.md` row, update the Source Map (`docs/agents/source-map.md`) if the architecture moved, and open a PR
that closes the port issue.
