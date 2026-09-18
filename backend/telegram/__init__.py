"""Telegram integration — talk to the CakeCRM assistant from your phone (issue #7).

Ported and radically simplified from Chatty's ``integrations/telegram/`` to CakeCRM's
single-assistant model:

- ONE bot for the install (a ``telegram_settings`` singleton holding the Fernet-encrypted
  token, the username and the poll cursor), serving N private chats: since #193 each seat
  has its own row in ``telegram_links`` — its own link code, chat binding, assistant
  conversation and pending-confirmation batch — so a Telegram turn runs as the person
  holding the phone and its writes are attributed to them.
- Full chat with the built-in assistant, including CRM tool calls with **write
  confirmations** surfaced as Telegram inline-keyboard Approve/Deny buttons.
- Long-polling only (no webhooks): a single main-loop asyncio task offloads the
  blocking ``getUpdates`` via ``asyncio.to_thread`` and drives the assistant's async
  ``engine.chat`` loop on the SAME event loop as the SSE endpoint (so the provider's
  loop-bound cached client stays consistent). One update at a time — strictly
  sequential, which is exactly right for one conversation with ``UNIQUE(conversation_id, seq)``.
- Outbound: ``service.notify_user_telegram(user_id, text)`` and
  ``service.broadcast_telegram(text)`` — pure-sync send paths the #6
  heartbeat/notifications module calls through ``notifications.delivery._send_telegram``
  to deliver a notification to one seat or to everyone.

Every module is importable without side effects: httpx and provider SDKs are imported
lazily inside functions (repo convention), no DB work happens at import, and the poller
is started explicitly from the app lifespan — never on import.
"""
