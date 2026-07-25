"""THE enforcement test for the hard product rule (issue #8): CakeCRM can read
email and create drafts, but there is NO send path anywhere in the runtime source.

Because Google's `gmail.compose` scope technically permits sending, this guarantee
cannot be enforced by OAuth scopes — it is enforced at the tool layer (no send op/
executor/def exists) and pinned here so CI fails the instant a send surface is
introduced. See SECURITY.md.
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# Runtime source only — exclude tests (this file names the forbidden patterns) and
# the virtualenv (third-party libs legitimately contain .send()).
_EXCLUDE_DIRS = {"tests", ".venv", "migrations", "__pycache__"}

# Send-surface patterns that must NEVER appear in runtime source.
_FORBIDDEN = [
    (re.compile(r"messages\(\)\s*\.\s*send\s*\("), "Gmail messages().send() call"),
    (re.compile(r"drafts\(\)\s*\.\s*send\s*\("), "Gmail drafts().send() call"),
    (re.compile(r"messages/send"), "raw Gmail /messages/send REST path"),
    (re.compile(r"drafts/[^\s\"']*/send"), "raw Gmail /drafts/.../send REST path"),
    (re.compile(r"\bsend_email\b"), "send_email identifier"),
    (re.compile(r"\breply_to_email\b"), "reply_to_email identifier"),
    (re.compile(r"gmail\.send"), "gmail.send scope"),
    (re.compile(r"gmail\.modify"), "gmail.modify scope"),
    (re.compile(r"getattr\([^)]*['\"]send['\"]"), "dynamic getattr(..., 'send') access"),
]

# The Gmail package additionally may not contain a bare `.send(` at all.
_GMAIL_SEND = re.compile(r"\.send\s*\(")


def _runtime_py_files():
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        yield path


def test_no_send_surface_in_runtime_source():
    offenders = []
    for path in _runtime_py_files():
        text = path.read_text(encoding="utf-8")
        for pattern, label in _FORBIDDEN:
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(BACKEND)}:{line} — {label}")
    assert not offenders, (
        "A Gmail SEND surface was found in runtime source. CakeCRM must never send "
        "email (see SECURITY.md):\n" + "\n".join(offenders)
    )


def test_gmail_package_has_no_bare_send_call():
    gmail_dir = BACKEND / "gmail"
    offenders = []
    for path in gmail_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in _GMAIL_SEND.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.relative_to(BACKEND)}:{line}")
    assert not offenders, (
        "backend/gmail/ contains a `.send(` call — no send is permitted:\n"
        + "\n".join(offenders)
    )


def test_gmail_tool_surface_is_exactly_three():
    from gmail.tools import GMAIL_TOOL_DEFS, GMAIL_TOOL_EXECUTORS

    names = {d["name"] for d in GMAIL_TOOL_DEFS}
    assert names == {"gmail_search", "gmail_read_thread", "gmail_create_draft"}
    assert set(GMAIL_TOOL_EXECUTORS) == names
    # No tool name may imply sending or replying.
    for n in names:
        assert "send" not in n and "reply" not in n, f"tool name implies send/reply: {n}"
    # Exactly one write (the draft); the two reads are not writes.
    writes = {d["name"]: d["writes"] for d in GMAIL_TOOL_DEFS}
    assert writes == {
        "gmail_search": False,
        "gmail_read_thread": False,
        "gmail_create_draft": True,
    }
    # Every def carries an explicit writes flag.
    assert all("writes" in d for d in GMAIL_TOOL_DEFS)


def test_scopes_are_minimal():
    from gmail import oauth

    joined = " ".join(oauth.SCOPES)
    assert oauth.GMAIL_READONLY_SCOPE in oauth.SCOPES
    assert oauth.GMAIL_COMPOSE_SCOPE in oauth.SCOPES
    assert "gmail.send" not in joined
    assert "gmail.modify" not in joined
    # Exactly the two Gmail scopes — no identity scopes, nothing broader.
    assert set(oauth.SCOPES) == {oauth.GMAIL_READONLY_SCOPE, oauth.GMAIL_COMPOSE_SCOPE}


def test_client_op_allowlist_excludes_send():
    from gmail import client, ops

    assert client._APPROVED_OPS == {
        ops.list_messages_op,
        ops.get_thread_op,
        ops.create_draft_op,
        ops._get_profile_op,
    }
    # ops exposes no send/reply operation.
    assert not any(hasattr(ops, n) for n in ("send_email_op", "reply_to_email_op"))
