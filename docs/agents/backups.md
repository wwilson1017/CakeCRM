# Built-in database backups (#404)

`backend/backups/` owns verified pg_dump custom archives, optional streaming age encryption,
S3 SigV4 multipart uploads, retention and the singleton `backup_settings`. Six sync admin
routes live under `/api/backups`; Settings → Workspace visibility is in settingsSections.ts.
The scheduler checks every five minutes, uploads every 24 hours and retries failures hourly.
It needs no AI key and is never an assistant tool.

`backup_settings` is installation configuration, deliberately outside both `_truncate_all`
branches, like MCP credentials. It is also excluded from CSV exports. Secrets use
core.encryption (whose key comes from core.secret_store); public status exposes booleans.
Secret values are JSON-wrapped before encrypt_value so a literal enc:v1: prefix cannot bypass encryption.
Stored-but-unreadable secrets fail closed, including download passphrases. No passphrase
means a plain dump. Destination writes lock the singleton with SELECT FOR UPDATE; every
write increments config_version. Network work snapshots settings and records outcomes only
against its version. Endpoint/bucket/region/prefix changes reset health; credential and
retention edits preserve it. Upload success is saved before pruning, so failed retention
never erases evidence of a good upload.

Downloads and scheduled generation share a nonblocking process lock. Busy downloads return
409; a busy scheduled run records no attempt. Temp files live in private directories under
the system temp directory's cakecrm-backups folder; failed generation and completed
FileResponses clean them, and the next generation reaps directories older than a day (not an hour: a slow download keeps streaming its file after the lock is released).
The download is a bodyless GET, so it needs no request-limit row. FileResponse streams from
disk. The browser still uses authenticated res.blob(), like ExportCard: a few hundred MB is
the practical range; a short-lived signed link is the upgrade path for huge downloads.

Docker installs pg_dump/pg_restore 18 via PGDG (Railway's newest supported server is 18;
compose and integration tests use 16). libpq options and passwords go through the process
environment, not argv. `pg_restore --list` must pass before encryption or transfer.
The age v1 writer follows https://c2sp.org/age; two 64 KiB chunks bound payload memory.
Scrypt uses N=2^18 and approximately 256 MiB of memory. Runtime uses existing cryptography;
pyrage 1.4.0 is test-only and independently decrypts boundaries and integration backups.

The S3 client has no SDK: path-style HTTP(S) endpoints, signed requests, paginated lists,
multipart uploads with 64 MiB parts (larger only to stay within 10,000 parts for huge files),
and abort on errors. HTTP 200 completion documents containing Error are failures too.
Endpoints may include a proxy base path; bucket and prefix are separate fields. Retention only deletes
direct names matching cakecrm-<UTC stamp>.dump[.age] in the selected prefix. That prefix must
belong exclusively to this install. Connection tests use a random object and attempt
cleanup on failure. Provider lifecycle rules can reap multipart uploads abandoned by a
process crash. The service's destination/put_file boundary is the extension seam for any
future destination, but no others ship now.

Restore is manual: decrypt with age, createdb, pg_restore --exit-on-error --no-owner
--no-privileges. The environment's ENCRYPTION_KEY (or keychain/data-file fallback) and the
branding logo are outside the dump. Documented in help, README and the rebuilt manual.
After restoring with a different key, re-enter AI/connection and backup secrets.

## Builder verification (2026-11-23)

- Backend: `ruff check --config ruff.toml . ../scripts ../docs/manual` passed.
- Targeted pytest: `tests/test_backups_*.py`, `test_route_authz.py`, `test_crm_export.py`,
  `test_heartbeat.py`, `test_help_library.py`, `test_manual.py`,
  `test_prompt_genericization.py`, `test_query_determinism.py`: **400 passed**, three
  integration tests deselected (two new restore cases plus an existing selected marker).
  The full backend suite was not run, as requested.
- `test_backups_integration.py -m integration`: **2 passed** on PostgreSQL 16 with client
  16, using isolated fresh databases. Plain and encrypted archives restored with matching
  counts for every public table, including the migration ledger, and identical attachment
  bytes. Both CRM reset modes preserved backup configuration and the passphrase.
- Frontend: `npm run build` passed; `npm run lint` passed with seven existing React Refresh
  warnings in unchanged components. Node 22 vitest for `BackupsCard.test.tsx`,
  `settingsSections.test.ts`, `SettingsPage.test.tsx`, `CrmLayout.test.tsx`: **111 passed**.
- Independent interoperability: the real age v1.2.1 CLI decrypted the streaming writer's
  output using its passphrase prompt; all 256,000 plaintext bytes matched. Local MinIO
  accepted a 70 MiB upload spanning two parts, including an escaped space/plus/Unicode
  prefix; downloaded bytes matched, retention kept the newest backup and a foreign text
  object, and deletion emptied the prefix. The temporary MinIO container was stopped.
- Manual rebuilt; `git diff --check` passed. The genericization scanner was also explicitly
  applied to all 18 new unstaged files, since its normal git enumeration covers tracked
  files. No commits or staging. The Docker image itself was not built; PostgreSQL 18
  packaging and other S3 providers remain review/deploy checks.

Small implementation refinements to the plan:

- JSON envelopes before encryption prevent encrypt_value's already-encrypted shortcut from
  interpreting a user-entered `enc:v1:` prefix as ciphertext. Tests cover this literal value.
- Multipart parts grow beyond 64 MiB only when needed to respect S3's 10,000-part limit.
  Completion must return the expected XML document; HTTP 200 Error documents still fail.
- Endpoint proxy base paths are encoded and included in signing. A dedicated small
  BackupWarning component handles admin-only fetches, navigation refresh and five-minute
  polling, keeping the layout simple and the banner fresh in long-lived sessions.
- Passphrase PUT requires an explicit field, so an empty JSON object cannot disable
  encryption; explicit null removes it. Missing/unreadable stored secrets fail closed.
- Existing schema/export and help guards required classifying backup_settings as excluded
  credential configuration and pg_dump/pg_restore as external commands, respectively.
- The local virtual environment lacked runtime dependencies despite the brief's setup
  assumptions; the pinned requirements were installed there. Node 22's npm cache was kept
  inside frontend/node_modules because the default home cache is read-only.
