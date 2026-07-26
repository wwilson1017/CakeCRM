"""VAPID key management (notifications/vapid.py) — C3 / R9.

Hermetic: pg helpers + key generation mocked; encryption runs for real against
the per-test key. Covers env-override, reject-partial-config, generate-and-persist
(won + lost race), and fail-loud on an undecryptable stored key.
"""

import pytest

from core.encryption import encrypt_value
from notifications import vapid


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    # Default: no env-provided VAPID keys (individual tests opt in).
    monkeypatch.setattr(vapid.settings, "vapid_public_key", "", raising=False)
    monkeypatch.setattr(vapid.settings, "vapid_private_key", "", raising=False)
    monkeypatch.setattr(vapid, "_generate_keypair", lambda: ("GENPUB", "GENPRIV"))


def _queue_fetchone(monkeypatch, results):
    seq = list(results)
    monkeypatch.setattr(vapid, "pg_fetchone", lambda *a, **k: seq.pop(0) if seq else None)


def test_env_override_returns_env_no_db(monkeypatch):
    monkeypatch.setattr(vapid.settings, "vapid_public_key", "ENVPUB")
    monkeypatch.setattr(vapid.settings, "vapid_private_key", "ENVPRIV")

    def _boom(*a, **k):
        raise AssertionError("DB should not be touched when env keys are set")

    monkeypatch.setattr(vapid, "pg_fetchone", _boom)
    monkeypatch.setattr(vapid, "pg_execute", _boom)
    assert vapid.get_vapid_keys() == ("ENVPUB", "ENVPRIV")


def test_partial_env_config_rejected(monkeypatch):
    monkeypatch.setattr(vapid.settings, "vapid_public_key", "ENVPUB")  # private missing
    with pytest.raises(RuntimeError, match="Partial VAPID"):
        vapid.get_vapid_keys()


def test_stored_key_is_decrypted(monkeypatch):
    _queue_fetchone(monkeypatch, [{"public_key": "PUB", "private_key_enc": encrypt_value("STOREDPRIV")}])
    monkeypatch.setattr(vapid, "pg_execute", lambda *a, **k: 0)
    assert vapid.get_vapid_keys() == ("PUB", "STOREDPRIV")


def test_undecryptable_stored_key_fails_loud(monkeypatch):
    _queue_fetchone(monkeypatch, [{"public_key": "PUB", "private_key_enc": "enc:v1:not-a-valid-token"}])
    with pytest.raises(RuntimeError, match="could not be decrypted"):
        vapid.get_vapid_keys()


def test_generate_and_persist_wins(monkeypatch):
    _queue_fetchone(monkeypatch, [None])            # no row yet
    captured = {}

    def pg_execute(sql, params):
        captured["params"] = params
        return 1                                     # our insert won

    monkeypatch.setattr(vapid, "pg_execute", pg_execute)
    pub, priv = vapid.get_vapid_keys()
    assert (pub, priv) == ("GENPUB", "GENPRIV")
    # persisted private key is encrypted at rest
    assert captured["params"][1].startswith("enc:v1:")


def test_lost_race_reuses_stored(monkeypatch):
    stored = {"public_key": "OTHERPUB", "private_key_enc": encrypt_value("OTHERPRIV")}
    _queue_fetchone(monkeypatch, [None, stored])     # first miss, re-read after losing race
    monkeypatch.setattr(vapid, "pg_execute", lambda *a, **k: 0)   # someone else won
    assert vapid.get_vapid_keys() == ("OTHERPUB", "OTHERPRIV")


def test_public_key_wrapper(monkeypatch):
    _queue_fetchone(monkeypatch, [{"public_key": "PUB", "private_key_enc": encrypt_value("P")}])
    monkeypatch.setattr(vapid, "pg_execute", lambda *a, **k: 0)
    assert vapid.get_vapid_public_key() == "PUB"
