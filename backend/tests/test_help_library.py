"""Guard tests for the product help library (issue #143).

A hand-written manual rots. Prose truth is the one thing CI cannot check, so this file
enforces the parts that CAN be structural, and says plainly which part is convention:

1. **Tool-name integrity.** Every tool-shaped token in the library must be a tool this
   product actually registers. A renamed or deleted tool fails the build instead of
   leaving the manual recommending a ghost.
2. **Shape.** Slug grammar, front-matter validity, size ceilings, deterministic ordering.
3. **Contracts the content must not break.** No topic may promise to deliver mail, and no
   topic may contain an untrusted-content fence marker.
4. **Claims about the product that a source file can settle.** Which flows are admin-only
   is read out of the interface's own settings registry, not out of a second hand-written
   list here.

The convention half — a pull request that changes a user-facing flow updates its topic —
is stated in CLAUDE.md and is NOT mechanically testable. Guard 1 exists precisely because
it is the part that can be.

Per this repo's guard rules, every detector below carries a self-test on synthetic input
(a case it must flag AND a case it must not) plus an assertion that it reached the real
content **per topic**, never one repo-wide total: a sweep that quietly stops matching is a
permanent green, which reads as coverage and is worse than no guard.
"""

import re
from pathlib import Path

import pytest

from assistant import delimiters, identity
from assistant.registry import ToolRegistry
from help import library as lib, search as help_search
from help.tools import HELP_TOOL_DEFS, HELP_TOOL_EXECUTORS, get_help_tools

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
SETTINGS_SECTIONS_TS = ROOT / "frontend" / "src" / "crm" / "settingsSections.ts"
PROVIDERS_ROUTER = BACKEND / "providers" / "router.py"


def _topic_paths() -> list[Path]:
    return sorted(lib.CONTENT_ROOT.rglob("*.md"))


def _slug_of(path: Path) -> str:
    return path.relative_to(lib.CONTENT_ROOT).as_posix()[: -len(".md")]


# ── 1. tool-name integrity ──────────────────────────────────────────────────────────

# Any lowercase snake_case token. Deliberately NOT prefix-based: the issue proposed
# matching crm_*/todo_*/gmail_*/memory_*/help_*, which misses every unprefixed tool this
# product registers — the seven context-file tools and `notify_user` among them — so a
# manual could recommend a deleted `read_context_file` and the guard would say nothing.
# Matching everything tool-SHAPED and checking it against the exhaustive registered set
# is the only version that cannot be out-argued by a naming convention.
_TOOL_SHAPED = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")

# Tool-shaped tokens that are NOT tools. Kept short on purpose: user-facing prose should
# say "next action", not a column name, so an entry here has to justify itself. These
# three are stored status VALUES a user types and Baker writes through `todo_update`, so
# the manual has to spell them exactly.
_NOT_TOOLS = {
    "next_action": "a GTD status value the user and the todo tools both write",
    "waiting_for": "a GTD status value the user and the todo tools both write",
    "someday_maybe": "a GTD status value the user and the todo tools both write",
}


def _registered_tool_names() -> set[str]:
    """Every tool name this product can advertise or execute, in EITHER task mode.

    Read from the feature modules' own constants rather than from one constructed
    registry, because two composition rules would otherwise hide real tools from the
    guard: `get_gmail_tools()` returns nothing until Gmail is connected, and the task
    tools swap with the task mode, so a registry built here holds one mode's half.
    Executor-only aliases are included — they are dispatchable, so naming one is legal.
    """
    from context_files.tools import CONTEXT_FILE_TOOL_DEFS, CONTEXT_FILE_TOOL_EXECUTORS
    from crm.gtd_tools import GTD_TOOL_DEFS, GTD_TOOL_EXECUTORS
    from crm.tools import CRM_TOOL_DEFS, TOOL_EXECUTORS
    from gmail.tools import GMAIL_TOOL_DEFS
    from memory.tools import get_memory_tools
    from notifications.tools import get_notification_tools

    names: set[str] = set()
    for defs in (CRM_TOOL_DEFS, GTD_TOOL_DEFS, GMAIL_TOOL_DEFS, CONTEXT_FILE_TOOL_DEFS,
                 HELP_TOOL_DEFS, get_memory_tools()[0],
                 get_notification_tools(ToolRegistry())[0]):
        names |= {d["name"] for d in defs}
    names |= set(TOOL_EXECUTORS) | set(GTD_TOOL_EXECUTORS) | set(CONTEXT_FILE_TOOL_EXECUTORS)
    return names


