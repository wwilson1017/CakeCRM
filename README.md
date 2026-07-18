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
- Single-binary simplicity: SQLite storage, one required env var, one-click Railway
  deploy or `python run.py` locally

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

## Lineage

CakeCRM is built from two proven codebases by the same author: the agent platform
from [Chatty](https://github.com/WWilson1017/chatty) and the CRM feature set
battle-tested inside a real bakery's internal operations platform. New features land
there first, get daily production use, and flow here through an AI-assisted sync.

## License

TBD before public launch (AGPL-3.0 planned).
