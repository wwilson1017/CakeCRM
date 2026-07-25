"""Assistant identity — default resolution, partial update, system-prompt assembly."""

import pytest

from assistant import identity


@pytest.fixture
def pg(monkeypatch):
    calls = {"execute": []}

    def _execute(sql, params=()):
        calls["execute"].append((" ".join(sql.split()), list(params)))
        return 1

    monkeypatch.setattr(identity, "pg_execute", _execute)
    return calls


def test_get_identity_blank_personality_uses_default(monkeypatch):
    monkeypatch.setattr(identity, "pg_fetchone", lambda *a: {"name": "Baker", "personality": ""})
    out = identity.get_identity()
    assert out["name"] == "Baker"
    assert out["using_default"] is True
    assert out["personality"] == identity.DEFAULT_PERSONALITY


def test_get_identity_custom_personality(monkeypatch):
    monkeypatch.setattr(identity, "pg_fetchone", lambda *a: {"name": "Ace", "personality": "Be terse."})
    out = identity.get_identity()
    assert out["name"] == "Ace"
    assert out["using_default"] is False
    assert out["personality"] == "Be terse."


def test_get_identity_missing_row_falls_back(monkeypatch):
    monkeypatch.setattr(identity, "pg_fetchone", lambda *a: None)
    out = identity.get_identity()
    assert out["name"] == identity.DEFAULT_NAME
    assert out["using_default"] is True


def test_update_identity_name_only_leaves_personality(pg, monkeypatch):
    monkeypatch.setattr(identity, "pg_fetchone", lambda *a: {"name": "Ace", "personality": ""})
    identity.update_identity(name="Ace")
    sql = pg["execute"][0][0]
    assert "name = %s" in sql and "personality = %s" not in sql


def test_update_identity_blank_personality_reverts_to_default(pg, monkeypatch):
    monkeypatch.setattr(identity, "pg_fetchone", lambda *a: {"name": "Baker", "personality": ""})
    identity.update_identity(personality="   ")
    params = pg["execute"][0][1]
    assert "" in params  # stored blank → get_identity resolves to default


def test_build_system_prompt_interpolates_name_and_includes_safety():
    static, volatile = identity.build_system_prompt(
        {"name": "Ace", "personality": "You are {name}, a helper.", "using_default": False}
    )
    assert "You are Ace, a helper." in static
    assert "{name}" not in static  # interpolated
    assert "pending_user_approval" in static  # confirmation note present
    assert "untrusted_file_content" in static  # upload-safety instruction present
    assert "Current date and time:" in volatile


# ── Long-term memory injection (issue #5) ─────────────────────────────────────

def test_build_system_prompt_injects_memory_into_volatile_only():
    """The per-turn memory block rides the volatile half; it must NEVER enter the
    cached static block (a stale cached prefix would hide fact updates)."""
    ident = {"name": "Ace", "personality": "You are {name}.", "using_default": False}
    static, volatile = identity.build_system_prompt(ident, memory_context="MEM-SENTINEL")
    assert "MEM-SENTINEL" in volatile
    assert "MEM-SENTINEL" not in static
    # The constant framing (MEMORY_NOTE) is cacheable and lives in static.
    assert "Long-term memory" in static


def test_build_system_prompt_empty_memory_is_back_compat():
    ident = {"name": "Ace", "personality": "p", "using_default": True}
    _, volatile = identity.build_system_prompt(ident)   # no memory_context
    assert volatile.startswith("Current date and time:")
    assert "\n\n" not in volatile   # exactly the date line, nothing appended
