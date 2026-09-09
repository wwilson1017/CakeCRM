"""Deal deep links — the shape, and the rule that every deal-returning tool carries one.

WHY THIS FILE EXISTS. Issue #145 was filed because nothing in the CRM *produced* a
per-deal URL, so the assistant could not hand one out and the format was discoverable
nowhere. These tests pin the two things that would silently undo that:

1. **The exact path string.** ``frontend/src/crm/PipelinePage.tsx`` is the one parser;
   ``crm/links.py`` and ``frontend/src/crm/dealDeepLink.ts`` are two independent
   producers of what it parses, and the duplication is unavoidable because a browser
   cannot call Python. ``test_the_frontend_producer_agrees_with_the_backend_one``
   **reads the TypeScript source** and compares the template it emits against the Python
   one.

   That cross-language read is the whole point, and it is deliberately not "both suites
   assert the same hardcoded literal". Two independent literals pin nothing: editing
   ``DEAL_PATH_TEMPLATE`` and this file's expectation in one commit is a natural,
   self-consistent change that leaves every suite green and the two producers emitting
   different shapes.

2. **That every tool which attaches a URL also tells the model about it.** Derived from
   the executors' own source, so it cannot be satisfied by editing a list to match the
   bug.

   **Be precise about what this proves**, because overstating it is what let the
   blueprint's first cut ship: it enforces *attaches => registered => carries the
   guidance*. On its own it structurally CANNOT enforce the converse — *returns deal
   records => attaches* — because an executor that attaches nothing contains nothing to
   match. ``test_no_executor_reaches_a_deal_returning_service_without_deciding_about_links``
   closes that from the other side, and the two together are what make the rule real.

DB-free by construction: everything here is string building or source inspection.
"""

import inspect
import pathlib
import re

import pytest

from crm import links, tools

#: The literal the frontend must agree with. Deliberately spelled out rather than built
#: from links.DEAL_PATH_TEMPLATE — a test that derives its expectation from the code
#: under test passes no matter what that code says.
FRONTEND_DEAL_PATH = "/crm/pipeline?deal=42"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEAL_DEEP_LINK_TS = REPO_ROOT / "frontend" / "src" / "crm" / "dealDeepLink.ts"


# ── The shape ────────────────────────────────────────────────────────────────────


def test_deal_path_matches_the_frontend_literal():
    assert links.deal_path(42) == FRONTEND_DEAL_PATH


def test_the_frontend_producer_agrees_with_the_backend_one():
    """The real cross-language pin (see this module's docstring).

    Reads the TypeScript source, extracts the template ``dealDeepLink`` emits, and
    compares it to the Python one.
    """
    assert DEAL_DEEP_LINK_TS.is_file(), f"frontend deep-link module not found at {DEAL_DEEP_LINK_TS}"
    body = DEAL_DEEP_LINK_TS.read_text(encoding="utf-8")

    # Anchor on the function name, then take the first backtick template after it. Loose
    # enough to survive a signature or return-type reformat, tight enough that it can
    # only ever read `dealDeepLink`'s own template.
    match = re.search(r"export function dealDeepLink\b[^`]*`([^`]+)`", body)
    assert match, (
        "could not find dealDeepLink's returned template in dealDeepLink.ts — if the "
        "function was refactored away from a template literal, update this extraction "
        "rather than deleting the check; it is the only thing tying the frontend and "
        "backend deep-link shapes together."
    )

    # `${dealId}` on the TS side is `{id}` on the Python side; normalise, then compare
    # the literal text around it exactly.
    ts_template = match.group(1).replace("${dealId}", "{id}")
    assert ts_template == links.DEAL_PATH_TEMPLATE, (
        "frontend dealDeepLink and backend DEAL_PATH_TEMPLATE have diverged — "
        f"TS emits {ts_template!r}, Python emits {links.DEAL_PATH_TEMPLATE!r}. "
        "PipelinePage parses only one shape; update both producers together."
    )


