"""Model-facing todo pages stay compact without losing public-capture provenance."""

from unittest.mock import Mock

import pytest

from assistant.delimiters import fence_public_rows
from crm import gtd_service, gtd_tools


def _row(**fields):
    return {"id": 1, "title": "Call supplier", "status": "inbox", "source": "capture_web", **fields}


def test_compact_keeps_source_and_omits_unset_fields():
    assert gtd_tools._compact(_row(context="", project_name=None, due_date="", star=False,
                                   tags=[], repeat="", notes="", bring_back_on=None)) == _row()


def test_compact_keeps_set_fields_and_marks_notes():
    fields = {"context": "@calls", "due_date": "2026-12-01", "bring_back_on": "2026-11-30",
              "star": True, "tags": ["phone"], "repeat": "weekly"}
    assert gtd_tools._compact(_row(**fields, project_name="Launch", notes="Details",
                                   created_at="yesterday")) == _row(
        **fields, project="Launch", has_notes=True,
    )


@pytest.mark.parametrize("offset,size,total,next_offset", [
    (0, 100, 250, 100), (100, 100, 250, 200), (200, 50, 250, None),
    (300, 0, 250, None), (0, 0, 0, None), (0, 0, 250, None),
])
def test_page_metadata_and_filter_forwarding(monkeypatch, offset, size, total, next_offset):
    rows = [_row(id=i) for i in range(size)]
    listing = Mock(return_value=rows)
    counting = Mock(return_value=total)
    monkeypatch.setattr(gtd_service, "list_todos", listing)
    monkeypatch.setattr(gtd_service, "count_todos", counting)
    result = gtd_tools.GTD_TOOL_EXECUTORS["todo_list"](
        offset=offset, limit=100, status="inbox", owner_id=7,
    )
    assert result["count"] == size
    assert result["total"] == total
    assert result["next_offset"] == next_offset
    assert result["todos"] == rows
    listing.assert_called_once_with(offset=offset, limit=100, status="inbox", owner_id=7)
    counting.assert_called_once_with(status="inbox", owner_id=7)


def test_a_null_offset_is_the_first_page(monkeypatch):
    listing = Mock(return_value=[_row()])
    monkeypatch.setattr(gtd_service, "list_todos", listing)
    monkeypatch.setattr(gtd_service, "count_todos", Mock(return_value=2))
    assert gtd_tools.GTD_TOOL_EXECUTORS["todo_list"](offset=None)["next_offset"] == 1
    assert listing.call_args.kwargs["offset"] == 0


def test_detail_returns_full_rows_untouched(monkeypatch):
    rows = [_row(notes="Full notes", created_at="2026-11-16")]
    monkeypatch.setattr(gtd_service, "list_todos", Mock(return_value=rows))
    monkeypatch.setattr(gtd_service, "count_todos", Mock(return_value=1))
    assert gtd_tools.GTD_TOOL_EXECUTORS["todo_list"](detail=True)["todos"] is rows


def test_negative_offset_is_a_service_error_and_tool_error_dict(monkeypatch):
    query = Mock(side_effect=AssertionError("No database read expected"))
    monkeypatch.setattr(gtd_service, "pg_fetchall", query)
    monkeypatch.setattr(gtd_service, "pg_fetchone", query)
    with pytest.raises(gtd_service.ValidationError, match="offset"):
        gtd_service.list_todos(offset=-1)
    assert gtd_tools.GTD_TOOL_EXECUTORS["todo_list"](offset=-1) == {
        "error": "offset must be non-negative",
    }
    query.assert_not_called()


def test_compact_capture_is_still_fenced_and_taints(monkeypatch):
    monkeypatch.setattr(gtd_service, "list_todos", Mock(return_value=[_row()]))
    monkeypatch.setattr(gtd_service, "count_todos", Mock(return_value=1))
    payload = gtd_tools.GTD_TOOL_EXECUTORS["todo_list"]()
    fenced, tainted = fence_public_rows(payload)
    assert tainted is True
    assert fenced["todos"][0]["title"].startswith('<untrusted_external_content id=')
    assert payload["todos"][0]["source"] == "capture_web"
