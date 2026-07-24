"""CakeCRM — the built-in AI assistant.

A single built-in assistant (no roster, no onboarding wizard): an SSE streaming
chat loop over the AIProvider ABC, a thin registry over the CRM tools, write-tool
confirmation modes (read-only / normal / power), file uploads, and a Postgres
conversation history. Ported and slimmed from Chatty's ``core/agents/``.

Nothing here touches Postgres or a provider SDK at import time — the FastAPI app
imports ``assistant.router`` with no DATABASE_URL during the CI import check.
"""
