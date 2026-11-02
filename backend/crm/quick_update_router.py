"""Authenticated Quick update endpoints; any member may update any live deal."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from core.auth import get_current_user
from crm import quick_update

router = APIRouter()


class PreviewRequest(BaseModel):
    sentence: str = Field(..., max_length=quick_update.SENTENCE_MAX)

    @model_validator(mode="after")
    def _sentence(self):
        self.sentence = self.sentence.strip()
        if not self.sentence:
            raise ValueError("Tell Baker what happened")
        return self


@router.get("/deals")
def search_deals(q: str = Query("", max_length=200), user=Depends(get_current_user)):
    return {"deals": quick_update.search_deals(q)}


@router.post("/deals/{deal_id}")
def update_deal(deal_id: int, body: quick_update.QuickUpdateBody, user=Depends(get_current_user)):
    try:
        return quick_update.apply(deal_id, body, actor_id=user["id"])
    except quick_update.DealNotFound:
        raise HTTPException(404, "Deal not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/deals/{deal_id}/preview")
async def preview(deal_id: int, body: PreviewRequest, user=Depends(get_current_user)):
    try:
        return await quick_update.preview(deal_id, body.sentence)
    except quick_update.DealNotFound:
        raise HTTPException(404, "Deal not found")
    except quick_update.BakerUnavailable:
        raise HTTPException(503, quick_update.UNAVAILABLE)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
