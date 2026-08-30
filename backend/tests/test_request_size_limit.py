"""The request-size ceiling (#57) — the only layer that runs before a body is spooled.

This exists because the obvious place to enforce an upload cap does not work for multipart:
FastAPI parses the form — spooling the entire body, to /tmp past 1 MB — BEFORE it solves a
route's dependencies, so neither the handler's bounded read nor a `Depends` guard can stop
an oversized body from being written to disk first. The first test below proves that
ordering rather than asserting it, so the middleware's reason to exist is checked, not
remembered.
"""

import re

import pytest
import starlette.formparsers as formparsers
from conftest import fake_admin
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.testclient import TestClient
from starlette._utils import get_route_path

import main
from core.auth import get_current_user
from crm import attachment_service


def test_fastapi_spools_a_multipart_body_before_dependencies_run(monkeypatch):
    """The premise. If this ever stops being true, the middleware can move to a Depends."""
    parsed = []
    real_parse = formparsers.MultiPartParser.parse

    async def counting_parse(self, *a, **k):
        parsed.append(1)
        return await real_parse(self, *a, **k)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", counting_parse)

    def guard(request: Request):
        raise HTTPException(status_code=413, detail="too big")

    probe = FastAPI()

    @probe.post("/probe")
    def _probe(file: UploadFile = File(...), _=Depends(guard)):
        return {"ok": True}

    r = TestClient(probe).post("/probe", files={"file": ("f.bin", b"x" * 5000)})
    assert r.status_code == 413
    # The dependency rejected the request — and the body was spooled anyway.
    assert parsed == [1]


@pytest.fixture
def client():
    app = FastAPI()
    app.middleware("http")(main.request_size_limit_middleware)

    @app.post("/echo")
    def echo(file: UploadFile = File(...), user=Depends(get_current_user)):
        return {"n": len(file.file.read())}

    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


