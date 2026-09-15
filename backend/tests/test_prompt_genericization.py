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

Four limits are known and accepted, and are written down here so nobody has to
rediscover them:

* **Compressed containers are opaque.** A .xlsx, .docx or .zip stores its text
  deflate-compressed, so no byte-level reader sees inside one and only its filename is
  scanned. Worth knowing because the motivating scenario below — a spreadsheet export
  of real customer names — arrives as .xlsx at least as often as .csv. Nothing of the
  sort is committed today; if that changes, the cheap fail-closed move is to refuse
  archive extensions outright rather than to teach this scan to unzip.

* **History is out of scope.** The scan reads the working tree, not the log. A token
  committed and later scrubbed still sits in history, where only a rewrite reaches it.
  This guard stops the next one; it does not clean up the last one.
* **The file list comes from the index, the content from the working tree.** They agree
  in CI — which is where this guard has authority — but locally you could stage a leak
  and then scrub it unstaged, and the scan would read the scrubbed copy. Pushing it
  still fails CI.
* **An allowance counts occurrences, not their content.** Deleting the sanctioned
  mention in an exempted file and adding a real one keeps the count intact. Pinning
  exact snippets instead was rejected as too brittle to survive ordinary reflowing;
  what the count does buy is that any change in the NUMBER is a visible edit here.
