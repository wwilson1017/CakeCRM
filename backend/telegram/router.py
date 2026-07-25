"""Telegram admin API — mounted at ``/api/telegram`` (see main.py).

JWT-protected management for the single-user integration: connect/validate a bot token,
read connection + link status, regenerate the link code, and disconnect. Handlers are
sync (FastAPI runs them in its threadpool, so the blocking DB/getMe calls never touch
the event loop). The bot token is never returned in any response.

The running poll task re-reads ``telegram_settings`` each iteration, so a connect or
disconnect is picked up automatically within a few seconds — no task restart here.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import get_current_user

from . import client, poller, store

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectRequest(BaseModel):
    bot_token: str


def _status_payload() -> dict:
    """The connection/link status the frontend renders. Never includes the token."""
    s = store.get_settings()
    username = s.get("bot_username") or ""
    code = s.get("link_code") or ""
    # link_code/link_url are returned only to the authenticated owner (JWT-gated), who
    # needs them to link their phone — never exposed to Telegram senders.
    link_url = f"https://t.me/{username}?start={code}" if username and code else ""
    return {
        "connected": bool(s.get("connected")),
        "bot_username": username,
        "linked": bool(s.get("linked")),
        "linked_name": s.get("linked_name") or "",
        "link_code": code,
        "link_url": link_url,
    }


@router.get("/status")
def get_status(user=Depends(get_current_user)):
    return _status_payload()


@router.post("/connect")
async def connect(body: ConnectRequest, user=Depends(get_current_user)):
    token = (body.bot_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Bot token is required.")
    info = await asyncio.to_thread(client.validate_token, token)
    if not info:
        raise HTTPException(
            status_code=400,
            detail="That bot token is invalid. Create one with @BotFather and try again.",
        )
    username = info.get("username") or ""
    # Stop the poll task BEFORE swapping config, so an in-flight getUpdates against the
    # OLD bot can't process a stale update or skew the NEW bot's freshly-reset offset.
    await poller.stop()
    # A previously-used bot may have a webhook set, which blocks getUpdates polling.
    await asyncio.to_thread(client.delete_webhook, token, False)
    await asyncio.to_thread(store.connect, token, username)
    poller.start()  # fresh task picks up the new bot immediately
    logger.info("telegram bot connected (@%s)", username)
    return await asyncio.to_thread(_status_payload)


@router.post("/disconnect")
async def disconnect(user=Depends(get_current_user)):
    await poller.stop()
    await asyncio.to_thread(store.disconnect)
    poller.start()  # task resumes but idles until a token is connected again
    logger.info("telegram bot disconnected")
    return await asyncio.to_thread(_status_payload)


@router.post("/link-code/regenerate")
def regenerate_link_code(user=Depends(get_current_user)):
    store.regenerate_link_code()
    return _status_payload()
