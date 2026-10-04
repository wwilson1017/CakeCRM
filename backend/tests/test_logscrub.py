"""The no-login link tokens never reach a log line (#267).

The access line is written by uvicorn's HTTP protocol, not by the ASGI app, so a
TestClient never produces one — the headline test therefore serves `main.app` through
a REAL uvicorn server on a loopback socket (lifespan off, so no database) and captures
everything the two uvicorn loggers write. It goes through `main` on
purpose: what is pinned is that the app INSTALLS the scrubber, not merely that the
filter works when someone remembers to attach it.
"""

import io
import logging
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from core import logscrub
from crm import todo_capture, todo_web

WEB_TOKEN = "WebTok3nAbcdefghijklmnopqrstuvwxyz0123456789"
CAPTURE_TOKEN = "CaptureTok3nZyxwvutsrqponmlkjihgfedcba98765"
WRONG_TOKEN = "GuessTok3nQwertyuiopasdfghjklzxcvbnm111111"
ALL_TOKENS = (WEB_TOKEN, CAPTURE_TOKEN, WRONG_TOKEN)


# ── The pure scrubber ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        (f"/todo/{WEB_TOKEN}", "/todo/<redacted>"),
        (f"/todo/{WEB_TOKEN}/inbox", "/todo/<redacted>/inbox"),
        (f"/capture/{CAPTURE_TOKEN}", "/capture/<redacted>"),
        (f"/api/capture/{CAPTURE_TOKEN}", "/api/capture/<redacted>"),
        (f"/api/todo-web/{WEB_TOKEN}/todos/3", "/api/todo-web/<redacted>/todos/3"),
        (f"/todo/{WEB_TOKEN}?x=1", "/todo/<redacted>?x=1"),
        (f'GET "https://h.example/todo/{WEB_TOKEN}" 404', 'GET "https://h.example/todo/<redacted>" 404'),
    ],
)
def test_scrub_redacts_every_token_path_shape(raw, clean):
    assert logscrub.scrub(raw) == clean


@pytest.mark.parametrize(
    "path",
    ["/api/crm/todos/5", "/crm/todos", "/api/crm/todo-mode", "/todo", "/capture", "/api/health"],
)
def test_scrub_leaves_ordinary_paths_readable(path):
    assert logscrub.scrub(path) == path


def test_filter_keeps_non_string_args_intact():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", f"/todo/{WEB_TOKEN}", "1.1", 200), None,
    )
    logscrub.ScrubTokens().filter(record)
    assert record.getMessage() == '127.0.0.1:1 - "GET /todo/<redacted> HTTP/1.1" 200'


def test_filter_scrubs_dict_args_and_tracebacks():
    try:
        raise RuntimeError(f"boom at /api/todo-web/{WEB_TOKEN}/todos")
    except RuntimeError:
        import sys
        exc = sys.exc_info()
    record = logging.LogRecord(
        "gunicorn.access", logging.INFO, __file__, 1, "%(r)s", None, exc,
    )
    record.args = {"r": f"GET /capture/{CAPTURE_TOKEN}"}
    logscrub.ScrubTokens().filter(record)
    text = logging.Formatter().format(record)
    assert "<redacted>" in text
    assert not any(t in text for t in ALL_TOKENS)