"""

import functools
import json
import os
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

# Token boundaries, deliberately NOT `\b`. Python's `\b` treats "_" as a word character,
# so `\btnc\b` misses `tnc_internal` and `cake_os\b` misses `cake_os_prompt` — which are
# exactly the shapes these names take inside real identifiers, filenames and env vars
# (six such bypasses were measured before this changed). These break on anything that is
# not a letter or digit, so "_", "-" and "." all end a token. Under re.IGNORECASE the
# character class covers A-Z too.
_L = r"(?<![0-9a-z])"
_R = r"(?![0-9a-z])"

# The company itself. Never legitimate in a repo that goes public with permanent
# history — not in a doc, not in a comment, not in a test fixture.
_COMPANY = [
    (r"tncheesecake", "company domain"),
    (r"tn[\s_.-]+cheesecake", "company name"),
    (_L + r"tnc" + _R, "company abbreviation"),
    (r"cheesecake", "company product"),
]

# Trade-vertical jargon carried in from the blueprint's prompt examples. It describes
# one industry's business, so it neither reaches the model nor gets committed.
_VERTICAL = [
    (_L + r"iddba" + _R, "trade-show acronym from blueprint examples"),
    (_L + r"nra[\s_.-]*\d", "trade-show acronym from blueprint examples"),
    (r"restaurant[\s_.-]*type", "vertical-specific field key from blueprint examples"),
    (_L + r"cuisines?" + _R, "vertical-specific field value from blueprint examples"),
    (r"distributor[\s_.-]*tier", "vertical-specific field key from blueprint examples"),
    # The #70 issue body proposed "@oven-room" as an example GTD context. The shipped
    # tool descriptions use @calls/@office/@errands instead; this keeps it that way.
    (_L + r"ovens?" + _R, "vertical-specific context from blueprint examples"),
]

# Blueprint identifiers. Banned from the model-facing payload — a shipped product must
# not name the codebase it was ported from — but ALLOWED in committed prose, where
# citing the source is the convention the whole repo is built on.
_BLUEPRINT = [
    (_L + r"casey" + _R, "blueprint agent name (this assistant is user-named)"),
    (r"cake[\s_.-]?os" + _R, "blueprint product name"),
    # Left literal on purpose: this repo's own name is CakeCRM, so widening the
    # separators here would match the product name in every file that mentions it.
    (r"cake_crm_", "blueprint tool prefix (CakeCRM uses crm_)"),
]

_FORBIDDEN = _COMPANY + _VERTICAL + _BLUEPRINT  # nothing here may reach the model
_REPO_FORBIDDEN = _COMPANY + _VERTICAL  # nothing here may be committed, anywhere

# Strips // line comments and /* */ blocks from the TSX so a provenance comment there
# is treated the same way a Python comment is — only the shipped strings are scanned.
_TS_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


# Format characters that are invisible in an editor and in `git diff` but that a reader
# never sees at all: a name pasted out of Word, a PDF or a web page can arrive with a
# soft hyphen or a zero-width space inside it, read perfectly to every human, and match
# nothing. Measured: "chee<ZWSP>secake" and "cheese<SHY>cake" both walked straight
# through. Stripped before matching — none of them is a newline, so reported line
# numbers stay correct. Homoglyph substitution (a Cyrillic "с") is still open by
# construction; this is an anti-accident control, and a determined committer defeats any
# content scan.
# Written as escapes on purpose: a literal here would be an invisible character in this
# file, which is precisely the problem it exists to solve.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u2060\ufeff]")


def _offenders(text: str, label: str, patterns: list[tuple[str, str]] = _FORBIDDEN) -> list[str]:
    text = _INVISIBLE.sub("", text)
    found = []
    for pattern, why in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append(f"{label} — {m.group(0)!r} ({why})")
    return found


# --- the committed-file surface (#90) -----------------------------------------------

# This file necessarily spells out every forbidden token — but it is NOT exempt. It gets
# a counted allowance like any other file, below, so that the one file with the most
# license to hold these strings is not also the one place nobody is watching.
_GUARD = Path(__file__).resolve().relative_to(ROOT).as_posix()

# Deliberate exemptions: path -> ({pattern: how many occurrences are expected}, why).
#
# The allowance is per PATTERN and is a CEILING, not an on/off switch. Exempting a whole
# file for a whole token class would repeat a mistake this repo has already written down
# once: CLAUDE.md's own gitleaks guidance rejects an allowlist keyed on file alone
# because "the latter exempts every finding in that file in that commit, including a
# real one". CLAUDE.md is also the most-edited file here — every landed feature appends
# a paragraph — so an unbounded exemption on it would be the likeliest hole of all.
#
# Every entry so far is a place the repo must talk ABOUT the denylist, which is the one
# thing a denylist cannot express about itself. Scrub the file instead whenever scrubbing
# is possible — this list is meant to stay short enough to read in full.
#
# An entry may name a file that has not landed on this branch yet (feature branches merge
# in arbitrary order, and the guard must not turn main red the moment a sibling PR
# lands); test_repo_allowlist_has_no_dead_entries validates every entry whose file exists.
#
# Counts COMPOUND across overlapping patterns: one written company name spends two
# budgets at once, because the broadest pattern in a class carries no boundaries and so
# also matches inside the narrower one. That catches out everyone editing an allowed
# line for the first time, hence this note.
_REPO_ALLOW = {
    "CLAUDE.md": (
        {r"tn[\s_.-]+cheesecake": 1, _L + r"tnc" + _R: 1, r"cheesecake": 1},
        "the 'Don't Do This' rule has to name what it forbids — one bullet, one mention each",
    ),
    ".claude/coach-lessons.md": (
        {_L + r"ovens?" + _R: 1},
        "a lesson about this guard quotes the very token the guard was missing",
    ),
    "frontend/src/crm/stageCriteria.test.ts": (
        {r"cheesecake": 1},
        "sibling guard (#74/PR #109): one denylist literal for the stage-criteria copy",
    ),
    # The guard scans itself. The numbers below are its own denylist patterns and the
    # prose explaining them; they will need updating whenever this file's wording
    # changes, and that friction is the point — the alternative is a blanket exemption
    # on the single file most able to hide a real name.
    _GUARD: (
        {
            r"tncheesecake": 2,
            r"tn[\s_.-]+cheesecake": 1,
            _L + r"tnc" + _R: 5,
            r"cheesecake": 10,
            _L + r"iddba" + _R: 3,
            _L + r"cuisines?" + _R: 2,
            _L + r"ovens?" + _R: 4,
        },
        "the denylist patterns themselves, plus the prose that explains them",
    ),
}


def _denul(raw: bytes) -> str:
    """The bytes with NULs dropped, read as latin-1.

    This one reading covers EVERY fixed-width encoding of ASCII at once — UTF-16 and
    UTF-32, either byte order, BOM or none — without having to detect which is which,
    because all of them differ only in how many NULs they pad each character with.
    That generality is the point: guessing the encoding meant a heuristic, and the
    heuristic had a hole (it keyed on NUL density, which most real binaries also have,
    and UTF-32 slipped past the UTF-16 BOM check because they share a two-byte prefix).
    """
    return raw.replace(b"\x00", b"").decode("latin-1")


def _decode(raw: bytes) -> str:
    """The file's text. This never gives up, because every file it declines to read is a
    blind spot, and a leaked name is exactly as permanent inside a blob as inside a .md.

    Two OPPOSITE traps are handled explicitly, and a single decode attempt walks into one
    or the other:

    * BOM-less UTF-16 is byte-wise VALID UTF-8, so it decodes "successfully" into
      NUL-interleaved text that matches no regex at all — it hides in the SUCCESS path.
      This is the likely shape of a CSV or spreadsheet export of real customer names.
    * A mostly-binary file carrying a plain ASCII name fails UTF-8 decoding, so treating
      the error (or the NUL bytes) as "binary, skip" hides it in the FAILURE path.

    latin-1 is the backstop: it never raises and maps every byte 1:1, so an ASCII name
    embedded anywhere stays visible. A false positive costs one line in _REPO_ALLOW; a
    false negative is public and permanent, so this errs toward reading too much.
    """
    if raw[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        readings = [raw.decode("utf-32", "replace")]  # before UTF-16: shared BOM prefix
    elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        readings = [raw.decode("utf-16", "replace")]
    else:
        try:
            readings = [raw.decode("utf-8")]
        except UnicodeDecodeError:
            readings = [raw.decode("latin-1")]
    if b"\x00" in raw:
        # Anything NUL-bearing gets the de-NUL reading appended as well: a BOM-less wide
        # encoding, or an ASCII name sitting inside an otherwise-binary blob. Costs
        # nothing on the ~all-NUL-free files in this repo, and the reading is additive,
        # so the primary one above still supplies the line numbers.
        readings.append(_denul(raw))
    return "\n".join(readings)


@functools.cache
def _committed_files() -> tuple[tuple[str, str], ...]:
    """(repo-relative path, text) for every committed file — this one included.

    ``git ls-files`` IS the definition of "committed", so the scan covers a file CLASS
    rather than a hand-maintained directory list: a new docs/, scripts/ or .github/ file
    is in scope the moment it is staged, with nothing to remember to update.
    """
    try:
        listing = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AssertionError(
            f"could not enumerate committed files via git in {ROOT} — the repo-wide "
            f"genericization scan cannot run, and an unrunnable guard must fail rather "
            f"than pass: {exc}"
        ) from exc
    files = []
    for rel in listing.split("\0"):
        if not rel:
            continue
        path = ROOT / rel
        if path.is_symlink():
            # git stores a symlink's own content as its target path. read_bytes() would
            # follow the link and scan a different file's bytes instead.
            files.append((rel, os.readlink(path)))
            continue
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            # Tracked but absent from the working tree. Deliberately narrow: a
            # PermissionError must fail loudly, never silently shrink the scan.
            continue
        files.append((rel, _decode(raw)))
    return tuple(files)


def _repo_offenders(files: tuple[tuple[str, str | None], ...] | None = None) -> list[str]:
    """Every forbidden token in the committed tree, minus the declared allowances.

    Takes the file list as an argument so a test can drive THIS code path with synthetic
    content: a probe that only exercised the regex helper would leave this function's own
    wiring — enumeration, allowance lookup, reporting — unproven, and a one-character slip
    in the allowance lookup would make the whole scan pass vacuously forever.
    """
    found = []
    for rel, text in files if files is not None else _committed_files():
        # The PATH is normalized too. A filename can carry a zero-width space just as a
        # body can, and for a file whose bytes no decoder reads it is the only surface.
        scanned_rel = _INVISIBLE.sub("", rel)
        text = _INVISIBLE.sub("", text) if text else text
        budget, _ = _REPO_ALLOW.get(rel, ({}, ""))
        for pattern, why in _REPO_FORBIDDEN:
            # The PATH is scanned too, and for a binary file it is the only thing there
            # is to scan: a file named after the company leaks exactly as permanently as
            # one that spells the name inside.
            for m in re.finditer(pattern, scanned_rel, re.IGNORECASE):
                found.append(f"{rel} — filename contains {m.group(0)!r} ({why})")
            if text is None:
                continue
            allowed = budget.get(pattern, 0)
            for i, m in enumerate(re.finditer(pattern, text, re.IGNORECASE)):
                if i < allowed:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                over = " — beyond the allowance declared in _REPO_ALLOW" if allowed else ""
                found.append(f"{rel}:{line} — {m.group(0)!r} ({why}){over}")
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
    from help.tools import HELP_TOOL_DEFS
    from memory.tools import get_memory_tools
    from notifications.tools import get_notification_tools
    from reminders.tools import get_reminder_tools

    # GTD_TOOL_DEFS is read directly, not through get_gtd_tools(): that returns ([], {})
    # unless GTD mode is active, so calling it here would silently scan nothing (#70).
    defs = list(CRM_TOOL_DEFS) + list(GTD_TOOL_DEFS) + list(GMAIL_TOOL_DEFS)
    defs += list(get_memory_tools()[0]) + list(get_reminder_tools()[0])
    defs += list(get_context_file_tools()[0])
    defs += list(HELP_TOOL_DEFS)
    defs += list(get_notification_tools(ToolRegistry())[0])
    return defs


def _help_topic_texts() -> list[tuple[str, str]]:
    """(label, RAW file text) for every committed help topic (#143).

    Help content becomes model-facing payload the moment a tool returns it, so it belongs
    on the payload surface — where the BLUEPRINT tokens are banned too, not just the
    company and vertical ones that the committed-file sweep already covers. The file is
    read RAW rather than through the parsed library because the front matter (title,
    description, aliases) rides search results and so reaches the provider as well.
    """
    from help.library import CONTENT_ROOT

    return [
        (f"help topic {path.relative_to(CONTENT_ROOT).as_posix()}",
         path.read_text(encoding="utf-8"))
        for path in sorted(CONTENT_ROOT.rglob("*.md"))
    ]


@pytest.fixture
def model_facing(monkeypatch):
    """(label, text) pairs for everything that actually reaches the AI provider."""
    from assistant import compaction, identity
    from crm import touch_count_service
    from heartbeat import service as heartbeat_service

    monkeypatch.setattr(
        identity, "get_identity",
        lambda: {"name": "Baker", "personality": "", "using_default": True},
    )
    # Normal mode first — patching the mode below is one-way for this fixture.
    # Pinned EXPLICITLY: with no database the fail-safe answers 'gtd' since #102, so
    # relying on it here would assemble the GTD prompt twice and leave every
    # normal-mode-only string (the crm_list_tasks heartbeat wording) unscanned. A
    # silent loss of coverage in a test whose whole job is to catch leaked strings.
    monkeypatch.setattr(identity, "_task_mode", lambda: "normal")
    monkeypatch.setattr(heartbeat_service, "_task_mode", lambda: "normal")
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
        # Conversation compaction (#72 Phase 3) is the third provider caller with a
        # system prompt of its own — same reason the touch-count one is scanned here.
        ("compaction summary prompt", compaction._SUMMARY_SYSTEM_PROMPT),
        ("assistant system prompt (static, GTD mode)", gtd_static),
        ("heartbeat prompt (GTD mode)", gtd_hb_static),
    ]
    # Tool defs go to the provider verbatim — name, description AND the JSON schema
    # (property descriptions, enums and defaults are all example-text hiding places).
    texts += [(f"tool def {d['name']}", json.dumps(d)) for d in _all_tool_defs()]
    texts += _help_topic_texts()
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
        "the token, add a commented entry to _REPO_ALLOW.\n"
        "If the match is inside a machine-generated hash (a package-lock.json integrity "
        "line, say), it is a coincidence in base64: regenerate the artifact, which "
        "rerolls the hash. Do NOT add an allowance for it — the count would break on the "
        "next regeneration.\n" + shown + extra
    )


def test_the_repo_scan_reads_the_whole_repo():
    """A file list that came back empty — no git, wrong cwd, a sparse checkout — would
    pass forever while checking nothing. Pin the size AND one file from each class the
    scan is meant to cover, so a scope regression fails loudly instead of silently."""
    rels = {rel for rel, _ in _committed_files()}
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
        "backend/migrations/20260723221920_crm_core.sql",  # migrations, append-only
    ):
        assert expected in rels, f"{expected} is committed but is not being scanned"
    # The frontend churns too much to pin one component by name — a legitimate rename
    # would fail this guard with a message about genericization, which is worse than
    # useless. Pin the CLASS instead.
    assert any(r.startswith("frontend/src/") and r.endswith(".tsx") for r in rels), (
        "no frontend component is being scanned"
    )


def test_the_repo_scan_reports_a_leak_end_to_end():
    """The scan's OWN wiring — allowance lookup, match loop, reporting — must be proven,
    not just the regex list. Otherwise a one-character slip (defaulting the allowance
    lookup to _REPO_FORBIDDEN instead of {}) exempts every file from every pattern and
    the whole guard passes vacuously forever, with every other test still green.

    The probe text is built FROM a declared pattern rather than typed out, so this
    fixture can never introduce a token the denylist does not already carry."""
    plain = next(p for p, _ in _COMPANY if p.isalnum())
    found = _repo_offenders((("docs/probe.md", f"line one\nreach us at ops@{plain}.example\n"),))
    assert found, "the repo scan reported nothing for a file that plainly contains a token"
    assert "docs/probe.md:2" in found[0], f"wrong file or line reported: {found[0]}"


def test_the_allowance_is_a_ceiling_not_a_switch():
    """The whole point of a per-pattern budget: an exempted file may keep its ONE known
    mention and still fail on a second. A file-wide exemption would make the repo's
    most-edited file permanently blind to the tokens it is exempt for."""
    rel, (budget, _) = "CLAUDE.md", _REPO_ALLOW["CLAUDE.md"]
    pattern = next(iter(budget))
    token = re.search(pattern, "tn cheesecake tnc", re.IGNORECASE)
    assert token, "the fixture below must contain a real match for the pattern under test"
    body = token.group(0)
    assert not _repo_offenders(((rel, body),)), "the declared allowance was not honoured"
    twice = _repo_offenders(((rel, f"{body}\n{body}\n"),))
    assert twice, "a second occurrence in an exempted file was silently absorbed"
    assert "beyond the allowance" in twice[0], twice[0]


def test_a_leaked_filename_is_caught_even_when_the_content_is_binary():
    """A file NAMED after the company leaks exactly as permanently as one that spells the
    name inside — and a binary asset has no scannable content at all, so the path is the
    only surface there is."""
    plain = next(p for p, _ in _COMPANY if p.isalnum())
    found = _repo_offenders(((f"docs/assets/{plain}-dashboard.png", None),))
    assert found and "filename contains" in found[0], found


def test_the_decoder_reads_every_encoding_a_leak_could_hide_in():
    """One decode attempt walks into one of two opposite traps, so both are pinned here.
    Each case is a measured bypass of the previous NUL-probe implementation."""
    plain = next(p for p, _ in _COMPANY if p.isalnum())
    doc = f"customer,{plain}\n"

    assert _decode(doc.encode("utf-8")) == doc

    # UTF-16 WITH a BOM, both byte orders — built explicitly, because encoding twice with
    # "utf-16" would just produce the runner's native order twice and never test the other.
    # The assertion is that the token is FINDABLE, not that _decode returns one canonical
    # string: for NUL-bearing input it deliberately returns several readings joined.
    for codec, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
        raw = bom + doc.encode(codec)
        assert doc.strip() in _decode(raw), f"UTF-16 text was not decoded ({codec})"
        assert _repo_offenders((("docs/export.csv", _decode(raw)),)), f"leak missed ({codec})"

    # UTF-32 shares its first two bytes with a UTF-16 BOM, so a naive BOM check sends it
    # down the wrong branch and it stays NUL-interleaved. Measured bypass.
    for raw in (doc.encode("utf-32"), doc.encode("utf-32-le"), doc.encode("utf-32-be")):
        assert _repo_offenders((("docs/export.csv", _decode(raw)),)), "UTF-32 leak missed"

    # BOM-less wide encodings: byte-wise VALID UTF-8, so they decode "fine" into
    # NUL-interleaved mush that matches nothing. The trap that hides in the success path.
    for codec in ("utf-16-le", "utf-16-be"):
        text = _decode(doc.encode(codec))
        assert _repo_offenders((("docs/export.csv", text),)), f"BOM-less leak missed ({codec})"

    # A plain ASCII name inside an otherwise-binary blob: the trap in the failure path.
    # The NUL padding is load-bearing — real binaries are NUL-dense, and a short tidy
    # fixture would not represent one.
    blob = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + b"\x00" * 1000
        + b"tEXtComment\x00Exported for " + plain.encode() + b" sales meeting\x00"
    )
    assert _repo_offenders((("docs/logo.png", _decode(blob)),)), "ASCII-in-binary leak missed"

    # Legacy 8-bit text still reads; nothing is ever skipped outright.
    assert "latin-1 text" in _decode(b"caf\xe9 latin-1 text")


def test_invisible_characters_cannot_split_a_token():
    """A name pasted out of Word, a PDF or a web page can carry a soft hyphen or a
    zero-width space inside it: invisible in every editor and in `git diff`, absent to
    every reader, and enough to match nothing. Measured as a real bypass before the
    strip existed. This is an accident route, not just an adversarial one."""
    plain = next(p for p, _ in _COMPANY if p.isalnum())
    # chr() rather than literals: typing these into the file would put invisible
    # characters in the very guard that exists to strip them.
    for name, code in (("soft hyphen", 0x00AD), ("zero-width space", 0x200B),
                       ("word joiner", 0x2060), ("zero-width nbsp", 0xFEFF)):
        split = plain[:4] + chr(code) + plain[4:]
        assert _repo_offenders((("docs/x.md", split),)), f"{name} split the token"
        assert _offenders(split, "probe"), f"{name} split it on the model surface"


def test_an_invisible_character_cannot_hide_in_a_filename_either():
    """The path is the only surface a compressed container or binary asset has, so a
    zero-width space in a filename would otherwise be a free pass on exactly the
    files whose contents nothing can read."""
    plain = next(p for p, _ in _COMPANY if p.isalnum())
    name = f"docs/{plain[:4]}{chr(0x200B)}{plain[4:]}-export.xlsx"
    assert _repo_offenders(((name, None),)), "invisible character hid a leaked filename"


def test_repo_allowlist_has_no_dead_entries():
    """An exemption that has stopped being needed is a hole nobody is watching.

    Checked per PATTERN and for an EXACT count, both deliberately. Per pattern, because
    an entry exempting four patterns where one is in use would otherwise pass while
    silently blinding the file to the other three. Exactly, because a mere ceiling can be
    inflated: bumping an allowance to 99 would make a real CI failure disappear and look
    like a fix. The number has to keep matching reality, so changing it is a visible,
    deliberate edit to this list rather than a knob that turns the guard down."""
    present = dict(_committed_files())
    known = {p for p, _ in _REPO_FORBIDDEN}
    for rel, (budget, why) in _REPO_ALLOW.items():
        # An allowance is keyed by the pattern STRING, so a retyped or stale copy would
        # silently never match and quietly grant nothing — which then reads as a real
        # leak in a file everyone believes is exempt. Pin the keys to the denylist.
        unknown = set(budget) - known
        assert not unknown, (
            f"_REPO_ALLOW[{rel!r}] names patterns that are not in _REPO_FORBIDDEN: "
            f"{sorted(unknown)}. Reference the same expression the denylist uses."
        )
        if rel not in present:
            continue  # not landed on this branch yet — see the note on _REPO_ALLOW
        text = present[rel] or ""
        for pattern, expected in budget.items():
            actual = len(re.findall(pattern, text, re.IGNORECASE))
            assert actual == expected, (
                f"_REPO_ALLOW[{rel!r}] declares {expected} × {pattern!r} but the file has "
                f"{actual}. If {actual} is right and every one of them belongs, change the "
                f"number; otherwise scrub the file. ({why})"
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


def test_the_guard_scans_every_help_topic(model_facing):
    """The help library is committed prose that a tool hands to the provider, so it is
    payload — and payload is the surface where blueprint identifiers are banned as well.
    A topic file added without reaching this fixture would be scanned against the narrower
    committed-file denylist only, which is exactly the hole this test closes."""
    from help.library import CONTENT_ROOT

    on_disk = {p.relative_to(CONTENT_ROOT).as_posix() for p in CONTENT_ROOT.rglob("*.md")}
    assert on_disk, f"no help topics found under {CONTENT_ROOT} — the help scan is not running"
    scanned = {
        label[len("help topic "):] for label, _ in model_facing
        if label.startswith("help topic ")
    }
    assert scanned == on_disk, (
        "help topics that reach the model but are not scanned for leaked tokens: "
        f"{sorted(on_disk - scanned)}"
    )
