# Upload admission is a two-layer contract (`backend/main.py`)

**Since #127 (PR #134),** request-size admission is enforced in middleware BEFORE the body is
parsed, per route:

- `_ROUTE_REQUEST_LIMIT_SPECS` — hand-edited, keyed on the route TEMPLATE (e.g.
  `/api/crm/chatter/note/{note_id}/attachments`), one row per upload route.
- `_ROUTE_REQUEST_LIMITS` — compiled from the specs via Starlette's `compile_path`, matched
  against `get_route_path(scope)` (never `request.url.path` — that breaks under `root_path`).

## The contract

Adding an upload route WITHOUT a spec row silently admits the 64MB global backstop —
6.4× the 10MB attachment cap. The guard test
`test_every_upload_route_is_bounded_below_the_backstop` fails CI in that case: it walks the
real app's routes with an upload detector and asserts every hit has a row below the backstop.

## Detector rules (learned the hard way)

- Read `get_flat_dependant(route.dependant)` and check `isinstance(field_info, params.File)`.
  `route.dependant.body_params` is NOT flattened (misses `Depends`-supplied files), and an
  annotation-string match misses `data: bytes = File(...)`.
- Never hand-write the path pattern. `{note_id}` with `note_id: int` compiles to `[^/]+` and a
  StringConvertor — the `int` is Pydantic validation applied AFTER the body is parsed. A
  hand-written `\d+` let `/note/abc/attachments` spool a 10.5MB body before its 422 (#127's
  review catch). A guard in front of a router must be at least as wide as the router.
- Known blind spot on the pinned FastAPI: `Annotated[SomeModel, Form()]` where the BaseModel
  holds an `UploadFile` field carries `params.Form`, not `params.File`. If that idiom ever
  enters the codebase, widen the marker to `params.Form` (MRO `File → Form → Body`; verified a
  no-op on today's routes) and rework the test controls. `await request.form()` on a raw
  `Request` is undetectable by any dependency-graph scan — don't write routes that way.

## Repo gotcha

The login route is `POST /api/login`, not `/api/auth/login` (that prefix hosts only 2FA +
change-password).
