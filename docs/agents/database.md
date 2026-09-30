# Database, secrets and the login credential

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **One database: PostgreSQL, and it's mandatory** — the backend refuses to start
  without `DATABASE_URL` (decided 2026-07-18; single engine, ready for multi-user
  growth). Locally `docker compose up -d`; on Railway the template provisions
  Postgres and injects `DATABASE_URL`. No Redis or other external services.
  **Where a FILE can live, stated once because two places in this repo used to imply
  different answers:** the Railway container filesystem is ephemeral and is replaced on
  every redeploy — EXCEPT `/app/backend/data`, which `railway.json` requires as a mounted
  volume (`requiredMountPath`), so a deploy without one does not start. That is the
  directory `backend/data/` resolves to (Dockerfile `WORKDIR /app` + `COPY backend/
  ./backend/`), and it is why the branding logo, the `.encryption-key` fallback and
  (since #222) the `.jwt-secret` fallback persist today. So "Railway filesystems are ephemeral" (the `assistant_context_files` migration
  header) and "the volume is real" (#57's) are both true and are not in conflict. Anything
  written OUTSIDE `backend/data/` is gone on the next deploy. New durable state should
  still default to a Postgres row — one store, one transaction, one `pg_dump` — and #57
  put attachment bytes there for exactly that reason even though the volume would have
  held them.
  Required env vars: `AUTH_PASSWORD` + `DATABASE_URL`; `ADMIN_EMAIL`/`ADMIN_NAME`
  seed the first admin's identity; `JWT_SECRET` and `ENCRYPTION_KEY` auto-generate
  — and since #222 both resolve through ONE ladder (`core/secret_store.PersistedSecret`:
  env var → OS keychain → a 0600 file under `backend/data/` → generate once and store),
  so an install that sets neither keeps the same signing key and the same Fernet key
  across restarts instead of signing every seat out on each deploy. **The JWT secret
  opts OUT of the keychain rung** (`use_keychain=False`) and that is the only rung the
  two do not share: a keychain entry is scoped to the OS ACCOUNT, so two checkouts under
  one login would sign with the same key and each accept the other's tokens — a fresh
  install seeds admin id 1 at epoch 0, so a token from one is an admin session on the
  other. The file under `backend/data/` is install-local by construction. The encryption
  key KEEPS the rung unchanged: sharing a Fernet key between two of your own checkouts
  grants nobody a session, and renaming its keychain account to scope it would make every
  existing install generate a new key and orphan every encrypted credential. `jwt_secret_is_auto`
  still means "the operator did not supply one" and the startup warning still says to pin
  it; the two secrets differ in exactly one constructor argument, `ephemeral_fallback`
  — the JWT secret boots on a process-local value (loudly) when the volume cannot be
  written, the encryption key raises instead, because a Fernet key that dies at restart
  would encrypt new credentials into ciphertext nobody can read back. An existing but
  UNREADABLE secret file is never silently replaced; only one whose contents fail
  validation is. The file is claimed with `O_CREAT|O_EXCL` at mode 0600, so two processes
  booting together converge on one value rather than each caching its own.
  **The login credential is DB-backed** (#78): the
  `auth_credential` singleton holds a bcrypt hash the logged-in user changes from
  `/crm/settings`, and `core.auth.verify_password()` resolves DB-hash-first, falling
  back to `AUTH_PASSWORD` only while that hash IS NULL — so the env var is a
  *bootstrap* value that goes inert once the user sets their own password, and can
  never silently override it on a later boot. Every credential check in the app routes
  through that one function (login, the three 2FA confirmation endpoints, and
  change-password's pre-check), so the resolution order has exactly one definition.
  `POST /api/auth/change-password` verifies the current password and writes the new
  hash in ONE `SELECT … FOR UPDATE` transaction (`set_password`); the migration
  **seeds** the singleton row with a NULL hash so that lock always has a row to hold
  — locking an absent row is a no-op, which would let two concurrent first-time
  changes both pass. The endpoint also runs a **non-consuming `verify_password`
  pre-check before the 2FA code**, because verifying a code spends it (`verify_totp_code`
  burns the timeslot, `consume_backup_code` destroys a single-use code) and a typo in the
  current-password field must not cost the user a recovery code; `set_password`'s locked
  re-check stays authoritative. It answers a wrong current password with **400, not 401**,
  because the frontend `api()` wrapper treats every 401 as an expired session and
  ejects the user to `/login`. On success it revokes trusted 2FA devices and returns
  a fresh token. **Changing the password ends every other session immediately**:
  `auth_credential.token_epoch` is bumped in the *same statement* as the hash and
  stamped into every JWT as `pwd_epoch` (injected centrally in `create_access_token`,
  so all four mint sites carry it), and `get_current_user` rejects a token whose epoch
  is stale. The epoch is cached in-process — the deploy pins `gunicorn --workers 1` and
  the **happy path does zero DB reads**; it is loaded once in the lifespan. A *mismatch*
  re-reads before rejecting, so a stale cache (a multi-worker fork) self-heals instead of
  spuriously signing valid sessions out. A token predating the feature has no claim and
  reads as epoch 0 — deploying this signs nobody out; the first password change does.
  `AUTH_PASSWORD_RESET` bumps the epoch too (a rescue must end the sessions that may have
  caused the lockout). `AUTH_PASSWORD_RESET` is the operator's
  recovery lever, consumed in the lifespan right after `run_migrations()`: it
  overwrites the stored hash on **every** boot while set (a lever that disarms itself
  can only be pulled once) and logs a loud warning to remove it. Login **fails
  closed** — an unreadable credential is a 503, never a fallback to the env var.
  Schema is owned by `backend/migrations/*.sql`,
  applied automatically at startup in lexicographic order — name migrations
  `YYYYMMDDHHMMSS_<name>.sql` (use `date +%Y%m%d%H%M%S`), never sequential
  prefixes. Access Postgres through `core/postgres.py` helpers
  (`pg_fetchall`/`pg_fetchone`/`pg_execute`/`get_connection`/`row_to_dict`).

- **Never cap a reader whose `ORDER BY` isn't a TOTAL order** (#58). A `LIMIT`/`OFFSET`
  over a non-unique sort key has no defined result — Postgres may break the tie
  differently on each execution, so a row shows up on two pages or on none. End every
  such `ORDER BY` on a unique term: `id` (matching the preceding key's direction, so
  the tiebreak reads the way the sort does), or a column that is already UNIQUE
  (`assistant_context_files.filename`, `assistant_messages.seq` within one
  conversation), or — for a grouped reader — the rest of the GROUP BY key
  (`find_duplicate_deals` orders by `title` **and** `contact_id`, because the group is
  the pair). Ties are the normal case, not an edge: `created_at`/`updated_at` default
  to `now()`, which is **transaction-start** time, so every row written in one
  transaction is byte-identical — a CSV import, `seed_data`, `merge_deals`' note
  copies. Uncapped readers carry the term too, so adding a `LIMIT` later can't quietly
  reintroduce the bug — which is why #59, server-side pipeline pagination, was
  `Blocked by: #58`, and #59 has since cashed that promise in: `get_pipeline`'s keyset
  page is a real capped reader now, and its **default** read stays uncapped and stays in
  `test_hardened_uncapped_readers_keep_their_tiebreaker`.
  Enforced by `backend/tests/test_query_determinism.py`, which AST-scans every non-test
  backend module (it reads f-strings and implicitly-concatenated literals). An ORDER BY
  assembled at RUNTIME is reported as `unknown`, never waved through: the exact set is
  pinned in `UNDECIDABLE_SITES`, keyed by enclosing function, and each entry owes a
  behavioral test on the SQL that reader really emits — so a reader cannot opt out of
  the guard by moving its ordering into a variable. Expect to edit that registry when a
  reader starts or stops interpolating its ORDER BY (#59 and #77 both touch such
  readers); the failure message says which way it moved and what to do.
