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
