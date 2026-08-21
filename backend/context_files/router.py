"""Context-files REST API (issue #72 Phase 2) — the Memory page's file half.

Closes the gap that made this issue worth filing: until now the ONLY way to see or
change what the assistant knows was to ask the assistant. Fully keyless — files are
plain rows, so browsing and editing them never touches a provider.

  GET    /api/context-files                    — list metadata (?kind=, ?include_archived=)
  GET    /api/context-files/search?q=          — full-text search
  GET    /api/context-files/file/{filename}    — one file with its body
  PUT    /api/context-files/file/{filename}    — create/overwrite (optimistic concurrency)
  DELETE /api/context-files/file/{filename}    — delete (protected files refused)

Filenames contain '/' (``topics/x.md``), which a plain ``{filename}`` path parameter
cannot match — hence the ``/file/`` prefix plus a ``:path`` converter. The prefix also
keeps ``/search`` from ever being parsed as a filename.

Writes here are ``written_by='user'``: this router is behind ``get_current_user``, so
anything arriving through it is the human editing their assistant's knowledge, which is
what makes "who last changed soul.md" answerable on the Memory page.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from context_files import service
from core.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

_CODE_TO_STATUS = {
    "bad_request": 400,
    "conflict": 409,
    "forbidden": 403,
    "too_large": 413,
}


def _http(exc: service.ContextFileError) -> HTTPException:
    """Map a service error to a status via its structured ``code``, never its wording —
    so rephrasing a message can't silently change the HTTP contract."""
    return HTTPException(status_code=_CODE_TO_STATUS.get(exc.code, 400), detail=exc.message)


class ContextFileWriteRequest(BaseModel):
    content: str
    # The updated_at the editor loaded. When present it must still match, else 409 —
    # otherwise a save silently discards whatever the assistant wrote in between.
    expected_updated_at: str | None = None


@router.get("")
async def list_context_files(
    kind: str | None = Query(None),
    include_archived: bool = Query(False),
    _user: dict = Depends(get_current_user),
):
    files = await run_in_threadpool(service.list_files, kind, include_archived)
    return {"files": files, "count": len(files)}


@router.get("/search")
async def search_context_files(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=service.SEARCH_LIMIT_CAP),
    _user: dict = Depends(get_current_user),
):
    results = await run_in_threadpool(service.search_files, q, limit)
    return {"results": results, "count": len(results)}


@router.get("/file/{filename:path}")
async def get_context_file(filename: str, _user: dict = Depends(get_current_user)):
    try:
        row = await run_in_threadpool(service.read_file, filename)
    except service.ContextFileError as exc:
        raise _http(exc) from None
    if not row:
        raise HTTPException(status_code=404, detail=f"No context file named '{filename}'.")
    return row


@router.put("/file/{filename:path}")
async def put_context_file(
    filename: str,
    req: ContextFileWriteRequest,
    _user: dict = Depends(get_current_user),
):
    try:
        return await run_in_threadpool(
            service.write_file, filename, req.content, "user", req.expected_updated_at,
        )
    except service.ContextFileError as exc:
        raise _http(exc) from None


@router.delete("/file/{filename:path}")
async def delete_context_file(filename: str, _user: dict = Depends(get_current_user)):
    try:
        deleted = await run_in_threadpool(service.delete_file, filename)
    except service.ContextFileError as exc:
        raise _http(exc) from None
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No context file named '{filename}'.")
    return {"filename": filename, "deleted": True}
