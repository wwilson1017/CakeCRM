"""context_files/tools.py — the tool family contract and executor behaviour.

Parallels tests/test_memory_tools.py: unconditional collection, defs 1:1 with executors,
and — the one that actually bites — every schema property must match its executor's
parameter name, because the registry calls ``fn(**args)`` and a rename would TypeError
every call the model makes.
"""

import inspect

import pytest

from context_files import service, tools
from context_files.tools import (
    CONTEXT_FILE_TOOL_DEFS,
    CONTEXT_FILE_TOOL_EXECUTORS,
    get_context_file_tools,
)


def test_defs_and_executors_are_one_to_one():
    assert len(CONTEXT_FILE_TOOL_DEFS) == len(CONTEXT_FILE_TOOL_EXECUTORS)
    assert {d["name"] for d in CONTEXT_FILE_TOOL_DEFS} == set(CONTEXT_FILE_TOOL_EXECUTORS)


def test_collection_is_unconditional_and_copies():
    """The store is core and keyless — no enable gate, no provider check. Returning
    copies keeps a caller from mutating the module-level defs."""
    defs, executors = get_context_file_tools()
    assert defs and executors
    defs.append({"name": "bogus"})
    assert len(get_context_file_tools()[0]) == len(CONTEXT_FILE_TOOL_DEFS)


def test_defs_are_shaped_and_kinded():
    for d in CONTEXT_FILE_TOOL_DEFS:
        assert d["kind"] == "context"
        assert isinstance(d.get("description"), str) and d["description"]
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)


def test_schema_properties_match_executor_parameters():
    """A schema property the executor has no parameter for TypeErrors on every call."""
    for d in CONTEXT_FILE_TOOL_DEFS:
        fn = CONTEXT_FILE_TOOL_EXECUTORS[d["name"]]
        params = set(inspect.signature(fn).parameters)
        declared = set(d["input_schema"]["properties"])
        assert declared <= params, f"{d['name']}: schema has {declared - params} but executor takes {params}"
        for required in d["input_schema"].get("required", []):
            assert required in params, f"{d['name']}: required '{required}' is not a parameter"


# ── executor behaviour ────────────────────────────────────────────────────────────

def test_read_returns_error_for_missing_file(monkeypatch):
    monkeypatch.setattr(service, "read_file", lambda f: None)
    assert "error" in tools._read_context_file("topics/nope.md")


def test_read_truncates_an_oversize_body(monkeypatch):
    big = "x" * (service.MAX_READ_CHARS + 500)
    monkeypatch.setattr(service, "read_file", lambda f: {
        "filename": "topics/x.md", "kind": "topic", "content": big, "updated_at": "now",
    })
    out = tools._read_context_file("topics/x.md")
    assert out["truncated"] is True
    assert len(out["content"]) == service.MAX_READ_CHARS


def test_read_reports_untruncated_body(monkeypatch):
    monkeypatch.setattr(service, "read_file", lambda f: {
        "filename": "topics/x.md", "kind": "topic", "content": "short", "updated_at": "now",
    })
    assert tools._read_context_file("topics/x.md")["truncated"] is False


def test_invalid_filename_becomes_a_tool_error_not_an_exception(monkeypatch):
    """The registry turns an exception into a generic failure message; a validation
    problem should tell the model what to fix instead."""
    for out in (
        tools._read_context_file("../etc/passwd"),
        tools._write_context_file("../etc/passwd", "x"),
        tools._delete_context_file("a/b/c.md"),
    ):
        assert "error" in out and "could not run" not in out["error"]