def _ghost_tools(texts: dict[str, str], known: set[str] | None = None) -> list[str]:
    """Tool-shaped tokens in ``texts`` that name no registered tool.

    Takes the corpus as an argument so the self-test below can drive THIS function with
    synthetic content. A probe that only exercised the regex would leave the lookup — the
    part a one-character slip would break — unproven, and the sweep would pass vacuously.
    """
    known = _registered_tool_names() if known is None else known
    offenders = []
    for slug, text in texts.items():
        for token in _TOOL_SHAPED.findall(text):
            if token in known or token in _NOT_TOOLS:
                continue
            offenders.append(f"{slug}: {token!r} is not a registered tool")
    return offenders


def _library_texts() -> dict[str, str]:
    """Raw file text per topic slug. Raw, because front matter reaches the model too."""
    return {_slug_of(p): p.read_text(encoding="utf-8") for p in _topic_paths()}


def test_every_tool_the_library_names_is_registered():
    offenders = _ghost_tools(_library_texts())
    assert not offenders, (
        "The help library recommends tools that do not exist. A renamed or deleted tool "
        "leaves the manual telling the assistant to call a ghost — fix the topic, or add "
        "a commented entry to _NOT_TOOLS if the token is genuinely not a tool name:\n"
        + "\n".join(offenders)
    )


def test_the_ghost_tool_detector_flags_a_fake_and_spares_a_real_one():
    """Detector self-test, both directions. Without the second assertion a detector that
    flags everything would pass the first one and fail the build on every real topic."""
    assert _ghost_tools({"probe": "call `crm_not_a_real_tool` to do it"}), (
        "the detector did not flag a tool-shaped token that names no tool"
    )
    assert not _ghost_tools({"probe": "call `crm_get_deal_health` and `todo_create`"}), (
        "the detector flagged real registered tools"
    )
    assert not _ghost_tools({"probe": "file it as next_action"}), (
        "the declared _NOT_TOOLS allowance was not honoured"
    )


def test_the_ghost_tool_guard_reaches_every_topic_and_finds_real_tools():
    """Reached-the-real-content, PER TOPIC. A scan whose corpus quietly shrank to one
    file would still pass the assertion above; this fails instead."""
    texts = _library_texts()
    on_disk = {_slug_of(p) for p in _topic_paths()}
    assert texts.keys() == on_disk and on_disk, f"scanned {sorted(texts)}, expected {sorted(on_disk)}"

    known = _registered_tool_names()
    naming = {slug for slug, text in texts.items()
              if any(t in known for t in _TOOL_SHAPED.findall(text))}
    # Not every topic names a tool, and it would be wrong to force one. But a corpus where
    # NO topic does means the regex, the reader or the known-set has stopped working.
    assert len(naming) >= 10, (
        f"only {len(naming)} topics name a registered tool — the tool-name scan is not "
        "actually matching the shipped content"
    )


def test_the_known_tool_set_covers_the_real_registry():
    """The exhaustive set above is hand-composed from module constants, so pin it against
    what the registry really builds — in both the interactive and background shapes.

    This direction alone is NOT sufficient, which is why the test below exists: a
    connection-gated family is absent from a hermetically constructed registry, so
    dropping it from the hand-composed tuple would leave this set difference empty and
    this test green.
    """
    known = _registered_tool_names()
    for reg in (ToolRegistry(), ToolRegistry(background=True)):
        missing = set(reg.writes_map) - known
        assert not missing, f"tools the help guard would call ghosts: {sorted(missing)}"


def test_the_known_tool_set_covers_the_connection_gated_families_too():
    """Gmail's tools are advertised only once a mailbox is connected, so the registry in a
    hermetic test never carries them. Pin them against their own module constant instead,
    or dropping that source from `_registered_tool_names` would turn every Gmail tool the
    manual names into a false 'ghost' — and the test above would not notice."""
    from gmail.tools import GMAIL_TOOL_DEFS

    known = _registered_tool_names()
    gmail_names = {d["name"] for d in GMAIL_TOOL_DEFS}
    assert gmail_names and gmail_names <= known, sorted(gmail_names - known)
    assert not ({d["name"] for d in GMAIL_TOOL_DEFS} & set(ToolRegistry().writes_map)), (
        "Gmail tools are advertised without a connection now — this test's premise is "
        "stale, and the one above can cover them directly"
    )