def test_the_frontend_parser_reads_the_parameter_the_backend_writes():
    """The template pin above compares whole strings, so it would still pass if both
    producers moved to `?d=` while the page kept reading `?deal=`. Assert the parameter
    name the TS module exports is the one the Python template emits."""
    body = DEAL_DEEP_LINK_TS.read_text(encoding="utf-8")
    match = re.search(r"DEAL_DEEP_LINK_PARAM\s*=\s*'([^']+)'", body)
    assert match, "DEAL_DEEP_LINK_PARAM not found in dealDeepLink.ts"
    assert f"?{match.group(1)}=" in links.DEAL_PATH_TEMPLATE, (
        f"PipelinePage reads ?{match.group(1)}= but the backend emits "
        f"{links.DEAL_PATH_TEMPLATE!r}"
    )


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("https://crm.example.com", "https://crm.example.com" + FRONTEND_DEAL_PATH),
        # A trailing slash must not produce "//crm/pipeline?..." — deal_path supplies the
        # leading slash, so the base is rstrip'd.
        ("https://crm.example.com/", "https://crm.example.com" + FRONTEND_DEAL_PATH),
    ],
)
def test_deal_url_joins_a_configured_base_without_doubling_the_slash(
    monkeypatch, configured, expected,
):
    from core.config import settings

    monkeypatch.setattr(settings, "frontend_url", configured)
    monkeypatch.setattr(settings, "frontend_url_is_default", False)
    assert links.deal_url(42) == expected


def test_deal_url_stays_relative_when_no_public_address_was_configured(monkeypatch):
    """`settings.frontend_url` always holds a string, but its last fallback is the
    localhost dev default nobody chose. Asserting that hostname in a message sent to
    someone's phone is worse than a path their browser can resolve."""
    from core.config import settings

    monkeypatch.setattr(settings, "frontend_url", "http://localhost:5173")
    monkeypatch.setattr(settings, "frontend_url_is_default", True)
    assert links.deal_url(42) == FRONTEND_DEAL_PATH


# ── with_deal_url ────────────────────────────────────────────────────────────────


def test_with_deal_url_attaches_a_url_and_preserves_every_other_key(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "frontend_url", "https://crm.example.com")
    monkeypatch.setattr(settings, "frontend_url_is_default", False)
    deal = {"id": 42, "title": "Big One", "value": 1000}
    result = links.with_deal_url(deal)
    assert result["url"] == "https://crm.example.com" + FRONTEND_DEAL_PATH
    # Purely additive — a consumer reading the old shape is unaffected.
    assert result["title"] == "Big One"
    assert result["value"] == 1000


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"id": None},
        {"id": "42"},
        # bool is an int subclass, so `isinstance(True, int)` passes and this would have
        # emitted `?deal=True`. This case IS the reason the guard spells `type(...) is int`.
        {"id": True},
        "not-a-dict",
    ],
)
def test_with_deal_url_is_inert_without_an_integer_id(bad):
    """A tool can return an error dict, None, or a projection that dropped the id.
    A link built from a missing or string id points at a deal that does not exist,
    which is worse than no link — such payloads pass through untouched."""
    result = links.with_deal_url(bad)
    assert result is bad
    if isinstance(bad, dict):
        assert "url" not in bad


# ── The registry, derived from the executors themselves ──────────────────────────


def _executors_that_attach_a_deal_url() -> set[str]:
    """Derive, from the executors' own source, which CRM tools attach a deal URL.

    Deliberately NOT a hand-written list. The blueprint's first cut guarded this with a
    literal six-name tuple that matched the six places the code happened to append the
    guidance — so the four tools that attached a `url` and never mentioned it sailed
    through a green test. A hand-list can only ever re-assert the mistake it was written
    beside.
    """
    names = set()
    for name, executor in tools.TOOL_EXECUTORS.items():
        try:
            source = inspect.getsource(executor)
        except (OSError, TypeError):  # pragma: no cover - executors are plain functions
            continue
        if "with_deal_url" in source or "deal_url(" in source:
            names.add(name)
    return names


