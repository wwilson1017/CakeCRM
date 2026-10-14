# Reminders

> Topic doc. `AGENTS.md` keeps the enforceable invariants; this file is the implementation
> record. Add new implementation notes ("landed #N as …", design reasoning, divergences) HERE.

- **Landed #283 (phase 1, the engine)** as `backend/reminders/{recurrence,service,router,tools}.py`,
  migration `20261014035038_reminders.sql`, the `reminders_tick` scheduler job, the
  `/crm/reminders` page (`frontend/src/crm/{RemindersPage.tsx,remindersCollection.ts,components/ReminderForm.tsx}`),
  four Baker tools, a Today-panel tail and the help topic `reminders/overview`. Ported
  mechanics only from cake_os `56e19637b` + `e2a57c8d7`. Phase 2 (company/contact anchoring,
  the pipeline-card section, the dashboard band) is a later issue.

- **A reminder is data, not an agent job.** A row is who (`owner_id` + `people`), what
  (`note`), when (`due_date`, optional `due_time`, and `due_at`, the instant the service
  computes in `core.localtime.tz()`), and optionally how often (`repeat` daily / weekly /
  monthly / yearly, anchored on `anchor_date`). It is delivered verbatim with no model run,
  so it works with zero AI keys. #188 dropped the #6-era `reminders` table; this is a fresh
  shape under the same name (`test_integration_heartbeat_pg` proves both migrations ran).

- **`people` is an `INTEGER[]`, not a join table.** Users are deactivated, never deleted, so
  there is no cascade to want, and every reader needs the whole list. The service checks
  each id is an ACTIVE user on every write; delivery re-checks and skips a deactivated one.
  Empty people means the owner.

- **Status: `upcoming → fired` or `upcoming → voided`.** Nothing is deleted. A void needs a
  reason (DB CHECK `reminders_voided_needs_reason`). Only an upcoming row can be edited or
  voided. Any row can be copied with *Remind again* (a new one-time row with the same note
  and its still-active people).

- **Firing is claim-before-send, an AT-MOST-ONCE delivery attempt** (cake_os #3710). The
  60s `reminders_tick` (its own APScheduler job, because it delivers over the network) runs
  `service.fire_due`: one transaction selects due upcoming rows `FOR UPDATE SKIP LOCKED`,
  marks them fired and inserts each repeating row's successor; only after the commit does
  `deliver_notification` run per active person (bell + web push + Telegram when linked;
  never email). A crash between commit and send loses that one occurrence, chosen over a
  duplicate. A failure inside the transaction rolls back the claim and sends nothing.
  `FIRE_BATCH = 50` per tick; a backlog drains over the following ticks.

- **Recurrence is computed from the anchor, never from the previous fire**, so a monthly on
  the 31st lands on Feb 28 and comes back to Mar 31, and a Feb 29 yearly lands on Feb 28 in
  common years. The successor is the first occurrence strictly after `max(due_at, now)`: a
  series found late fires once and resumes at the next future time instead of catching up.
  DST keeps the wall-clock time. `uq_reminders_series_due` is the row-level backstop
  against a double spawn; the claim already prevents one. A series has exactly one
  upcoming row, so voiding it ends the series with no extra machinery.

- **When.** A date-only reminder fires at 09:00 install-local (`recurrence.DEFAULT_FIRE_TIME`,
  a marked simplification; a per-install setting is the upgrade path). A date before today
  is refused, and so is a TIMED instant already past; a date-only reminder for today is
  accepted at any hour (after 09:00 it fires on the next tick). The schedule is re-validated only when a
  patch touches the date or time, so a note edit on an overdue-but-unfired row works (the
  page sends only changed fields for the same reason).

- **Access.** `owner_id` is not access control: any member may read, edit or void any
  reminder, and no route is admin-only. *Mine* (page facet, `scope=mine`, the tools'
  default, the Today panel's owner scope) = I set it or I am one of its people.

- **Baker tools.** `reminder_create`, `reminder_update`, `reminder_void` are writes and NONE
  is routine (rule 2: they notify someone later), pinned by
  `test_confirm_tier.test_reminder_tools_are_never_routine`. `reminder_list` is a read, so
  it is in the background allowlist; its payload restates that reminder text is data. The
  owner and author are bound server-side; the model names people as 'me', an email or an
  exact name, resolved to active users only. The create executor takes explicit
  parameters so the model cannot reach the service's clock.

- **Today panel.** Reminders due today (upcoming or sent, never voided) join the unranked
  tail as `kind: 'reminder'` after brought-back todos and before the hot-deal tail. See
  `today-panel.md`.

- **Tests.** `test_reminder_recurrence.py` (pure maths), `test_reminders.py` (commit-before-send,
  rollback, validation), `test_reminders_router.py`, `test_reminder_tools.py`,
  `test_integration_reminders_pg.py` (two concurrent ticks deliver once, anchor spawn,
  late-skip, CHECKs, the scheduler seam); frontend `remindersCollection.test.ts`,
  `RemindersPage.test.tsx`, plus reminder cases in the Today panel tests.