# ── 2. shape: slugs, front matter, bounds, determinism ──────────────────────────────

def test_the_shipped_library_loads_and_is_not_empty():
    library = lib.get_library()
    assert len(library.topics) == len(_topic_paths()) >= 20


def test_every_topic_is_within_its_bounds():
    for topic in lib.get_library().topics:
        assert len(topic.title) <= lib.MAX_TITLE_CHARS, topic.slug
        assert len(topic.description) <= lib.MAX_DESCRIPTION_CHARS, topic.slug
        assert len(topic.aliases) <= lib.MAX_ALIASES, topic.slug
        assert len(topic.slug) <= lib.MAX_SLUG_CHARS, topic.slug
        assert topic.body, topic.slug
        assert len(topic.body) <= lib.MAX_TOPIC_CHARS, (
            f"{topic.slug} is {len(topic.body)} characters — split it into child topics"
        )


def _write(root: Path, rel: str, body: str = "Body text.", **meta) -> None:
    front = {"title": "T", "description": "D", **meta}
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(f"{k}: {v}" for k, v in front.items())
    path.write_text(f"---\n{lines}\n---\n{body}\n", encoding="utf-8")


def test_a_good_slug_loads(tmp_path):
    """Control for the test below: a detector that rejected everything would pass it."""
    _write(tmp_path, "settings/gmail.md")
    assert [t.slug for t in lib.load_library(tmp_path).topics] == ["settings/gmail"]


@pytest.mark.parametrize("bad", [
    "Settings/Gmail.md",            # uppercase
    "settings/deep/nested.md",      # more than one folder level
    "settings/has space.md",        # space
    "settings/Trailing_Underscore.md",  # underscore
    "settings/double--hyphen.md",   # empty segment between hyphens
])
def test_a_bad_slug_is_rejected(tmp_path, bad):
    _write(tmp_path, bad)
    with pytest.raises(lib.HelpLibraryError):
        lib.load_library(tmp_path)


@pytest.mark.parametrize("meta, body", [
    ({"title": ""}, "Body."),                      # empty title
    ({"description": ""}, "Body."),                # empty description
    ({"admin": "maybe"}, "Body."),                 # not a boolean
    ({"colour": "red"}, "Body."),                  # unknown key
    ({}, ""),                                      # empty body
    ({}, "x" * (lib.MAX_TOPIC_CHARS + 1)),         # over the ceiling
    ({"title": "x" * (lib.MAX_TITLE_CHARS + 1)}, "Body."),
])
def test_malformed_front_matter_is_rejected(tmp_path, meta, body):
    _write(tmp_path, "topic.md", body=body, **meta)
    with pytest.raises(lib.HelpLibraryError):
        lib.load_library(tmp_path)


def test_a_file_with_no_front_matter_is_rejected(tmp_path):
    (tmp_path / "topic.md").write_text("# Just a heading\n", encoding="utf-8")
    with pytest.raises(lib.HelpLibraryError):
        lib.load_library(tmp_path)


@pytest.mark.parametrize("rel", [
    "settings/" + "a" * (lib.MAX_SLUG_CHARS + 1) + ".md",
    "a" * (lib.MAX_SLUG_CHARS + 1) + ".md",
])
def test_an_overlong_slug_is_rejected(tmp_path, rel):
    """MAX_SLUG_CHARS is checked before the grammar, so an otherwise legal name that is
    simply enormous still has to fail — the bounds comment claims every ceiling is pinned,
    and three of them were not until this and the test below existed."""
    _write(tmp_path, rel)
    with pytest.raises(lib.HelpLibraryError):
        lib.load_library(tmp_path)


@pytest.mark.parametrize("aliases", [
    ", ".join(f"alias{i}" for i in range(lib.MAX_ALIASES + 1)),  # too many
    "x" * (lib.MAX_ALIAS_CHARS + 1),                             # one too long
])
def test_alias_bounds_are_enforced(tmp_path, aliases):
    _write(tmp_path, "topic.md", aliases=aliases)
    with pytest.raises(lib.HelpLibraryError):
        lib.load_library(tmp_path)