#: Service functions that return deal records. An executor calling one of these either
#: attaches a URL or is explicitly waived below — this is the converse the source-grep
#: guard cannot prove on its own, and the reason it is worth having. In the blueprint
#: three separate review rounds each surfaced another unlinked tool, and each time the
#: miss was a DECISION nobody was ever asked to make. Now CI asks.
DEAL_RETURNING_SERVICES = (
    "get_pipeline", "search_deals", "get_deal_detail", "get_deal", "create_deal",
    "update_deal", "update_deal_stage", "mark_deal_won", "mark_deal_lost",
    "archive_deal", "merge_deals", "bulk_move_deals", "get_stale_deals",
    "find_duplicates", "scan_gaps", "get_deal_health", "get_dashboard_stats",
    "get_contact_detail", "get_company_detail", "summarize_analytics",
    # Reachable only through `find_duplicates`, which is itself listed — but a future tool
    # could call it directly, and it does hand back deal identities ({id, label} pairs that
    # the deal branch links).
    "find_duplicate_deals",
    # No tool reaches it today; the REST board does. Listed rather than waived so the first
    # tool that calls it has to decide about links rather than inherit an omission.
    "list_deals",
    # #146's per-rep drill-down helper. Its SELECT hands back deal records — id, title,
    # value, stage, owner_id, contact_name, company_name — so it belongs here even though
    # only the REST weekly-touches surface calls it today, for the same reason as
    # `list_deals` above: the first tool to reach it must decide about links.
    "_touched_deal_rows",
    # #131's Today-panel reader. It hands back deal records — id, title, value, owner_id,
    # idle time — for the dashboard only; no tool reaches it today, and it is listed rather
    # than waived so the first one that does has to decide about links.
    "_fetch_hot_deals",
)

#: Service functions that read the deals table but hand back no deal RECORD, each checked
#: against its return statement. This is the other half of the list above:
#: ``test_the_deal_returning_service_list_covers_every_service_that_reads_deals`` requires
#: every deals-reading service to appear in one list or the other, so a new one cannot be
#: silently absent from both.
NON_RECORD_DEAL_SERVICES = {
    "get_analytics": "daily series and bucket counts; summarize_analytics carries the records",
    "get_pipeline_analytics": "per-stage durations and conversion rates, no rows",
    "get_weekly_touches": "per-deal touch counts for one card — id, title, count, no deal row",
    "get_contact_staleness": "contact rows; the deal join is only a has-open-deal test",
    "is_crm_empty": "a boolean",
    "_crm_empty_in_txn": "a boolean",
    "_truncate_all": "no return value",
    "clear_all": "no return value",
    "load_sample_data": "no return value",
    "_touch_rep_rows": "one row per owner bucket — open/computed/touched counts, no deal rows",
    "_write_deal_update": "returns True/False — the caller re-reads the row it wants",
    "delete_company": "returns True/False; it reads deals only to unlink them",
    "delete_contact": "returns True/False; it reads deals only to unlink them",
}

#: Executors that reach a deal-returning service but legitimately return no deal record.
#: Each verified by reading the service's return shape — a waiver is a recorded decision,
#: not a way to silence the test.
NO_DEAL_PAYLOAD_WAIVERS = {
    "crm_bulk_move_deals": (
        "bulk_move_deals returns {ok, updated, updated_ids, errors} — counts and bare "
        "ids, no deal records. The ids are the deals it moved, but the assistant already "
        "holds those from the call it just made."
    ),
    "crm_get_lead_score": (
        "score_deal returns {score, factors}; the crm.get_deal row it also reads is used "
        "only to pull stored_score off it, and is never returned."
    ),
    "crm_get_deal_fields": "returns custom-field definitions and values, not a deal record",
    "crm_set_deal_fields": "returns the written field values, not a deal record",
    "crm_recompute_lead_scores": "returns a per-scope count of rows rescored",
}


def test_no_executor_reaches_a_deal_returning_service_without_deciding_about_links():
    """The converse guard.

    ``_executors_that_attach_a_deal_url`` proves *attaches => registered => guided*. It
    cannot prove *returns deal records => attaches*, because an executor that forgets the
    helper contains nothing to match — which is exactly how the blueprint kept finding
    another unlinked tool. This closes that by approaching from the other side: reach a
    service that hands back deals and you must either attach the URL or record why not.
    """
    attaching = _executors_that_attach_a_deal_url()
    service_re = re.compile(r"\b(" + "|".join(DEAL_RETURNING_SERVICES) + r")\b")

    offenders = []
    for name, executor in tools.TOOL_EXECUTORS.items():
        if name in attaching or name in NO_DEAL_PAYLOAD_WAIVERS:
            continue
        try:
            source = inspect.getsource(executor)
        except (OSError, TypeError):  # pragma: no cover
            continue
        hit = service_re.search(source)
        if hit:
            offenders.append(f"{name} (calls {hit.group(1)})")

    assert not offenders, (
        "These CRM tools reach a deal-returning service but neither attach a deal `url` "
        "nor carry a waiver: " + ", ".join(sorted(offenders)) + ". Trace what the service "
        "actually returns — do not reason from the tool's name, which is how the "
        "blueprint twice documented its dashboard tool as 'an aggregate' while it "
        "returned five full deal rows. If it really returns no deal record, add it to "
        "NO_DEAL_PAYLOAD_WAIVERS with the reason."
    )


