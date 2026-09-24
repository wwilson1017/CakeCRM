"""The JWT signing secret survives a restart (issue #222).

`core.config` used to substitute `secrets.token_hex(32)` in the `JWTSettings`
class body whenever `JWT_SECRET` was unset or left at the shipped placeholder.
That value lived only in the process, so every redeploy and every `python run.py`
minted a new signing key and invalidated every session on the install.

The ladder itself is tested in `test_secret_store.py`. What is tested here is the
wiring — that `core.config` is actually on that ladder — and the end-to-end
property the issue is about, proven the only way it can be: by running two real
processes against one data directory and verifying a token minted by the first in
the second.

The suite pins `JWT_SECRET` in `conftest.py`, so the child processes below strip
it back out of their environment to exercise the generate-and-persist path.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from core import config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _boot(data_dir: Path, mode: str = "report", token: str = "") -> dict:
    """Run a fresh Python process that boots `core.config` against `data_dir`.

    A second in-process `Settings` would prove nothing — `JWTSettings.secret_key`
    is evaluated once in the class body — so "restart" here means a real process.
    """
    child = textwrap.dedent(
        f"""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        os.environ.pop("JWT_SECRET", None)

        # Point the ladder's file step at the test's data dir and take the OS
        # keychain out of play BEFORE core.config resolves the secret at import.
        from core import secret_store
        secret_store.DATA_DIR = Path({str(data_dir)!r})
        secret_store._keychain_read = lambda account: None
        secret_store._keychain_write = lambda account, value: False

        from core import config
        out = {{
            "secret": config.JWT_SECRET_STORE.resolve(),
            "source": config.JWT_SECRET_STORE.source,
            "secret_key": config.settings.jwt.secret_key,
            "is_auto": config.settings.jwt_secret_is_auto,
        }}
        mode, token = {mode!r}, {token!r}
        if mode == "mint":
            from core.auth import create_access_token
            out["token"] = create_access_token({{"sub": "1"}})
        elif mode == "verify":
            from core.auth import decode_access_token
            out["claims"] = decode_access_token(token)
        print(json.dumps(out))
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "JWT_SECRET"}
    done = subprocess.run(
        [sys.executable, "-c", child], capture_output=True, timeout=180, env=env
    )
    assert done.returncode == 0, done.stderr.decode()
    return json.loads(done.stdout.decode().strip().splitlines()[-1])


def test_a_restarted_process_keeps_the_same_signing_secret(tmp_path):
    first = _boot(tmp_path)
    second = _boot(tmp_path)

    assert first["source"] == "generated", "expected the first boot to mint the secret"
    assert second["source"] == "file", "expected the second boot to read it back"
    assert first["secret"] == second["secret"]


def test_a_token_minted_before_a_restart_still_verifies_after_it(tmp_path):
    """The user-visible property: a redeploy no longer signs everyone out."""
    before = _boot(tmp_path, mode="mint")
    after = _boot(tmp_path, mode="verify", token=before["token"])

    assert after["claims"]["sub"] == "1"


def test_the_secret_settings_hands_out_is_the_resolved_one(tmp_path):
    booted = _boot(tmp_path)

    assert booted["secret_key"] == booted["secret"]
    assert booted["is_auto"] is True, "nobody supplied JWT_SECRET, so it is still auto"


def test_a_supplied_jwt_secret_is_what_the_app_signs_with():
    """The suite's own environment is the operator-supplied case: `conftest.py`
    exports a real JWT_SECRET, so this asserts against the already-imported
    settings rather than re-resolving."""
    assert config.settings.jwt.secret_key == os.environ["JWT_SECRET"]
    assert config.JWT_SECRET_STORE.from_env is True
    assert config.settings.jwt_secret_is_auto is False


def test_config_is_wired_to_the_shared_ladder():
    """Guards the two names that must match the rest of the system: the env var
    operators set, and the file on the Railway volume."""
    store = config.JWT_SECRET_STORE

    assert store.env_var == "JWT_SECRET"
    assert store.filename == ".jwt-secret"
    assert store.file_path.parent.name == "data"
    assert store._ephemeral_fallback is True, "an unmounted volume must not stop the app"
    assert "change-me-in-production" in store._ignored_env_values


@pytest.mark.parametrize("placeholder", ["change-me-in-production"])
def test_the_shipped_placeholder_never_becomes_the_signing_key(tmp_path, placeholder):
    """`.env.example` ships it and `run.py` writes it into a generated `.env`.
    Every install signing with the same public string would be worse than the bug
    this issue fixes."""
    child = textwrap.dedent(
        f"""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        os.environ["JWT_SECRET"] = {placeholder!r}
        from core import secret_store
        secret_store.DATA_DIR = Path({str(tmp_path)!r})
        secret_store._keychain_read = lambda account: None
        secret_store._keychain_write = lambda account, value: False
        from core import config
        print(json.dumps({{
            "secret_key": config.settings.jwt.secret_key,
            "is_auto": config.settings.jwt_secret_is_auto,
        }}))
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", child], capture_output=True, timeout=180, env=dict(os.environ)
    )
    assert done.returncode == 0, done.stderr.decode()
    out = json.loads(done.stdout.decode().strip().splitlines()[-1])

    assert out["secret_key"] != placeholder
    assert out["is_auto"] is True
