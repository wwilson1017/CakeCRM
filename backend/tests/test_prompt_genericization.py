"""THE enforcement test for the genericization rule (issue #22, CLAUDE.md "Don't Do
This"): CakeCRM's sales prompting and tool descriptions are ported from the CAKE OS
sales agent, whose text is saturated with one company's customers, staff, products,
and industry jargon. This repo goes public and its history is permanent, so a single
leaked token is unfixable after the fact.

Modeled on ``test_gmail_guard.py``: the rule is enforced by a CI-failing scan, not by
reviewer vigilance.

It scans TWO surfaces against two different denylists (#90):

* the **model-facing payload** — the assembled system prompt, every tool
  name/description/schema, and the UI starter chips — against every token, because
  that is the text an AI provider (and therefore a shipped product) receives; and
* **every committed text file** — docs, scripts, workflows, migrations, source and
  tests alike — against the company and vertical tokens only.

The split is by what a token IS, not by which file it sits in. Blueprint identifiers
(``cake_os``, ``casey``) are banned from the payload but deliberately legitimate in
committed prose: this repo cites its source by name throughout ("ported from
cake_os/..."), and the Source Map could not be written without it. The company's own
name has no such excuse anywhere, which is why the second surface exists at all — CI
was fully green on both a hardcoded upstream org URL and six real upstream directory
names, and multi-persona review caught them, which is luck rather than a control.
"""

import functools
import json
import re
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
QUICK_ACTIONS = ROOT / "frontend" / "src" / "assistant" / "QuickActions.tsx"

# --- the denylist, in three classes -------------------------------------------------
#
# Class membership decides WHERE a token is banned, so a new token goes in the narrowest
# class that is honest about it. Every pattern is matched case-insensitively.

# The company itself. Never legitimate in a repo that goes public with permanent
# history — not in a doc, not in a comment, not in a test fixture.
_COMPANY = [
    (r"tncheesecake", "company domain"),
    (r"tn\s+cheesecake", "company name"),
    (r"\btnc\b", "company abbreviation"),
    (r"cheesecake", "company product"),
]

# Trade-vertical jargon carried in from the blueprint's prompt examples. It describes
# one industry's business, so it neither reaches the model nor gets committed.
_VERTICAL = [
    (r"\biddba\b", "trade-show acronym from blueprint examples"),
    (r"\bnra\s*\d", "trade-show acronym from blueprint examples"),
    (r"restaurant_type", "vertical-specific field key from blueprint examples"),
    (r"\bcuisine\b", "vertical-specific field value from blueprint examples"),
    (r"distributor_tier", "vertical-specific field key from blueprint examples"),
    # The #70 issue body proposed "@oven-room" as an example GTD context. The shipped
    # tool descriptions use @calls/@office/@errands instead; this keeps it that way.
    (r"\boven\b", "vertical-specific context from blueprint examples"),
]

# Blueprint identifiers. Banned from the model-facing payload — a shipped product must
# not name the codebase it was ported from — but ALLOWED in committed prose, where
# citing the source is the convention the whole repo is built on.
_BLUEPRINT = [
    (r"\bcasey\b", "blueprint agent name (this assistant is user-named)"),
    (r"cake[_\s]os\b", "blueprint product name"),
    (r"cake_crm_", "blueprint tool prefix (CakeCRM uses crm_)"),
]

_FORBIDDEN = _COMPANY + _VERTICAL + _BLUEPRINT  # nothing here may reach the model
_REPO_FORBIDDEN = _COMPANY + _VERTICAL  # nothing here may be committed, anywhere

# Strips // line comments and /* */ blocks from the TSX so a provenance comment there
# is treated the same way a Python comment is — only the shipped strings are scanned.
_TS_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _offenders(text: str, label: str, patterns: list[tuple[str, str]] = _FORBIDDEN) -> list[str]:
    found = []
    for pattern, why in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append(f"{label} — {m.group(0)!r} ({why})")
    return found


# --- the committed-file surface (#90) -----------------------------------------------

# This file necessarily spells out every forbidden token, so it cannot scan itself.
_GUARD = Path(__file__).resolve().relative_to(ROOT).as_posix()