def _services_that_read_the_deals_table() -> set[str]:
    """Public service functions whose own SQL reads the deals table.

    Derived from source rather than hand-listed, because the hand list is exactly where
    this guard went blind once already: ``crm_get_company`` returns the company rollup's
    full deal rows, and it sailed through because ``get_company_detail`` was not in
    ``DEAL_RETURNING_SERVICES``. A converse guard whose input is a hand-written list can
    only ever catch what someone already thought of.
    """
    from crm import analytics_service, service as crm_service, today_service

    names = set()
    # Every module that reads the deals table, not just the two that started out doing
    # so — a scan whose input is a hand-picked module list has the same blind spot as a
    # hand-picked function list, and #131 put a deal reader in `today_service`.
    for module in (crm_service, analytics_service, today_service):
        for name, fn in vars(module).items():
            if not callable(fn) or not getattr(fn, "__module__", "").startswith("crm."):
                continue
            try:
                source = inspect.getsource(fn)
            except (OSError, TypeError):  # pragma: no cover
                continue
            if re.search(r"\bFROM\s+deals\b", source, re.IGNORECASE):
                names.add(name)
    return names


def test_the_deal_returning_service_list_covers_every_service_that_reads_deals():
    """Every service whose SQL reads `deals` must be classified — as one that hands back
    deal records (so an unlinked caller fails the converse guard), or as one that does not
    (with the reason). Unclassified is the state that let `get_company_detail` through."""
    classified = set(DEAL_RETURNING_SERVICES) | set(NON_RECORD_DEAL_SERVICES)
    unclassified = sorted(_services_that_read_the_deals_table() - classified)
    assert not unclassified, (
        "These services read the deals table but are in neither DEAL_RETURNING_SERVICES nor "
        "NON_RECORD_DEAL_SERVICES: " + ", ".join(unclassified) + ". Read the function's "
        "return statement. If it hands back deal records, add it to the first list — every "
        "tool that calls it must then attach a `url` or carry a waiver. If it returns only "
        "counts or aggregates, add it to the second with the reason."
    )


def test_the_derived_service_scan_actually_finds_things():
    """A scan that silently matched nothing would make the guard above vacuous while
    reading green — the classification would trivially cover an empty set."""
    found = _services_that_read_the_deals_table()
    assert len(found) >= 10, f"the deals-table scan found only {sorted(found)}"
    assert "get_company_detail" in found, "the scan misses the service that motivated it"
    # One name per scanned MODULE, because dropping a module from the tuple above is
    # otherwise silent: the classification test only flags names it FOUND and left
    # unclassified, so a module that stops being scanned takes its readers out of the
    # guard while every assertion stays green.
    assert "get_stale_deals" in found, "the scan misses crm.analytics_service"
    assert "_fetch_hot_deals" in found, "the scan misses crm.today_service"


def test_every_waiver_still_names_a_real_tool():
    """A waiver for a deleted or renamed tool is dead weight that quietly widens the
    exemption surface."""
    stale = set(NO_DEAL_PAYLOAD_WAIVERS) - set(tools.TOOL_EXECUTORS)
    assert not stale, f"stale waivers: {sorted(stale)}"


def test_the_deal_url_registry_matches_what_the_executors_actually_do():
    """`CRM_DEAL_URL_TOOLS` drives which descriptions get the guidance, so it must equal
    the set of executors that really attach a URL. A new deal-returning tool fails here
    until it is registered — which is the whole point."""
    assert _executors_that_attach_a_deal_url() == set(tools.CRM_DEAL_URL_TOOLS)


