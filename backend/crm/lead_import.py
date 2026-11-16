"""One-step lead import for Baker (#370; port of upstream 211fe70dd).

Previews hold parsed rows, never CRM ids. Execute recomputes matches and consumes the
seat-owned batch in the same transaction as every company, contact, deal and note.
"""

import csv
import io
import json
import logging
import math
import secrets
from datetime import date, datetime
from itertools import islice

from assistant import history
from core.postgres import get_connection, pg_fetchall, pg_fetchone
from crm import chatter_service, scoring_service, service
from crm.smart_import import COLUMN_ALIASES

logger = logging.getLogger(__name__)

# One import is one approval, one transaction and one scoring pass. Chunking can
# replace this bound if larger imports are needed.
MAX_ROWS = 500
PREVIEW_TTL_MINUTES = 60
NOTE_MAX = 2000
TARGETS = (
    "company", "name", "first_name", "last_name", "email", "phone", "title",
    "source", "tags", "notes", "deal_title", "deal_value", "skip",
)
NEEDS_USER = "Lead import needs a signed-in person; it is not available on an unattended run."
EXPIRED = (
    "That import preview has expired or was already imported (a preview lasts "
    f"{PREVIEW_TTL_MINUTES} minutes and imports once). Preview the list again."
)
_ALIAS_TO_FIELD = {alias: field for field, aliases in COLUMN_ALIASES.items() for alias in aliases}


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _take(rows) -> list[list[str]]:
    """Nonblank rows, bounded to the header + MAX_ROWS + one overflow probe."""
    cleaned = ([_cell_text(c) for c in row] for row in rows)
    return list(islice((r for r in cleaned if any(r)), MAX_ROWS + 2))


def _text_cells(text: str) -> list[list[str]]:
    first = next((line for line in text.splitlines() if line.strip()), "")
    try:
        return _take(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in first else ","))
    except csv.Error as e:
        raise ValueError(f"Could not read those rows as CSV: {e}") from e


def _xlsx_cells(data: bytes) -> tuple[list[list[str]], list[str]]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            first, *others = wb.sheetnames
            warnings = ([f"Read sheet '{first}' only; not imported: {', '.join(others)}"]
                        if others else [])
            return _take(wb[first].iter_rows(values_only=True)), warnings
        finally:
            wb.close()
    except Exception as e:
        raise ValueError("Could not read that spreadsheet. Is it a valid .xlsx file?") from e


def _file_cells(filename: str, data: bytes) -> tuple[list[list[str]], list[str]]:
    if filename.lower().endswith(".xlsx"):
        return _xlsx_cells(data)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return _text_cells(text), []


def _table(cells: list[list[str]]) -> tuple[list[str], list[dict[str, str]]]:
    """Drop unnamed columns, reject ambiguous headers, and pad short rows with blanks."""
    if not cells:
        return [], []
    head = [h.strip() for h in cells[0]]
    seen = set()
    for h in head:
        if not h:
            continue
        if h.lower() in seen:
            raise ValueError(f"Two columns are both named '{h}'. Rename one and attach the file again.")
        seen.add(h.lower())
    rows = [{h: (row[i] if i < len(row) else "") for i, h in enumerate(head) if h}
            for row in cells[1:]]
    return [h for h in head if h], rows


def _mapping(headers: list[str], column_mapping) -> dict[str, str]:
    """Header -> target, including explicit skips so preview can report them separately."""
    if column_mapping is not None and not isinstance(column_mapping, dict):
        raise ValueError("column_mapping must be an object of column header -> target.")
    by_key = {h.lower(): h for h in headers}
    mapping = {h: _ALIAS_TO_FIELD[h.lower()] for h in headers if h.lower() in _ALIAS_TO_FIELD}
    for key, target in (column_mapping or {}).items():
        header = by_key.get(str(key).strip().lower())
        if header is None:
            raise ValueError(f"column_mapping names '{key}', which is not a column in this "
                             f"list. Columns: {', '.join(headers)}")
        if target not in TARGETS:
            raise ValueError(f"'{target}' is not a mapping target. Use one of: {', '.join(TARGETS)}")
        mapping[header] = target
    assigned = {}
    for header, target in mapping.items():
        if target in ("notes", "skip"):
            continue
        if target in assigned:
            raise ValueError(f"Columns '{assigned[target]}' and '{header}' both map to {target}. "
                             "Map one of them to skip.")
        assigned[target] = header
    return mapping