# Deliberate exemptions: path -> (exempt patterns, why). Every entry so far is a place
# the repo has to talk ABOUT the denylist, which is the one thing a denylist can never
# express about itself. Scrub the file instead whenever scrubbing is possible — this
# list is meant to stay short enough to read in full.
#
# An entry may name a file that has not landed on this branch yet (feature branches
# merge in arbitrary order, and the guard must not turn main red the moment a sibling
# PR lands); test_repo_allowlist_has_no_dead_entries checks the ones that do exist.
_REPO_ALLOW = {
    "CLAUDE.md": (_COMPANY, "the 'Don't Do This' rule has to name what it forbids"),
    ".claude/coach-lessons.md": (
        _VERTICAL,
        "a lesson about this guard quotes the very token the guard was missing",
    ),
    "frontend/src/crm/stageCriteria.test.ts": (
        _COMPANY,
        "sibling guard (#74/PR #109): its own denylist literal for the stage-criteria copy",
    ),
}


@functools.cache
def _committed_text_files() -> tuple[tuple[str, str], ...]:
    """(repo-relative path, text) for every committed file except this one.

    ``git ls-files`` IS the definition of "committed", so the scan covers a file CLASS
    rather than a hand-maintained directory list: a new docs/ or scripts/ file is in
    scope the moment it is added, with nothing to remember to update.
    """
    listing = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = []
    for rel in listing.split("\0"):
        if not rel or rel == _GUARD:
            continue
        try:
            raw = (ROOT / rel).read_bytes()
        except OSError:  # tracked but absent from the working tree
            continue
        if b"\x00" in raw[:8192]:  # binary — nothing greppable in it
            continue
        files.append((rel, raw.decode("utf-8", "replace")))
    return tuple(files)


def _repo_offenders() -> list[str]:
    found = []
    for rel, text in _committed_text_files():
        exempt, _ = _REPO_ALLOW.get(rel, ([], ""))
        for pattern, why in _REPO_FORBIDDEN:
            if (pattern, why) in exempt:
                continue
            for m in re.finditer(pattern, text, re.IGNORECASE):
                line = text.count("\n", 0, m.start()) + 1
                found.append(f"{rel}:{line} — {m.group(0)!r} ({why})")
    return found


def _all_tool_defs() -> list[dict]:
    """EVERY tool def the assistant can advertise, from the real modules.

    Must stay exhaustive — a guard that silently covers a subset is worse than none,
    because people stop double-checking it. Pinned by
    test_the_guard_covers_every_registered_tool.
    """
    from assistant.registry import ToolRegistry
    from context_files.tools import get_context_file_tools
    from crm.gtd_tools import GTD_TOOL_DEFS
    from crm.tools import CRM_TOOL_DEFS
    from gmail.tools import GMAIL_TOOL_DEFS
    from memory.tools import get_memory_tools
    from notifications.tools import get_notification_tools
    from reminders.tools import get_reminder_tools

    # GTD_TOOL_DEFS is read directly, not through get_gtd_tools(): that returns ([], {})
    # unless GTD mode is active, so calling it here would silently scan nothing (#70).
    defs = list(CRM_TOOL_DEFS) + list(GTD_TOOL_DEFS) + list(GMAIL_TOOL_DEFS)
    defs += list(get_memory_tools()[0]) + list(get_reminder_tools()[0])
    defs += list(get_context_file_tools()[0])
    defs += list(get_notification_tools(ToolRegistry())[0])
    return defs


