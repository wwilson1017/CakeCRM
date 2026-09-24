"""The shared secret ladder (core.secret_store) and the encryption key that rides it.

Both of this app's long-lived secrets have the same failure mode: a value that
only lives in the process. A regenerated Fernet key orphans every encrypted
credential; a regenerated JWT secret signs every seat out (issue #222). One
ladder, so one test module.

Every test points `secret_store.DATA_DIR` at a tmp dir and neutralizes the
keychain, so the suite never reads or writes the developer's real keychain and
never touches `backend/data/`.
"""

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from core import secret_store
from core.encryption import EncryptionKeyManager, decrypt_value, encrypt_value
from core.secret_store import PersistedSecret, SecretPersistenceError

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _raises(exc):
    """A stand-in that turns one filesystem call into the failure under test."""
    def _boom(*args, **kwargs):
        raise exc
    return _boom


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A throwaway `backend/data/` with no keychain behind it."""
    monkeypatch.setattr(secret_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(secret_store, "_keychain_read", lambda account: None)
    monkeypatch.setattr(secret_store, "_keychain_write", lambda account, value: False)
    return tmp_path


def _jwt_like(**overrides) -> PersistedSecret:
    """A store configured the way core.config configures the JWT secret."""
    kwargs = {
        "env_var": "JWT_SECRET",
        "filename": ".jwt-secret",
        "generate": lambda: os.urandom(16).hex(),
        "ignored_env_values": ("change-me-in-production",),
        "ephemeral_fallback": True,
    }
    kwargs.update(overrides)
    return PersistedSecret(**kwargs)


# ── The ladder ───────────────────────────────────────────────────────────────

def test_two_resolutions_against_the_same_data_dir_agree(data_dir, monkeypatch):
    """The whole point of #222: a restart must not mint a new signing key. A
    second, independent store models the next process — a cache hit on the first
    instance would prove nothing."""
    monkeypatch.delenv("JWT_SECRET", raising=False)

    first = _jwt_like()
    second = _jwt_like()

    assert first.resolve() == second.resolve()
    assert first.source == "generated"
    assert second.source == "file"


def test_the_generated_file_is_unreadable_by_anyone_else(data_dir, monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    store = _jwt_like()
    store.resolve()

    mode = stat.S_IMODE(store.file_path.stat().st_mode)
    assert mode == 0o600, f"secret file is mode {mode:o}"


def test_a_supplied_env_value_wins_over_a_stored_one(data_dir, monkeypatch):
    (data_dir / ".jwt-secret").write_text("secret-already-on-disk", encoding="utf-8")
    monkeypatch.setenv("JWT_SECRET", "operator-supplied")

    store = _jwt_like()

    assert store.resolve() == "operator-supplied"
    assert store.source == "env"
    assert store.from_env is True


def test_the_shipped_placeholder_does_not_count_as_supplied(data_dir, monkeypatch):
    """`.env.example` ships JWT_SECRET=change-me-in-production, and `run.py` writes
    it into a generated .env. Treating that as an operator value would sign every
    install with the same publicly known secret."""
    monkeypatch.setenv("JWT_SECRET", "change-me-in-production")

    store = _jwt_like()

    assert store.resolve() != "change-me-in-production"
    assert store.from_env is False
    assert store.source == "generated"


def _fake_keychain(monkeypatch, vault: dict[str, str]) -> None:
    """Back the keychain steps with a dict instead of the developer's real one."""
    def _write(account: str, value: str) -> bool:
        vault[account] = value
        return True

    monkeypatch.setattr(secret_store, "_keychain_read", lambda account: vault.get(account))
    monkeypatch.setattr(secret_store, "_keychain_write", _write)


def test_a_generated_secret_goes_into_the_keychain_when_there_is_one(data_dir, monkeypatch):
    """The ladder's step 2 — the path a local macOS/Windows dev machine takes for
    BOTH secrets, and the one the `data_dir` fixture otherwise turns off."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    vault: dict[str, str] = {}
    _fake_keychain(monkeypatch, vault)

    store = _jwt_like()

    assert store.resolve() == vault["jwt-secret"]
    assert store.source == "generated"
    assert not store.file_path.exists(), "the keychain held it; no file should be written"


def test_a_process_that_loses_the_keychain_race_adopts_what_is_stored(data_dir, monkeypatch):
    """`_generate_and_store` reads the keychain BACK instead of trusting its own
    write, so two processes generating at the same moment still agree. Here the
    keychain is empty when the ladder looks and holds another process's value by
    the time the write returns."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    vault: dict[str, str] = {}
    _fake_keychain(monkeypatch, vault)

    def _write_but_lose(account: str, value: str) -> bool:
        vault[account] = "the-other-process-won"
        return True

    monkeypatch.setattr(secret_store, "_keychain_write", _write_but_lose)

    store = _jwt_like()

    assert store.resolve() == "the-other-process-won"
    assert store.source == "generated"


def test_the_keychain_beats_the_file(data_dir, monkeypatch):
    (data_dir / ".jwt-secret").write_text("from-the-file", encoding="utf-8")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr(secret_store, "_keychain_read", lambda account: "from-the-keychain")

    store = _jwt_like()

    assert store.resolve() == "from-the-keychain"
    assert store.source == "keychain"


def test_the_keychain_account_is_derived_from_the_file_name():
    """One name, two places it has to match — derived so they cannot drift."""
    assert _jwt_like().keychain_account == "jwt-secret"


def test_an_env_value_that_fails_validation_is_ignored(data_dir, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "malformed")

    store = _jwt_like(
        validate=lambda value: value.startswith("ok-"), generate=lambda: "ok-generated"
    )

    assert store.resolve() == "ok-generated"
    assert store.from_env is False


