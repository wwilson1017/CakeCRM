---
title: Workspace settings
summary: Admin-only settings for your company name and logo, your team, custom fields, exporting your data, backups and restore, and AI tool connections.
shots: [settings-workspace]
---

The **Workspace** tab is for admins only. Members do not see it.

{{shot:settings-workspace}}

## Branding

- **Company name** — shown throughout the app.
- **Logo** — press **Upload logo**. PNG, JPEG, GIF or WebP, up to 2 MB after shrinking. Large logos are shrunk in the browser before upload; files that remain over the limit are rejected. SVG is not accepted. You can remove the logo from the same card.

Press **Save branding** to keep your changes.

There is no colour picker. CakeCRM has one fixed look, in light and dark. Light or dark is each person's own choice.

## Team

Everyone shares one CRM. There are two roles:

- **Admin** — can change how the install is set up: branding, team, custom fields, integrations, todo mode and Baker's protected memory files.
- **Member** — can do everything else, including working with every contact, company, deal and todo.

Records are the same for both. Owning a record is not a permission. See [Ownership](../04-contacts-and-companies/09-ownership.md).

What you can do here:

- **Add user** — email, name, password (at least 8 characters) and role.
- Change a person's role from the list beside their name.
- **Reset password** — set a new password for someone else. An optional box also clears their two-step sign-in, for someone who lost their authenticator. Use it only when needed.
- **Deactivate** — stop someone signing in. You can reactivate them later.

Accounts are never deleted, so a past colleague's name still shows on the records they owned. You cannot deactivate yourself, remove your own admin role, or demote or deactivate the last active admin. To change your own password, use [Personal settings](01-personal.md).

There is no seat limit.

## Custom fields

Pick **Contacts**, **Companies** or **Deals**, then **+ Add custom field**. Give it a name and a type: text, number, boolean, date or select. Deleting a field deletes its values everywhere, with no undo. See [Custom fields](../04-contacts-and-companies/10-custom-fields.md).

## Export data

**Download CSV export** on the Export data card downloads everything in the CRM as one ZIP, with a CSV file per table: companies, contacts, deals and their stage history, activity, todos and projects, notes, custom fields, saved views, Baker's memory, and a users file for looking up owner names. Archived deals are included. Passwords, two-factor secrets, API keys, connection tokens, note attachments and Baker conversations are never exported.

It is a snapshot, not a sync: download again for fresh numbers. Take one before a big change, such as clearing data or a large import.

## Backups

### Download a backup

Open Settings → Workspace → **Backups** and press **Download backup now**. CakeCRM makes a
full PostgreSQL custom-format dump and checks it with `pg_restore --list` before offering
it. This includes attachments, accounts, credentials and conversations. Treat it as sensitive.
Only admins can use this card or download backups. One backup is made at a time; if one is
already running, try again in a minute. Browser downloads buffer the file in memory, so use
the bucket copy for large databases.

### Daily off-site copy

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

### Optional passphrase

Set a backup passphrase (at least 8 characters) to encrypt downloads and uploads in the
standard age format, with a `.dump.age` filename. Without one, backups work as plain `.dump`
files. If you lose this passphrase, your backups cannot be opened. CakeCRM cannot recover it.
Changing or removing it affects future files only; keep older passphrases for older files.
If the install's encryption key changes, re-enter the backup passphrase before making
another encrypted backup; CakeCRM will not silently make a plain backup instead.

### Restore into a fresh database

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

## AI tool connections

**Members may connect their own AI tools** is off until you turn it on. Admins can always connect. Turning it off stops every member's connection straight away without deleting it, and turning it back on restores them.

The card lists every person's connected AI tools: what each one is, whether it signed in or uses a personal token, whether it is read-only, and when it was connected and last used. **Calls** shows a connection's log for the last 90 days, and **Revoke** ends it and takes it, with its log, off the list, so check **Calls** first. See [Personal settings](01-personal.md) for how people connect.
