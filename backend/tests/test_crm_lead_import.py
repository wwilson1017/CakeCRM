"""Lead parsing, preview persistence, identity refusals and bounded approval summaries."""

import io
import json
from datetime import date
from unittest.mock import Mock

import pytest

from assistant import background, delimiters, history
from assistant.registry import ToolRegistry
from crm import lead_import as imp

CSV = ('First Name,Last Name,Company,E-mail,,Phone,Booth Notes,Budget\n'
       'Ana,Lopez,Northwind Foods,ana@example.test,x,555-0100,Wants samples,"$1,200"\n'
       'Bo,,Northwind Foods,,,,,maybe\n,,,,stray,,,\n')
COUNTS = {"contacts_new": 7, "contacts_existing": 0, "companies_new": 0, "companies_existing": 0}


def test_mapping_and_leads_preserve_cells_and_drop_unnamed_only_row():
    headers, rows = imp._table(imp._text_cells(CSV))
    mapping = imp._mapping(headers, {"first name": "first_name", "Last Name": "last_name",
                                     "Booth Notes": "notes", "Budget": "deal_value"})
    assert mapping["Company"] == "company"
    assert mapping["E-mail"] == "email"
    assert mapping["Phone"] == "phone"
    leads = imp._leads(rows, mapping, "Spring trade show")
    assert len(leads) == 2
    assert leads[0] == {
        "company": "Northwind Foods", "name": "Ana Lopez", "email": "ana@example.test",
        "phone": "555-0100", "title": "", "source": "Spring trade show", "tags": "",
        "deal_title": "Northwind Foods - New Lead", "deal_value": 1200.0,
        "notes": "Booth Notes: Wants samples",
    }
    assert leads[1]["deal_value"] == 0
    assert leads[1]["notes"] == "Budget: maybe"


@pytest.mark.parametrize("mapping", [{"absent": "name"}, {"Name": "wrong"}, []])
def test_invalid_mapping_refused(mapping):
    with pytest.raises(ValueError):
        imp._mapping(["Name"], mapping)


@pytest.mark.parametrize("headers", [["Name", "name"], ["Name", " NAME "]])
def test_duplicate_headers_refused(headers):
    with pytest.raises(ValueError, match="Two columns are both named"):
        imp._table([headers, ["A", "B"]])


@pytest.mark.parametrize("headers,mapping", [(["Name", "Full Name"], None),
                                             (["Name", "Other"], {"Other": "name"})])
def test_duplicate_targets_refused(headers, mapping):
    with pytest.raises(ValueError, match="both map to name"):
        imp._mapping(headers, mapping)


def test_skip_resolves_conflict_and_notes_can_accumulate():
    mapping = imp._mapping(["Name", "Full Name", "Notes", "Other"],
                           {"Full Name": "skip", "Other": "notes"})
    leads = imp._leads([{"Name": "A", "Full Name": "B", "Notes": "one", "Other": "two"}], mapping, "")
    assert leads[0]["name"] == "A"
    assert leads[0]["notes"] == "Notes: one\nOther: two"


def test_tab_latin1_and_row_limit():
    assert imp._text_cells("Name\tCompany\nA\tB") == [["Name", "Company"], ["A", "B"]]
    assert imp._file_cells("list.csv", b"Name\nAndr\xe9")[0][1] == ["André"]
    text = "Name\n" + "A\n" * 600
    assert len(imp._text_cells(text)) == 502
    assert "more than 500" in imp.preview(user_id=7, text=text)["error"]


def test_xlsx_cells_are_exact_and_other_sheets_warn():
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Name", "Phone", "Date", "Company"])
    wb.active.append(["Ana", 5551234567.0, date(2026, 5, 1), "Northwind"])
    wb.create_sheet("Not imported")
    data = io.BytesIO()
    wb.save(data)
    wb.close()
    cells, warnings = imp._file_cells("leads.XLSX", data.getvalue())
    assert cells[1] == ["Ana", "5551234567", "2026-05-01T00:00:00", "Northwind"]
    assert len(warnings) == 1 and "Not imported" in warnings[0]
    with pytest.raises(ValueError, match="valid .xlsx"):
        imp._file_cells("bad.xlsx", b"garbage")


@pytest.mark.parametrize("name", ["A" * 300, "漢" * 120, "😀" * 120, '\\"' * 120])
def test_summary_fits_serialized_pending_placeholder(name):
    summary = imp._summary([{"name": name, "company": ""}] * 7, COUNTS)
    assert summary["headline"].startswith("Import 7 leads")
    assert len(json.dumps({"status": history.PENDING_STATUS, "summary": summary})) <= 1500
    shown = len(summary["lines"]) - 1
    assert summary["lines"][-1] == f"…and {7 - shown} more"
    if name == "A" * 300:
        assert len(summary["lines"]) == 4