@pytest.fixture
def model_facing(monkeypatch):
    """(label, text) pairs for everything that actually reaches the AI provider."""
    from assistant import identity
    from crm import touch_count_service
    from heartbeat import service as heartbeat_service

    monkeypatch.setattr(
        identity, "get_identity",
        lambda: {"name": "Baker", "personality": "", "using_default": True},
    )
    # Normal mode first — patching the mode below is one-way for this fixture.
    static, volatile = identity.build_system_prompt({"name": "Baker", "personality": ""})
    hb_static, hb_volatile = heartbeat_service._heartbeat_prompt()
    rm_static, rm_volatile = heartbeat_service._reminder_prompt(
        {"id": 1, "message": "", "context": ""}
    )

    # GTD mode appends GTD_GUIDE to the static half and renames the heartbeat's task
    # tool, so both prompts are assembled a SECOND time under that mode — otherwise
    # every GTD-only string reaches the model unscanned (#70).
    monkeypatch.setattr(identity, "_task_mode", lambda: "gtd")
    monkeypatch.setattr(heartbeat_service, "_task_mode", lambda: "gtd")
    gtd_static, _ = identity.build_system_prompt({"name": "Baker", "personality": ""})
    gtd_hb_static, _ = heartbeat_service._heartbeat_prompt()
    texts = [
        ("assistant system prompt (static)", static),
        ("assistant system prompt (volatile)", volatile),
        # The built-in soul (#72) seeds soul.md, which loads UNFENCED into the static
        # half — so its text reaches the model verbatim and must be scanned. It is not
        # part of `static` above because build_system_prompt takes it as a kwarg the
        # engine supplies from the DB.
        ("default soul", identity.DEFAULT_SOUL),
        ("heartbeat prompt", f"{hb_static}\n{hb_volatile}"),
        ("reminder prompt", f"{rm_static}\n{rm_volatile}"),
        ("quick-action starters", _TS_COMMENTS.sub("", QUICK_ACTIONS.read_text(encoding="utf-8"))),
        # The touch-count worker (#16) is a second provider caller with its own system
        # prompt, and it was never scanned here until #56 rewrote it for per-line verdicts.
        ("touch count prompt", touch_count_service.TOUCH_COUNT_SYSTEM_PROMPT),
        ("assistant system prompt (static, GTD mode)", gtd_static),
        ("heartbeat prompt (GTD mode)", gtd_hb_static),
    ]
    # Tool defs go to the provider verbatim — name, description AND the JSON schema
    # (property descriptions, enums and defaults are all example-text hiding places).
    texts += [(f"tool def {d['name']}", json.dumps(d)) for d in _all_tool_defs()]
    return texts


def test_no_company_specific_tokens_reach_the_model(model_facing):
    offenders = []
    for label, text in model_facing:
        offenders += _offenders(text, label)
    assert not offenders, (
        "Company-specific text leaked into the model-facing payload. CakeCRM is public "
        "and its history is permanent — genericize it:\n" + "\n".join(offenders)
    )


def test_the_guard_covers_every_registered_tool():
    """The docstring claims it scans every tool name/description/schema. Prove it
    against the real registry rather than a hand-maintained list that drifts."""
    from assistant.registry import ToolRegistry

    scanned = {d["name"] for d in _all_tool_defs()}
    registered = set(ToolRegistry().writes_map)
    missing = registered - scanned
    assert not missing, f"tools the genericization guard never scans: {missing}"


def test_the_guard_actually_catches_a_leak():
    """A scanner that matches nothing passes vacuously forever. Prove it bites."""
    assert _offenders("Ask Casey about the IDDBA lead", "probe")


def test_quick_actions_file_is_where_the_test_thinks_it_is():
    assert QUICK_ACTIONS.exists(), f"QuickActions moved — fix the path: {QUICK_ACTIONS}"


def test_no_company_tokens_in_committed_files():
    """The second surface (#90): docs, scripts, workflows and source, not just the
    payload. The two incidents that motivated it — a hardcoded upstream org URL in
    scripts/, six real upstream directory names in docs/ — were both CI-green."""
    offenders = _repo_offenders()
    shown = "\n".join(offenders[:40])
    extra = "" if len(offenders) <= 40 else f"\n… and {len(offenders) - 40} more"
    assert not offenders, (
        "Company- or vertical-specific text is committed to a repo that goes public "
        "with permanent history. Genericize it — or, if the file genuinely has to name "
        "the token, add a commented entry to _REPO_ALLOW:\n" + shown + extra
    )


def test_the_repo_scan_reads_the_whole_repo():
    """A file list that came back empty — no git, wrong cwd, a sparse checkout — would
    pass forever while checking nothing. Pin the size AND one file from each class the
    scan is meant to cover, so a scope regression fails loudly instead of silently."""
    rels = {rel for rel, _ in _committed_text_files()}
    assert len(rels) > 200, f"only {len(rels)} files enumerated — the repo scan is not running"
    for expected in (
        "CLAUDE.md",  # root markdown
        "README.md",
        "SECURITY.md",
        "docs/SYNC.md",  # docs/
        "scripts/sync_intake.py",  # scripts/
        ".github/workflows/ci.yml",  # workflows
        ".claude/coach-lessons.md",  # operational notes — in scope, see #90
        "backend/crm/service.py",  # backend source
        "backend/migrations/20260723221920_crm_core.sql",  # migrations
        "frontend/src/crm/CrmDashboardPage.tsx",  # frontend source
    ):
        assert expected in rels, f"{expected} is committed but is not being scanned"


