"""Deal temperature (issue #125) — the rep's own hot/warm/cold read on a deal.

Hermetic: the scoring factor is a pure multiplier, the normalizer is pure, and the rest is
structural (the writable-column sets, the tool schemas, the REST allowlist). The integration
half — the column, its CHECK, and a same-value write staying a no-op — is at the bottom,
marked and deselected by default.

`deal_temperature` is a real `deals` COLUMN here, where the blueprint keeps it as a #19
custom field. `scoring_service`'s own docstring recorded that this factor was dropped from
the #18 port "because CakeCRM ships zero custom-field definitions", and the EAV route does
not retire that reason: `crm_field_definitions` ships empty by deliberate design and nothing
seeds it, so a custom-field temperature would leave the scoring factor dead on every install
until an admin hand-created a definition under exactly the right key.
"""

from datetime import datetime, timezone

import pytest

from crm import scoring_service as ss
from crm import service, tools
from crm.router import DealCreate, DealUpdate

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


# ── The scoring factor ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value,mult", [
    ("hot", 1.6), ("warm", 1.1), ("cold", 0.5),
    ("  HOT  ", 1.6),            # normalised on read, so a stray case never halves a score
])
def test_temperature_bands(value, mult):
    assert ss._temperature_multiplier(value) == mult


@pytest.mark.parametrize("value", [None, "", "   ", "cool", "lukewarm", "COLDISH"])
def test_unset_and_unrecognised_are_neutral(value):
    """1.0, never a penalty.

    Two separate claims in one assertion. UNSET is neutral because nobody has judged the
    deal, and only a judgment should move a score — the divergence from the blueprint, whose
    Cold default would have multiplied every existing deal by 0.4 on deploy. UNRECOGNISED is
    neutral because the migration's CHECK makes it unrepresentable, so reaching that branch
    means the column drifted from this table, and a score that silently halves is worse than
    one that ignores a value it cannot read.
    """
    assert ss._temperature_multiplier(value) == 1.0


def _score(temperature):
    deal = {
        "stage": "qualified", "value": 5_000, "contact_id": 1, "company_id": 1,
        "created_at": NOW, "deal_temperature": temperature,
    }
    return ss._compose_deal(deal, 3, NOW, NOW)


def test_deploying_this_changes_no_existing_score():
    """The regression that matters most, and the only one a user would ever notice.

    Every deal in every install has a NULL temperature the moment this migration runs. If
    unset were anything but 1.0, the next daily refresh would rewrite every lead score with
    no user action behind it — a silent install-wide change to a number people sort by.
    """
    assert _score(None)["score"] == _score("nonsense")["score"]
    deal = {"stage": "qualified", "value": 5_000, "contact_id": 1, "company_id": 1, "created_at": NOW}
    assert ss._compose_deal(deal, 3, NOW, NOW)["score"] == _score(None)["score"], (
        "a deal row with no deal_temperature key at all must score identically to a NULL one"
    )


def test_the_factor_is_ordered_and_reported():
    hot, warm, unset, cold = _score("hot"), _score("warm"), _score(None), _score("cold")
    assert hot["score"] > warm["score"] > unset["score"] > cold["score"]
    # Emitted even when unset, so a breakdown says "nobody has triaged this" rather than
    # staying silent about a factor that exists.
    assert unset["factors"]["temperature"] == {"value": None, "multiplier": 1.0}
    assert hot["factors"]["temperature"] == {"value": "hot", "multiplier": 1.6}


@pytest.mark.parametrize("stage,expected", [("won", 100), ("lost", 0)])
def test_terminal_deals_ignore_temperature(stage, expected):
    for temperature in ("hot", "cold", None):
        r = ss._compose_deal(
            {"stage": stage, "value": 5_000, "created_at": NOW, "deal_temperature": temperature},
            3, NOW, NOW,
        )
        assert r["score"] == expected
        assert "temperature" not in r["factors"]


def test_hot_does_not_saturate_the_clamp_across_the_ordinary_range():
    """Why 1.6 and not the blueprint's 2.5.

    At 2.5x a hot `proposal` computes ~101 and a hot `negotiation` ~143 with typical
    secondary factors — both clamp to 99, so two deals a rep would rank very differently
    render identically. The clamp is still reachable, and should be, for a deal that is
    strong on every factor; what must not happen is the top of the range collapsing for
    ordinary ones.
    """
    typical = dict(value=5_000, contact_id=1, company_id=1, created_at=NOW)
    hot_mid = ss._compose_deal({**typical, "stage": "proposal", "deal_temperature": "hot"}, 3, NOW, NOW)
    hot_late = ss._compose_deal({**typical, "stage": "negotiation", "deal_temperature": "hot"}, 3, NOW, NOW)
    assert hot_mid["score"] < 99 and hot_late["score"] < 99
    assert hot_mid["score"] != hot_late["score"], "the two stages must stay distinguishable"


