"""Telegram API — mounted at ``/api/telegram`` (see main.py).

Two kinds of route, gated differently since #193 (Phase B / B4):

* **Install-wide bot config — admin only.** ``connect``/``disconnect`` own the shared bot
  token and the single poller. They are async: they must ``stop()`` the poll task, mutate
  config, then ``start()`` a fresh one — so an in-flight ``getUpdates`` against the OLD
  bot can't process a stale update or skew the NEW bot's reset offset — and they offload
  their blocking DB/getMe calls with ``asyncio.to_thread``.
* **My own link — any seat.** ``link-code`` mints/rotates the code that binds MY chat, and
  ``unlink`` releases it. No admin gate, and no redaction: a per-seat code claims the
  caller's own row and nothing else. (Before #193 one code claimed the single install-wide
  binding, so handing it to a member would have made the admin gate on connect/disconnect
  pointless — hence the old member redaction, now deleted along with its premise.)

``POST /api/telegram/link-code`` replaces the admin-only
``POST /api/telegram/link-code/regenerate``; the bidirectional ``ADMIN_ONLY`` pin in
``tests/test_route_authz.py`` is edited to match.

The bot token is never returned in any response.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import get_current_user, require_admin

from . import client, poller, store

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectRequest(BaseModel):
    bot_token: str


def _status_payload(user_id: int) -> dict:
    """Install status (the bot) + MY link status, in one flat object.

    ``connected``/``bot_username`` describe the install; ``linked``/``linked_name``/
    ``link_code``/``link_url`` describe the caller's own link and nobody else's.
    ``link_code`` is empty until this seat mints one — ``POST /link-code`` does that, so
    a GET never has a side effect.
    """
    s = store.get_settings()
    link = store.get_link(user_id) or {}
    username = s.get("bot_username") or ""
    code = link.get("link_code") or ""
    link_url = f"https://t.me/{username}?start={code}" if username and code else ""
    return {
        "connected": bool(s.get("connected")),
        "bot_username": username,
        "linked": bool(link.get("chat_id")),
        "linked_name": link.get("telegram_name") or "",
        "link_code": code,
        "link_url": link_url,
    }


@router.get("/status")
def get_status(user=Depends(get_current_user)):
    return _status_payload(user["id"])


@router.post("/connect")
async def connect(body: ConnectRequest, user=Depends(require_admin)):
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
    try:
        # A previously-used bot may have a webhook set, which blocks getUpdates polling.
        # drop_pending_updates=True: at connect time, discard the new bot's pre-connect
        # backlog so up-to-24h of old queued messages aren't replayed to their senders.
        await asyncio.to_thread(client.delete_webhook, token, True)
        # Also clears every seat's chat binding: a chat id is per (user, bot) pair, so a
        # new bot invalidates them all and each seat re-links with its own fresh code.
        await asyncio.to_thread(store.connect, token, username)
    finally:
        # ALWAYS restart the poller — even if the mutation raised — so a DB blip during
        # connect can't leave polling permanently dead (store.connect is atomic, so on
        # failure the prior config simply resumes).
        poller.start()
    logger.info("telegram bot connected (@%s)", username)
    return await asyncio.to_thread(_status_payload, user["id"])


@router.post("/disconnect")
async def disconnect(user=Depends(require_admin)):
    await poller.stop()
    try:
        await asyncio.to_thread(store.disconnect)
    finally:
        poller.start()  # always resume the task (it idles until a token is connected)
    logger.info("telegram bot disconnected")
    return await asyncio.to_thread(_status_payload, user["id"])


@router.post("/link-code")
def mint_link_code(user=Depends(get_current_user)):
    """Mint (or rotate) MY single-use link code, releasing any device I had bound.

    Member-legal by design: the code claims the caller's own row, so there is nothing
    here an admin gate would protect. Rotating is how you move your assistant to a new
    phone, which is a personal action, not an install-configuration one.
    """
    store.mint_link_code(user["id"])
    return _status_payload(user["id"])


@router.post("/unlink")
def unlink(user=Depends(get_current_user)):
    """Release MY chat binding. Self only — there is no route to unlink someone else.

    An admin who needs to cut every link disconnects the bot, which clears them all.
    """
    store.unlink(user["id"])
    return _status_payload(user["id"])
