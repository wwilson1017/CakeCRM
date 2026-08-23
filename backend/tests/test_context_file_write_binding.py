"""A pending write_context_file is bound to the file version it was proposed against
(issue #72 follow-up).

The assistant composes a whole-file overwrite from what it just read, but a write to a
PROTECTED file confirms in every mode — so the write waits for as long as the human
takes to decide, and the user can edit that same file in the Memory page while it waits.
Approving used to upsert unconditionally, silently discarding their edit. The engine now
stamps the row version into the pending placeholder at the gate and injects it as a
precondition when the write finally runs; these tests pin both halves plus every
degradation path.

Hermetic: context_files.service's row read/write are stubbed, no DB.
"""

from __future__ import annotations

import json

import pytest

from assistant import engine, history
from context_files import service, tools as cf_tools

_VERSION = "2026-08-21 10:00:00+00"


@pytest.fixture
def at_version(monkeypatch):
    """Pin the stored updated_at that a read of soul.md reports (None = absent)."""

    def _set(value):
        def _read(filename):
            return {"filename": "soul.md", "updated_at": value} if value is not None else None

        monkeypatch.setattr(service, "read_file", _read)

    return _set


class _Registry:
    """Minimal ToolRegistry stand-in: records the args the executor actually got."""

    def __init__(self, result=None):
        self.executed = []
        self._result = result if result is not None else {"ok": True}

    def is_write(self, tool):
        return tool == "write_context_file"

    def execute_tool_sync(self, tool, args):
        self.executed.append((tool, args))
        return self._result


def _claimed(content):
    return {
        "msg_id": "m1",
        "tool": "write_context_file",
        "args": {"filename": "soul.md", "content": "rewritten"},
        "content": content,
    }


def _placeholder(version):
    return json.dumps({"status": history.PENDING_STATUS, "context_updated_at": version})


# ── context_files.tools helpers ───────────────────────────────────────────────────

def test_pending_binding_reads_the_stored_version(at_version):
    at_version(_VERSION)
    assert cf_tools.pending_binding({"filename": "soul.md"}) == {"context_updated_at": _VERSION}


def test_pending_binding_is_absent_for_a_file_that_does_not_exist_yet(at_version):
    """simplification: a create binds to nothing, so it stays an unconditional upsert."""
    at_version(None)
    assert cf_tools.pending_binding({"filename": "topics/new.md"}) is None


@pytest.mark.parametrize("args", [None, "soul.md", [], {}, {"filename": 7}, {"filename": None}])
def test_pending_binding_binds_nothing_without_a_usable_filename(at_version, args):
    at_version(_VERSION)
    assert cf_tools.pending_binding(args) is None


def test_pending_binding_fails_open_when_the_read_raises(monkeypatch):
    """A bogus binding would refuse a legitimate write forever — worse than the race."""

    def _boom(filename):
        raise service.ContextFileError("nope")

    monkeypatch.setattr(service, "read_file", _boom)
    assert cf_tools.pending_binding({"filename": "soul.md"}) is None


def test_binding_kwargs_extracts_the_precondition():
    assert cf_tools.binding_kwargs({"context_updated_at": _VERSION}) == {
        "expected_updated_at": _VERSION,
    }


@pytest.mark.parametrize("parsed", [{}, {"context_updated_at": ""}, {"context_updated_at": None},
                                    {"context_updated_at": 7}, {"other": "x"}, None])
def test_binding_kwargs_is_empty_when_unbound(parsed):
    assert cf_tools.binding_kwargs(parsed) == {}


# ── The placeholder written at the confirmation gate ──────────────────────────────

async def test_placeholder_carries_the_version_for_a_context_write(at_version):
    at_version(_VERSION)
    content = await engine._pending_placeholder("write_context_file", {"filename": "soul.md"})
    assert json.loads(content) == {
        "status": history.PENDING_STATUS, "context_updated_at": _VERSION,
    }


async def test_bound_placeholder_is_still_recognised_as_pending(at_version):
    """The extra key must stay inert to the pending/executing state machine."""
    at_version(_VERSION)
    content = await engine._pending_placeholder("write_context_file", {"filename": "soul.md"})
    assert history.is_pending_result(content) is True