def test_temperature_is_the_strongest_single_factor():
    """The point of the feature: a human's read beats every inferred signal.

    Compared on SPREAD (best/worst), which is what decides how much a factor can move a
    score, rather than on either endpoint alone.
    """
    def spread(values, fn):
        results = [fn(v) for v in values]
        return max(results) / min(results)

    temperature = spread([*service.DEAL_TEMPERATURES], ss._temperature_multiplier)
    others = [
        spread([0, 3, 11], ss._engagement_multiplier),
        spread([0, 10_000, 200_000], ss._value_multiplier),
        spread([0, 30, 200], ss._recency_multiplier),
        spread([0, 100, 400], ss._age_multiplier),
        spread([(False, False), (True, False), (True, True)],
               lambda p: ss._relationship_multiplier(*p)),
    ]
    assert temperature > max(others)


# ── The normalizer: the one validator both untrusted callers share ───────────

@pytest.mark.parametrize("raw,expected", [
    ("hot", "hot"), ("WARM", "warm"), ("  Cold  ", "cold"),
    (None, None), ("", None), ("   ", None),   # both spellings of "clear it"
])
def test_normalize_accepts_and_clears(raw, expected):
    assert service.normalize_deal_temperature(raw) == expected


@pytest.mark.parametrize("raw", ["cool", "blazing", "hot ish", 3, True, 1.5, ["hot"], {"t": "hot"}])
def test_normalize_refuses_everything_else(raw):
    """Raises rather than dropping or coercing.

    Both callers forward unvalidated input — the REST body, and `crm_update_deal`'s raw model
    kwargs, which nothing schema-checks server-side. Silently dropping a bad value would
    report success on a write that never happened; letting it through would hit the CHECK as
    an opaque 500. Non-strings are rejected here too, so `3` gets a sentence naming the valid
    tiers instead of an AttributeError from `.strip()` that the registry reports as a generic
    "the tool failed".
    """
    with pytest.raises(ValueError) as exc:
        service.normalize_deal_temperature(raw)
    assert "hot, warm, cold" in str(exc.value)


def test_update_deal_rejects_a_bad_tier_before_touching_the_database(monkeypatch):
    def explode(*a, **k):  # pragma: no cover - the point is that it is never reached
        raise AssertionError("update_deal must refuse before opening a connection")

    monkeypatch.setattr(service, "_write_deal_update", explode)
    with pytest.raises(ValueError):
        service.update_deal(1, deal_temperature="scalding")


# ── The boundary: which columns a caller may write ───────────────────────────

def test_temperature_is_user_writable_and_typed():
    assert "deal_temperature" in service._DEAL_USER_WRITABLE
    # Needed for `_write_deal_update`'s `IS DISTINCT FROM %s::<type>` cast; without it the
    # chokepoint's undeclared-column guard raises instead of writing.
    assert service._DEAL_COLUMN_TYPES["deal_temperature"] == "text"
    assert service._DEAL_USER_WRITABLE <= set(service._DEAL_COLUMN_TYPES)


def test_lead_score_stays_unwritable():
    """Adding an input to the score must not open the score itself."""
    assert "lead_score" not in service._DEAL_USER_WRITABLE
    assert "archived_at" not in service._DEAL_USER_WRITABLE


def test_the_three_declarations_of_the_tier_list_agree():
    """The constant, the tool schemas and the migration's CHECK are one list in three places.

    The migration is read as text rather than trusted: it is the only one of the three that
    the Python process never imports, so nothing else would notice it drifting.
    """
    from pathlib import Path

    assert service.DEAL_TEMPERATURES == ("hot", "warm", "cold")
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    sql = next(p for p in migrations.glob("*_deal_temperature.sql")).read_text()
    for tier in service.DEAL_TEMPERATURES:
        assert f"'{tier}'" in sql
    assert "deal_temperature IS NULL" in sql, "NULL must stay representable — it is untriaged"


