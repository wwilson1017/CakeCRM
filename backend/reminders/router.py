"""Reminders REST API — full CRUD for the frontend (issue #6).

All endpoints require JWT auth. Chatty's reminder REST was read-only (mutation
happened only via the agent tools); acceptance here requires "reminders CRUD from
the UI", so create/update/cancel/delete are net-new. Reminders are keyless — no
``ai_ready`` gate — a reminder that FIRES may involve AI, but managing them never
does.

  GET    /api/reminders            — list (?status=pending|fired|cancelled|all, ?limit=)
  POST   /api/reminders            — create (message, due_at, context?, recurrence?)
  PATCH  /api/reminders/:id        — edit a pending reminder
  POST   /api/reminders/:id/cancel — cancel a pending reminder (stops a series)
  DELETE /api/reminders/:id        — delete a fired/cancelled reminder
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from core.auth import get_current_user
from reminders import recurrence, service

logger = logging.getLogger(__name__)
router = APIRouter()


class ReminderCreateRequest(BaseModel):
    message: str
    due_at: str
    context: str = ""
    recurrence: str = ""


class ReminderUpdateRequest(BaseModel):
    message: str | None = None
    due_at: str | None = None
    context: str | None = None
    recurrence: str | None = None  # "" clears the rule; None leaves it unchanged


def _parse_recurrence_or_400(raw: str) -> dict | None:
    rule = recurrence.parse_recurrence(raw)
    if raw and raw.strip() and rule is None:
        raise HTTPException(status_code=400, detail=f"Unrecognized recurrence: {raw!r}")
    return rule


_CODE_TO_STATUS = {"not_found": 404, "conflict": 409, "bad_request": 400}


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
async def list_reminders(
    status: str = Query("pending"),
    limit: int = Query(50, ge=1, le=200),
    _user: dict = Depends(get_current_user),
):
    reminders = await run_in_threadpool(service.list_reminders, status, limit)
    return {"reminders": reminders, "count": len(reminders)}


@router.post("")
async def create_reminder(req: ReminderCreateRequest, _user: dict = Depends(get_current_user)):
    rule = _parse_recurrence_or_400(req.recurrence)
    result = await run_in_threadpool(
        service.create_reminder, req.message, req.due_at, req.context or None, rule,
    )
    return _raise_for_error(result)


@router.patch("/{reminder_id}")
async def update_reminder(reminder_id: str, req: ReminderUpdateRequest,
                         _user: dict = Depends(get_current_user)):
    kwargs: dict = {}
    if req.message is not None:
        kwargs["message"] = req.message
    if req.due_at is not None:
        kwargs["due_at"] = req.due_at
    if req.context is not None:
        kwargs["context"] = req.context
    if req.recurrence is not None:
        # "" → clear; non-empty → parse (400 on bad).
        kwargs["recurrence_rule"] = _parse_recurrence_or_400(req.recurrence)
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = await run_in_threadpool(lambda: service.update_reminder(reminder_id, **kwargs))
    return _raise_for_error(result)


@router.post("/{reminder_id}/cancel")
async def cancel_reminder(reminder_id: str, _user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(service.cancel_reminder, reminder_id)
    return _raise_for_error(result)


@router.delete("/{reminder_id}")
async def delete_reminder(reminder_id: str, _user: dict = Depends(get_current_user)):
    result = await run_in_threadpool(service.delete_reminder, reminder_id)
    return _raise_for_error(result)