def test_every_deal_returning_tool_tells_the_model_to_share_the_url():
    """A `url` the model is never told about is still no link — the exact state #145 was
    filed to fix. Derived from the executors, so it cannot be satisfied by editing a list
    to match the bug."""
    defs = {t["name"]: t["description"] for t in tools.CRM_TOOL_DEFS}
    attaching = _executors_that_attach_a_deal_url()
    assert attaching, "no CRM tool attaches a deal URL — the feature vanished"
    for name in sorted(attaching):
        assert tools.CRM_DEAL_URL_GUIDANCE in defs[name], (
            f"{name} returns a deal record carrying a `url` but its description never "
            "tells the model the field exists or to share it"
        )


def test_the_guidance_is_appended_exactly_once_per_tool():
    """It is applied at import against the shared module-level `CRM_TOOL_DEFS`. If that
    pass ever moves inside `get_crm_tools()` — which is called per turn and returns the
    same list — every description would grow a paragraph per call."""
    for _ in range(3):
        tools._apply_deal_url_guidance()
    for tool in tools.CRM_TOOL_DEFS:
        assert tool["description"].count(tools.CRM_DEAL_URL_GUIDANCE) <= 1, tool["name"]


def test_get_crm_tools_carries_the_guidance_in_both_task_modes(monkeypatch):
    """The defs the assistant actually receives come from `get_crm_tools()`, which
    filters by task mode. The guidance must survive that path, not just exist on the
    module constant."""
    for mode in ("normal", "gtd"):
        monkeypatch.setattr(tools.crm, "get_task_mode", lambda mode=mode: mode)
        defs, _ = tools.get_crm_tools()
        by_name = {d["name"]: d["description"] for d in defs}
        # crm_get_deal is present in both modes and is a registered deal-URL tool.
        assert tools.CRM_DEAL_URL_GUIDANCE in by_name["crm_get_deal"], mode


def test_the_guidance_forbids_hand_built_links():
    """The failure #145 exists to prevent is the assistant inventing a path. Saying
    'here is a url' without 'do not build your own' leaves that open — the deal id is in
    the same payload."""
    guidance = tools.CRM_DEAL_URL_GUIDANCE.lower()
    assert "`url`" in guidance
    assert "never construct this link yourself" in guidance


def test_the_sales_guide_tells_the_assistant_to_hand_over_links():
    """The tool descriptions say the field exists; SALES_GUIDE is where the working
    practice lives, and it is the static block a user-written personality cannot
    replace."""
    from assistant import identity

    guide = identity.SALES_GUIDE.lower()
    assert "`url`" in guide
    assert "never build a link yourself" in guide


# ── Per-tool attachment, at the payload shapes that differ ───────────────────────


@pytest.fixture
def absolute_base(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "frontend_url", "https://crm.example.com")
    monkeypatch.setattr(settings, "frontend_url_is_default", False)
    return "https://crm.example.com"


def test_get_deal_health_links_the_nested_deal_row(absolute_base, monkeypatch):
    """`get_deal_health` nests its deal under a `deal` key rather than returning it flat,
    so it gets no coverage from a top-level check. The link must come from the DB row's
    own id: tool dispatch does not coerce arguments (the schema's "type": "integer" only
    steers), so the argument is the one value here not proven to be an int."""
    monkeypatch.setattr(
        tools.analytics_service, "get_deal_health",
        lambda **kwargs: {"deal": {"id": 42, "title": "Big One"}, "score": 70, "flags": []},
    )
    result = tools.crm_get_deal_health("42")
    assert result["deal"]["url"] == absolute_base + FRONTEND_DEAL_PATH


def test_get_stale_deals_links_every_deal_in_the_chase_list(absolute_base, monkeypatch):
    """This is the list the assistant reads out when asked what needs attention, so a
    deal missing its link is the feature failing where it matters most."""
    monkeypatch.setattr(
        tools.analytics_service, "get_stale_deals",
        lambda **kwargs: {"deals": [{"id": 42, "title": "A"}, {"id": 7, "title": "B"}],
                          "total_stale": 2, "count": 2},
    )
    urls = [d["url"] for d in tools.crm_get_stale_deals()["deals"]]
    assert urls == [absolute_base + FRONTEND_DEAL_PATH,
                    absolute_base + "/crm/pipeline?deal=7"]