def test_the_repo_scan_actually_catches_a_leak():
    """A scanner that matches nothing passes vacuously forever. The probe text is built
    FROM a declared pattern rather than typed out, so this fixture can never introduce a
    token the denylist does not already carry."""
    plain = next(p for p, _ in _COMPANY if p.isalnum())
    assert _offenders(f"reach us at ops@{plain}.example", "probe", _REPO_FORBIDDEN), (
        "the committed-file scanner matched nothing in a document that plainly contains "
        f"{plain!r} — every green run of the repo scan is meaningless until this passes"
    )


def test_repo_allowlist_has_no_dead_entries():
    """An exemption that has stopped being needed is a hole nobody is watching."""
    present = dict(_committed_text_files())
    for rel, (patterns, why) in _REPO_ALLOW.items():
        if rel not in present:
            continue  # not landed on this branch yet — see the note on _REPO_ALLOW
        assert any(re.search(p, present[rel], re.IGNORECASE) for p, _ in patterns), (
            f"_REPO_ALLOW[{rel!r}] exempts tokens the file no longer contains "
            f"— delete the entry ({why})"
        )


def test_the_repo_denylist_is_a_strict_subset_of_the_model_facing_one():
    """The repo scan is deliberately NARROWER — blueprint provenance is legitimate in
    committed prose — and must never be wider: a token banned from a doc while still
    allowed to reach a provider would have the trust boundary backwards. The second
    assertion pins that a token appended straight to _FORBIDDEN, bypassing the three
    classes, fails here rather than silently skipping the committed-file surface."""
    assert set(_REPO_FORBIDDEN) < set(_FORBIDDEN), (
        "tokens banned from committed files but not from the model-facing payload: "
        f"{sorted(set(_REPO_FORBIDDEN) - set(_FORBIDDEN))}"
    )
    assert set(_COMPANY + _VERTICAL + _BLUEPRINT) == set(_FORBIDDEN), (
        "_FORBIDDEN no longer equals _COMPANY + _VERTICAL + _BLUEPRINT — a token added "
        "directly to it would skip the committed-file surface entirely. Put it in the "
        "narrowest class instead. Stray entries: "
        f"{sorted(set(_FORBIDDEN) ^ set(_COMPANY + _VERTICAL + _BLUEPRINT))}"
    )


def test_sales_guide_is_static_and_survives_a_custom_personality():
    """The sales practices must NOT live in DEFAULT_PERSONALITY: a user who writes a
    custom personality replaces that string wholesale, and would silently lose every
    CRM working practice with it."""
    from assistant import identity

    assert identity.SALES_GUIDE not in identity.DEFAULT_PERSONALITY
    static, _ = identity.build_system_prompt(
        {"name": "Baker", "personality": "You are a laconic robot."}
    )
    assert identity.SALES_GUIDE in static
    assert "laconic robot" in static


def test_sales_guide_is_in_the_cacheable_static_half():
    """It is constant text, so it belongs in the cached prefix — putting it in the
    volatile half would rewrite the prompt every turn and defeat prompt caching."""
    from assistant import identity

    static, volatile = identity.build_system_prompt({"name": "Baker", "personality": ""})
    assert identity.SALES_GUIDE in static
    assert identity.SALES_GUIDE not in volatile


def test_sales_guide_never_promises_to_send_email():
    """Gmail is read + create-draft forever (SECURITY.md). The prompt must not tell the
    model it can send — a model that believes it can will promise the user it did."""
    from assistant import identity

    text = identity.SALES_GUIDE.lower()
    assert "draft" in text
    for claim in ("send the email", "send an email", "i can send", "sends the email"):
        assert claim not in text, f"SALES_GUIDE implies sending: {claim!r}"
