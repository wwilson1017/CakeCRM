"""All-data CSV export (#302, ported from PlaybackCRM #34): one CSV per table, zipped.

Admin-only via `GET /api/crm/export`. The data is the install's, so it must be able to
leave without the app: a ZIP a spreadsheet opens, not a Postgres dump. Every CSV has a
header row and is ordered by its unique key, so two exports diff cleanly. Nothing is
filtered (archived deals are data too) and nothing is joined; the id columns are the joins.

Every table in the schema is placed, deliberately, in ``TABLES`` (exported) or
``NOT_EXPORTED`` (left out, with the reason). ``tests/test_crm_export.py`` fails on a
table in neither, and on a table ``crm.service._truncate_all`` resets that is in neither,
so a new migration has to decide. Left out: credentials and tokens, integration state,
the assistant's own bookkeeping, note attachments (binary, not rows), and assistant
conversations, which are owner-only by rule (an admin export must not read another
seat's chats). ``users`` ships its public columns only.

All reads share one REPEATABLE READ, read-only snapshot, so the files agree with each
other even while people work. The ZIP is streamed: each table is read through a
server-side cursor a page at a time and each page's compressed bytes go out before the
next is fetched, so neither a table nor the ZIP is ever held in memory whole.
"""

import csv
import io
import json
import zipfile
from collections.abc import Iterator

from psycopg2 import sql

from core.postgres import get_connection

# (table, columns or "*", unique order key). Order is the order inside the ZIP.
TABLES: tuple[tuple[str, str, str], ...] = (
    ("companies", "*", "id"),
    ("contacts", "*", "id"),
    ("deals", "*", "id"),
    ("deal_stage_events", "*", "id"),
    ("activity_log", "*", "id"),
    ("todos", "*", "id"),
    ("todo_projects", "*", "id"),
    ("reminders", "*", "id"),
    ("crm_chatter", "*", "id"),
    ("crm_chatter_mentions", "*", "id"),
    ("crm_field_definitions", "*", "id"),
    ("crm_field_values", "*", "id"),
    ("crm_field_provenance", "*", "id"),
    ("crm_stage_criteria", "*", "stage"),
    ("saved_views", "*", "id"),
    ("memory_facts", "*", "id"),
    ("assistant_context_files", "*", "id"),
    # Who the owner_id / actor_id / author_id columns point at. Public columns only.
    ("users", "id, email, name, role, is_active, created_at", "id"),
)

# Table -> why it stays out. The pin test holds every schema table to one of the two sets.
NOT_EXPORTED: dict[str, str] = {
    "auth_credential": "credential",
    "totp_config": "credential",
    "trusted_devices": "credential",
    "todo_web_sessions": "credential (todo app browser sessions, hashes only)",
    "todo_web_signin_tokens": "credential (todo app sign-in codes, hashes only)",
    "vapid_keys": "credential",
    "push_subscriptions": "per-device push endpoints",
    "ai_providers": "API keys",
    "ai_settings": "provider configuration",
    "ai_model_tiers": "provider configuration",
    "gmail_connection": "OAuth tokens",
    "gmail_scan_state": "integration state",
    "gmail_scanned_messages": "integration state",
    "gmail_unmatched_correspondents": "integration state",
    "telegram_settings": "bot token",
    "telegram_links": "integration state",
    "crm_meta": "install flags and the capture token",
    "assistant_identity": "assistant configuration",
    "assistant_conversations": "owner-only by rule; an admin export must not read other seats' chats",
    "assistant_messages": "owner-only by rule; an admin export must not read other seats' chats",
    "chat_turns": "owner-only conversation bookkeeping",
    "chat_turn_events": "owner-only conversation bookkeeping",
    "dreaming_runs": "assistant bookkeeping",
    "heartbeat_state": "assistant bookkeeping",
    "proactive_nudges": "assistant bookkeeping",
    "deal_ai_touch_evidence": "assistant bookkeeping",
    "notifications": "transient, per seat",
    "alerts": "transient",
    "crm_chatter_attachments": "binary files, not rows",
}

PAGE_ROWS = 1000

# Spreadsheets run a cell that starts with one of these as a formula, and the help topic
# tells the admin to open these files in one, so such a cell gets an apostrophe: shown as
# text, never executed. A phone number written "+1 …" reads fine with the mark in front.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str)
    if isinstance(value, str) and value.startswith(_FORMULA_LEADS):
        return "'" + value
    return value


def _query(table: str, columns: str, key: str) -> sql.Composed:
    cols = sql.SQL("*") if columns == "*" else sql.SQL(", ").join(
        sql.Identifier(c.strip()) for c in columns.split(",")
    )
    return sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
        cols, sql.Identifier(table), sql.Identifier(key)
    )


class _Sink:
    """A write-only, unseekable file for ZipFile: it collects bytes until drained."""

    def __init__(self):
        self._chunks: list[bytes] = []

    def write(self, b) -> int:
        self._chunks.append(bytes(b))
        return len(b)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


def stream_zip() -> Iterator[bytes]:
    """The whole export as ZIP chunks, one `<table>.csv` entry per table.

    Holds one pooled connection until the last chunk is sent; an abandoned download
    closes the generator and the pool rolls the read-only transaction back.
    """
    sink = _Sink()
    with get_connection() as conn:
        # First statement of the transaction, so every table below reads one snapshot.
        conn.cursor().execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as z:
            for table, columns, key in TABLES:
                with conn.cursor(name=f"export_{table}") as cur:
                    cur.execute(_query(table, columns, key))
                    rows = cur.fetchmany(PAGE_ROWS)  # a named cursor's description needs a fetch
                    entry = z.open(f"{table}.csv", "w", force_zip64=True)
                    with io.TextIOWrapper(entry, encoding="utf-8", newline="") as text:
                        writer = csv.writer(text)
                        writer.writerow([d[0] for d in cur.description])
                        while rows:
                            writer.writerows([_cell(v) for v in row] for row in rows)
                            text.flush()
                            yield sink.drain()
                            rows = cur.fetchmany(PAGE_ROWS)
                yield sink.drain()
        yield sink.drain()
