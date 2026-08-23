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
    # fromisoformat() accepts these, but they are DIFFERENT names for a day whose
    # canonical filename is hyphenated — a second row read_daily_note could never reach.
    "daily/20260821.md",
    "daily/2026-W34-5.md",
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

def test_blank_soul_resolves_to_the_builtin_at_the_read_boundary(monkeypatch):
    """Resolved HERE, not only where the prompt is built: otherwise the system prompt
    would carry the default text while read_context_file and the Memory editor both
    showed an empty file — Baker governed by identity text it cannot see."""
    from assistant.identity import DEFAULT_SOUL

    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"content": "  "})
    assert service.read_file("soul.md")["content"] == DEFAULT_SOUL


def test_a_written_soul_is_returned_verbatim(monkeypatch):
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"content": "my own words"})
    assert service.read_file("soul.md")["content"] == "my own words"


def test_the_fallback_is_soul_only(monkeypatch):
    monkeypatch.setattr(service, "pg_fetchone", lambda *a, **k: {"content": ""})
    assert service.read_file("MEMORY.md")["content"] == ""


def test_write_file_rejects_non_string_content(monkeypatch):
    """Coercing to '' would let a schema-invalid but parseable {"content": []} ERASE the
    file — unconfirmed, for an ordinary topic file in power mode."""
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    for bad in ([], {}, 42, None, True):
        with pytest.raises(service.ContextFileError):
            service.write_file("topics/x.md", bad)


def test_append_daily_note_guards_the_insert_path_too(monkeypatch):
    """The upsert's WHERE only covers the CONFLICT branch; a fresh note needs its own
    check or the date/timestamp scaffolding pushes an at-the-cap body over."""
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    with pytest.raises(service.ContextFileError) as exc:
        service.append_daily_note("x" * service.MAX_FILE_CHARS, day="2026-08-21")
    assert exc.value.code == "too_large"


def test_append_daily_note_refuses_to_grow_past_the_file_cap(monkeypatch):
    """The guard is in the upsert's WHERE, so a full note reports zero rows. Capping only
    the new ENTRY would let many valid appends grow a note without bound until Postgres
    failed building search_tsv."""
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 0)
    with pytest.raises(service.ContextFileError) as exc:
        service.append_daily_note("one more entry", day="2026-08-21")
    assert exc.value.code == "too_large"


def test_append_daily_note_bounds_the_merged_length_in_sql(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "pg_execute",
                        lambda sql, params: captured.update(sql=sql, params=params) or 1)
    service.append_daily_note("entry", day="2026-08-21")
    assert "WHERE length(assistant_context_files.content) + %s <= %s" in captured["sql"]
    assert captured["params"][-1] == service.MAX_FILE_CHARS


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


def test_write_file_conflicts_when_precondition_matches_no_row(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 0)   # 0 rows updated
    with pytest.raises(service.ContextFileError) as exc:
        service.write_file("topics/x.md", "body", expected_updated_at="2026-08-20T09:00:00Z")
    assert exc.value.code == "conflict"


def test_write_file_passes_matching_precondition(monkeypatch):
    monkeypatch.setattr(service, "pg_execute", lambda *a, **k: 1)
    monkeypatch.setattr(service, "read_file", lambda f: {"filename": f})
    assert service.write_file(
        "topics/x.md", "body", expected_updated_at="2026-08-21T10:00:00Z",
    )["filename"] == "topics/x.md"


def test_precondition_is_enforced_in_one_statement(monkeypatch):
    """A SELECT-then-UPDATE would be a TOCTOU window: an append landing between the two
    would be silently discarded, which is the exact loss the token exists to prevent."""
    calls = []
    monkeypatch.setattr(service, "pg_execute", lambda sql, params: calls.append(sql) or 1)
    monkeypatch.setattr(service, "read_file", lambda f: {"filename": f})
    service.write_file("topics/x.md", "body", expected_updated_at="2026-08-21T10:00:00Z")
    assert len(calls) == 1
    assert "WHERE filename = %s AND updated_at = %s::timestamptz" in calls[0]


def test_unparseable_version_token_is_a_conflict_not_a_crash(monkeypatch):
    def explode(*a, **k):
        raise ValueError("bad timestamp literal")

    monkeypatch.setattr(service, "pg_execute", explode)
    with pytest.raises(service.ContextFileError) as exc:
        service.write_file("topics/x.md", "body", expected_updated_at="not-a-timestamp")
    assert exc.value.code == "conflict"


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
