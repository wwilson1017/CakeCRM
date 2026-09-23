"""Loader for the product help library — committed markdown, read from disk, cached.

## Why files, and not any of the three obvious alternatives (issue #143)

* **Not a Python constant in the system prompt.** A page of setup notes fits there; a
  product manual does not. It would grow the cached prefix without bound, ride every
  turn whether or not anyone asked a how-to question, and invalidate the provider's
  prompt cache on every edit. Only the slim ``identity.HELP_NOTE`` enters the prompt;
  the library's bulk is fetched by tool call.
* **Not Postgres rows.** This is product content versioned WITH the code, not user
  state. A database copy needs a seed or migration on every wording change and can
  drift from the binary reading it; a file changes atomically with the release that
  changes the feature it describes. ``assistant_context_files`` stays what it is —
  Baker's own writable notes. Different store, different trust, different lifecycle.
  The same reasoning declines building a Postgres FTS index over these files at boot:
  a runtime copy of committed content is a second store that can disagree with the
  first.
* **Not embeddings.** Deterministic ranking over a small immutable corpus needs no
  provider, no key and no new infrastructure. ``search.py`` is a pure ranking pass.

Committed files also mean ``tests/test_prompt_genericization.py``'s repo-wide scan
covers every topic the moment it is staged, and its model-facing sweep covers them too
(help text becomes model-facing payload the moment a tool returns it).

## Loading

Lazy and cached, NOT loaded at import. ``main.py`` imports the app with no database in
CI, and putting disk I/O plus a possible exception on the import path would make a bad
topic file an import-time crash. ``warm()`` is called once from the lifespan so the
first user-facing question does not pay for the read, and it CANNOT raise.

Content is immutable per deploy, so one cache with no invalidation is the whole cache
story. Tests that write a synthetic corpus call ``load_library(path)`` directly rather
than poking the cache.
"""

import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CONTENT_ROOT = Path(__file__).resolve().parent / "content"

# Bounds. Every one of these is pinned by a test, because the whole point of a bounded
# corpus is that a turn budget cannot be blown by one topic file growing without anyone
# noticing. A subject too big for the ceiling splits into children inside its folder.
MAX_TOPIC_CHARS = 8000
MAX_TITLE_CHARS = 80
MAX_DESCRIPTION_CHARS = 200
MAX_ALIASES = 12
MAX_ALIAS_CHARS = 40
MAX_SLUG_CHARS = 60

# Slug grammar. One optional folder segment plus a topic segment; lowercase letters,
# digits and single hyphens. ONE normalizer, the same way context_files.normalize_filename
# is the one normalizer for that store — two spellings of a slug is how a "topic not
# found" bug gets born.
_SEGMENT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_SLUG_RE = re.compile(rf"^{_SEGMENT}(?:/{_SEGMENT})?$")

_FRONT_MATTER_FENCE = "---"


class HelpLibraryError(Exception):
    """A malformed topic file. Raised by ``load_library``; never escapes ``warm()``."""


@dataclass(frozen=True)
class Topic:
    """One help topic: its identity, its searchable metadata, and its body."""

    slug: str            # "settings/gmail" — path under content/, without ".md"
    folder: str          # "settings", or "" for a top-level topic
    title: str
    description: str
    aliases: tuple[str, ...]
    admin_only: bool     # the flow this topic describes is admin-only
    body: str            # markdown, front matter stripped
    author: str = ""     # "Chris Voss with Tahl Raz" — set on playbooks, "" elsewhere

    @property
    def headings(self) -> tuple[str, ...]:
        return tuple(
            line.lstrip("#").strip()
            for line in self.body.splitlines()
            if line.startswith("#")
        )


@dataclass(frozen=True)
class Library:
    """The whole corpus, in deterministic slug order."""

    topics: tuple[Topic, ...]

    @property
    def by_slug(self) -> dict[str, Topic]:
        return {t.slug: t for t in self.topics}

    @property
    def folders(self) -> tuple[str, ...]:
        """Folder names in sorted order. Deterministic: it is quoted in the static
        system prompt, which must be byte-identical turn to turn."""
        return tuple(sorted({t.folder for t in self.topics if t.folder}))

    def get(self, slug: str) -> Topic | None:
        return self.by_slug.get(slug)

    def in_folder(self, folder: str) -> tuple[Topic, ...]:
        return tuple(t for t in self.topics if t.folder == folder)

    @property
    def root_topics(self) -> tuple[Topic, ...]:
        return tuple(t for t in self.topics if not t.folder)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a topic file into its front-matter mapping and its body.

    Hand-parsed on purpose: the grammar is five scalar keys, so a YAML dependency would
    buy nothing but a parser with far more surface than the format needs. Unknown keys
    are a hard error rather than a silent ignore — a typo in ``description`` would
    otherwise ship a topic with no search snippet and nothing would say so.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_FENCE:
        raise HelpLibraryError("missing front matter: the file must open with '---'")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FRONT_MATTER_FENCE)
    except StopIteration:
        raise HelpLibraryError("unterminated front matter: no closing '---'") from None

    meta: dict[str, str] = {}
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise HelpLibraryError(f"front-matter line is not 'key: value': {raw!r}")
        key = key.strip()
        if key in meta:
            raise HelpLibraryError(f"duplicate front-matter key: {key!r}")
        meta[key] = value.strip()
    return meta, "\n".join(lines[end + 1:]).strip()