def test_a_body_over_the_ceiling_is_refused(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BYTES", 500)
    r = client.post("/echo", files={"file": ("f.bin", b"x" * 5000)})
    assert r.status_code == 413
    assert r.json()["detail"] == "Request too large."


def test_a_body_under_the_ceiling_passes_through(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BYTES", 100_000)
    r = client.post("/echo", files={"file": ("f.bin", b"x" * 5000)})
    assert r.status_code == 200
    assert r.json()["n"] == 5000


def test_an_unparseable_content_length_is_not_a_500(client, monkeypatch):
    """A malformed header is the ASGI server's problem, not ours — but it must not become
    a ValueError inside the middleware."""
    monkeypatch.setattr(main, "MAX_REQUEST_BYTES", 100_000)
    r = client.post("/echo", files={"file": ("f.bin", b"x" * 10)},
                    headers={"Content-Length": "not-a-number"})
    assert r.status_code != 500


def test_the_ceiling_clears_the_largest_legitimate_request():
    """It is a disk backstop, not a feature limit: if it were ever tightened below what a
    real route accepts, that route would start 413ing correct requests."""
    from assistant import uploads
    from crm import attachment_service

    assert main.MAX_REQUEST_BYTES > uploads.MAX_FILES * uploads.MAX_FILE_SIZE
    assert main.MAX_REQUEST_BYTES > attachment_service.MAX_ATTACHMENT_BYTES


# ── Per-route ceilings (issue #127) ──────────────────────────────────────────
#
# The global ceiling is a disk backstop at 64 MB, which is 6.4x what a chatter attachment
# may be. Everything between the two was admitted, spooled and parsed before the route's
# bounded read rejected it — so the route needs its own, tighter admission number.

ATTACHMENT_PATH = "/api/crm/chatter/note/7/attachments"


@pytest.fixture
def attachment_client():
    """A stand-in for the real attachment route: same path, same multipart shape."""
    app = FastAPI()
    app.middleware("http")(main.request_size_limit_middleware)

    @app.post("/api/crm/chatter/note/{note_id}/attachments")
    def upload(note_id: int, file: UploadFile = File(...), user=Depends(get_current_user)):
        return {"n": len(file.file.read())}

    app.dependency_overrides[get_current_user] = fake_admin
    return TestClient(app)


def test_an_oversized_attachment_is_refused_before_the_body_is_parsed(
    attachment_client, monkeypatch
):
    """The whole point of the issue: not merely that it 413s — the route already did that —
    but that the multipart parser never runs, so nothing is spooled to disk.

    The body size is derived from the FEATURE limit, never from `_request_limit_for`. Sizing
    it off the function under test is what made an earlier version of this test vacuous: with
    the route table emptied the call resolved to the 64 MB backstop, the test sent 64 MB + 1,
    the GLOBAL ceiling refused it, and the assertions passed with the feature switched off.
    """
    parsed = []
    real_parse = formparsers.MultiPartParser.parse

    async def counting_parse(self, *a, **k):
        parsed.append(1)
        return await real_parse(self, *a, **k)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", counting_parse)

    # Over the attachment cap, but far UNDER the global backstop — so only the per-route
    # ceiling can produce this rejection.
    oversized = attachment_service.MAX_ATTACHMENT_BYTES + main.MULTIPART_ENVELOPE_BYTES + 1
    assert oversized < main.MAX_REQUEST_BYTES, "the global ceiling would confound this test"

    r = attachment_client.post(ATTACHMENT_PATH, files={"file": ("f.bin", b"x" * oversized)})

    assert r.status_code == 413
    assert parsed == []


def test_a_legitimate_attachment_upload_still_passes(attachment_client):
    """The ceiling must clear a real max-size upload plus its envelope, or this hardening
    would 413 correct requests — the failure mode the global ceiling's own test guards."""
    body = b"x" * attachment_service.MAX_ATTACHMENT_BYTES
    r = attachment_client.post(ATTACHMENT_PATH, files={"file": ("f.bin", body)})

    assert r.status_code == 200
    assert r.json()["n"] == attachment_service.MAX_ATTACHMENT_BYTES


def test_a_body_exactly_at_the_ceiling_is_admitted(attachment_client):
    """The boundary is `>`, not `>=` — a request declaring exactly the limit must pass, or
    the envelope headroom is one byte short of what it advertises."""
    limit = main._request_limit_for(ATTACHMENT_PATH)
    r = attachment_client.post(
        ATTACHMENT_PATH,
        content=b"x" * limit,
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(limit)},
    )
    # It gets past the middleware; the route then rejects it as malformed multipart (422),
    # which is the point — anything but 413 proves admission happened.
    assert r.status_code != 413


def test_the_attachment_ceiling_clears_the_feature_limit():
    """The same rule as the global ceiling, applied to the per-route one."""
    limit = main._request_limit_for(ATTACHMENT_PATH)
    assert limit > attachment_service.MAX_ATTACHMENT_BYTES
    # ...and is genuinely tighter than the backstop it replaces, or it buys nothing.
    assert limit < main.MAX_REQUEST_BYTES


@pytest.mark.parametrize(
    "path",
    [
        "/api/crm/chatter/note/7/attachments/extra",  # deeper path: a different route
        "/api/crm/chatter/note/attachments",  # missing the id segment
        "/api/assistant/chat",  # a route with a legitimately larger body
        "/api/crm/importer",  # a longer name that must not be swallowed by /import
        "/",
    ],
)
def test_other_paths_keep_the_global_ceiling(path):
    """A near-miss must fall back to the backstop, never inherit a limit meant for
    something else."""
    assert main._request_limit_for(path) == main.MAX_REQUEST_BYTES


@pytest.mark.parametrize("note_id", ["abc", "7", "0", "-1", "1e5", "%20"])
def test_any_id_the_router_accepts_is_bounded(note_id):
    """The regression Codex caught: a hand-written `\\d+` gate is a bypass.

    `note_id: int` is FastAPI VALIDATION, not routing — Starlette compiles `{note_id}` to
    `[^/]+`, so `/note/abc/attachments` reaches the multipart parser and only afterwards
    returns 422. Under a `\\d+` pattern that path drew the 64 MB backstop and spooled an
    oversized body: the exact hole this table exists to close. Every id the ROUTER accepts
    must therefore be bounded, not just the ones the handler will go on to accept.
    """
    limit = main._request_limit_for(f"/api/crm/chatter/note/{note_id}/attachments")
    assert limit < main.MAX_REQUEST_BYTES


def test_a_root_path_prefix_does_not_lift_the_ceiling():
    """Behind a path-prefixing proxy the raw ASGI path carries a prefix the compiled
    patterns don't have, while the ROUTER matches the stripped path — so reading
    `request.url.path` would reach the endpoint with the ceiling silently back at 64 MB."""
    scope = {
        "type": "http",
        "path": "/cakecrm/api/crm/chatter/note/7/attachments",
        "root_path": "/cakecrm",
    }
    limit = main._request_limit_for(get_route_path(scope))
    assert limit < main.MAX_REQUEST_BYTES

    # ...and the un-stripped path is exactly what would have gone wrong, which is why the
    # middleware must not use it.
    assert main._request_limit_for(scope["path"]) == main.MAX_REQUEST_BYTES


def test_a_non_numeric_id_does_not_reach_the_parser(attachment_client, monkeypatch):
    """The same regression, proven end-to-end rather than by pattern inspection."""
    parsed = []
    real_parse = formparsers.MultiPartParser.parse

    async def counting_parse(self, *a, **k):
        parsed.append(1)
        return await real_parse(self, *a, **k)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", counting_parse)

    oversized = attachment_service.MAX_ATTACHMENT_BYTES + main.MULTIPART_ENVELOPE_BYTES + 1
    r = attachment_client.post(
        "/api/crm/chatter/note/abc/attachments", files={"file": ("f.bin", b"x" * oversized)}
    )

    assert r.status_code == 413
    assert parsed == []


def _concrete(path: str) -> str:
    """A route template with a real value substituted for each `{param}`.

    A `{name:path}` converter matches multiple segments, so it is given a two-segment
    value — a single-segment substitution would under-represent what that route accepts.
    """
    path = re.sub(r"\{[^}:]+:path\}", "a/b", path)
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_route_limit_names_a_real_mounted_route():
    """The one failure this table can suffer silently: an entry that matches nothing.

    Every request test here drives a stand-in app at a hand-written path, so renaming or
    re-prefixing a real route would turn its ceiling off while leaving them all green — a
    permanent pass that reads as coverage. Because the table is keyed by path TEMPLATE this
    is an exact string identity against the app's own mounted routes, which is strictly
    stronger than asking whether some concrete path happens to match.

    The emptiness assertion is not ceremony: a `for` loop over an empty table passes while
    checking nothing, so without it deleting the table would satisfy this guard.
    """
    assert main._ROUTE_REQUEST_LIMIT_SPECS, "the route-limit table is empty — nothing is bounded"

    mounted = {
        getattr(route, "path", "")
        for route in main.app.routes
        if "POST" in getattr(route, "methods", set())
    }
    for template, _limit in main._ROUTE_REQUEST_LIMIT_SPECS:
        assert template in mounted, (
            f"{template!r} is not a mounted POST route — the per-route ceiling it declares "
            f"is silently inactive. Update it to the route's current path."
        )


def test_each_ceiling_is_exactly_its_feature_cap_plus_the_envelope():
    """A wrong VALUE passes every structural guard above.

    A row set to 63 MB still names a real route and still sits under the backstop, while
    reopening almost all of the admission window; a row set below its feature cap would
    413 correct uploads. Both are caught only by pinning the arithmetic per row.
    """
    from branding.router import MAX_LOGO_BYTES
    from crm.router import MAX_UPLOAD_BYTES

    expected = {
        "/api/crm/chatter/note/{note_id}/attachments": attachment_service.MAX_ATTACHMENT_BYTES,
        "/api/crm/import": MAX_UPLOAD_BYTES,
        "/api/crm/smart-import/parse": MAX_UPLOAD_BYTES,
        "/api/branding/logo": MAX_LOGO_BYTES,
    }
    templates = [template for template, _ in main._ROUTE_REQUEST_LIMIT_SPECS]
    # Uniqueness first: matching is first-match-wins, so a duplicate row with a WRONG value
    # placed first would win at runtime while `dict()` below silently kept the correct one.
    assert len(templates) == len(set(templates)), f"duplicate template rows: {templates}"

    actual = dict(main._ROUTE_REQUEST_LIMIT_SPECS)
    assert actual.keys() == expected.keys(), "a row was added or removed without a value pinned"
    for template, feature_cap in expected.items():
        assert actual[template] == feature_cap + main.MULTIPART_ENVELOPE_BYTES, (
            f"{template} admits {actual[template]}, expected its feature cap "
            f"({feature_cap}) plus the envelope"
        )


#: Deliberately unbounded, with the reason. The assistant's legitimate maximum is
#: MAX_FILES x MAX_FILE_SIZE = 50 MB against the same 64 MB backstop, so the global ceiling
#: is already the tight one there and a row would only restate it.
_EXEMPT_UPLOAD_ROUTES = {"/api/assistant/chat/upload"}


def _upload_routes() -> list[str]:
    """Every mounted route that accepts a file, read from FastAPI's own dependency graph.

    `dependant.body_params` rather than the endpoint's `__annotations__`: the annotation
    string misses `list[UploadFile]`, a `bytes = File(...)` parameter, and anything supplied
    through a dependency — each of which is a real upload the guard would wave through.
    Body-bearing methods, not just POST, since a PUT/PATCH upload spools identically.
    """
    found = []
    for route in main.app.routes:
        methods = getattr(route, "methods", set()) or set()
        if not methods & {"POST", "PUT", "PATCH"}:
            continue
        dependant = getattr(route, "dependant", None)
        params = getattr(dependant, "body_params", []) if dependant else []
        takes_upload = any(
            "UploadFile" in str(getattr(p.field_info, "annotation", "")) for p in params
        )
        if takes_upload:
            found.append(getattr(route, "path", ""))
    return found


def test_the_upload_route_detector_sees_the_known_uploads():
    """The guard below is only as good as this detector, and a detector that quietly stops
    matching turns it into a permanent green. Pin what it must find."""
    found = set(_upload_routes())
    assert {
        "/api/crm/chatter/note/{note_id}/attachments",
        "/api/crm/import",
        "/api/crm/smart-import/parse",
        "/api/branding/logo",
        "/api/assistant/chat/upload",  # list[UploadFile] — missed by an annotation scan
    } <= found, f"the upload detector stopped seeing known upload routes; found {found}"
    # ...and it must not simply return everything.
    assert "/api/crm/deals/bulk-move" not in found


def test_every_upload_route_is_bounded_below_the_backstop():
    """The table's real contract, stated as a property rather than a list.

    An upload route left out of the table silently admits 64 MB — exactly the bug #127
    fixed — so a NEW upload route must not be able to reintroduce it unnoticed.
    """
    unbounded = [
        path
        for path in _upload_routes()
        if path not in _EXEMPT_UPLOAD_ROUTES
        and main._request_limit_for(_concrete(path)) >= main.MAX_REQUEST_BYTES
    ]

    assert not unbounded, (
        f"upload route(s) with no per-route ceiling, so they admit {main.MAX_REQUEST_BYTES} "
        f"bytes before their own cap can refuse: {unbounded}. Add a row to "
        f"main._ROUTE_REQUEST_LIMIT_SPECS, or add the path to _EXEMPT_UPLOAD_ROUTES "
        f"with a reason."
    )


def test_a_trailing_slash_does_not_reach_the_parser(attachment_client, monkeypatch):
    """Two reviewers disagreed about whether `redirect_slashes` opens a hole here, so the
    answer is pinned rather than remembered.

    The compiled patterns are end-anchored and carry no optional slash, so
    `/note/7/attachments/` matches no row. It also reaches no endpoint: the router answers
    with a 307 before any body is parsed, and the redirect target is the canonical path,
    which IS bounded. A redirect is not a bypass.
    """
    parsed = []
    real_parse = formparsers.MultiPartParser.parse

    async def counting_parse(self, *a, **k):
        parsed.append(1)
        return await real_parse(self, *a, **k)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", counting_parse)

    oversized = attachment_service.MAX_ATTACHMENT_BYTES + main.MULTIPART_ENVELOPE_BYTES + 1
    body = {"file": ("f.bin", b"x" * oversized)}

    r = attachment_client.post(ATTACHMENT_PATH + "/", files=body, follow_redirects=False)
    assert r.status_code == 307
    assert parsed == []

    # Followed, it lands on the bounded path and is refused there — still unparsed.
    r = attachment_client.post(ATTACHMENT_PATH + "/", files=body, follow_redirects=True)
    assert r.status_code == 413
    assert parsed == []