# ── The two write surfaces that carry unvalidated input ──────────────────────

def _tool(name):
    return next(t for t in tools.CRM_TOOL_DEFS if t["name"] == name)


@pytest.mark.parametrize("tool_name", ["crm_update_deal", "crm_create_deal"])
def test_both_deal_write_tools_advertise_the_tiers(tool_name):
    prop = _tool(tool_name)["input_schema"]["properties"]["deal_temperature"]
    # None is in the enum because clearing is a real operation, not an omission.
    assert prop["enum"] == [*service.DEAL_TEMPERATURES, None]
    assert prop["type"] == ["string", "null"]


def test_create_deal_accepts_the_column_so_the_tool_cannot_typeerror():
    """`crm_create_deal` forwards the model's raw kwargs into `create_deal`'s signature.

    A model that learned the field from `crm_update_deal`'s schema will try it on create too,
    and an explicit signature turns an unknown kwarg into a TypeError rather than ignoring it.
    """
    import inspect

    assert "deal_temperature" in inspect.signature(service.create_deal).parameters


def test_a_summarised_deal_still_carries_its_temperature():
    """`_summarize_deal` keeps only what `_DEAL_SUMMARY_FIELDS` names.

    A `SELECT d.*` upstream is not enough: without this entry the column is invisible to
    `crm_get_pipeline` and `crm_search_deals`, which are exactly the reads a pipeline review
    uses.
    """
    assert "deal_temperature" in tools._DEAL_SUMMARY_FIELDS
    summarised = tools._summarize_deal({"id": 1, "title": "T", "deal_temperature": "hot"})
    assert summarised["deal_temperature"] == "hot"


def test_rest_models_carry_the_field_and_can_express_a_clear():
    assert "deal_temperature" in DealCreate.model_fields
    assert "deal_temperature" in DealUpdate.model_fields
    # `PUT /deals/{id}` drops nulls except for an allowlist, so without membership there
    # `deal_temperature: null` would be silently discarded and the value never cleared.
    # Mirrors the route's own comprehension rather than importing it — the handler is an
    # async closure with the filter inline, so there is nothing to call directly.
    body = DealUpdate(deal_temperature=None)
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k in ("contact_id", "company_id", "owner_id", "deal_temperature")
    }
    assert updates == {"deal_temperature": None}


# ── Integration: the column itself ───────────────────────────────────────────

@pytest.mark.integration
def test_column_exists_with_its_check(pg):
    from core.postgres import pg_fetchone

    col = pg_fetchone(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'deals' AND column_name = 'deal_temperature'"
    )
    assert col is not None, "the migration did not run"
    assert col["data_type"] == "text"
    assert col["is_nullable"] == "YES", "NULL is the untriaged state and must stay legal"


@pytest.mark.integration
def test_the_database_refuses_a_tier_the_normalizer_would_have_caught(pg):
    """Defence in depth: the CHECK is what makes an invalid tier unrepresentable rather than
    merely unreachable through the two validated paths."""
    import psycopg2

    from core.postgres import pg_execute

    deal = service.create_deal(title="Temp check")
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_execute("UPDATE deals SET deal_temperature = %s WHERE id = %s", ("cool", deal["id"]))


@pytest.mark.integration
def test_a_same_value_write_is_a_no_op_and_does_not_bump_updated_at(pg):
    """#96's rule, inherited for free by routing through `_write_deal_update`.

    This is the property the custom-field route could not have given us:
    `field_service.set_field_values` never touches the parent row, so an EAV temperature
    would neither participate in the no-op test nor trigger a rescore.
    """
    from core.postgres import pg_fetchone

    deal = service.create_deal(title="No-op temp")
    service.update_deal(deal["id"], deal_temperature="hot")
    before = pg_fetchone("SELECT updated_at, lead_score FROM deals WHERE id = %s", (deal["id"],))

    service.update_deal(deal["id"], deal_temperature="HOT")  # same stored value
    after = pg_fetchone("SELECT updated_at, lead_score FROM deals WHERE id = %s", (deal["id"],))
    assert after["updated_at"] == before["updated_at"]

    service.update_deal(deal["id"], deal_temperature="cold")  # a real change
    changed = pg_fetchone("SELECT updated_at, lead_score FROM deals WHERE id = %s", (deal["id"],))
    assert changed["updated_at"] > before["updated_at"]
    assert changed["lead_score"] < before["lead_score"], "the rescore rode the same chokepoint"