def test_refusals_before_database_access(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("database must not be touched"))
    for name in ("pg_fetchone", "pg_fetchall", "get_connection"):
        monkeypatch.setattr(imp, name, forbidden)
    assert imp.preview(user_id=None, text="Name\nX") == {"error": imp.NEEDS_USER}
    assert imp.execute("b", user_id=None) == {"error": imp.NEEDS_USER}
    assert "limit is 2000" in imp.execute("b", user_id=7, note="x" * 2001)["error"]
    assert imp.preview(user_id=7) == {"error": "Pass attachment_id (an attached CSV or XLSX file) or text (CSV or tab-separated rows with a header line)."}
    assert "No data rows" in imp.preview(user_id=7, text="Name")["error"]
    assert "No row names" in imp.preview(user_id=7, text="Unknown\nX")["error"]
    forbidden.assert_not_called()


def test_preview_stores_no_ids_and_reports_skips(fake_conn, monkeypatch):
    conn = fake_conn(monkeypatch, imp)
    monkeypatch.setattr(imp, "_counts", lambda leads: {"leads": len(leads), **COUNTS})
    result = imp.preview(user_id=7, text="Name,Email,Extra\nAna,a@example.test,extra",
                         column_mapping={"Email": "skip"})
    assert result["skipped_columns"] == ["Email"]
    assert result["unmapped_columns"] == ["Extra"]
    assert result["columns"] == {"Name": "name"}
    sql, params = conn.executed[-1]
    assert "now() + make_interval" in sql
    assert params[:2] == (result["batch_id"], 7)
    payload = json.loads(params[2])
    assert payload["leads"][0]["email"] == ""
    assert not any(k.endswith("_id") for k in payload["leads"][0])
    assert payload["summary"]["headline"].startswith("Import 1 lead:")
    assert params[3] == 60
    assert "DELETE" in conn.executed[0][0]


def test_attachment_lookup_scoped_and_missing_refused(monkeypatch):
    lookup = Mock(return_value=None)
    monkeypatch.setattr(history, "get_message_file", lookup)
    assert "No stored CSV" in imp.preview(user_id=7, attachment_id="f1")["error"]
    lookup.assert_called_once_with("f1", user_id=7)


def test_pending_binding_scoped_and_fail_open(monkeypatch):
    summary = {"headline": "Import 2 leads", "lines": ["A"]}
    fetch = Mock(return_value={"summary": summary})
    monkeypatch.setattr(imp, "pg_fetchone", fetch)
    assert imp.pending_binding({"batch_id": "b"}, 7) == {"summary": summary}
    assert fetch.call_args.args[1] == ("b", 7)
    assert "user_id = %s AND expires_at > now()" in fetch.call_args.args[0]
    fetch.return_value = None
    assert imp.pending_binding({"batch_id": "b"}, 7) is None
    fetch.reset_mock()
    assert imp.pending_binding({"batch_id": 1}, 7) is None
    assert imp.pending_binding({"batch_id": "b"}, None) is None
    fetch.assert_not_called()
    fetch.side_effect = RuntimeError("offline")
    assert imp.pending_binding({"batch_id": "b"}, 7) is None


def test_preview_fenced_taints_and_is_excluded_from_background():
    registry = ToolRegistry(background=True)
    assert "crm_preview_lead_import" in background.BACKGROUND_EXCLUDED_TOOLS
    allowed = background.background_allowlist(registry)
    assert "crm_preview_lead_import" not in allowed
    assert "crm_execute_lead_import" not in allowed
    assert registry.execute_tool_sync("crm_preview_lead_import", {"text": "Name\nX"}) == {"error": imp.NEEDS_USER}
    content, tainted = delimiters.fence_tool_result("crm_preview_lead_import", {"sample": ["untrusted"]})
    assert content.startswith("<untrusted_external_content") and tainted


@pytest.mark.parametrize("cell", ["NaN", "Infinity", "-1", "not money"])
def test_invalid_money_is_preserved_as_notes(cell):
    leads = imp._leads([{"Name": "Ana", "Value": cell}], {"Name": "name", "Value": "deal_value"}, "")
    assert leads[0]["deal_value"] == 0
    assert leads[0]["notes"] == f"Value: {cell}"


def test_contact_lookup_uses_the_callers_cursor(monkeypatch):
    cursor = Mock()
    cursor.fetchall.return_value = [("a@example.test", 9)]
    forbidden = Mock(side_effect=AssertionError("lookup escaped the transaction"))
    monkeypatch.setattr(imp, "pg_fetchall", forbidden)
    assert imp._existing_contacts(["A@example.test", "a@example.test", ""], cur=cursor) == {"a@example.test": 9}
    assert cursor.execute.call_args.args[1] == (["a@example.test"],)
    assert "MIN(c.id)" in cursor.execute.call_args.args[0]
    assert "c.status <> 'archived'" in cursor.execute.call_args.args[0]
    assert imp._existing_contacts([], cur=cursor) == {}
    assert cursor.execute.call_count == 1
    forbidden.assert_not_called()


def test_summary_headline_counts_read_in_the_singular():
    one = {"contacts_new": 1, "contacts_existing": 0, "companies_new": 1, "companies_existing": 2}
    headline = imp._summary([{"name": "Ana", "company": "Northwind"}], one)["headline"]
    assert headline == ("Import 1 lead: 1 new deal, 1 new contact (0 already in the CRM), "
                        "1 new company (2 already in the CRM)")
