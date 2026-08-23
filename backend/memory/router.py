"""Memory REST API (issue #72 Phase 2) — the Memory page's facts half.

Facts had no REST surface at all before this: they could only be reached by asking the
assistant. Keyless — facts are plain rows and the FTS is pure SQL.

  GET    /api/memory/facts            — list/filter (?subject=, ?memory_type=, ?include_*)
  GET    /api/memory/facts/search?q=  — full-text search
  POST   /api/memory/facts/{id}/invalidate — end a fact's validity window (the soft path)
  DELETE /api/memory/facts/{id}       — hard-delete (the human's purge path)
  GET    /api/memory/dreaming/runs    — what the nightly cycle archived, for transparency

Two removal verbs on purpose. ``invalidate`` is what the assistant uses and what keeps
point-in-time queries honest; ``DELETE`` is for a human who needs something actually
gone. Only the human gets the second one.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from core.auth import get_current_user
from core.postgres import pg_fetchall
from memory import service

logger = logging.getLogger(__name__)
router = APIRouter()


class InvalidateRequest(BaseModel):
    valid_to: str | None = None


@router.get("/facts")
async def list_facts(
    subject: str | None = Query(None),
    memory_type: str | None = Query(None),
    include_expired: bool = Query(False),
    include_archived: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    _user: dict = Depends(get_current_user),
):
    facts = await run_in_threadpool(
        service.query_facts,
        subject, None, None, memory_type, include_expired, include_archived, limit,
        False,   # track_retrieval — browsing the UI is not the assistant "using" a fact,
                 # and counting it would keep dormant facts alive forever (issue #5's rule).
    )
    return {"facts": facts, "count": len(facts)}


@router.get("/facts/search")
async def search_facts(
    q: str = Query(..., min_length=1),
    memory_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    _user: dict = Depends(get_current_user),
):
    facts = await run_in_threadpool(
        service.search_facts, q, memory_type, None, None, limit, False,
    )
    return {"facts": facts, "count": len(facts)}


@router.post("/facts/{fact_id}/invalidate")
async def invalidate_fact(
    fact_id: int, req: InvalidateRequest, _user: dict = Depends(get_current_user),
):
    result = await run_in_threadpool(service.invalidate_fact, fact_id, req.valid_to)
    if result.get("error"):
        raise HTTPException(
            status_code=404 if result.get("not_found") else 400, detail=result["error"],
        )
    return result


@router.delete("/facts/{fact_id}")
async def delete_fact(fact_id: int, _user: dict = Depends(get_current_user)):
    deleted = await run_in_threadpool(service.delete_fact, fact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No fact with id {fact_id}.")
    return {"id": fact_id, "deleted": True}


@router.get("/dreaming/runs")
async def dreaming_runs(
    limit: int = Query(20, ge=1, le=100), _user: dict = Depends(get_current_user),
):
    """Recent dreaming cycles — how many facts were scored and archived, and when.

    Read directly rather than through a service call: ``dreaming`` exposes no list API,
    and inventing one for a single read-only table would be more code than the query.

    Columns are listed explicitly, NOT ``SELECT *``. ``dreaming_runs.details`` embeds the
    top-scoring facts' ids and subjects, so returning it would let a fact the user
    hard-deleted for being wrong or private stay readable in an audit row — which would
    quietly contradict what DELETE /facts/{id} promises.
    """
    runs = await run_in_threadpool(
        pg_fetchall,
        "SELECT id, started_at, finished_at, status, facts_scored, facts_archived, "
        "       duration_ms, error "
        "FROM dreaming_runs ORDER BY id DESC LIMIT %s",
        (limit,),
    )
    return {"runs": runs, "count": len(runs)}
