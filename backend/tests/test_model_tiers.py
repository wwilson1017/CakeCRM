"""Postgres-backed tier persistence (pg helpers mocked)."""

from providers import model_tiers
from providers.tiers import TIER_MODELS


def test_get_resolved_override_beats_inferred(monkeypatch):
    monkeypatch.setattr(
        model_tiers, "pg_fetchone",
        lambda sql, params=(): {"overrides": {"top": "o"}, "inferred": {"top": "i", "mid": "m"}},
    )
    r = model_tiers.get_resolved("anthropic")
    assert r["top"] == "o"
    assert r["mid"] == "m"


def test_get_resolved_db_error_falls_back_to_hardcoded(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(model_tiers, "pg_fetchone", boom)
    assert model_tiers.get_resolved("anthropic") == TIER_MODELS["anthropic"]


def test_has_explicit_tier(monkeypatch):
    monkeypatch.setattr(
        model_tiers, "pg_fetchone",
        lambda sql, params=(): {"overrides": {}, "inferred": {"mid": "x"}},
    )
    assert model_tiers.has_explicit_tier("anthropic", "mid") is True
    assert model_tiers.has_explicit_tier("anthropic", "top") is False


def test_set_inferred_upsert_and_filtering(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, model_tiers)
    model_tiers.set_inferred("anthropic", {"top": "a", "mid": "b", "light": "c", "bogus": "x"})
    sql, params = conn.executed[-1]
    assert "INSERT INTO ai_model_tiers" in sql and "ON CONFLICT" in sql
    provider, inferred_json = params
    assert provider == "anthropic"
    # 'bogus' (not a tier key) dropped; only top/mid/light kept
    assert inferred_json.adapted == {"top": "a", "mid": "b", "light": "c"}


def test_set_inferred_drops_overlong_ids(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, model_tiers)
    model_tiers.set_inferred("openai", {"top": "x" * 201, "mid": "ok"})
    _, params = conn.executed[-1]
    _, inferred_json = params
    assert inferred_json.adapted == {"mid": "ok"}


def test_set_overrides_merge_and_clear(monkeypatch, fake_conn):
    # Existing overrides {top: old, light: keep}; set top->new, clear light.
    conn = fake_conn(
        monkeypatch, model_tiers,
        fetchone_results=[({"top": "old", "light": "keep"},)],  # raw-cursor tuple -> [0] is the dict
    )
    model_tiers.set_overrides("anthropic", {"top": "new", "light": ""})
    update_sql, update_params = conn.executed[-1]
    assert update_sql.startswith("UPDATE ai_model_tiers SET overrides")
    merged_json, provider = update_params
    assert provider == "anthropic"
    assert merged_json.adapted == {"top": "new"}  # top updated, light cleared
