"""
CakeCRM — one resolution ladder for the process's long-lived secrets.

Two secrets must come back IDENTICAL on the next boot or the install breaks:
the Fernet key (a new one orphans every stored credential) and the JWT signing
secret (a new one signs every seat out — issue #222). They resolve the same way,
and this module is the single place that ladder is written:

1. an environment variable   (deployed instances that pin their own value)
2. the OS keychain           (local macOS / Windows dev machines)
3. a file under ``backend/data/``  (headless Linux, CI, Docker, Railway)
4. otherwise: generate once, store it, and reuse it forever after

Step 3 is the one that matters on Railway. The container filesystem is replaced
on every redeploy EXCEPT ``/app/backend/data``, which ``railway.json`` requires
as a mounted volume, and that is the directory ``backend/data/`` resolves to.

The two secrets differ in exactly one respect, which is a constructor argument
rather than a second ladder: what to do when the *store* step fails (no volume,
read-only mount, unreadable existing file). The JWT secret must still boot
(``ephemeral_fallback=True``) and says loudly that sessions will not survive a
restart; the encryption key raises instead, because silently encrypting new
credentials under a key that dies at restart is worse than a visible failure.
"""

import logging
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

# The Railway volume (`requiredMountPath: /app/backend/data`). The module-level
# name is what tests repoint at a tmp dir — `PersistedSecret.file_path` reads it
# on every call rather than capturing it at construction.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

KEYCHAIN_SERVICE = "cakecrm"


def _keychain_read(account: str) -> str | None:
    """Read one secret from the OS keychain, or ``None`` if there isn't one.

    `keyring` is imported inside the function: on Linux and in the Railway
    container no backend is installed, so this raises `NoKeyringError` and the
    ladder falls through to the file step. Module-level so tests can neutralize
    the keychain without touching the developer's real one.
    """
    try:
        import keyring
        return keyring.get_password(KEYCHAIN_SERVICE, account) or None
    except Exception as exc:
        logger.debug("Keychain not available for read (%s): %s", account, exc)
        return None


def _keychain_write(account: str, value: str) -> bool:
    """Store one secret in the OS keychain. False when there is no keychain."""
    try:
        import keyring
        keyring.set_password(KEYCHAIN_SERVICE, account, value)
        return True
    except Exception as exc:
        logger.debug("Keychain not available for write (%s): %s", account, exc)
        return False


class SecretPersistenceError(RuntimeError):
    """The secret could not be read back or stored durably."""


