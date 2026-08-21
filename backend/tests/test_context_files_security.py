"""The #72 security model, pinned.

soul.md is a SELF-REWRITABLE document that loads UNFENCED into the static system prompt.
That is a real escalation over anything else the assistant can persist: injected text
that reaches `write_context_file('soul.md', ...)` becomes a permanent system instruction
replayed on every future turn, surviving conversation deletion. The approved mitigation
package is what keeps it safe, so each mitigation gets a test that fails loudly if a
later refactor removes it — several of them hold today only as a CONSEQUENCE of other
code (the background allowlist derives from writes_map), and a derivation is exactly the
kind of guarantee that silently stops holding.
"""

from assistant import engine, identity
from assistant.background import background_allowlist
from assistant.registry import ToolRegistry
from context_files import tools as context_tools

_WRITE_TOOLS = {"write_context_file", "delete_context_file", "append_daily_note"}
_READ_TOOLS = {
    "list_context_files", "read_context_file", "read_daily_note", "search_context_files",
}


# ── Mitigation 1: every write routes through the confirmation gate ────────────────

def test_context_writes_declare_writes_true():
    writes = {d["name"]: d["writes"] for d in context_tools.CONTEXT_FILE_TOOL_DEFS}
    for name in _WRITE_TOOLS:
        assert writes[name] is True, f"{name} must be a write tool"
    for name in _READ_TOOLS:
        assert writes[name] is False, f"{name} must not be a write tool"


def test_every_def_declares_a_boolean_writes_flag():
    missing = [
        d["name"] for d in context_tools.CONTEXT_FILE_TOOL_DEFS
        if not isinstance(d.get("writes"), bool)
    ]
    assert not missing, f"context tool defs missing a boolean 'writes' flag: {missing}"


# ── Mitigation 2: writes never reach an unattended background turn ────────────────

def test_context_writes_are_excluded_from_the_background_allowlist():
    """Holds today only because background_allowlist() derives from writes_map. Asserted
    explicitly so it cannot quietly stop holding: a background turn that could rewrite
    soul.md would turn one injected reminder into a permanent identity change."""
    allow = set(background_allowlist(ToolRegistry(background=True)))
    leaked = _WRITE_TOOLS & allow
    assert not leaked, f"context write tools reachable from a background turn: {leaked}"


def test_context_reads_are_background_callable():
    """The other half of the same rule — reads are keyless SQL and safe to run unattended."""
    allow = set(background_allowlist(ToolRegistry(background=True)))
    assert _READ_TOOLS <= allow


# ── Mitigation 3: protected-file writes ALWAYS confirm, power mode included ───────

def test_protected_writes_always_require_confirmation():
    """`writes: True` alone is NOT enough: engine power mode executes writes immediately
    unless the turn is already tainted. soul.md/MEMORY.md need a stronger rule."""
    for filename in ("soul.md", "MEMORY.md", "SOUL.MD", "  soul.md  "):
        assert context_tools.requires_confirmation("write_context_file", {"filename": filename})
        assert context_tools.requires_confirmation("delete_context_file", {"filename": filename})


def test_ordinary_files_use_the_normal_gate():
    assert not context_tools.requires_confirmation(
        "write_context_file", {"filename": "topics/pricing.md"}
    )
    assert not context_tools.requires_confirmation("crm_create_contact", {"name": "Dana"})


def test_requires_confirmation_fails_closed():
    """A missing, non-string or unparseable filename confirms. One needless click costs
    nothing; one missed confirmation costs Baker's identity."""
    for args in ({}, None, {"filename": None}, {"filename": 42}, {"filename": "../x.md"},
                 {"filename": ""}):
        assert context_tools.requires_confirmation("write_context_file", args)


def test_engine_consults_the_always_confirm_hook():
    """Pins the wiring, not just the predicate — the rule is worthless unmounted."""
    assert engine.context_file_tools is context_tools


# ── The fence ─────────────────────────────────────────────────────────────────────

def test_recorded_context_is_not_in_the_taint_set():
    """Deliberate: the taint set encodes THIRD-PARTY origin (Gmail, uploads). Baker
    reading its own notes must not kill power mode for the rest of the turn."""
    assert engine._RECORDED_CONTEXT_MARKER not in engine._UNTRUSTED_MARKERS


def test_recorded_context_is_excluded_from_user_text():
    """...but it must never be mistaken for something the user typed, or file content
    could choose which memories surface."""
    assert engine._RECORDED_CONTEXT_MARKER in engine._NON_USER_MARKERS
    fenced = [{"role": "user", "content": '<recorded_context id="ab12">notes</recorded_context id="ab12">'}]
    assert engine._last_user_text(fenced) is None


def test_context_reads_are_fenced_on_return():
    assert engine._CONTEXT_READ_TOOLS == {"read_context_file", "read_daily_note"}


# ── The prompt split ──────────────────────────────────────────────────────────────

def test_soul_is_static_and_knowledge_is_volatile():
    """The approved identity/knowledge split, and the cache correction that came with it:
    a nonce-fenced block MUST NOT enter the cached static half."""
    static, volatile = identity.build_system_prompt(
        {"name": "Baker", "personality": ""},
        soul="## Your soul\n\nI am helpful.",
        knowledge_context='<recorded_context id="deadbeef">notes</recorded_context id="deadbeef">',
    )
    assert "I am helpful." in static
    # Assert on the NONCE, not the tag name: the static CONTEXT_FILES_NOTE legitimately
    # names <recorded_context> while explaining it. The per-turn entropy is the thing
    # that must never reach the cached half.
    assert "deadbeef" not in static, "a per-turn nonce would re-key the cache every turn"
    assert "deadbeef" in volatile
    assert "notes" in volatile


def test_security_contracts_come_after_the_editable_soul():
    """Ordering is load-bearing: a self-rewritten soul may add to who Baker is, but must
    never sit after — and so appear to override — the tool and safety contracts."""
    static, _ = identity.build_system_prompt(
        {"name": "Baker", "personality": "custom"}, soul="## Your soul\n\nSOULTEXT",
    )
    assert static.index("SOULTEXT") < static.index(identity.CONFIRMATION_NOTE)
    assert static.index("SOULTEXT") < static.index(identity.SALES_GUIDE)
    assert static.index("SOULTEXT") < static.index(identity.CONTEXT_FILES_NOTE)


def test_static_half_is_byte_identical_for_identical_inputs():
    """The real cache invariant: no per-turn entropy in static. (It may legitimately
    differ when the soul or personality CHANGES — that is a deliberate edit.)"""
    args = ({"name": "Baker", "personality": ""},)
    kwargs = {"soul": "## Your soul\n\nstable", "knowledge_context": ""}
    first, _ = identity.build_system_prompt(*args, **kwargs)
    second, _ = identity.build_system_prompt(*args, **kwargs)
    assert first == second


def test_empty_store_leaves_static_free_of_soul_scaffolding():
    """A brand-new install has no soul content; the static half must not carry an empty
    heading for it."""
    static, _ = identity.build_system_prompt({"name": "Baker", "personality": ""}, soul="")
    # The heading form, not the phrase: CONTEXT_FILES_NOTE refers to "Your soul" in prose.
    assert "## Your soul" not in static
