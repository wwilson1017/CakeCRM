"""Gmail integration (issue #8) — read + create-draft ONLY.

There is no email-send path anywhere in this package or the wider codebase: the
assistant registry exposes exactly three tools (gmail_search, gmail_read_thread,
gmail_create_draft). Google's OAuth scopes cannot express "draft but never send"
(gmail.compose permits sending at the API level), so the guarantee is enforced at
the tool layer — see SECURITY.md and backend/tests/test_gmail_guard.py.

Google/googleapiclient SDKs and httpx are imported LAZILY inside functions only,
never at module top level, so a missing SDK or absent connection never breaks
import/startup (CI imports the app with no DATABASE_URL).
"""
