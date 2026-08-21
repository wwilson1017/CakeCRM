"""context_files/service.py — filename grammar, headline derivation, and the write SQL.

Hermetic: the pg helpers are monkeypatched, so these pin the logic and the statements we
emit, not Postgres itself. The concurrency behaviour that needs a real server lives in
tests/test_integration_context_files_pg.py.
"""

import pytest

from context_files import service

# ── filename grammar ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("soul.md", "soul.md"),
    ("MEMORY.md", "MEMORY.md"),
    ("pricing.md", "topics/pricing.md"),              # bare names normalize to topics/
    ("topics/pricing.md", "topics/pricing.md"),
    ("TOPICS/pricing.md", "topics/pricing.md"),       # folder is case-insensitive
    ("daily/2026-08-21.md", "daily/2026-08-21.md"),
    ("  soul.md  ", "soul.md"),
])
def test_normalize_accepts_and_canonicalizes(raw, expected):
    assert service.normalize_filename(raw) == expected


@pytest.mark.parametrize("raw", ["soul.MD", "SOUL.md", "Memory.md", "MEMORY.MD"])
def test_protected_names_are_case_insensitive(raw):
    """A store where 'Soul.md' and 'soul.md' are different rows means two identities,
    and UNIQUE(filename) would happily hold both."""
    assert service.normalize_filename(raw) in service.PROTECTED_FILES


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "notes.txt",                    # must be .md
    "../secrets.md",
    "topics/../soul.md",
    "topics\\pricing.md",
    "a/b/c.md",                     # at most one '/'
    "archive/old.md",               # only topics/ and daily/ exist
    "topics//x.md",
    "topics/.md",                   # needs a stem
    "topics/-lead.md",              # must start alphanumeric
    "daily/2026-13-01.md",          # regex-valid, calendar-invalid
    "daily/2026-02-30.md",
    "daily/not-a-date.md",
    "topics/bad\x00name.md",
])
def test_normalize_rejects(raw):
    with pytest.raises(service.ContextFileError):
        service.normalize_filename(raw)


def test_normalize_rejects_overlong_name():
    with pytest.raises(service.ContextFileError):
        service.normalize_filename("topics/" + ("a" * 200) + ".md")


def test_unicode_is_nfc_normalized():
    """'café.md' can arrive pre- or post-composed; both must resolve to ONE row."""
    composed = service.normalize_filename("topics/café.md")
    decomposed = service.normalize_filename("topics/café.md")
    assert composed == decomposed


def test_daily_filename_uses_configured_timezone(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "UTC")
    assert service.daily_filename().startswith("daily/")
    assert service.daily_filename("2026-08-21") == "daily/2026-08-21.md"


# ── headline ──────────────────────────────────────────────────────────────────────

def test_headline_prefers_explicit_then_heading_then_first_line():
    assert service._first_headline("Headline: The real one\n# Ignored") == "The real one"
    assert service._first_headline("# A Topic\n\nbody text") == "A Topic"
    assert service._first_headline("just body text") == "just body text"


def test_headline_skips_date_and_time_headings():
    """A daily note's own '# 2026-08-21' / '### 3:04 PM' scaffolding is useless as a
    summary — this is what makes the INSERT-only headline correct for appends."""
    note = "# 2026-08-21\n\n### 3:04 PM\n\nCalled Dana about renewal\n"
    assert service._first_headline(note) == "Called Dana about renewal"


def test_headline_of_empty_is_empty():
    assert service._first_headline("") == ""
    assert service._first_headline("\n\n---\n") == ""


# ── writes ────────────────────────────────────────────────────────────────────────

def test_write_file_rejects_oversize_content(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    with pytest.raises(service.ContextFileError) as exc:
        service.write_file("topics/x.md", "x" * (service.MAX_FILE_CHARS + 1))
    assert exc.value.code == "too_large"


def test_write_file_unarchives(monkeypatch):
    """UNIQUE(filename) is global, so without this a name dreaming archived could never
    be reused."""
    captured = {}

    def fake_execute(sql, params):
        captured["sql"] = sql
        return 1

    monkeypatch.setattr(service, "pg_execute", fake_execute)
    monkeypatch.setattr(service, "read_file", lambda f: {"filename": f})
    service.write_file("topics/x.md", "body")
    assert "archived_at = NULL" in captured["sql"]


def test_write_file_conflicts_on_stale_precondition(monkeypatch):
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"updated_at": "2026-08-21T10:00:00Z"})
    with pytest.raises(service.ContextFileError) as exc:
        service.write_file("topics/x.md", "body", expected_updated_at="2026-08-20T09:00:00Z")
    assert exc.value.code == "conflict"


def test_write_file_passes_matching_precondition(monkeypatch):
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"updated_at": "2026-08-21T10:00:00Z"})
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "read_file", lambda f: {"filename": f})
    assert service.write_file(
        "topics/x.md", "body", expected_updated_at="2026-08-21T10:00:00Z",
    )["filename"] == "topics/x.md"


def test_delete_refuses_protected_files(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    for name in ("soul.md", "MEMORY.md"):
        with pytest.raises(service.ContextFileError) as exc:
            service.delete_file(name)
        assert exc.value.code == "forbidden"


def test_delete_allows_topic_file(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    assert service.delete_file("topics/x.md") is True


def test_append_daily_note_is_one_statement(monkeypatch):
    """The no-check-then-write claim rests on this being a single upsert."""
    calls = []
    monkeypatch.setattr(service, "pg_execute", lambda sql, params: calls.append((sql, params)) or 1)
    service.append_daily_note("Called Dana", day="2026-08-21")
    assert len(calls) == 1
    sql = calls[0][0]
    assert "ON CONFLICT (filename) DO UPDATE" in sql
    # It must also advance the audit columns — appending content while leaving
    # updated_at/written_by stale was a real review finding.
    assert "written_by = EXCLUDED.written_by" in sql
    assert "updated_at = now()" in sql


def test_append_daily_note_entry_format(monkeypatch):
    """Ports chatty's '### {h:mm am/pm}' entry shape, with the zone from TIMEZONE."""
    monkeypatch.setenv("TIMEZONE", "UTC")
    captured = {}
    monkeypatch.setattr(service, "pg_execute", lambda sql, params: captured.update(params=params) or 1)
    service.append_daily_note("Called Dana", day="2026-08-21")
    first_body = captured["params"][1]
    assert first_body.startswith("# 2026-08-21\n")
    assert "### " in first_body and "Called Dana" in first_body
    assert "UTC" in first_body            # real abbreviation, never a hardcoded 'CT'
    assert "\n### 0" not in first_body    # leading zero stripped, as chatty does


def test_append_daily_note_rejects_empty(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    with pytest.raises(service.ContextFileError):
        service.append_daily_note("   ")


def test_search_returns_empty_without_hitting_db_on_noise(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("no DB hit expected for an unusable query")

    monkeypatch.setattr(service, "pg_fetchall", explode)
    assert service.search_files("") == []
    assert service.search_files("   ") == []
