"""Saved views REST API (issue #181).

Team-visible named snapshots of a collection surface's filters, search, sort and view mode.
Keyless — no ``ai_ready`` gate; saving a view never involves AI.

  GET    /api/saved-views?surface=crm_pipeline — every view on a surface
  POST   /api/saved-views                      — save the current state under a name
  PUT    /api/saved-views/:id                  — rename and/or overwrite (creator or admin)
  DELETE /api/saved-views/:id                  — delete (creator or admin)

Every route depends on ``get_current_user`` and NONE depends on ``require_admin``: the
creator-or-admin rule is per ROW, not per route, so it is decided in ``saved_views.service``
(inside the write transaction) and this module only maps its ``code`` to a status. Adding
these routes to ``test_route_authz.ADMIN_ONLY`` would therefore be wrong.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from core.auth import get_current_user
from saved_views import service

logger = logging.getLogger(__name__)
router = APIRouter()


class SavedViewCreateRequest(BaseModel):
    surface: str
    name: str
    version: int
    payload: dict


class SavedViewUpdateRequest(BaseModel):
    name: str | None = None
    version: int | None = None
    payload: dict | None = None


_CODE_TO_STATUS = {"not_found": 404, "conflict": 409, "bad_request": 400, "forbidden": 403}


def _raise_for_error(result: dict) -> dict:
    """Map a service ``{"error": ..., "code": ...}`` to the right HTTP status.

    Keys off the structured ``code`` (not the message text), so rewording a service
    error can never silently flip the HTTP status.
    """
    err = result.get("error")
    if not err:
        return result
    status = _CODE_TO_STATUS.get(result.get("code", "bad_request"), 400)
    raise HTTPException(status_code=status, detail=err)


@router.get("")
async def list_views(
    surface: str = Query(..., min_length=1, max_length=64),
    user: dict = Depends(get_current_user),
):
    result = await run_in_threadpool(service.list_views, surface, user)
    if isinstance(result, dict):
        _raise_for_error(result)
    return {"views": result}


@router.post("")
async def create_view(req: SavedViewCreateRequest, user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(
        service.create_view, req.surface, req.name, req.version, req.payload, user,
    )
    return _raise_for_error(result)


@router.put("/{view_id}")
async def update_view(view_id: int, req: SavedViewUpdateRequest,
                      user: dict = Depends(get_current_user)):
    kwargs: dict = {}
    if req.name is not None:
        kwargs["name"] = req.name
    if req.payload is not None:
        kwargs["payload"] = req.payload
    if req.version is not None:
        kwargs["version"] = req.version
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = await run_in_threadpool(lambda: service.update_view(view_id, user, **kwargs))
    return _raise_for_error(result)


@router.delete("/{view_id}")
async def delete_view(view_id: int, user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(service.delete_view, view_id, user)
    return _raise_for_error(result)
