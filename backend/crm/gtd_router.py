"""Todo-GTD — REST API.

The CRUD lives in ``build_router(guard)`` so it can be mounted TWICE from one set of
endpoint definitions:

  * authenticated at ``/api/crm/gtd`` (this module's ``router``), and
  * token-guarded at ``/api/todo-web[/{token}]`` for the no-login web app
    (``crm/todo_web.py``).

``guard`` is a FastAPI dependency that either authorizes the request or raises. It
returns nothing — CakeCRM is single-user, so unlike the blueprint there is no owner
identity to thread through; the guard's only job is to decide whether the caller may
proceed at all.

Handlers are plain ``def`` so FastAPI runs the blocking psycopg2 work in its
threadpool.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.auth import get_current_user
from crm import gtd_service
from crm.gtd_common import (
    MAX_BULK_IDS,
    MAX_SHORT_CHARS,
    MAX_TEXT_CHARS,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)


# ── Request models ────────────────────────────────────────────────────────────
# Field caps mirror the service-layer limits so an oversized body is rejected at the
# model boundary. All-optional *Update models, so the router's
# model_dump(exclude_unset=True) means an omitted field is untouched while an
# explicit null/empty clears it.

class TodoCreate(BaseModel):
    title: str = Field(max_length=MAX_TEXT_CHARS)
    notes: str = Field(default="", max_length=MAX_TEXT_CHARS)
    project: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    project_id: int | None = None
    context: str = Field(default="", max_length=MAX_SHORT_CHARS)
    tags: list[str] = Field(default_factory=list, max_length=50)
    status: str = Field(default="inbox", max_length=MAX_SHORT_CHARS)
    star: bool = False
    due_date: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    repeat: str = Field(default="", max_length=MAX_SHORT_CHARS)
    auto_star_on_due: bool = False
    contact_id: int | None = None
    deal_id: int | None = None


class TodoUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    project: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    project_id: int | None = None
    context: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    tags: list[str] | None = Field(default=None, max_length=50)
    status: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    star: bool | None = None
    due_date: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    repeat: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    auto_star_on_due: bool | None = None


class TodoBulkUpdate(BaseModel):
    ids: list[int] = Field(max_length=MAX_BULK_IDS)
    fields: dict


class ProjectCreate(BaseModel):
    name: str = Field(max_length=MAX_SHORT_CHARS)
    notes: str = Field(default="", max_length=MAX_TEXT_CHARS)
    status: str = Field(default="active", max_length=MAX_SHORT_CHARS)
    purpose: str = Field(default="", max_length=MAX_SHORT_CHARS)
    outcome: str = Field(default="", max_length=MAX_SHORT_CHARS)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    status: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    purpose: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)
    outcome: str | None = Field(default=None, max_length=MAX_SHORT_CHARS)


def _call(fn, *args, **kwargs):
    """Run a service call, mapping domain errors onto their HTTP shape."""
    try:
        return fn(*args, **kwargs)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Not found")


def build_router(guard) -> APIRouter:
    """One copy of the todo/project/filter endpoints, mounted behind `guard`.

    ⚠️ Every route added here is reachable from the PUBLIC web-app mount as well as
    the authenticated one. A route that must never be public belongs on the
    authenticated `router` below instead.
    """
    r = APIRouter(dependencies=[Depends(guard)])

    # ── Todos ─────────────────────────────────────────────────────────────────

    @r.get("/todos")
    def list_todos(
        status: str | None = None,
        project: str | None = None,
        context: str | None = None,
        tag: str | None = None,
        starred: bool | None = None,
        due_before: str | None = None,
        due_after: str | None = None,
        search: str | None = None,
        limit: int = Query(default=200, ge=1, le=500),
    ):
        todos = _call(
            gtd_service.list_todos,
            status=status, project=project, context=context, tag=tag, starred=starred,
            due_before=due_before, due_after=due_after, search=search, limit=limit,
        )
        return {"todos": todos}

    @r.get("/today")
    def today():
        return {"todos": _call(gtd_service.today_view)}

    @r.post("/todos")
    def create_todo(body: TodoCreate):
        return _call(
            gtd_service.create_todo, body.title,
            notes=body.notes, project=body.project, project_id=body.project_id,
            context=body.context, tags=body.tags, status=body.status, star=body.star,
            due_date=body.due_date, repeat=body.repeat,
            auto_star_on_due=body.auto_star_on_due,
            contact_id=body.contact_id, deal_id=body.deal_id, source="ui",
        )

    @r.get("/todos/{todo_id}")
    def get_todo(todo_id: int):
        todo = gtd_service.get_todo(todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="Todo not found")
        return todo

    @r.put("/todos/{todo_id}")
    def update_todo(todo_id: int, body: TodoUpdate):
        # exclude_unset: only fields the client actually sent are updated, so an
        # explicit null/empty clears a value without clobbering the rest.
        todo = _call(gtd_service.update_todo, todo_id, body.model_dump(exclude_unset=True))
        if not todo:
            raise HTTPException(status_code=404, detail="Todo not found")
        return todo

    @r.delete("/todos/{todo_id}")
    def delete_todo(todo_id: int):
        if not _call(gtd_service.delete_todo, todo_id):
            raise HTTPException(status_code=404, detail="Todo not found")
        return {"ok": True}

    @r.post("/todos/bulk")
    def bulk_update(body: TodoBulkUpdate):
        return _call(gtd_service.bulk_update, body.ids, body.fields)

    # ── Projects ──────────────────────────────────────────────────────────────

    @r.get("/projects")
    def list_projects(status: str | None = None):
        return {"projects": _call(gtd_service.list_projects, status=status)}

    @r.post("/projects")
    def create_project(body: ProjectCreate):
        return _call(
            gtd_service.create_project, body.name, notes=body.notes, status=body.status,
            purpose=body.purpose, outcome=body.outcome,
        )

    @r.put("/projects/{project_id}")
    def update_project(project_id: int, body: ProjectUpdate):
        project = _call(
            gtd_service.update_project, project_id, body.model_dump(exclude_unset=True)
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @r.delete("/projects/{project_id}")
    def delete_project(project_id: int):
        if not gtd_service.delete_project(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        return {"ok": True}

    # ── Filters ───────────────────────────────────────────────────────────────

    @r.get("/filters")
    def get_filters():
        return gtd_service.get_filters()

    return r


# The authenticated mount. `main.py` includes this at /api/crm/gtd.
router = build_router(get_current_user)