def test_aliases_within_the_bounds_load(tmp_path):
    """Control: a detector that rejected every alias list would pass the test above."""
    _write(tmp_path, "topic.md", aliases="one, two, three")
    assert lib.load_library(tmp_path).topics[0].aliases == ("one", "two", "three")


@pytest.mark.parametrize("front, message", [
    ("title: T\ndescription: D\nthis line has no colon", "not 'key: value'"),
    ("title: T\ntitle: Second\ndescription: D", "duplicate front-matter key"),
])
def test_malformed_front_matter_lines_are_rejected(tmp_path, front, message):
    """Both rejections are deliberate per the parser's docstring — a typo in a key must be
    a hard error, not a silently dropped field — so both are forced to fire.

    The expected MESSAGE is pinned, not merely the exception type. Without it the first
    case proves nothing: a line with no colon would go on to fail the unknown-key check
    anyway, so the branch under test could be deleted and the test would stay green on a
    different error.
    """
    (tmp_path / "topic.md").write_text(f"---\n{front}\n---\nBody.\n", encoding="utf-8")
    with pytest.raises(lib.HelpLibraryError, match=message):
        lib.load_library(tmp_path)


def test_a_missing_required_key_is_rejected(tmp_path):
    (tmp_path / "topic.md").write_text("---\ntitle: T\n---\nBody.\n", encoding="utf-8")
    with pytest.raises(lib.HelpLibraryError):
        lib.load_library(tmp_path)


def test_an_empty_content_directory_is_rejected(tmp_path):
    with pytest.raises(lib.HelpLibraryError):
        lib.load_library(tmp_path)


def test_warm_never_raises(monkeypatch, caplog):
    """A help outage must never take the server down: warm() runs in the lifespan."""
    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr(lib, "get_library", boom)
    lib.warm()  # must not raise
    assert any("help library failed to warm" in r.message for r in caplog.records)


def test_search_is_deterministic():
    library = lib.get_library()
    for query in ("deal", "how do I connect gmail", "task"):
        first = [(h.topic.slug, h.score) for h in help_search.search(library, query, 8)]
        second = [(h.topic.slug, h.score) for h in help_search.search(library, query, 8)]
        assert first == second


def test_search_order_does_not_depend_on_the_order_topics_were_read():
    """Ties are the normal case on a corpus this small, so the slug tie-break is what
    makes the answer a function of the query alone. Reversing the corpus proves it."""
    library = lib.get_library()
    reversed_library = lib.Library(topics=tuple(reversed(library.topics)))
    for query in ("deal", "settings", "todo"):
        assert (
            [h.topic.slug for h in help_search.search(library, query, 8)]
            == [h.topic.slug for h in help_search.search(reversed_library, query, 8)]
        )


@pytest.mark.parametrize("query, expected", [
    ("connect gmail", "settings/gmail"),
    ("why can't Baker send email", "settings/gmail"),
    ("restore an archived deal", "pipeline/archived-deals"),
    ("how do I add an AI key?", "settings/ai-providers"),
    ("change my password", "settings/passwords-and-2fa"),
    ("what is a lead score", "pipeline/deal-health-and-scores"),
    ("who owns this contact", "contacts-and-companies/ownership"),
    ("bulk move deals", "pipeline/bulk-moves"),
    ("merge two contacts", "contacts-and-companies/dedupe"),
    ("how do I import a spreadsheet", "contacts-and-companies/import"),
    ("capture a todo from my phone", "tasks/no-login-surfaces"),
    ("upload a logo", "settings/branding"),
    ("add a user to my team", "settings/team"),
])
def test_search_answers_the_questions_it_exists_for(query, expected):
    """A determinism test passes on a search that is uselessly consistent. These are the
    real questions, against the real corpus — the test that fails when search stops
    working rather than when it stops being reproducible."""
    hits = help_search.search(lib.get_library(), query, 3)
    assert hits, f"no result at all for {query!r}"
    assert hits[0].topic.slug == expected, (
        f"{query!r} ranked {[h.topic.slug for h in hits]}, wanted {expected} first"
    )


def test_nearest_always_answers_even_when_nothing_scores():
    """`nearest` backs help_read_topic's fail-closed miss, and its promise is that a
    genuinely nonsense topic name still gets suggestions. The real-hit branch is exercised
    by the miss test below; this drives the fallback, which otherwise could regress to an
    empty list with nothing failing."""
    library = lib.get_library()
    assert not help_search.search(library, "zzzqxx wwvvyy", 3), "the probe must score nothing"
    fallback = help_search.nearest(library, "zzzqxx wwvvyy", 3)
    assert fallback == list(library.topics[:3])