def _money(cell: str) -> float | None:
    # Only 1,200.50-style numbers; other formats are preserved in the deal notes.
    try:
        value = float(cell.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _leads(rows: list[dict], mapping: dict[str, str], source: str) -> list[dict]:
    """One lead per row naming a company or person; other rows are dropped."""
    leads = []
    for row in rows:
        got: dict[str, str] = {}
        notes: list[str] = []
        value = 0.0
        for header, target in mapping.items():
            cell = row.get(header, "")
            if not cell or target == "skip":
                continue
            if target == "notes":
                notes.append(f"{header}: {cell}")
            elif target == "deal_value":
                parsed = _money(cell)
                if parsed is None:
                    notes.append(f"{header}: {cell}")
                else:
                    value = parsed
            else:
                got.setdefault(target, cell)
        full = " ".join(p for p in (got.get("first_name"), got.get("last_name")) if p)
        name = got.get("name") or full or got.get("email") or got.get("phone") or ""
        company = got.get("company", "")
        if not (company or name):
            continue
        leads.append({
            "company": company, "name": name,
            "email": got.get("email", ""), "phone": got.get("phone", ""),
            "title": got.get("title", ""), "source": got.get("source") or source,
            "tags": got.get("tags", ""),
            "deal_title": got.get("deal_title") or f"{company or name} - New Lead",
            "deal_value": value, "notes": "\n".join(notes),
        })
    return leads


def _summary(leads: list[dict], counts: dict) -> dict:
    """Bound the serialized placeholder, including escaped Unicode, for history reload."""
    n = len(leads)
    headline = (
        f"Import {n} lead{'' if n == 1 else 's'}: {n} new deal{'' if n == 1 else 's'}, "
        f"{counts['contacts_new']} new contacts ({counts['contacts_existing']} already in the CRM), "
        f"{counts['companies_new']} new companies ({counts['companies_existing']} already in the CRM)"
    )
    lines: list[str] = []
    for lead in leads[:3]:
        line = " · ".join(p for p in (lead["name"], lead["company"]) if p)[:120]
        candidate = lines + [line]
        remaining = n - len(candidate)
        if remaining:
            candidate = candidate + [f"…and {remaining} more"]
        placeholder = {"status": history.PENDING_STATUS,
                       "summary": {"headline": headline, "lines": candidate}}
        if len(json.dumps(placeholder)) > 1500:
            break
        lines.append(line)
    if n > len(lines):
        lines.append(f"…and {n - len(lines)} more")
    return {"headline": headline, "lines": lines}


def _existing_contacts(emails: list[str], *, cur=None) -> dict[str, int]:
    """Lowercased email -> lowest-id live contact, optionally in the caller's transaction."""
    emails = list(dict.fromkeys(e.lower() for e in emails if e))
    if not emails:
        return {}
    sql = """SELECT t.e AS email, MIN(c.id) AS id
             FROM unnest(%s::text[]) AS t(e)
             JOIN contacts c ON LOWER(btrim(c.email)) = LOWER(btrim(t.e))
             WHERE c.status <> 'archived'
             GROUP BY t.e"""
    if cur is not None:
        cur.execute(sql, (emails,))
        return dict(cur.fetchall())
    return {r["email"]: r["id"] for r in pg_fetchall(sql, (emails,))}


def _counts(leads: list[dict]) -> dict:
    emails = {lead["email"].lower() for lead in leads if lead["email"]}
    existing = len(_existing_contacts(list(emails)))
    without_email = sum(1 for lead in leads if lead["name"] and not lead["email"])
    names = [lead["company"] for lead in leads if lead["company"]]
    # Same normalized-name expression as the company resolver and its unique index.
    co = pg_fetchone(
        r"""SELECT COUNT(DISTINCT LOWER(btrim(t.n, E' \t\n\r\f\x0b'))) AS total,
                   COUNT(DISTINCT LOWER(btrim(t.n, E' \t\n\r\f\x0b')))
                       FILTER (WHERE co.id IS NOT NULL) AS existing
            FROM unnest(%s::text[]) AS t(n)
            LEFT JOIN companies co
              ON LOWER(btrim(co.name, E' \t\n\r\f\x0b')) = LOWER(btrim(t.n, E' \t\n\r\f\x0b'))""",
        (names,),
    ) if names else {"total": 0, "existing": 0}
    return {
        "leads": len(leads),
        "contacts_new": len(emails) - existing + without_email,
        "contacts_existing": existing,
        "companies_new": co["total"] - co["existing"],
        "companies_existing": co["existing"],
    }


def preview(*, user_id: int | None, attachment_id: str = "", text: str = "",
            column_mapping: dict | None = None, source: str = "") -> dict:
    """Parse a list and store it as a batch. Writes no CRM data."""
    if user_id is None:
        return {"error": NEEDS_USER}
    try:
        if attachment_id:
            stored = history.get_message_file(str(attachment_id), user_id=user_id)
            if stored is None:
                return {"error": (
                    "No stored CSV or XLSX upload has that attachment_id. Use the "
                    "attachment_id on the file's tag in this conversation, or pass the rows as text."
                )}
            cells, warnings = _file_cells(stored["filename"], stored["data"])
        elif (text or "").strip():
            cells, warnings = _text_cells(text), []
        else:
            return {"error": "Pass attachment_id (an attached CSV or XLSX file) or text "
                             "(CSV or tab-separated rows with a header line)."}
        headers, rows = _table(cells)
        if not rows:
            return {"error": "No data rows found under the header row."}
        if len(rows) > MAX_ROWS:
            return {"error": f"That list has more than {MAX_ROWS} rows, the most one import "
                             "takes. Split it and import each part."}
        mapping = _mapping(headers, column_mapping)
    except ValueError as e:
        return {"error": str(e)}
    leads = _leads(rows, mapping, str(source or "").strip())
    if not leads:
        return {"error": (
            "No row names a company or a person with the current column mapping. "
            f"Columns: {', '.join(headers)}. Map them with column_mapping."
        )}
    counts = _counts(leads)
    batch_id = "imp_" + secrets.token_hex(8)
    payload = json.dumps({"leads": leads, "summary": _summary(leads, counts)})
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM assistant_lead_import_previews WHERE expires_at <= now()")
        cur.execute(
            "INSERT INTO assistant_lead_import_previews (batch_id, user_id, payload, expires_at) "
            "VALUES (%s, %s, %s::jsonb, now() + make_interval(mins => %s))",
            (batch_id, user_id, payload, PREVIEW_TTL_MINUTES),
        )
    return {
        "batch_id": batch_id, "expires_in_minutes": PREVIEW_TTL_MINUTES, **counts,
        "skipped_rows": len(rows) - len(leads),
        "columns": {h: t for h, t in mapping.items() if t != "skip"},
        "skipped_columns": [h for h, t in mapping.items() if t == "skip"],
        "unmapped_columns": [h for h in headers if h not in mapping],
        "sample": leads[:5], "warnings": warnings,
    }


def pending_binding(args: dict | None, user_id: int | None) -> dict | None:
    """Server-built summary for a pending execute; failure leaves a working plain card."""
    try:
        batch_id = (args or {}).get("batch_id")
        if user_id is None or not isinstance(batch_id, str):
            return None
        row = pg_fetchone(
            "SELECT payload->'summary' AS summary FROM assistant_lead_import_previews "
            "WHERE batch_id = %s AND user_id = %s AND expires_at > now()",
            (batch_id, user_id),
        )
    except Exception:
        logger.warning("lead import: no summary for the Approve card", exc_info=True)
        return None
    return {"summary": row["summary"]} if row and row.get("summary") else None


_LIVE_BATCH = ("FROM assistant_lead_import_previews "
               "WHERE batch_id = %s AND user_id = %s AND expires_at > now()")


def execute(batch_id: str, *, note: str = "", user_id: int | None) -> dict:
    """Create the batch's companies, contacts, deals and notes, or roll them all back."""
    if user_id is None:
        return {"error": NEEDS_USER}
    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        return {"error": f"note is {len(note)} characters; the limit is {NOTE_MAX}."}
    deal_ids: list[int] = []
    new_contacts: list[int] = []
    linked: set[int] = set()
    with get_connection() as conn:
        cur = conn.cursor()
        # A second execute waits here, then finds the consumed row gone. No lookup
        # or company creation happens before the live, owner-scoped claim.
        cur.execute("SELECT payload " + _LIVE_BATCH + " FOR UPDATE", (str(batch_id), user_id))
        row = cur.fetchone()
        if row is None:
            return {"error": EXPIRED}
        leads = row[0]["leads"]
        company_ids = service.resolve_or_create_company_ids(
            [lead["company"] for lead in leads if lead["company"]], cur=cur)
        # Emails are not unique in this CRM: another import can concurrently create
        # a contact with this email. Within this batch every repeated email shares one.
        by_email = _existing_contacts([lead["email"] for lead in leads], cur=cur)
        existing_ids = set(by_email.values())
        now = service._now()
        for lead in leads:
            company_id = company_ids.get(lead["company"])
            email_key = lead["email"].lower()
            contact_id = by_email.get(email_key) if email_key else None
            if contact_id is None and lead["name"]:
                cur.execute(service._CONTACT_INSERT_SQL, (
                    lead["name"], lead["email"], lead["phone"], lead["company"], lead["title"],
                    lead["source"], "active", service._normalize_tags(lead["tags"]), "",
                    company_id, user_id,
                ))
                contact_id = cur.fetchone()[0]
                new_contacts.append(contact_id)
                if email_key:
                    by_email[email_key] = contact_id
            elif contact_id in existing_ids:
                linked.add(contact_id)
            cur.execute(service._DEAL_INSERT_SQL, (
                lead["deal_title"], contact_id, "lead", lead["deal_value"], lead["notes"],
                "", 0, "USD", company_id, user_id, None, None,
            ))
            deal_id = cur.fetchone()[0]
            deal_ids.append(deal_id)
            if note:
                # No per-row AI touch-count recompute.
                chatter_service.insert_note_cur(cur, "deal", deal_id, note, user_id, now)
        cur.execute("DELETE FROM assistant_lead_import_previews WHERE batch_id = %s", (str(batch_id),))
    # After commit, like single-record creation; this hook never raises.
    scoring_service.score_on_event(deal_ids=deal_ids, contact_ids=sorted(set(new_contacts) | linked))
    return {
        "ok": True, "deals_created": len(deal_ids), "contacts_created": len(new_contacts),
        "contacts_linked": len(linked), "companies_linked": len(set(company_ids.values())),
        "notes_created": len(deal_ids) if note else 0, "deal_ids": deal_ids,
    }
