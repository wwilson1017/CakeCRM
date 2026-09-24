# <img src="frontend/public/logo-mark.svg" alt="" height="32" align="absmiddle"> CakeCRM

**A free, open-source, self-hostable CRM with an AI sales assistant built in.**

No SaaS fees, no per-seat pricing, no vendor lock-in. The CRM is fully usable with
zero AI configuration — add any AI provider API key (Anthropic, OpenAI, Google
Gemini, Together AI, or local models via Ollama) and it comes with a sales assistant
that works your pipeline for you.

**Website:** [mycakecrm.com](https://mycakecrm.com) — the short version, with screenshots.

> **Status: pre-v0.1, under active construction.** The build is spec'd as GitHub
> issues in this repo and being assembled in phases. Star/watch for the launch.

## What it will do

**The CRM** (works with no AI at all):
- Contacts, companies, deals, and todos with an activity timeline
- Kanban pipeline with drag-and-drop stages and per-stage value totals
- Filter any list or board by preset or custom from/to date range, and save the whole view — filters, search, sort and layout — as a named view the team can apply
- CSV import, custom fields, lead scoring, analytics dashboard
- Accounts for your whole team: admin/member roles, an owner on every record, and
  "Mine vs Everyone" filters on the lists, the board and the dashboard
- PostgreSQL storage — the Railway template provisions it automatically; locally
  `python run.py` starts it via Docker Compose

**The assistant** (bring any AI API key):
- Chats in a context-aware drawer inside the CRM — it knows which deal or contact
  you have open
- Full CRM tool access with write confirmations: search, create, update, log
  activities, move deals
- **Talk to your CRM from your phone via Telegram**
- Proactive heartbeat: stale-deal nudges, untouched-lead alerts, daily pipeline
  digest — delivered by push notification or Telegram
- **Gmail, read + draft only**: drafts client emails into your Drafts folder for
  you to review and send; scans your inbox (read-only) to log email touches to
  contact timelines automatically. There is no send capability anywhere in the
  codebase — by design.
- Memory that persists across conversations

## Deploy to Railway

[![Deploy on Railway](docs/railway-deploy-button.svg)](https://railway.com/deploy/cakecrm?referralCode=HMgK-M)

1. Click the button and set your `AUTH_PASSWORD` — the only input you provide
2. Railway provisions PostgreSQL and deploys CakeCRM automatically
3. Open your CakeCRM URL, log in, and optionally add an AI provider key in-app
   (the CRM is fully usable without one)

`DATABASE_URL` is injected by the template's Postgres service; `JWT_SECRET` and
`ENCRYPTION_KEY` auto-generate. Prefer your own hardware? `python run.py` runs
everything locally, with Postgres via Docker Compose.

### Accounts

You sign in with an **email and a password**. On first start CakeCRM creates one
admin account for you: the address is `ADMIN_EMAIL` (default
`admin@cakecrm.local`) and the password is `AUTH_PASSWORD`. Both are printed to the
logs on that first boot, so check them if you are not sure what was used.

Upgrading an install that predates accounts? Nothing is lost. Your existing
password keeps working — sign in with it and the bootstrap email above. Your
two-factor setup and trusted devices carry over, and every record you already had
becomes yours.

**Take a backup first.** This upgrade changes the two-factor tables, so it is a
one-way door: going back to an older CakeCRM build means restoring a pre-upgrade
`pg_dump`, not just redeploying the old image.

Add the rest of your team at **Settings → Workspace → Team**. Two roles:

- **Member** — the whole CRM: records, pipeline, todos, notes, import, and the
  assistant.
- **Admin** — all of that, plus managing users, AI keys, branding, integrations and
  the destructive operations (clear-all, sample data, backfills). Two things the
  assistant does are admin-only too: using the connected Gmail mailbox (unless the
  install shares it — see below) and changing the assistant's own `soul.md` and
  `MEMORY.md`, which every seat can still read. That second one holds on both paths —
  the Memory page refuses the save, and the assistant refuses the rewrite — because
  those two files are standing instructions replayed in every later conversation. Every
  other file the assistant keeps is open to every seat.

**Ownership is not a permission.** Every contact, company, deal and todo can carry
an owner, which drives the "Mine" filters and the per-rep numbers — but any member
can still see and edit anything. There are no per-record permissions, deliberately.

**What is per-seat, and what the team shares.** Your chat history with the assistant is
yours alone — owner-only, with no admin override. So are your notifications and your
Telegram link. What the whole team shares is the assistant's **memory**: the facts it
records and the context files it keeps (`soul.md`, `MEMORY.md`, topic notes) are one
shared brain, deliberately — per-seat memory would make the assistant amnesiac for
every new person. System alerts and the AI provider keys are install-wide too.

Say the cost of that plainly: **memory is team memory.** A fact the assistant records
out of your private conversation becomes visible to every seat through the Memory page
and through the assistant itself. Don't ask it to remember something your colleagues
must not see.

The **Gmail connection** is install-wide but its access is not: only admin seats can
have the assistant search the connected mailbox or draft from it, and a member's
assistant is never shown those tools. An admin can switch on *"share the connected
mailbox with all seats"* under **Settings → Integrations → Gmail** for teams that
genuinely work one shared inbox; it is off by default. Per-user Gmail, where everyone
connects their own account, is tracked as [issue #189](https://github.com/wwilson1017/CakeCRM/issues/189) — say so there if you want it.

**Notifications carry a recipient.** Each one is either addressed to a single seat —
visible and dismissible only by them, and pushed only to their browsers and their own
Telegram chat — or sent to everyone, which is what the daily digest and a nudge about
an unowned record do.

**Telegram is per-seat.** One bot serves the whole workspace: an admin connects it once
under **Settings → Integrations → Telegram**, and then everyone links their own phone
from **Settings → Personal → Link my Telegram**. Your link code is yours alone — it
binds Telegram to *your* account, so anything the assistant changes from your phone is
recorded as you, and one chat can only ever speak for one seat.

> **Upgrading from a version before per-seat Telegram?** The old install-wide binding
> stored a Telegram account id with no way to tell which CakeCRM seat it belonged to, so
> nothing is guessed: whoever was linked simply links again from **Settings → Personal →
> Link my Telegram**. It takes about twenty seconds, and the bot itself does not need to
> be reconnected.

### Passwords

`AUTH_PASSWORD` is only the **initial** password for that first admin. Change it in
the app at **Settings → Personal → Change password**; the new one is stored (bcrypt-hashed) in
the database, and from then on `AUTH_PASSWORD` is ignored — editing it later won't
change how you sign in, and won't override your password on the next restart.
If two-factor authentication is on, changing the password also asks for a code.

Forgot a **member's** password? An admin resets it at **Settings → Workspace → Team →
Reset password**, then tells them the new one. There is no email-link reset, because a
self-hosted CakeCRM has no mail server to send one.

**Changing your password signs out every other device immediately** — the tab you
changed it in stays signed in. Any trusted-device status for two-factor auth is
cleared too, so other devices re-do 2FA at their next sign-in.

**Locked out of the admin account?** The operator can rescue it without database
access:

1. Set `AUTH_PASSWORD_RESET` to a new password and restart (on Railway, add the
   variable — the redeploy is the restart).
2. Sign in as the admin with that password. The logs will carry a warning that the
   reset ran.
3. **Remove `AUTH_PASSWORD_RESET` and restart again.** While it is set, every
   restart re-applies it, so a password changed in the app won't survive one.

The lever targets the first admin account, re-activates it if it was deactivated,
and **turns that account's two-factor authentication off** — a password-only reset
is no help to someone who also lost their authenticator. Turn 2FA back on from
Settings once you are in.

## Lineage

CakeCRM is built from two proven codebases by the same author: the agent platform
from [Chatty](https://github.com/WWilson1017/chatty) and the CRM feature set
battle-tested inside a real bakery's internal operations platform. New features land
there first, get daily production use, and flow here through an AI-assisted sync.

## License

[AGPL-3.0](LICENSE) — free to use, self-host, and modify. If you host a modified
version as a service, you share your modifications. Contributions are accepted
under the DCO (`Signed-off-by`).
