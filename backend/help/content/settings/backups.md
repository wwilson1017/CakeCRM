---
title: Backups and restore
description: Download a restorable database backup, copy it daily to your own S3-compatible bucket, and optionally encrypt it. Admin only.
aliases: backup, backups, restore, pg_dump, pg_restore, off-site copy, s3, backup passphrase
admin: true
---
## Download a backup

Open Settings → Workspace → **Backups** and press **Download backup now**. CakeCRM makes a
full PostgreSQL custom-format dump and checks it with `pg_restore --list` before offering
it. This includes attachments, accounts, credentials and conversations. Treat it as sensitive.
Only admins can use this card or download backups. One backup is made at a time; if one is
already running, try again in a minute. Browser downloads buffer the file in memory, so use
the bucket copy for large databases.

## Daily off-site copy

Enter the endpoint URL, bucket, region, access key ID, secret key and optional path prefix
for your own S3-compatible storage. The region defaults to `us-east-1`; for R2 use `auto`.
Use a bucket or prefix dedicated to this install: CakeCRM prunes its dated backup files
there, keeping the newest number you choose (30 by default, between 1 and 365).
Credentials need permission to list the bucket, write and delete objects, and perform and
abort multipart uploads. Use HTTPS except for local testing.

Press **Save destination**, then **Test connection**. The test writes, lists and removes a
small temporary object using the saved settings. Leave the secret field blank when editing
to keep the saved secret. Saving enables automatic copies: the first is due on the next
five-minute check, then about every 24 hours while the app is running. Failures retry hourly.
The card shows the last successful upload and its size, plus the last error. Admins see a
banner if no upload has succeeded for more than two days. A pruning failure is shown even
when the upload itself succeeded.

**Remove destination** stops future uploads; it does not delete existing files in the
bucket. Clearing CRM data keeps backup settings and history. Changing the bucket, endpoint,
region or prefix starts a new backup history. Downloads do not count as off-site uploads.

## Optional passphrase

Set a backup passphrase (at least 8 characters) to encrypt downloads and uploads in the
standard age format, with a `.dump.age` filename. Without one, backups work as plain `.dump`
files. If you lose this passphrase, your backups cannot be opened. CakeCRM cannot recover it.
Changing or removing it affects future files only; keep older passphrases for older files.
If the install's encryption key changes, re-enter the backup passphrase before making
another encrypted backup; CakeCRM will not silently make a plain backup instead.

## Restore into a fresh database

Install the standard `age` command if the backup is encrypted, and PostgreSQL client tools
at least as new as the client that made the dump (the Docker image uses version 18). Restore
into a PostgreSQL server of the same major version or newer. Keep the app stopped while
restoring, then set CakeCRM’s `DATABASE_URL` to the restored database before starting it. Use the same CakeCRM
version that made the backup, or a newer one so its migrations can run.

For an encrypted file, decrypt first; `age` prompts for the backup passphrase:

```sh
age -d -o backup.dump cakecrm-20261123T010000Z.dump.age
```

For a plain file, use its `.dump` filename below. Set `PGHOST`, `PGPORT`, `PGUSER` and the
other usual PostgreSQL connection options for your fresh server. Create a new empty database
and restore it (replace `cakecrmrestored` with your database name):

```sh
createdb cakecrmrestored
pg_restore --exit-on-error --no-owner --no-privileges -d cakecrmrestored backup.dump
```

There is no restore button inside CakeCRM. **ENCRYPTION_KEY is an environment variable and
is not in the dump.** When it is unset, CakeCRM can keep the key in the OS keychain or in
`backend/data/.encryption-key`; keep the resolved key somewhere safe alongside the backups.
The backup passphrase is separate from this install key. Restoring with a different
ENCRYPTION_KEY keeps all CRM data, but you must re-enter the AI key, connections, backup
bucket secret and backup passphrase. The branding logo file under `backend/data/` is also
outside the dump; keep it separately or upload it again after restoring.