_REQUIRED_KEYS = {"title", "description"}
_OPTIONAL_KEYS = {"aliases", "admin", "author"}


def _parse_topic(slug: str, text: str) -> Topic:
    meta, body = parse_front_matter(text)

    unknown = set(meta) - _REQUIRED_KEYS - _OPTIONAL_KEYS
    if unknown:
        raise HelpLibraryError(f"{slug}: unknown front-matter keys: {sorted(unknown)}")
    missing = _REQUIRED_KEYS - set(meta)
    if missing:
        raise HelpLibraryError(f"{slug}: missing front-matter keys: {sorted(missing)}")

    title, description = meta["title"], meta["description"]
    if not title or len(title) > MAX_TITLE_CHARS:
        raise HelpLibraryError(f"{slug}: title must be 1-{MAX_TITLE_CHARS} characters")
    if not description or len(description) > MAX_DESCRIPTION_CHARS:
        raise HelpLibraryError(
            f"{slug}: description must be 1-{MAX_DESCRIPTION_CHARS} characters"
        )
    if not body:
        raise HelpLibraryError(f"{slug}: has no body")
    if len(body) > MAX_TOPIC_CHARS:
        raise HelpLibraryError(
            f"{slug}: body is {len(body)} characters, over the {MAX_TOPIC_CHARS} ceiling — "
            f"split the subject into child topics inside its folder"
        )

    raw_aliases = [a.strip() for a in meta.get("aliases", "").split(",") if a.strip()]
    if len(raw_aliases) > MAX_ALIASES:
        raise HelpLibraryError(f"{slug}: more than {MAX_ALIASES} aliases")
    for alias in raw_aliases:
        if len(alias) > MAX_ALIAS_CHARS:
            raise HelpLibraryError(f"{slug}: alias {alias!r} is over {MAX_ALIAS_CHARS} characters")

    admin_raw = meta.get("admin", "false").lower()
    if admin_raw not in ("true", "false"):
        raise HelpLibraryError(f"{slug}: 'admin' must be true or false, got {admin_raw!r}")

    # Title-sized on purpose: a byline is one line, and a fifth ceiling constant with a
    # single use would be a constant nobody pins. Optional HERE — the 27 product topics
    # carry no byline; only the playbook guard in tests/test_help_library.py demands one,
    # and only of playbooks, where attribution IS the copyright posture (CONTRIBUTING.md).
    author = meta.get("author", "")
    if len(author) > MAX_TITLE_CHARS:
        raise HelpLibraryError(f"{slug}: author must be at most {MAX_TITLE_CHARS} characters")

    folder, _, _ = slug.rpartition("/")
    return Topic(
        slug=slug,
        folder=folder,
        title=title,
        description=description,
        aliases=tuple(raw_aliases),
        admin_only=admin_raw == "true",
        body=body,
        author=author,
    )


def _slug_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    slug = rel[: -len(".md")]
    if len(slug) > MAX_SLUG_CHARS:
        raise HelpLibraryError(f"slug {slug!r} is over {MAX_SLUG_CHARS} characters")
    if not _SLUG_RE.match(slug):
        raise HelpLibraryError(
            f"{rel!r} does not match the slug grammar: lowercase letters, digits and single "
            f"hyphens, at most one folder level (e.g. 'settings/gmail.md')"
        )
    return slug


def load_library(root: Path | None = None) -> Library:
    """Read and validate every topic under ``root`` (default: the shipped content).

    Raises ``HelpLibraryError`` on a malformed corpus. That is deliberate — a broken
    topic must fail a test loudly, not degrade into a library that silently lost a page.
    ``warm()`` is the one caller that swallows it, because a help outage must never
    take the server down.
    """
    root = root or CONTENT_ROOT
    # sorted() is the determinism guarantee: rglob's order is filesystem-dependent, and
    # slug order decides tie-breaks in search results and the order of every listing.
    paths = sorted(root.rglob("*.md"), key=lambda p: p.relative_to(root).as_posix())
    topics = [_parse_topic(_slug_for(p, root), p.read_text(encoding="utf-8")) for p in paths]
    if not topics:
        raise HelpLibraryError(f"no help topics found under {root}")
    return Library(topics=tuple(topics))


@functools.lru_cache(maxsize=1)
def get_library() -> Library:
    """The shipped library, loaded once. Content is immutable per deploy."""
    return load_library()


def warm() -> None:
    """Load the library at boot so the first question does not pay for the disk read.

    CANNOT raise. ``rglob`` and ``read_text`` fail with OSError, a bad topic fails with
    HelpLibraryError, and a decode failure fails with UnicodeDecodeError — so this
    catches Exception rather than enumerating them and being wrong about the list. A
    library that failed to warm still loads lazily on the first tool call (and fails
    there, where the registry turns it into one tool error instead of a dead server).
    """
    try:
        count = len(get_library().topics)
    except Exception:
        logger.warning("help library failed to warm; help tools will retry lazily", exc_info=True)
        return
    logger.info("Help library loaded (%d topics)", count)