def test_a_query_that_matches_nothing_returns_the_shape_of_the_library():
    result = HELP_TOOL_EXECUTORS["help_search"](query="zzzz qqqq vvvv")
    assert result["results"] == []
    assert result["sections"] == list(lib.get_library().folders)


# ── 3. the executors reach the real content, per topic ──────────────────────────────

def test_every_topic_is_readable_through_the_real_tool():
    """Executor-level and PER TOPIC: call the shipped tool for every slug and assert a
    sentinel taken independently from that file's own bytes reached the payload. Running
    the same implementation on both sides of an assertion proves nothing, so the sentinel
    is read off disk here rather than through the library."""
    for path in _topic_paths():
        slug = _slug_of(path)
        raw = path.read_text(encoding="utf-8")
        sentinel = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")][-1]

        payload = HELP_TOOL_EXECUTORS["help_read_topic"](topic=slug)

        assert "error" not in payload, f"{slug}: {payload}"
        assert payload["topic"] == slug
        assert payload["title"] in raw
        assert sentinel.strip() in payload["content"], (
            f"{slug}: the tool's payload does not carry that file's own last line"
        )
        assert "---" not in payload["content"].splitlines()[:1], (
            f"{slug}: front matter leaked into the model-facing body"
        )


def test_every_topic_is_reachable_by_browsing():
    listing = HELP_TOOL_EXECUTORS["help_list_topics"]()
    reachable = {t["topic"] for t in listing["topics"]}
    for section in listing["sections"]:
        reachable |= {
            t["topic"]
            for t in HELP_TOOL_EXECUTORS["help_list_topics"](folder=section["section"])["topics"]
        }
    assert reachable == {_slug_of(p) for p in _topic_paths()}


def test_an_unknown_topic_fails_closed_without_echoing_the_argument():
    bogus = "settings/gmial-<script>"
    result = HELP_TOOL_EXECUTORS["help_read_topic"](topic=bogus)
    assert result["error"] == "No help topic by that name."
    assert result["closest"], "a miss must answer with the corpus's nearest matches"
    assert bogus not in repr(result), (
        "a model-supplied string was reflected back into an unfenced tool result"
    )


def test_an_unknown_section_fails_closed_without_echoing_the_argument():
    bogus = "nonsense-<script>"
    result = HELP_TOOL_EXECUTORS["help_list_topics"](folder=bogus)
    assert result["error"] == "No help section by that name."
    assert result["sections"] == list(lib.get_library().folders)
    assert bogus not in repr(result)


@pytest.mark.parametrize("name", ["help_search", "help_read_topic", "help_list_topics"])
def test_non_string_arguments_are_refused_rather_than_crashing(name):
    """A provider can decode malformed tool JSON to a list, a number or a dict."""
    arg = {"help_search": "query", "help_read_topic": "topic", "help_list_topics": "folder"}[name]
    for bad in (42, ["a"], {"a": 1}, True):
        assert "error" in HELP_TOOL_EXECUTORS[name](**{arg: bad})


@pytest.mark.parametrize("limit", ["abc", None, [1, 2], {}, 3.7, -5, 10**9])
def test_a_malformed_limit_degrades_instead_of_crashing(limit):
    """`limit` is model-supplied like every other argument, so a provider that decodes it
    to a string, a list or nothing at all must not take the turn down with it."""
    result = HELP_TOOL_EXECUTORS["help_search"](query="deal", limit=limit)
    assert "error" not in result, result
    assert 1 <= len(result["results"]) <= help_search.MAX_RESULTS


def test_search_results_and_snippets_are_bounded():
    library = lib.get_library()
    hits = help_search.search(library, "deal contact company settings todo", 999)
    assert len(hits) <= help_search.MAX_RESULTS
    for hit in hits:
        assert len(hit.snippet) <= help_search.SNIPPET_CHARS + 1  # +1 for the ellipsis


# ── 4. contracts the content must not break ────────────────────────────────────────

def _mail_delivery_claims(texts: dict[str, str]) -> list[str]:
    claims = ("send the email", "send an email", "i can send", "sends the email",
              "send emails", "can send mail for you")
    return [f"{slug}: {claim!r}" for slug, text in texts.items()
            for claim in claims if claim in text.lower()]