def test_get_contact_links_the_deals_it_embeds(absolute_base, monkeypatch):
    """"What's going on with Bob at Acme?" routes here, and the contact payload carries
    the contact's full deal rows. Without links the assistant names deals and, since the
    guidance forbids inventing one, hands over nothing."""
    monkeypatch.setattr(
        tools.crm, "get_contact_detail",
        lambda _id: {"id": 5, "name": "Bob",
                     "deals": [{"id": 42, "title": "A"}, {"id": 7, "title": "B"}],
                     "tasks": [{"id": 3, "title": "T"}]},
    )
    contact = tools.crm_get_contact(5)
    assert [d["url"] for d in contact["deals"]] == [
        absolute_base + FRONTEND_DEAL_PATH, absolute_base + "/crm/pipeline?deal=7",
    ]
    # The contact itself is not a deal — its own id must not become a deal link, and
    # neither must a task's.
    assert "url" not in contact
    assert "url" not in contact["tasks"][0]


def test_dashboard_links_its_top_deals(absolute_base, monkeypatch):
    """`top_deals` is five full deal rows, not a rollup — and "how's the pipeline
    looking?" is a top-frequency question. Aggregate sections must stay untouched."""
    monkeypatch.setattr(
        tools.crm, "get_dashboard_stats",
        lambda: {"total_contacts": 3, "pipeline_by_stage": [{"stage": "lead", "count": 2}],
                 "top_deals": [{"id": 42, "title": "Big One"}, {"id": 7, "title": "Small"}]},
    )
    result = tools.crm_dashboard()
    assert [d["url"] for d in result["top_deals"]] == [
        absolute_base + FRONTEND_DEAL_PATH, absolute_base + "/crm/pipeline?deal=7",
    ]
    assert "url" not in result["pipeline_by_stage"][0]


def test_analytics_links_the_stale_deals_beside_its_scalars(absolute_base, monkeypatch):
    """`summarize_analytics` is mostly counts, but it names up to five specific deals."""
    monkeypatch.setattr(tools.crm, "get_analytics", lambda **kwargs: {"raw": True})
    monkeypatch.setattr(
        tools.crm, "summarize_analytics",
        lambda _a: {"win_rate_pct": 40,
                    "stale_deals": [{"id": 42, "title": "A", "days_since_touch": 30}]},
    )
    result = tools.crm_analytics()
    assert result["stale_deals"][0]["url"] == absolute_base + FRONTEND_DEAL_PATH
    assert result["win_rate_pct"] == 40


def test_find_duplicates_links_both_deals_in_a_group_and_leaves_contacts_alone(
    absolute_base, monkeypatch,
):
    """Deciding whether two same-titled cards are a duplicate means opening both. The
    contact groups come out of the same shaper and must NOT acquire a deal URL."""
    monkeypatch.setattr(
        tools.analytics_service, "find_duplicates",
        lambda **kwargs: {
            "deals": [{"match_on": "title+contact", "value": "q1 renewal", "count": 2,
                       "records": [{"id": 42, "label": "Q1 renewal (lead)"},
                                   {"id": 7, "label": "Q1 renewal (won)"}]}],
            "contacts": [{"match_on": "email", "value": "a@x.com", "count": 2,
                          "records": [{"id": 42, "label": "Ann"}]}],
            "groups_returned": 2,
        },
    )
    result = tools.crm_find_duplicates()
    assert [r["url"] for r in result["deals"][0]["records"]] == [
        absolute_base + FRONTEND_DEAL_PATH, absolute_base + "/crm/pipeline?deal=7",
    ]
    # Same id, different entity — a contact must never be handed a deal link.
    assert "url" not in result["contacts"][0]["records"][0]


def test_scan_gaps_links_the_deals_it_asks_the_user_to_fix(absolute_base, monkeypatch):
    """The point of the scan is to go and fill the hole, which means opening the deal.
    `unverified_fields` is polymorphic (entity_type/entity_id) and is not a deal record."""
    monkeypatch.setattr(
        tools.analytics_service, "scan_gaps",
        lambda **kwargs: {
            "deals": [{"id": 42, "label": "Big One", "missing_fields": ["value"]}],
            "contacts": [{"id": 42, "label": "Ann", "missing_fields": ["email"]}],
            "unverified_fields": [{"entity_type": "deal", "entity_id": 42,
                                   "field_name": "value"}],
            "gaps_returned": 2,
        },
    )
    result = tools.crm_scan_gaps()
    assert result["deals"][0]["url"] == absolute_base + FRONTEND_DEAL_PATH
    assert "url" not in result["contacts"][0]
    assert "url" not in result["unverified_fields"][0]