async def test_placeholder_is_unchanged_for_other_writes(at_version):
    at_version(_VERSION)
    got = await engine._pending_placeholder("crm_create_contact", {"filename": "soul.md"})
    assert got == history.PENDING_RESULT_JSON


async def test_placeholder_falls_back_when_the_version_cannot_be_read(at_version):
    at_version(None)
    got = await engine._pending_placeholder("write_context_file", {"filename": "soul.md"})
    assert got == history.PENDING_RESULT_JSON


# ── resolve_confirmation: the precondition reaches the executor ───────────────────

def test_approve_injects_the_bound_version_as_a_precondition(monkeypatch):
    """THE race. Proposed against _VERSION; the executor must be told to enforce it, so
    a user edit landing in the gap loses the UPDATE instead of being overwritten."""
    registry = _Registry()
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **k: _claimed(_placeholder(_VERSION)))
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)

    engine.resolve_confirmation(registry, "c1", "t1", "approve", msg_id="m1")

    assert registry.executed == [("write_context_file", {
        "filename": "soul.md", "content": "rewritten", "expected_updated_at": _VERSION,
    })]


def test_approve_of_an_unbound_pending_write_stays_unconditional(monkeypatch):
    """A write proposed before this shipped (or on a file that did not exist) carries no
    binding. It must still execute rather than be refused on a missing token."""
    registry = _Registry()
    monkeypatch.setattr(history, "claim_pending_tool",
                        lambda *a, **k: _claimed(history.PENDING_RESULT_JSON))
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)

    engine.resolve_confirmation(registry, "c1", "t1", "approve", msg_id="m1")

    assert registry.executed == [("write_context_file",
                                 {"filename": "soul.md", "content": "rewritten"})]


def test_other_tools_args_are_untouched():
    args = {"name": "Ada"}
    assert engine._with_version_binding("crm_create_contact", args, _placeholder(_VERSION)) == args


@pytest.mark.parametrize("content", [None, "", "not json", "[1,2]", '"str"'])
def test_unreadable_placeholder_leaves_the_write_unconditional(content):
    args = {"filename": "soul.md", "content": "x"}
    assert engine._with_version_binding("write_context_file", args, content) == args


# ── the executor's own conflict handling ──────────────────────────────────────────

def test_stale_precondition_returns_a_model_facing_conflict(monkeypatch):
    """The service's message tells a BROWSER to reload. The model needs to be told to
    re-read and re-apply, or it will retry the same stale overwrite and win next time."""

    def _conflict(*a, **k):
        raise service.ContextFileError("This file changed since you opened it.", code="conflict")

    monkeypatch.setattr(service, "write_file", _conflict)

    out = cf_tools.CONTEXT_FILE_TOOL_EXECUTORS["write_context_file"](
        "soul.md", "rewritten", expected_updated_at=_VERSION,
    )

    assert "error" in out and "ok" not in out
    assert "soul.md" in out["error"]
    assert "read the file again" in out["error"].lower()


def test_the_precondition_is_forwarded_to_the_service(monkeypatch):
    seen = {}

    def _write(filename, content, written_by="assistant", expected_updated_at=None):
        seen.update(filename=filename, written_by=written_by, expected=expected_updated_at)
        return {"filename": filename, "updated_at": _VERSION}

    monkeypatch.setattr(service, "write_file", _write)

    out = cf_tools.CONTEXT_FILE_TOOL_EXECUTORS["write_context_file"](
        "soul.md", "rewritten", expected_updated_at=_VERSION,
    )

    assert seen == {"filename": "soul.md", "written_by": "assistant", "expected": _VERSION}
    assert out["ok"] is True


def test_expected_updated_at_is_not_advertised_to_the_model():
    """The server owns this token. A schema property would invite the model to invent
    one — and a model that omits it would silently opt out of the guard."""
    definition = next(d for d in cf_tools.CONTEXT_FILE_TOOL_DEFS
                      if d["name"] == "write_context_file")
    assert "expected_updated_at" not in definition["input_schema"]["properties"]
