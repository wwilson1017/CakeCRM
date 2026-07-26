"""Telegram integration — talk to the CakeCRM assistant from your phone (issue #7).

Ported and radically simplified from Chatty's ``integrations/telegram/`` to CakeCRM's
single-assistant, single-user model:

- ONE bot, ONE linked Telegram user (a ``telegram_settings`` singleton), Fernet-
  encrypted bot token.
- Full chat with the built-in assistant, including CRM tool calls with **write
  confirmations** surfaced as Telegram inline-keyboard Approve/Deny buttons.
- Long-polling only (no webhooks): a single main-loop asyncio task offloads the
  blocking ``getUpdates`` via ``asyncio.to_thread`` and drives the assistant's async
  ``engine.chat`` loop on the SAME event loop as the SSE endpoint (so the provider's
  loop-bound cached client stays consistent). One update at a time — strictly
  sequential, which is exactly right for one conversation with ``UNIQUE(conversation_id, seq)``.
- Outbound: ``service.notify_linked_user(text)`` — a pure-sync send path the #6
  heartbeat/notifications module calls to deliver messages.

Every module is importable without side effects: httpx and provider SDKs are imported
lazily inside functions (repo convention), no DB work happens at import, and the poller
is started explicitly from the app lifespan — never on import.
"""