def test_no_topic_promises_to_deliver_mail():
    """Read and create-a-draft, forever (SECURITY.md). A manual that says the assistant
    can deliver mail is worse than a prompt that does: the model will read it and repeat
    it as fact."""
    offenders = _mail_delivery_claims(_library_texts())
    assert not offenders, "help content claims mail can be delivered:\n" + "\n".join(offenders)


def test_the_mail_claim_detector_bites():
    assert _mail_delivery_claims({"probe": "Baker can send an email for you."})
    assert not _mail_delivery_claims({"probe": "Baker drafts a reply for you to review."})


def _marker_offenders(texts: dict[str, str]) -> list[str]:
    return [f"{slug}: contains {marker!r}" for slug, text in texts.items()
            for marker in delimiters.UNTRUSTED_MARKERS if marker in text]


def test_no_topic_contains_an_untrusted_content_marker():
    """The engine substring-scans the whole assembled conversation for these markers and
    permanently tightens write confirmation when it finds one. A topic that merely QUOTED
    a marker while explaining fences would silently downgrade every later turn of any
    conversation that read it. So the topics describe fences in words — and the probe is
    built FROM the real constants, so a renamed marker cannot slip past this."""
    offenders = _marker_offenders(_library_texts())
    assert not offenders, "\n".join(offenders)


def test_the_marker_detector_bites():
    marker = delimiters.UNTRUSTED_MARKERS[0]
    assert _marker_offenders({"probe": f"an example: {marker} name=x>"})
    assert not _marker_offenders({"probe": "uploaded content is wrapped and labelled"})


# ── 5. claims a source file can settle ─────────────────────────────────────────────

# Topic -> the settings card whose gating it must agree with. The card ids are the ones
# settingsSections.ts declares; parsing that file is what makes a real gating change fail
# here, where two hand-written lists in this repo would just agree with each other.
_TOPIC_TO_CARD = {
    "settings/gmail": "gmail",
    "settings/telegram": "telegram",
    "settings/task-mode": "task_mode",
    "settings/custom-fields": "custom_fields",
    "settings/branding": "branding",
    "settings/team": "team",
    "settings/notifications": "notifications",
    "settings/passwords-and-2fa": "change_password",
}


def _admin_only_cards() -> dict[str, bool]:
    source = SETTINGS_SECTIONS_TS.read_text(encoding="utf-8")
    found = dict(
        (card, flag == "true")
        for card, flag in re.findall(r"\{\s*id:\s*'([a-z_]+)',\s*adminOnly:\s*(true|false)\s*\}",
                                     source)
    )
    assert found, f"could not parse card gating out of {SETTINGS_SECTIONS_TS}"
    return found


def test_settings_topics_agree_with_the_interface_about_who_may_do_it():
    """"Don't offer what can only 403" applies to advice too. If a card's gating changes,
    the topic that walks someone through it must change with it."""
    cards = _admin_only_cards()
    unknown = set(_TOPIC_TO_CARD.values()) - set(cards)
    assert not unknown, f"_TOPIC_TO_CARD names cards that no longer exist: {sorted(unknown)}"

    library = lib.get_library()
    for slug, card in _TOPIC_TO_CARD.items():
        topic = library.get(slug)
        assert topic is not None, f"{slug} is missing from the library"
        assert topic.admin_only == cards[card], (
            f"{slug} says admin_only={topic.admin_only} but the {card!r} card says "
            f"adminOnly={cards[card]}"
        )


def test_the_card_gating_parser_reaches_real_data():
    cards = _admin_only_cards()
    assert cards.get("branding") is True and cards.get("change_password") is False, cards


def test_the_ai_provider_topic_is_admin_because_the_route_is():
    """AI providers are configured outside the Settings card registry, so this one is
    pinned against the route's own guard instead of against a second list."""
    source = PROVIDERS_ROUTER.read_text(encoding="utf-8")
    route = re.search(r'@router\.post\("/\{provider\}/connect-key"\)\s*\n\s*async def [^\n]+\n',
                      source)
    assert route, "the connect-key route moved — repoint this test"
    assert "require_admin" in route.group(0), route.group(0)
    assert lib.get_library().get("settings/ai-providers").admin_only is True


# ── 6. registry, prompt and trust wiring ───────────────────────────────────────────