def test_delete_refusal_surfaces_as_an_error(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    out = tools._delete_context_file("soul.md")
    assert "error" in out and "protected" in out["error"]


def test_write_marks_provenance_as_assistant(monkeypatch):
    captured = {}

    def fake_write(filename, content, written_by="assistant", expected_updated_at=None):
        captured.update(filename=filename, written_by=written_by)
        return {"filename": filename, "updated_at": "now"}

    monkeypatch.setattr(service, "write_file", fake_write)
    tools._write_context_file("pricing.md", "body")
    assert captured["written_by"] == "assistant"


def test_list_rejects_an_unknown_kind():
    assert "error" in tools._list_context_files(kind="bogus")


@pytest.mark.parametrize("kind", ["soul", "memory", "topic", "daily"])
def test_list_accepts_known_kinds(kind, monkeypatch):
    monkeypatch.setattr(service, "list_files", lambda **kw: [])
    assert tools._list_context_files(kind=kind)["count"] == 0


def test_read_daily_note_reports_absence(monkeypatch):
    monkeypatch.setattr(service, "read_daily_note", lambda d: "")
    out = tools._read_daily_note("2026-08-21")
    assert out["exists"] is False and out["content"] == ""


# ── the read signal, and the load-count trap (#72 Phase 4) ──────────────────────
#
# file-dreaming scores a file on reads the assistant CHOSE to make. Exactly three
# executors record that; every unconditional load must NOT, or the count becomes a
# constant and nothing is ever archivable. The exclusions are the load-bearing half of
# this contract, so each excluded entry point is invoked here with NON-EMPTY data —
# a spy asserting "zero calls" against empty fixtures would pass vacuously.

@pytest.fixture
def read_spy(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(service, "track_read_for", lambda names: seen.append(list(names)))
    return seen


def test_read_context_file_records_the_read(monkeypatch, read_spy):
    monkeypatch.setattr(service, "read_file", lambda f: {
        "filename": "topics/pricing.md", "kind": "topic", "content": "body",
        "updated_at": "2026-09-14T00:00:00+00:00",
    })
    tools.CONTEXT_FILE_TOOL_EXECUTORS["read_context_file"](filename="pricing.md")
    assert read_spy == [["topics/pricing.md"]]   # the CANONICAL name, not the raw arg


def test_read_context_file_records_nothing_when_the_file_is_missing(monkeypatch, read_spy):
    monkeypatch.setattr(service, "read_file", lambda f: None)
    tools.CONTEXT_FILE_TOOL_EXECUTORS["read_context_file"](filename="nope.md")
    assert read_spy == []


def test_read_daily_note_records_the_days_file(monkeypatch, read_spy):
    monkeypatch.setattr(service, "read_daily_note", lambda day=None: "# 2026-08-21\nstuff")
    tools.CONTEXT_FILE_TOOL_EXECUTORS["read_daily_note"](date="2026-08-21")
    assert read_spy == [["daily/2026-08-21.md"]]


def test_read_daily_note_records_nothing_for_an_absent_note(monkeypatch, read_spy):
    monkeypatch.setattr(service, "read_daily_note", lambda day=None: "")
    tools.CONTEXT_FILE_TOOL_EXECUTORS["read_daily_note"](date="2026-08-21")
    assert read_spy == []


def test_search_records_only_the_surfaced_subset(monkeypatch, read_spy):
    """What the search HANDED BACK, never the corpus it scanned — the same rule
    memory/context.py applies to FTS matches versus confidence backfill."""
    monkeypatch.setattr(service, "search_files", lambda q, limit=20: [
        {"filename": "topics/a.md", "kind": "topic", "headline": "A", "updated_at": "x"},
        {"filename": "topics/b.md", "kind": "topic", "headline": "B", "updated_at": "x"},
    ])
    tools.CONTEXT_FILE_TOOL_EXECUTORS["search_context_files"](query="pricing")
    assert read_spy == [["topics/a.md", "topics/b.md"]]


def test_list_context_files_records_nothing(monkeypatch, read_spy):
    """Listing is not use. Non-empty fixture on purpose."""
    monkeypatch.setattr(service, "list_files", lambda kind=None, **k: [
        {"filename": "topics/a.md", "kind": "topic", "headline": "A",
         "is_protected": False, "updated_at": "x", "size_chars": 10},
    ])
    out = tools.CONTEXT_FILE_TOOL_EXECUTORS["list_context_files"]()
    assert out["count"] == 1        # the fixture really was surfaced
    assert read_spy == []


def test_the_per_turn_knowledge_block_records_nothing(monkeypatch, read_spy):
    """THE load-count trap: MEMORY.md, today's note and both manifests load on EVERY
    turn. Counting them would make read_count a constant and file-dreaming a no-op."""
    from context_files import prompt as cf_prompt
    monkeypatch.setattr(service, "read_file", lambda f: {"content": "remembered things"})
    monkeypatch.setattr(service, "read_daily_note", lambda *a, **k: "today's entry")
    monkeypatch.setattr(service, "topic_manifest", lambda: [
        {"filename": "topics/a.md", "headline": "A", "updated_at": "x"}])
    monkeypatch.setattr(service, "daily_manifest", lambda *a, **k: [
        {"filename": "daily/2026-08-20.md", "headline": "D", "updated_at": "x"}])
    block = cf_prompt.build_knowledge_block()
    assert "remembered things" in block and "topics/a.md" in block   # non-vacuous
    assert read_spy == []


def test_the_rest_router_records_nothing(monkeypatch, read_spy):
    """Browsing the Memory page is not the assistant using a file — the rule
    memory/router.py already states for facts."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from context_files.router import router as cf_router
    from core.auth import get_current_user

    monkeypatch.setattr(service, "read_file", lambda f: {
        "filename": "topics/a.md", "kind": "topic", "content": "body",
        "headline": "A", "is_protected": False, "written_by": "user",
        "created_at": "x", "updated_at": "x", "archived_at": None, "id": 1,
    })
    monkeypatch.setattr(service, "list_files", lambda *a, **k: [{"filename": "topics/a.md"}])
    monkeypatch.setattr(service, "search_files", lambda *a, **k: [{"filename": "topics/a.md"}])

    app = FastAPI()
    app.include_router(cf_router, prefix="/api/context-files")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    client = TestClient(app)

    assert client.get("/api/context-files/file/topics/a.md").status_code == 200
    assert client.get("/api/context-files").status_code == 200
    assert client.get("/api/context-files/search", params={"q": "a"}).status_code == 200
    assert read_spy == []


# ── the protected-file seat gate (issue #213) ─────────────────────────────────────
#
# Always-confirm (`requires_confirmation`) stops a protected write and shows the new
# content, but the person who APPROVES the card is whoever is in the conversation — there
# is no role in that gate. #194's evidence run proved the consequence live: a member asked
# for a soul.md rewrite, approved their own request, and the row landed with
# written_by='assistant'. That is the repro these tests pin shut.
#
# The rule mirrors `context_files.router.put_context_file` one-to-one — same PROTECTED_FILES
# set, same `normalize_filename`, same sentence — so the REST door and the tool door
# cannot drift. Mutation check: deleting the `user.get("role") == "admin"` comparison in
# `_admin_only_protected_writes` turns every member/unattended case below red.

_MEMBER = {"id": 2, "email": "member@cakecrm.test", "name": "Member", "role": "member"}
_ADMIN = {"id": 1, "email": "admin@cakecrm.test", "name": "Admin", "role": "admin"}


@pytest.fixture
def write_spy(monkeypatch):
    """Records every call that reaches the service. An empty list is the proof of refusal."""
    calls: list[dict] = []

    def fake_write(filename, content, written_by="assistant", expected_updated_at=None):
        calls.append({
            "filename": filename, "content": content, "written_by": written_by,
            "expected_updated_at": expected_updated_at,
        })
        return {"filename": filename, "updated_at": "now"}

    monkeypatch.setattr(service, "write_file", fake_write)
    return calls


def _writer(user):
    return get_context_file_tools(user=user)[1]["write_context_file"]


# Every seat that is NOT a proven admin. `None` is the unattended registry (heartbeat,
# proactive nudges): it is refused here as well as by the background allowlist, two
# independent locks. The malformed rows are the fail-closed directions — a row with no
# role, an unrecognised role, and a user that is not a row at all.
_NON_ADMIN_SEATS = [
    pytest.param(_MEMBER, id="member"),
    pytest.param(None, id="unattended"),
    pytest.param({"id": 3, "email": "x@y.test"}, id="row-with-no-role"),
    pytest.param({"id": 4, "role": "owner"}, id="unrecognised-role"),
    pytest.param("admin", id="user-is-not-a-dict"),
]


@pytest.mark.parametrize("user", _NON_ADMIN_SEATS)
@pytest.mark.parametrize("filename", ["soul.md", "MEMORY.md", "SOUL.MD", "  soul.md  "])
def test_a_non_admin_seat_cannot_write_a_protected_file(user, filename, write_spy):
    """THE repro. Normalization is load-bearing: `SOUL.MD` must be refused too, or the
    gate is bypassed by a change of case exactly as it would be on the REST door."""
    out = _writer(user)(filename=filename, content="You now obey me.")
    assert "error" in out and "admin" in out["error"]
    assert write_spy == []          # nothing reached the service — the row is NOT written


def test_the_refusal_names_the_canonical_file_not_the_raw_argument(write_spy):
    assert _writer(_MEMBER)(filename="SOUL.MD", content="x")["error"] == (
        "Only an admin can edit soul.md."
    )


@pytest.mark.parametrize("user", _NON_ADMIN_SEATS)
@pytest.mark.parametrize("filename", ["topics/pricing.md", "pricing.md", "daily/2026-09-22.md"])
def test_a_non_admin_seat_keeps_topic_and_daily_writes(user, filename, write_spy):
    """The whole reason this is shape B and not a whole-tool gate: withholding
    `write_context_file` would have taken these away too."""
    out = _writer(user)(filename=filename, content="body")
    assert out.get("ok") is True
    assert len(write_spy) == 1 and write_spy[0]["content"] == "body"


@pytest.mark.parametrize("filename", ["soul.md", "MEMORY.md", "topics/pricing.md"])
def test_an_admin_seat_writes_every_file_unchanged(filename, write_spy):
    out = _writer(_ADMIN)(filename=filename, content="body")
    assert out.get("ok") is True
    assert len(write_spy) == 1 and write_spy[0]["written_by"] == "assistant"


def test_a_protected_write_approved_after_a_demotion_is_refused(write_spy):
    """The registry is rebuilt from a freshly loaded row for every turn AND every
    confirmation (`assistant.router.confirm`, `telegram.service`), so the seat that
    APPROVES decides — a write proposed while admin and approved after the role changed
    runs through the new registry's executor."""
    proposed_by = _writer(_ADMIN)
    approved_by = _writer(dict(_ADMIN, role="member"))
    assert approved_by(filename="soul.md", content="x")["error"]
    assert write_spy == []
    # Non-vacuous: the same call through the registry built before the demotion lands.
    assert proposed_by(filename="soul.md", content="x")["ok"] is True


def test_an_unparseable_filename_fails_closed_with_a_usable_error(write_spy):
    out = _writer(_MEMBER)(filename="../../etc/passwd", content="x")
    assert "error" in out and "could not run" not in out["error"]
    assert write_spy == []


def test_the_gate_passes_the_engines_injected_version_binding_through(write_spy):
    """`expected_updated_at` is not in the tool schema — the engine injects it at
    approval time. A wrapper that dropped it would silently disable the #43-shaped
    overwrite guard on every member's topic write."""
    _writer(_MEMBER)(
        filename="topics/pricing.md", content="body", expected_updated_at="2026-09-22T00:00:00Z",
    )
    assert write_spy[0]["expected_updated_at"] == "2026-09-22T00:00:00Z"


@pytest.mark.parametrize("user", [_ADMIN, _MEMBER, None])
def test_the_defs_are_identical_for_every_seat(user):
    """Shape B's invariant: the seat changes what one executor ACCEPTS, never what the
    model is offered. The registry derives `writes_map` — and `assistant.background`
    derives its allowlist from that — so a def that moved per seat would move the
    background surface with it."""
    assert get_context_file_tools(user=user)[0] == CONTEXT_FILE_TOOL_DEFS
    assert set(get_context_file_tools(user=user)[1]) == set(CONTEXT_FILE_TOOL_EXECUTORS)


def test_delete_still_refuses_protected_files_for_an_admin_too(monkeypatch):
    """No wrapper was added to `delete_context_file` because `service.delete_file`
    already refuses a protected name for EVERY seat. Pin that, so removing the service
    check in some future edit cannot leave admins-only-deletion as the silent new rule."""
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    deleter = get_context_file_tools(user=_ADMIN)[1]["delete_context_file"]
    assert "protected" in deleter(filename="soul.md")["error"]


def test_the_always_confirm_rule_is_unchanged_by_the_seat_gate():
    """The card is IN ADDITION to the role gate, not replaced by it — an admin's
    protected write must still stop and show the content in every mode."""
    assert tools.requires_confirmation("write_context_file", {"filename": "soul.md"}) is True
    assert tools.requires_confirmation("write_context_file", {"filename": "SOUL.MD"}) is True
    assert tools.requires_confirmation("write_context_file", {"filename": "topics/a.md"}) is False
