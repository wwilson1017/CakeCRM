"""Todo-GTD — secret path tokens for the two no-login surfaces (#70).

Both public surfaces authorize on an unguessable token that rides in the URL PATH,
not a header — that is what makes them bookmarkable on a phone. Everything here
exists to keep that path segment unambiguous and unguessable.
"""

import re
import secrets

# Tokens are URL path segments, so anything that could change which route matches is
# stripped rather than escaped — a token containing '/' or '.' would silently split
# the path or collide with the manifest route.
_TOKEN_STRIP_RE = re.compile(r"[^A-Za-z0-9_-]")

# Page slugs the no-login todo app routes to client-side, plus the API's own first
# segments. Only the capture token is clamped here since #293 (the todo app opens by
# browser sessions, crm/todo_sessions.py); the set is kept so a capture token can never
# read as a page or API path either.
#
# "manifest.webmanifest" is deliberately NOT listed: the strip above removes dots, so
# a clamped token can never equal it in the first place. That is also what makes the
# manifest routes safe to register ahead of the token routes.
RESERVED_TODO_WEB_SLUGS = frozenset({
    "today", "inbox", "next", "projects", "waiting", "someday", "done", "review",
    "search", "todos", "filters",
})

_TOKEN_BYTES = 32  # ~43 URL-safe chars


def clamp_token(value: str) -> str:
    """Normalize a user-supplied token to a safe path segment ('' for none).

    A value that reduces to a reserved slug is rejected as empty rather than
    accepted, since it could not be routed unambiguously anyway.
    """
    token = _TOKEN_STRIP_RE.sub("", str(value or "").strip())
    if token.lower() in RESERVED_TODO_WEB_SLUGS:
        return ""
    return token


def mint_token() -> str:
    """A fresh unguessable token. Regenerating one immediately 404s the old link —
    including any installed PWA that baked it into its launch URL."""
    while True:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        clamped = clamp_token(token)
        # token_urlsafe can emit '=' padding-free base64 that survives the clamp
        # unchanged, but re-clamping keeps ONE definition of what a valid token is.
        if clamped and clamped == token:
            return token