def test_lifecycle_writes_link_the_deal_they_just_changed(absolute_base, monkeypatch):
    """"Moved it to negotiation — here's the deal" is exactly the moment the assistant
    narrates an outcome and the user wants to look at it. All four write confirmations
    were missing the guidance in the blueprint's first cut."""
    monkeypatch.setattr(tools, "_record_provenance", lambda *a, **k: None)

    def deal(*_a, **_k):
        return {"id": 42, "title": "Big One", "stage": "won"}

    monkeypatch.setattr(tools.crm, "mark_deal_won", deal)
    monkeypatch.setattr(tools.crm, "mark_deal_lost", deal)
    monkeypatch.setattr(tools.crm, "update_deal_stage", deal)
    monkeypatch.setattr(tools.crm, "update_deal", deal)
    monkeypatch.setattr(tools.crm, "create_deal", deal)
    monkeypatch.setattr(tools.crm, "archive_deal", deal)
    monkeypatch.setattr(tools.crm, "merge_deals", deal)

    expected = absolute_base + FRONTEND_DEAL_PATH
    assert tools.crm_mark_deal_won(42)["url"] == expected
    assert tools.crm_mark_deal_lost(42)["url"] == expected
    assert tools.crm_update_deal_stage(42, "negotiation")["url"] == expected
    assert tools.crm_update_deal(42, value=1)["url"] == expected
    assert tools.crm_create_deal("Big One")["url"] == expected
    assert tools.crm_archive_deal(42)["deal"]["url"] == expected
    assert tools.crm_merge_deals(42, 7)["deal"]["url"] == expected


def test_pipeline_and_search_link_every_summarised_deal(absolute_base, monkeypatch):
    """Both project their rows through `_summarize_deal` before returning them, so the
    link has to be attached to the projection, not to the service row it came from."""
    row = {"id": 42, "title": "Big One", "stage": "lead", "value": 10, "notes": "drop me"}
    monkeypatch.setattr(
        tools.crm, "get_pipeline",
        lambda **kwargs: {"deals": [row], "stage_summary": [], "deals_truncated": False},
    )
    monkeypatch.setattr(tools.crm, "search_deals", lambda **kwargs: [dict(row)])
    monkeypatch.setattr(tools.field_service, "list_field_definitions", lambda _e: [])

    expected = absolute_base + FRONTEND_DEAL_PATH
    pipeline_deal = tools.crm_get_pipeline()["deals"][0]
    assert pipeline_deal["url"] == expected
    # The projection is still doing its job — `notes` is not a summary field.
    assert "notes" not in pipeline_deal
    assert tools.crm_search_deals()["deals"][0]["url"] == expected


def test_get_deal_links_the_record_it_returns(absolute_base, monkeypatch):
    """The structural guards are source-text greps: they prove `with_deal_url` is CALLED,
    never that it is called on the right object. `with_deal_url(deal_id)` — passing the
    int argument instead of the row, an easy slip beside `get_deal_health`, which really
    does pass something nested — would satisfy both guards, silently return an int to the
    model, and break the tool that exists to hand back the full record."""
    monkeypatch.setattr(
        tools.crm, "get_deal_detail",
        lambda _id: {"id": 42, "title": "Big One", "stage": "lead", "value": 1000},
    )
    result = tools.crm_get_deal(42)
    assert isinstance(result, dict)
    assert result["url"] == absolute_base + FRONTEND_DEAL_PATH
    assert result["title"] == "Big One"


def test_a_tool_reporting_a_missing_deal_attaches_no_link(monkeypatch):
    """An error dict has no id, so `with_deal_url` must leave it alone rather than
    emitting a link to a deal that does not exist."""
    monkeypatch.setattr(tools.crm, "get_deal_detail", lambda _id: None)
    result = tools.crm_get_deal(999)
    assert "url" not in result
    assert "not found" in result["error"]