def test_install_covers_the_root_handlers_our_own_loggers_propagate_to():
    """`logging.getLogger(__name__)` records reach the output through root's handlers,
    and a logger filter never sees a propagated record — so install() filters those
    handlers."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    root.addHandler(handler)
    server_loggers = [logging.getLogger(n) for n in logscrub._SERVER_LOGGERS]
    saved = [(lg, list(lg.filters)) for lg in server_loggers]
    try:
        logscrub.install()
        child = logging.getLogger("crm.todo_web")
        child.warning("refused /todo/%s", WEB_TOKEN)  # the template spells the path
        child.warning("refused %s", f"/capture/{CAPTURE_TOKEN}")  # an argument does
    finally:
        root.removeHandler(handler)
        # install() is idempotent and global; put the server loggers back exactly, or
        # this test would attach the filter the end-to-end test proves `main` attaches.
        for lg, filters in saved:
            lg.filters[:] = filters
    assert buf.getvalue().splitlines() == ["refused /todo/<redacted>", "refused /capture/<redacted>"]


# ── End to end, through the real server ──────────────────────────────────────

@pytest.fixture
def served_app(monkeypatch):
    """`main.app` behind a real uvicorn server, every log line captured."""
    import main

    for limiter in (todo_capture.capture_limiter, todo_web.web_limiter, todo_web.guess_limiter):
        limiter.clear()
    state = {"todo_capture_token": CAPTURE_TOKEN, "todo_web_enabled": True, "todo_web_token": WEB_TOKEN}
    monkeypatch.setattr(todo_capture.service, "get_todo_public_settings", lambda: dict(state))
    monkeypatch.setattr(todo_web.service, "get_todo_public_settings", lambda: dict(state))
    monkeypatch.setattr(todo_capture.gtd_service, "capture", lambda text, **kw: {"id": 1, "title": text})

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    # Handlers go straight on the uvicorn loggers and install() is NOT re-run here:
    # a logger-level filter is applied before that logger's own handlers, so these
    # lines are scrubbed only if importing `main` attached the filter.
    watched = [logging.getLogger(n) for n in ("uvicorn.access", "uvicorn.error")]
    saved = [(lg, lg.level) for lg in watched]
    for lg in watched:
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, lifespan="off", log_config=None, access_log=True))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)

    yield f"http://127.0.0.1:{port}", buf, state

    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
    for lg, level in saved:
        lg.removeHandler(handler)
        lg.setLevel(level)


def test_no_token_reaches_the_logs_through_the_real_server(served_app):
    base, buf, _ = served_app
    with httpx.Client(base_url=base) as c:
        assert c.get(f"/todo/{WEB_TOKEN}").status_code in (200, 503)
        assert c.get(f"/todo/{WEB_TOKEN}/manifest.webmanifest").status_code == 200
        assert c.get(f"/capture/{CAPTURE_TOKEN}").status_code == 200
        assert c.post(f"/api/capture/{CAPTURE_TOKEN}", json={"text": "x"}).status_code == 200
        # The 404-never-401 branch: a wrong token on each surface.
        assert c.get(f"/todo/{WRONG_TOKEN}").status_code == 404
        assert c.get(f"/capture/{WRONG_TOKEN}").status_code == 404
        assert c.get(f"/api/todo-web/{WRONG_TOKEN}/todos").status_code == 404
        assert c.post(f"/api/capture/{WRONG_TOKEN}?src=x", json={"text": "x"}).status_code == 404
    time.sleep(0.2)  # let the last access line flush
    out = buf.getvalue()
    # Not vacuous: the access lines are there, just without the secret.
    assert out.count("uvicorn.access") >= 8, out
    assert '"GET /todo/<redacted> HTTP/1.1" 404' in out, out
    assert "/api/todo-web/<redacted>/todos" in out
    for token in ALL_TOKENS:
        assert token not in out, out


def test_an_error_on_a_token_path_logs_no_token(served_app, monkeypatch):
    """An unhandled exception is logged with its traceback by uvicorn. Make the
    exception text itself name the token path, the worst case for an error log."""
    base, buf, _ = served_app

    def _boom():
        raise RuntimeError(f"settings unreadable while serving /todo/{WEB_TOKEN}")

    monkeypatch.setattr(todo_web.service, "get_todo_public_settings", _boom)
    with httpx.Client(base_url=base) as c:
        assert c.get(f"/todo/{WEB_TOKEN}").status_code == 500
    time.sleep(0.2)
    out = buf.getvalue()
    assert "settings unreadable while serving /todo/<redacted>" in out, out
    assert "Traceback" in out
    for token in ALL_TOKENS:
        assert token not in out, out