def test_the_three_help_tools_are_registered_and_read_only():
    defs, executors = get_help_tools()
    names = {d["name"] for d in defs}
    assert names == {"help_search", "help_read_topic", "help_list_topics"}
    assert set(executors) == names
    registry = ToolRegistry()
    for name in names:
        assert name in registry.writes_map, f"{name} is not composed into the registry"
        assert registry.is_write(name) is False, f"{name} must not be a write tool"


def test_composing_the_registry_does_not_read_the_disk(monkeypatch):
    """`get_help_tools()` runs on every chat turn. A def built from the corpus would put
    file I/O and a possible exception on the path that constructs the registry, so one bad
    topic file would break chat instead of breaking one tool call."""
    def boom():
        raise AssertionError("the registry must not load the help library")
    monkeypatch.setattr(lib, "get_library", boom)
    assert {d["name"] for d in ToolRegistry().tool_defs} >= {"help_search"}


def test_help_reads_do_not_taint_the_turn():
    """Pinned in BOTH directions. Help content is our own committed prose — the one
    payload class that cannot carry hostile user text — so a help read must not force
    every later write in the conversation back to asking. A later "fence everything"
    refactor has to fail here rather than silently downgrade power mode."""
    from assistant import engine

    names = {d["name"] for d in HELP_TOOL_DEFS}
    assert not (names & delimiters.UNTRUSTED_SOURCE_TOOLS)
    assert not (names & engine._CONNECTION_BOUND_WRITE_TOOLS)
    assert not (names & engine._VERSION_BOUND_WRITE_TOOLS)
    # The other direction: the sets this is absent from are not empty, so the assertions
    # above are testing something.
    assert delimiters.UNTRUSTED_SOURCE_TOOLS and engine._CONNECTION_BOUND_WRITE_TOOLS


def test_help_tools_are_callable_on_an_unattended_turn():
    """They are reads of our own content, so the background allowlist derives them in —
    and that is the right answer, unlike a live external read."""
    from assistant.background import background_allowlist

    registry = ToolRegistry(background=True)
    assert {d["name"] for d in HELP_TOOL_DEFS} <= background_allowlist(registry)


def test_help_note_is_in_the_cacheable_static_half():
    static, volatile = identity.build_system_prompt({"name": "Baker", "personality": ""})
    assert identity.HELP_NOTE in static
    assert identity.HELP_NOTE not in volatile


def test_help_note_sits_after_the_contracts_it_does_not_outrank():
    """Position, not presence (the NAME_NOTE treatment). It must come after the identity
    texts and the name contract, so nothing a user or the assistant writes can move it,
    and before the upload-safety instruction, which stays last."""
    static, _ = identity.build_system_prompt({"name": "Baker", "personality": ""})
    assert static.index(identity.NAME_NOTE) < static.index(identity.HELP_NOTE)
    assert static.index(identity.CONTEXT_FILES_NOTE) < static.index(identity.HELP_NOTE)
    assert static.index(identity.HELP_NOTE) < static.index(delimiters.UPLOAD_SAFETY_INSTRUCTION)


def test_help_note_survives_a_custom_personality():
    static, _ = identity.build_system_prompt(
        {"name": "Baker", "personality": "You are a laconic robot."}
    )
    assert identity.HELP_NOTE in static and "laconic robot" in static


def test_help_note_names_the_real_sections():
    """The note quotes the library's shape into the cached prompt. Adding a folder without
    updating that line leaves the model a stale map, so the two are pinned together."""
    for folder in lib.get_library().folders:
        assert folder in identity.HELP_NOTE, f"section {folder!r} is missing from HELP_NOTE"
    named = {word for word in re.findall(r"[a-z][a-z-]+", identity.HELP_NOTE)}
    stale = {f for f in ("reminders", "integrations", "gmail", "telegram")
             if f in named} - set(lib.get_library().folders)
    assert not stale, f"HELP_NOTE names sections that are not folders: {sorted(stale)}"


def test_help_note_stays_slim():
    """The whole design is that the library's bulk never enters the prompt. A note that
    grew into a per-topic manifest would put that cost back, one deploy at a time."""
    assert len(identity.HELP_NOTE) < 1200, len(identity.HELP_NOTE)
    body = sum(len(t.body) for t in lib.get_library().topics)
    assert len(identity.HELP_NOTE) < body / 10