def test_a_file_whose_contents_are_invalid_is_replaced(data_dir, monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    (data_dir / ".jwt-secret").write_text("garbage", encoding="utf-8")

    store = _jwt_like(validate=lambda value: value.startswith("ok-"), generate=lambda: "ok-new")

    assert store.resolve() == "ok-new"
    assert store.file_path.read_text(encoding="utf-8") == "ok-new"


def test_reset_cache_makes_the_store_resolve_again(data_dir, monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    store = _jwt_like()
    first = store.resolve()

    store.reset_cache()
    assert store._cached is None

    assert store.resolve() == first
    assert store.source == "file"  # re-read from disk, not remembered


# ── Persistence failures ─────────────────────────────────────────────────────

def test_a_missing_volume_still_boots(data_dir, monkeypatch):
    """The JWT secret's whole reason for `ephemeral_fallback`: an install with no
    writable data dir must start, loudly, rather than refuse to serve."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr(secret_store, "DATA_DIR", data_dir / "unwritable" / "data")
    monkeypatch.setattr(
        secret_store.Path, "mkdir", _raises(PermissionError("read-only file system"))
    )

    store = _jwt_like()

    assert store.resolve()  # a usable secret, no exception
    assert store.source == "ephemeral"


def test_a_secret_that_may_not_be_ephemeral_raises_instead(data_dir, monkeypatch):
    """The encryption key's policy. A Fernet key that dies at restart would encrypt
    new credentials into ciphertext nobody can read back, so it fails loudly."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr(
        secret_store.Path, "mkdir", _raises(PermissionError("read-only file system"))
    )

    store = _jwt_like(ephemeral_fallback=False)

    with pytest.raises(SecretPersistenceError):
        store.resolve()


def test_an_existing_but_unreadable_file_is_not_silently_replaced(data_dir, monkeypatch):
    """Distinct from a MISSING file. Falling through to "generate a new one" here
    would rotate a live secret because of a permissions mistake — for the
    encryption key that orphans every encrypted row."""
    (data_dir / ".jwt-secret").write_text("the-established-secret", encoding="utf-8")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr(
        secret_store.Path, "read_text", _raises(PermissionError("permission denied"))
    )

    store = _jwt_like(ephemeral_fallback=False)

    with pytest.raises(SecretPersistenceError):
        store.resolve()


# ── Concurrent first boot ────────────────────────────────────────────────────

def test_a_process_that_loses_the_race_adopts_the_winners_value(data_dir):
    """Two processes booting together (a rolling redeploy) must not end up holding
    different signing keys. `O_CREAT|O_EXCL` makes exactly one of them the writer;
    the other re-reads."""
    store = _jwt_like()
    store.file_path.write_text("the-winners-secret", encoding="utf-8")

    assert store._claim_file("the-losers-secret") == "the-winners-secret"
    assert store.file_path.read_text(encoding="utf-8") == "the-winners-secret"


def test_four_real_processes_starting_at_once_agree_on_one_secret(tmp_path):
    child = textwrap.dedent(
        f"""
        import os, sys
        from pathlib import Path
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        from core import secret_store
        secret_store.DATA_DIR = Path({str(tmp_path)!r})
        secret_store._keychain_read = lambda account: None
        secret_store._keychain_write = lambda account, value: False
        store = secret_store.PersistedSecret(
            env_var="RACE_SECRET",
            filename=".race-secret",
            generate=lambda: os.urandom(16).hex(),
            ephemeral_fallback=True,
        )
        print(store.resolve())
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "RACE_SECRET"}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
        )
        for _ in range(4)
    ]
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode()
        results.append(out.decode().strip())

    assert len(set(results)) == 1, f"processes disagreed on the secret: {results}"
    assert results[0] == (tmp_path / ".race-secret").read_text(encoding="utf-8").strip()


# ── The encryption key, which now rides the same ladder ──────────────────────

def test_the_encryption_key_still_comes_from_its_env_var(data_dir, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    EncryptionKeyManager.reset_cache()

    assert EncryptionKeyManager.get_key() == key.encode()
    assert decrypt_value(encrypt_value("round trip")) == "round trip"


def test_an_encryption_key_file_written_before_this_change_is_picked_up_unchanged(
    data_dir, monkeypatch
):
    """Installs already have `backend/data/.encryption-key` on the Railway volume.
    Reading a different file — or rewriting this one — would silently re-key the
    install and orphan every Fernet-encrypted row."""
    legacy = Fernet.generate_key().decode()
    (data_dir / ".encryption-key").write_text(legacy, encoding="utf-8")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    EncryptionKeyManager.reset_cache()

    assert EncryptionKeyManager.get_key() == legacy.encode()
    assert (data_dir / ".encryption-key").read_text(encoding="utf-8") == legacy


def test_an_invalid_encryption_key_env_var_is_ignored_for_a_stored_one(data_dir, monkeypatch):
    stored = Fernet.generate_key().decode()
    (data_dir / ".encryption-key").write_text(stored, encoding="utf-8")
    monkeypatch.setenv("ENCRYPTION_KEY", "not-a-fernet-key")
    EncryptionKeyManager.reset_cache()

    assert EncryptionKeyManager.get_key() == stored.encode()


def test_the_encryption_key_refuses_to_become_ephemeral(data_dir, monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(
        secret_store.Path, "mkdir", _raises(PermissionError("read-only file system"))
    )
    EncryptionKeyManager.reset_cache()

    with pytest.raises(SecretPersistenceError):
        EncryptionKeyManager.get_key()
