"""Request-only raw audio transcription for chat and chatter (#338)."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth import get_current_user, require_admin
from core.ratelimit import IPRateLimiter
from providers import get_ai_provider, get_transcriber
from voice import service

logger = logging.getLogger(__name__)
router = APIRouter()
# One worker; keyed by authenticated seat, not IP.
_limiter = IPRateLimiter(window=60, max_hits=20)


def _require_transcriber():
    provider = get_transcriber()
    if provider is None:
        raise HTTPException(400, "No AI provider that can transcribe is configured.")
    return provider


@router.post("/transcribe")
async def transcribe(request: Request, tidy: bool = False, user=Depends(get_current_user)):
    # Raw body: no multipart parser or disk spool before these refusals.
    transcriber = await asyncio.to_thread(_require_transcriber)
    if not _limiter.allow(str(user["id"])):
        raise HTTPException(429, "Too many recordings — wait a moment and try again.")
    mime = service.normalize_audio_type(request.headers.get("content-type"))
    if mime is None:
        raise HTTPException(400, "Unsupported audio type.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > service.MAX_AUDIO_BYTES:
            raise HTTPException(413, "That recording is too long to transcribe.")
        chunks.append(chunk)
    data = b"".join(chunks)
    del chunks
    if not data:
        raise HTTPException(400, "The recording was empty.")
    try:
        text = await transcriber.transcribe(data, mime, service.audio_filename(mime))
    except Exception as exc:
        logger.warning("voice: transcription failed (%s)", type(exc).__name__)
        raise HTTPException(502, "Transcription failed — try again.") from None
    finally:
        del data
    tidied = False
    if tidy and text:
        try:
            light = await asyncio.to_thread(lambda: get_ai_provider(agent_model_tier="light"))
            if light is not None:
                text, tidied = await service.tidy_transcript(text, light)
        except Exception:
            logger.warning("voice: tidy unavailable; returning the raw transcript")
    return {"text": text, "tidied": tidied}


def _voice_ready() -> bool:
    try:
        return get_ai_provider() is not None and get_transcriber() is not None
    except Exception:
        return False


class LivePreviewBody(BaseModel):
    enabled: bool


@router.get("/live-preview")
def live_preview(user=Depends(require_admin)):
    return {"enabled": service.get_live_preview(), "voice_ready": _voice_ready()}


@router.post("/live-preview")
def update_live_preview(body: LivePreviewBody, user=Depends(require_admin)):
    if body.enabled and not _voice_ready():
        raise HTTPException(400, "No AI provider that can transcribe is configured.")
    service.set_live_preview(body.enabled)
    return {"enabled": body.enabled}