class PersistedSecret:
    """One long-lived secret, resolved once per process and reused thereafter.

    ``keychain_account`` is derived from ``filename`` (``.jwt-secret`` →
    ``jwt-secret``) so the two names cannot drift apart.
    """

    def __init__(
        self,
        *,
        env_var: str,
        filename: str,
        generate: Callable[[], str],
        validate: Callable[[str], bool] | None = None,
        ignored_env_values: Iterable[str] = (),
        ephemeral_fallback: bool = False,
    ) -> None:
        self.env_var = env_var
        self.filename = filename
        self.keychain_account = filename.lstrip(".")
        self._generate = generate
        self._validate = validate or (lambda value: bool(value))
        self._ignored_env_values = frozenset(ignored_env_values)
        self._ephemeral_fallback = ephemeral_fallback
        self._cached: str | None = None
        self._source: str = ""

    @property
    def file_path(self) -> Path:
        return DATA_DIR / self.filename

    @property
    def source(self) -> str:
        """Where the resolved value came from: ``env``, ``keychain``, ``file``,
        ``generated`` (freshly made AND stored durably), or ``ephemeral`` (made
        for this process only — it dies on restart)."""
        self.resolve()
        return self._source

    @property
    def from_env(self) -> bool:
        """True only when an OPERATOR supplied the value via the env var. Drives
        `Settings.jwt_secret_is_auto`, so a placeholder must not count."""
        return self.source == "env"

    def reset_cache(self) -> None:
        """Forget the resolved value (tests; a process never needs this)."""
        self._cached = None
        self._source = ""

    def resolve(self) -> str:
        if self._cached is not None:
            return self._cached

        value, source = self._resolve_uncached()
        self._cached = value
        self._source = source
        return value

    # -- the ladder ----------------------------------------------------------

    def _resolve_uncached(self) -> tuple[str, str]:
        raw = os.environ.get(self.env_var, "")
        if raw and raw not in self._ignored_env_values:
            if self._validate(raw):
                logger.info("Using %s from the environment", self.env_var)
                return raw, "env"
            logger.warning("%s is set but is not a valid value, ignoring", self.env_var)

        stored = _keychain_read(self.keychain_account)
        if stored:
            if self._validate(stored):
                logger.info("Using %s from the OS keychain", self.env_var)
                return stored, "keychain"
            logger.warning(
                "The OS keychain entry for %s is invalid, ignoring", self.keychain_account
            )

        from_file, stale_file = self._read_file()
        if from_file is not None:
            return from_file, "file"

        return self._generate_and_store(replace_existing=stale_file)

    def _read_file(self) -> tuple[str | None, bool]:
        """``(stored secret, is there a stale file to replace)``.

        An existing-but-UNREADABLE file is deliberately not the same as a missing
        one: falling through to "generate a new one" there would silently rotate
        a live secret because of a permissions mistake. Only a file whose
        *contents* fail validation is treated as replaceable — and the caller has
        to be TOLD that, because the write below refuses to clobber a file it did
        not create, and would otherwise adopt the very value it just rejected.
        """
        path = self.file_path
        if not path.exists():
            return None, False
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            self._fail(
                f"{path} exists but could not be read ({exc}); refusing to silently "
                f"replace an established {self.env_var}"
            )
            return None, False
        if not self._validate(value):
            logger.warning("%s holds an invalid value, regenerating", path)
            return None, True
        logger.info("Using %s from file: %s", self.env_var, path)
        return value, False

    def _generate_and_store(self, *, replace_existing: bool = False) -> tuple[str, str]:
        value = self._generate()

        if _keychain_write(self.keychain_account, value):
            # Read back rather than trusting the write: if a second process was
            # booting at the same moment, the keychain holds ITS value and both
            # processes must agree on the one that is actually stored.
            stored = _keychain_read(self.keychain_account)
            logger.info(
                "Generated a new %s and stored it in the OS keychain", self.env_var
            )
            return (stored or value), "generated"

        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if replace_existing:
                # Clear the rejected file so the exclusive publish below can run.
                # Unlinking rather than overwriting keeps ONE write path. Two
                # processes that both reject the SAME corrupt file are
                # last-write-wins and may end up holding different secrets — an
                # edge of an already-broken state, and the next restart converges
                # them; a process that merely loses the ordinary create does
                # converge, by re-reading the winner's value.
                self.file_path.unlink(missing_ok=True)
            written = self._claim_file(value)
        except OSError as exc:
            self._fail(
                f"could not persist {self.env_var} to {self.file_path} ({exc})"
            )
            return value, "ephemeral"

        if written != value:
            # Another process booting at the same moment published the file
            # first. Its value is the one on disk, so adopt it rather than
            # keeping a secret no other process shares — but only if it is
            # usable. Caching an empty or malformed signing key because someone
            # else put it there would be worse than any of the paths above.
            if not self._validate(written):
                self._fail(
                    f"another process left an unusable {self.env_var} at {self.file_path}"
                )
                return value, "ephemeral"
            logger.info(
                "Another process stored %s first; using the value already on disk",
                self.env_var,
            )
            return written, "file"

        logger.info("Generated a new %s and stored it in file: %s", self.env_var, self.file_path)
        return value, "generated"

    def _claim_file(self, value: str) -> str:
        """Publish the secret file exclusively and return whatever ended up on disk.

        Write to a private temp file in the same directory, then ``os.link`` it
        into place. The link is the concurrency guard — it is atomic and fails
        with ``FileExistsError`` if another process got there first, and a loser
        then re-reads the winner's value — and it is also why the target file is
        never seen half-written: it appears only once it is complete. An
        ``O_CREAT|O_EXCL`` create would publish an EMPTY file for the length of
        the write, which a process racing it would read as the secret (or reject
        as corrupt and replace out from under the writer).

        ``mkstemp`` creates at mode 0600, so the bytes are unreadable by others
        from the first one and the published file inherits that — no
        write-then-chmod window either.
        """
        path = self.file_path
        fd, tmp_name = tempfile.mkstemp(prefix=f"{self.filename}.", dir=str(DATA_DIR))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                return path.read_text(encoding="utf-8").strip()
        finally:
            tmp.unlink(missing_ok=True)
        if os.name == "nt":
            logger.warning(
                "%s was written with default ACLs — restrict access manually on "
                "shared machines",
                path,
            )
        return value

    def _fail(self, message: str) -> None:
        """Apply this secret's persistence-failure policy."""
        if not self._ephemeral_fallback:
            raise SecretPersistenceError(message)
        logger.error(
            "%s — falling back to a secret that exists only in this process. "
            "It will change on the next restart. Set %s to a fixed value, or "
            "make sure the persistent volume is mounted.",
            message,
            self.env_var,
        )
