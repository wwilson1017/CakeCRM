"""THE enforcement test for the genericization rule (issue #22, CLAUDE.md "Don't Do
This"): CakeCRM's sales prompting and tool descriptions are ported from the CAKE OS
sales agent, whose text is saturated with one company's customers, staff, products,
and industry jargon. This repo goes public and its history is permanent, so a single
leaked token is unfixable after the fact.

Modeled on ``test_gmail_guard.py``: the rule is enforced by a CI-failing scan, not by
reviewer vigilance.

What it scans is deliberately the **model-facing payload** — the assembled system
prompt, every tool name/description/schema, and the UI starter chips — not raw source
files. Source COMMENTS must be free to cite the blueprint by name ("ported from
cake_os/..."), which the whole repo does by convention; what must never carry a
company's fingerprints is the text an AI provider (and therefore a shipped product)
actually receives.
"""

import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
QUICK_ACTIONS = BACKEND.parent / "frontend" / "src" / "assistant" / "QuickActions.tsx"

# Company-specific tokens that must never reach the model. Each is a case-insensitive
# pattern: the company and its domain, its product, the blueprint's own names, and the
# trade-vertical jargon the blueprint's prompts use in examples.
_FORBIDDEN = [
    (r"tncheesecake", "company domain"),
    (r"tn\s+cheesecake", "company name"),
    (r"\btnc\b", "company abbreviation"),
    (r"cheesecake", "company product"),
    (r"\bcasey\b", "blueprint agent name (this assistant is user-named)"),
    (r"cake[_\s]os\b", "blueprint product name"),
    (r"cake_crm_", "blueprint tool prefix (CakeCRM uses crm_)"),
    (r"\biddba\b", "trade-show acronym from blueprint examples"),
    (r"\bnra\s*\d", "trade-show acronym from blueprint examples"),
    (r"restaurant_type", "vertical-specific field key from blueprint examples"),
    (r"\bcuisine\b", "vertical-specific field value from blueprint examples"),
    (r"distributor_tier", "vertical-specific field key from blueprint examples"),
]

# Strips // line comments and /* */ blocks from the TSX so a provenance comment there
# is treated the same way a Python comment is — only the shipped strings are scanned.
_TS_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _offenders(text: str, label: str) -> list[str]:
    found = []
    for pattern, why in _FORBIDDEN:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append(f"{label} — {m.group(0)!r} ({why})")
    return found


def _all_tool_defs() -> list[dict]:
    """EVERY tool def the assistant can advertise, from the real modules.

    Must stay exhaustive — a guard that silently covers a subset is worse than none,
    because people stop double-checking it. Pinned by
    test_the_guard_covers_every_registered_tool.
    """
    from assistant.registry import ToolRegistry
    from context_files.tools import get_context_file_tools
    from crm.tools import CRM_TOOL_DEFS
    from gmail.tools import GMAIL_TOOL_DEFS
    from memory.tools import get_memory_tools
    from notifications.tools import get_notification_tools
    from reminders.tools import get_reminder_tools

    defs = list(CRM_TOOL_DEFS) + list(GMAIL_TOOL_DEFS)
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
    static, volatile = identity.build_system_prompt({"name": "Baker", "personality": ""})
    hb_static, hb_volatile = heartbeat_service._heartbeat_prompt()

    rm_static, rm_volatile = heartbeat_service._reminder_prompt(
        {"id": 1, "message": "", "context": ""}
    )
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
