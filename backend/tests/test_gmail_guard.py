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

# The Gmail package may not reference `.send` at all — not even as a bare attribute
# (`sender = svc.users().messages().send; sender(...)` aliases past a `.send(` check).
_GMAIL_SEND = re.compile(r"\.send\b")


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


def test_gmail_package_has_no_send_reference():
    gmail_dir = BACKEND / "gmail"
    offenders = []
    for path in gmail_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in _GMAIL_SEND.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.relative_to(BACKEND)}:{line}")
    assert not offenders, (
        "backend/gmail/ references `.send` (call OR bare attribute) — no send is "
        "permitted, and aliasing must not slip past:\n" + "\n".join(offenders)
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


def test_gmail_read_tools_are_in_the_engine_taint_set():
    """Coupling guard: every Gmail READ tool must be in the engine's
    _UNTRUSTED_SOURCE_TOOLS taint set. Otherwise a future attacker-controlled read
    (e.g. a message/attachment reader) added to the defs without wiring the taint
    would silently lose the prompt-injection power→normal downgrade AND the
    nonce-fence wrapping — with no failing test to warn the author (the taint set is
    a hand-maintained literal two modules away)."""
    from assistant.engine import _UNTRUSTED_SOURCE_TOOLS
    from gmail.tools import GMAIL_TOOL_DEFS

    reads = {d["name"] for d in GMAIL_TOOL_DEFS if not d["writes"]}
    missing = reads - _UNTRUSTED_SOURCE_TOOLS
    assert not missing, (
        "Gmail read tools missing from engine._UNTRUSTED_SOURCE_TOOLS — they would "
        f"produce un-tainted, un-fenced untrusted content: {missing}"
    )


def test_gmail_reads_are_not_background_callable(monkeypatch):
    """End-to-end pin for #114, on a REAL registry with Gmail connected.

    The Gmail reads carry writes:False, so the background allowlist — derived from that
    flag alone — used to admit them into every unattended turn (heartbeat, reminder
    firing, the proactive digest). The one notification such a turn may send then became
    an exfiltration channel: injected text in a reminder or CRM record could steer it
    gmail_search → gmail_read_thread → private mail in the notification body.

    Only the READ tools are asserted: gmail_create_draft is already excluded as a write,
    so including it would let this pass while the reads leaked."""
    from assistant.background import background_allowlist
    from assistant.registry import ToolRegistry
    from gmail import tools as gmail_tools

    monkeypatch.setattr(gmail_tools.store, "is_connected", lambda: True)
    reg = ToolRegistry(background=True)

    reads = {d["name"] for d in gmail_tools.GMAIL_TOOL_DEFS if not d["writes"]}
    # Prove the registry really carries them, or the disjointness below is vacuous.
    assert reads and reads <= reg.writes_map.keys(), (
        "Gmail tools were not registered — this guard would pass without testing anything"
    )
    leaked = reads & set(background_allowlist(reg))
    assert not leaked, f"Gmail read tools reachable from an unattended turn: {leaked}"


def test_gmail_write_tools_are_in_the_engine_connection_binding_set():
    """Coupling guard, mirroring the taint-set guard above: every Gmail WRITE tool
    must be in the engine's _CONNECTION_BOUND_WRITE_TOOLS. That set is a
    hand-maintained literal two modules away, so renaming or adding a Gmail write
    without updating it would silently drop the propose-time connection binding
    (#43) — a draft approved after the admin switches Google accounts would once
    again be created in the new account, with no failing test to warn the author."""
    from assistant.engine import _CONNECTION_BOUND_WRITE_TOOLS
    from gmail.tools import GMAIL_TOOL_DEFS

    writes = {d["name"] for d in GMAIL_TOOL_DEFS if d["writes"]}
    assert writes == _CONNECTION_BOUND_WRITE_TOOLS, (
        "Gmail write tools and engine._CONNECTION_BOUND_WRITE_TOOLS have drifted — "
        f"writes={writes} bound={_CONNECTION_BOUND_WRITE_TOOLS}"
    )


def test_client_op_allowlist_excludes_send():
    from gmail import client, ops

    assert client._APPROVED_OPS == {
        ops.list_messages_op,
        ops.get_thread_op,
        ops.create_draft_op,
        ops.get_profile_op,
    }
    # ops exposes no send/reply operation.
    assert not any(hasattr(ops, n) for n in ("send_email_op", "reply_to_email_op"))
