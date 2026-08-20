# 🍰 CakeCRM

**A free, open-source, self-hostable CRM with an AI sales assistant built in.**

No SaaS fees, no per-seat pricing, no vendor lock-in. The CRM is fully usable with
zero AI configuration — add any AI provider API key (Anthropic, OpenAI, Google
Gemini, Together AI, or local models via Ollama) and it comes with a sales assistant
that works your pipeline for you.

> **Status: pre-v0.1, under active construction.** The build is spec'd as GitHub
> issues in this repo and being assembled in phases. Star/watch for the launch.

## What it will do

**The CRM** (works with no AI at all):
- Contacts, companies, deals, and tasks with an activity timeline
- Kanban pipeline with drag-and-drop stages and per-stage value totals
- CSV import, custom fields, lead scoring, analytics dashboard
- PostgreSQL storage — the Railway template provisions it automatically; locally
  `python run.py` starts it via Docker Compose. Built to grow into multi-user

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

### Passwords

`AUTH_PASSWORD` is only the **initial** password. Change it in the app at
**Settings → Change password**; the new one is stored (bcrypt-hashed) in the
database, and from then on `AUTH_PASSWORD` is ignored — editing it later won't
change how you sign in, and won't override your password on the next restart.
If two-factor authentication is on, changing the password also asks for a code
and signs your other devices out of their trusted-device status.

**Locked out?** If someone forgets the password they set in-app, the operator can
reset it without database access:

1. Set `AUTH_PASSWORD_RESET` to a new password and restart (on Railway, add the
   variable — the redeploy is the restart).
2. Sign in with that password. The logs will carry a warning that the reset ran.
3. **Remove `AUTH_PASSWORD_RESET` and restart again.** While it is set, every
   restart re-applies it, so a password changed in the app won't survive one.

## Lineage

CakeCRM is built from two proven codebases by the same author: the agent platform
from [Chatty](https://github.com/WWilson1017/chatty) and the CRM feature set
battle-tested inside a real bakery's internal operations platform. New features land
there first, get daily production use, and flow here through an AI-assisted sync.

## License

[AGPL-3.0](LICENSE) — free to use, self-host, and modify. If you host a modified
version as a service, you share your modifications. Contributions are accepted
under the DCO (`Signed-off-by`).
