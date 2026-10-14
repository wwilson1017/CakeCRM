"""Hermetic pins for the all-data CSV export (#302). The real ZIP is read back against
Postgres in test_crm_export_integration.py, which also holds the live schema to the same
two sets."""

import io
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from crm import export, service

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _tables_in(text: str) -> tuple[set[str], dict[str, str], set[str]]:
    """(created, renamed old->new, dropped) table names in one migration's SQL."""
    created = {m.lower() for m in re.findall(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_]+)", text, flags=re.I)}
    renamed = {a.lower(): b.lower() for a, b in re.findall(
        r"ALTER\s+TABLE(?:\s+IF\s+EXISTS)?\s+([A-Za-z_]+)\s+RENAME\s+TO\s+([A-Za-z_]+)", text, flags=re.I)}
    dropped = {m.lower() for m in re.findall(
        r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?\s+([A-Za-z_]+)", text, flags=re.I)}
    return created, renamed, dropped


def _schema_tables(texts: list[str]) -> set[str]:
    """Replay migrations in order: a table exists if created and not later dropped."""
    tables: set[str] = set()
    for text in texts:
        created, renamed, dropped = _tables_in(text)
        tables |= created
        for old, new in renamed.items():
            if old in tables:
                tables.discard(old)
                tables.add(new)
        tables -= dropped
    return tables


def _real_schema() -> set[str]:
    return _schema_tables([p.read_text() for p in sorted(MIGRATIONS.glob("*.sql"))])


def _truncated_tables(include_definitions: bool) -> set[str]:
    """The tables one branch of service._truncate_all actually resets, captured off a fake
    cursor so the guard reads the real statement rather than a copy of it."""
    seen: list[str] = []

    class Cur:
        def execute(self, statement, params=None):
            seen.append(statement)

    service._truncate_all(Cur(), include_definitions=include_definitions)
    return _truncate_targets(" ".join(seen))


def _truncate_targets(statement: str) -> set[str]:
    m = re.search(r"TRUNCATE (.+?) RESTART IDENTITY", statement, flags=re.S)
    assert m, f"no TRUNCATE in {statement!r}"
    return {t.strip() for t in m.group(1).split(",")}


def _unplaced(tables: set[str]) -> set[str]:
    return tables - {t for t, _, _ in export.TABLES} - set(export.NOT_EXPORTED)


def test_the_detectors_flag_a_new_table_and_pass_a_placed_one():
    # Schema replay: a created table counts, a renamed one counts under its new name, a
    # dropped one does not.
    texts = [
        "CREATE TABLE IF NOT EXISTS widgets (id SERIAL); CREATE TABLE gone (id INT);",
        "ALTER TABLE widgets   RENAME TO gadgets; DROP TABLE IF EXISTS gone;",
    ]
    assert _schema_tables(texts) == {"gadgets"}
    assert _unplaced({"gadgets", "deals"}) == {"gadgets"}
    assert _unplaced({"deals", "crm_meta"}) == set()
    assert _truncate_targets("TRUNCATE a, b,\n c RESTART IDENTITY") == {"a", "b", "c"}


def test_every_schema_table_is_exported_or_deliberately_excluded():
    """A new migration's table has to be placed: exported, or named with its reason."""
    schema = _real_schema()
    # The replay reached the real files, including the tasks -> todos rename.
    assert {"deals", "todos", "todo_projects", "users"} <= schema
    assert not {"tasks", "task_projects", "reminders"} & schema
    exported = {t for t, _, _ in export.TABLES}
    assert _unplaced(schema) == set(), (
        f"place these in crm/export.py TABLES or NOT_EXPORTED: {sorted(_unplaced(schema))}")
    assert not (exported & set(export.NOT_EXPORTED))
    assert (exported | set(export.NOT_EXPORTED)) - schema == set(), "names a table the schema lacks"


def test_every_table_a_data_reset_clears_is_exported_or_deliberately_excluded():
    """Both branches of _truncate_all: anything a reset can wipe, the export has decided on."""
    for include_definitions in (False, True):
        cleared = _truncated_tables(include_definitions)
        assert {"companies", "deals", "crm_stage_criteria"} <= cleared  # reached the real SQL
        assert _unplaced(cleared) == set(), (
            f"_truncate_all(include_definitions={include_definitions}) clears "
            f"{sorted(_unplaced(cleared))}, which crm/export.py neither exports nor excludes")
    assert "crm_field_definitions" in _truncated_tables(True) - _truncated_tables(False)


def test_users_ships_public_columns_only():
    columns = {t: c for t, c, _ in export.TABLES}["users"]
    assert "password" not in columns and "token_epoch" not in columns


def test_secrets_stay_out():
    exported = {t for t, _, _ in export.TABLES}
    for table in ("auth_credential", "totp_config", "trusted_devices", "ai_providers",
                  "gmail_connection", "telegram_settings", "crm_chatter_attachments",
                  "assistant_conversations", "assistant_messages", "crm_meta"):
        assert table in export.NOT_EXPORTED and table not in exported


def test_cells_that_a_spreadsheet_would_run_are_neutralised():
    assert export._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert export._cell("+1 615 555 0100") == "'+1 615 555 0100"
    assert export._cell("-5") == "'-5"
    assert export._cell("@cmd") == "'@cmd"
    assert export._cell("plain text") == "plain text"
    assert export._cell(-5) == -5
    assert export._cell(None) is None
    assert export._cell({"a": 1}) == '{"a": 1}'
    assert export._cell(["x"]) == '["x"]'


def test_queries_order_by_the_unique_key_and_quote_identifiers():
    rendered = repr(export._query("users", "id, email", "id"))
    assert "ORDER BY" in rendered and "Identifier('users')" in rendered
    assert "Identifier('email')" in rendered
    assert "Identifier('stage')" in repr(export._query("crm_stage_criteria", "*", "stage"))


def test_route_streams_the_zip_as_a_dated_attachment(monkeypatch):
    from core.auth import require_admin
    from crm.router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/crm")
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "role": "admin"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("deals.csv", "id,title\n1,Fictional deal\n")
    blob = buf.getvalue()
    monkeypatch.setattr(export, "stream_zip", lambda: iter([blob[:10], blob[10:]]))

    r = TestClient(app).get("/api/crm/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert re.fullmatch(
        r'attachment; filename="cakecrm-export-\d{4}-\d{2}-\d{2}\.zip"',
        r.headers["content-disposition"],
    )
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist() == ["deals.csv"]
