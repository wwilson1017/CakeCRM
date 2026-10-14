"""Reminders — REST API (issue #283), mounted at ``/api/crm/reminders``.

Every route needs a signed-in seat (``get_current_user``) and none is admin-only:
ownership is not access control, so any member may read, edit or void any reminder.
The owner and author of a new reminder come from the token, never the body.

Handlers are plain ``def`` so FastAPI runs the blocking psycopg2 work in its threadpool.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.auth import get_current_user
from crm.gtd_common import NotFoundError, ValidationError
from reminders import service
from reminders.service import MAX_NOTE_CHARS, MAX_PEOPLE, MAX_VOID_REASON_CHARS

router = APIRouter()


class ReminderCreate(BaseModel):
    note: str = Field(max_length=MAX_NOTE_CHARS)
    due_date: str = Field(max_length=10)
    due_time: str | None = Field(default=None, max_length=5)
    people: list[int] = Field(default_factory=list, max_length=MAX_PEOPLE)
    repeat: str | None = Field(default=None, max_length=10)


class ReminderUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    due_date: str | None = Field(default=None, max_length=10)
    due_time: str | None = Field(default=None, max_length=5)
    people: list[int] | None = Field(default=None, max_length=MAX_PEOPLE)
    repeat: str | None = Field(default=None, max_length=10)


class ReminderVoid(BaseModel):
    reason: str = Field(max_length=MAX_VOID_REASON_CHARS)


class RemindAgain(BaseModel):
    due_date: str = Field(max_length=10)
    due_time: str | None = Field(default=None, max_length=5)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("")
def list_reminders(
    scope: Literal["mine", "all"] = "all",
    status: str | None = Query(None, max_length=10),
    due_from: str | None = Query(None, max_length=10),
    due_to: str | None = Query(None, max_length=10),
    limit: int = Query(200, ge=1, le=1000),
    sort: str | None = Query(None, max_length=10),
    after_id: int | None = Query(None, ge=0, le=2**63 - 1),
    user: dict = Depends(get_current_user),
):
    rows = _call(service.list_reminders, mine_user_id=user["id"] if scope == "mine" else None,
                 status=status, due_from=due_from, due_to=due_to, limit=limit, sort=sort,
                 after_id=after_id)
    return {"reminders": rows}


@router.post("")
def create_reminder(body: ReminderCreate, user: dict = Depends(get_current_user)):
    return _call(service.create_reminder, **body.model_dump(),
                 owner_id=user["id"], created_by=user["id"])


@router.get("/{reminder_id}")
def get_reminder(reminder_id: int, user: dict = Depends(get_current_user)):
    row = service.get_reminder(reminder_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.put("/{reminder_id}")
def update_reminder(reminder_id: int, body: ReminderUpdate,
                    user: dict = Depends(get_current_user)):
    return _call(service.update_reminder, reminder_id, body.model_dump(exclude_unset=True))


@router.post("/{reminder_id}/void")
def void_reminder(reminder_id: int, body: ReminderVoid, user: dict = Depends(get_current_user)):
    return _call(service.void_reminder, reminder_id, body.reason)


@router.post("/{reminder_id}/remind-again")
def remind_again(reminder_id: int, body: RemindAgain, user: dict = Depends(get_current_user)):
    return _call(service.remind_again, reminder_id, due_date=body.due_date,
                 due_time=body.due_time, owner_id=user["id"], created_by=user["id"])
