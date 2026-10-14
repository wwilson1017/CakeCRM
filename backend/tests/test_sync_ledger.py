"""SYNC_LEDGER.md hygiene: port PRs add rows by hand, and the file is public forever.

Kept from the removed intake test (#300). Deliberately NOT a staff-name denylist:
encoding those patterns here would publish the very PII the guard protects.
"""

import re
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / "SYNC_LEDGER.md"
_LEDGER_HEADER = "| Feature | Source | CakeCRM PR / issue | Date |"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _ledger_rows(text: str) -> list[list[str]]:
    rows, in_table = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _LEDGER_HEADER:
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                in_table = False
                continue
            if set(stripped) <= set("|- "):
                continue  # separator row
            rows.append([c.strip() for c in stripped.strip("|").split("|")])
    return rows


def test_ledger_parses_to_schema():
    rows = _ledger_rows(LEDGER.read_text(encoding="utf-8"))
    assert rows, "SYNC_LEDGER.md has no ledger rows — the pre-ledger seed is missing"
    for row in rows:
        assert len(row) == 4, f"ledger row does not have 4 columns: {row[0]!r}"
        assert row[2] == "seed" or re.fullmatch(r"#\d+", row[2]), (
            f"ledger row {row[0]!r} has a malformed CakeCRM reference: {row[2]!r}"
        )
        assert row[3] == "—" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[3]), (
            f"ledger row {row[0]!r} has a malformed date: {row[3]!r}"
        )


def test_ledger_carries_no_email_addresses_or_diff_fences():
    assert _EMAIL.search("reach a.person@example.org"), "the email pattern matches nothing"
    text = LEDGER.read_text(encoding="utf-8")
    assert not _EMAIL.search(text), "SYNC_LEDGER.md contains an email address"
    assert "```" not in text, "SYNC_LEDGER.md contains a code/diff fence"
