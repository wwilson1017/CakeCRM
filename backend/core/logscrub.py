"""Keep the no-login link tokens out of the app's logs (#267).

`/todo/{token}`, `/capture/{token}`, `/api/capture/{token}` and
`/api/todo-web/{token}/…` are authorized by the token IN THE PATH (SECURITY.md), so a
log line carrying the path carries the credential. This filter replaces the segment
after `/todo/`, `/capture/` or `/todo-web/` with `<redacted>` in every record the
app writes: the message, its arguments (uvicorn's access line is a `%`-format over a
`(client, method, path, http_version, status)` tuple; gunicorn's is a dict) and the
rendered traceback, so an exception whose text names the path is scrubbed too.

It redacts that segment whether or not it is a token — `/todo/manifest.webmanifest`
and a tokenless `/todo/inbox` come out as `/todo/<redacted>` as well. Losing a
readable client-side route in a log is cheaper than a rule that has to tell a token
from a route name.

What it cannot catch is a token logged WITHOUT its path prefix (`log.info("%s", token)`),
so never do that. Scope: the app's own output only. A proxy in front of it (Railway's HTTP log) records
the request line before the app ever sees it — see SECURITY.md. Ported from
todo-gtd's `backend/logscrub.py`.
"""

import logging
import re
from collections.abc import Mapping

REDACTED = "<redacted>"

# `/todo/` and `/capture/` (with or without `/api`), and `/api/todo-web/`. The slash
# before the name keeps `/api/crm/todos/…` and `/crm/todos` out: neither has `todo/`.
_TOKEN_PATH = re.compile(r"(/(?:todo|capture|todo-web)/)[^/?#\s\"']+")


def scrub(text: str) -> str:
    return _TOKEN_PATH.sub(r"\1" + REDACTED, text)


def _scrub_arg(arg):
    # Scrub strings and anything that PRINTS a token path (a URL, an exception); an
    # argument with nothing to hide passes through untouched, type and all, so `%d`
    # still gets its int.
    text = arg if isinstance(arg, str) else str(arg)
    clean = scrub(text)
    return arg if clean == text else clean


class ScrubTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_scrub_arg(a) for a in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {k: _scrub_arg(v) for k, v in record.args.items()}
        if isinstance(record.msg, str) and scrub(record.msg) != record.msg:
            if record.args:
                # The TEMPLATE spells the path and an argument fills the token in
                # (`"refused /todo/%s"`). Scrubbing the template alone would eat the
                # placeholder and break formatting, so render first, then scrub. Only
                # this case loses the args — uvicorn's access template never names a
                # path, so its formatter still gets the tuple it unpacks.
                record.msg, record.args = scrub(record.getMessage()), None
            else:
                record.msg = scrub(record.msg)
        if record.exc_info and not record.exc_text:
            # Render the traceback now so the scrubbed text is what every handler's
            # Formatter reuses (Formatter.format only renders exc_info when exc_text
            # is empty).
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


_SCRUBBER = ScrubTokens()

# A logger's filter sees only records logged ON that logger, never ones propagating
# through it — so the filter goes on each server logger by name, and on the root
# HANDLERS, which see every record our own `logging.getLogger(__name__)` loggers
# propagate up. Logger-level filters survive gunicorn's UvicornWorker replacing the
# uvicorn loggers' handlers.
_SERVER_LOGGERS = ("uvicorn.access", "uvicorn.error", "gunicorn.access", "gunicorn.error")


def install() -> None:
    """Attach the scrubber. Idempotent: `addFilter` ignores a filter already present."""
    for handler in logging.getLogger().handlers:
        handler.addFilter(_SCRUBBER)
    for name in _SERVER_LOGGERS:
        logging.getLogger(name).addFilter(_SCRUBBER)
