"""CakeCRM — assistant REST/SSE API (mounted at /api/assistant).

All endpoints require JWT auth (Depends(get_current_user)). The assistant degrades
off ``ai_ready``: when no provider is configured the chat endpoints return a clean
400 (the UI hides the affordance, so this is the belt-and-suspenders path, never a
500). Streaming endpoints are POST + fetch-stream SSE (get_current_user reads a
Bearer header, which a browser EventSource cannot set).

  POST   /api/assistant/chat                  — stream a turn (SSE); {} messages = continuation;
                                                 optional validated `context` (open CRM record, #14)
  POST   /api/assistant/chat/upload           — same, multipart (payload + files)
  POST   /api/assistant/confirm               — approve/deny a pending write (idempotent)
  GET    /api/assistant/conversations         — list (own only)
  GET    /api/assistant/conversations/:id     — one conversation with messages (own only)
  DELETE /api/assistant/conversations/:id     — delete (own only)
  PATCH  /api/assistant/conversations/:id/title — rename (own only)

Conversations are **owner-only, with no admin override** (issue #191) — the one place
the product access-controls a record rather than merely attributing it. Every path that
can reach a conversation from a seat is scoped to that seat: the four endpoints above,
``/chat``'s resume of a supplied ``conversation_id``, and ``/confirm``'s claim. A
conversation owned by someone else answers exactly as one that does not exist — 404,
never 403 — so the API is not an existence oracle for other people's threads.
  GET    /api/assistant/identity              — fixed name + personality
  PUT    /api/assistant/identity              — edit the personality (admin only; the
                                                 name is a fixed brand, #71)
"""

import asyncio
import json
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from assistant import engine, history, identity, uploads
from assistant.registry import ToolRegistry
from core.auth import get_current_user, require_admin
from providers import get_ai_provider

logger = logging.getLogger(__name__)
router = APIRouter()

_UI_RESULT_PREVIEW_CAP = 2000
_PERSONALITY_MAX = 20_000

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable proxy buffering so tokens flush live
}


# ── Request models ─────────────────────────────────────────────────────────

class ChatContext(BaseModel):
    """The CRM record the user has open — a prompt-injection boundary (#14).

    Only a validated enum + positive int ever cross this seam; the English sentence
    shown to the model is built SERVER-side (identity.build_context_note) from these
    values, never from client free text. ``record_id`` is STRICT so bool/str/float
    are rejected (422), not coerced. Unknown fields (e.g. a client display ``label``)
    are ignored by Pydantic's default and never reach the engine.
    """
    record_type: Literal["deal", "contact", "company"]
    # StrictInt (bool/str/float rejected, not coerced) + bounded to a positive int4 PK
    # (le) so an out-of-range id is a clean 422, never a downstream "integer out of
    # range" from the crm_get_* tools.
    record_id: Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]


class ChatRequest(BaseModel):
    messages: list[dict] = []
    conversation_id: str | None = None
    tool_mode: str = "normal"
    context: ChatContext | None = None


class ConfirmRequest(BaseModel):
    conversation_id: str
    tool_use_id: str
    decision: str  # "approve" | "deny"
    msg_id: str | None = None  # disambiguates positional-id providers (Gemini call_0 reuse)


class TitleRequest(BaseModel):
    title: str


class IdentityUpdateRequest(BaseModel):
    """Only the personality is editable — the assistant's name is a fixed brand (#71).

    A stale client that still sends ``name`` gets it ignored, not a 422: Pydantic drops
    unknown fields by default, and that is the behavior we want. Rejecting the request
    would break the old UI for no gain, while accepting the field would be the bug.
    """
    personality: str | None = None


# ── Streaming chat ─────────────────────────────────────────────────────────

def _require_provider():
    provider = get_ai_provider()
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="No AI provider configured — connect one in AI Setup to use the assistant.",
        )
    return provider


@router.post("/chat")
async def chat(req: ChatRequest, user=Depends(get_current_user)):
    if not req.messages and not req.conversation_id:
        raise HTTPException(status_code=400, detail="messages or conversation_id is required.")
    provider = await asyncio.to_thread(_require_provider)
    # ToolRegistry construction reads the Gmail connection state from Postgres
    # (issue #8), so build it off the event loop. `user` is what carries the caller's
    # seat into the tool layer (issue #190).
    registry = await asyncio.to_thread(ToolRegistry, user=user)
    stream = engine.chat(
        provider, registry, req.messages,
        tool_mode=req.tool_mode, conversation_id=req.conversation_id,
        context=req.context.model_dump() if req.context else None,
        user=user,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/chat/upload")
async def chat_upload(
    payload: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    user=Depends(get_current_user),
):
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid payload JSON.")
    # Validate the same shape /chat gets from Pydantic: a non-empty list of dict
    # messages. Without this, a non-dict message element 500s on messages[-1].get.
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid payload.")
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
        raise HTTPException(status_code=400, detail="messages must be a non-empty list of objects.")
    # The upload path concatenates onto the last message's content, so it must be a
    # string (or absent) — otherwise the join below would 500 instead of 400.
    if not isinstance(messages[-1].get("content"), (str, type(None))):
        raise HTTPException(status_code=400, detail="message content must be text.")
    conversation_id = data.get("conversation_id")
    tool_mode = data.get("tool_mode", "normal")

    # This path parses `payload` by hand, so mirror ChatRequest's Pydantic validation
    # for the record context — same injection boundary, same strict shape. Sits with
    # the other payload-shape checks (cheap), before _require_provider / file parse.
    context = None
    raw_context = data.get("context")
    if raw_context is not None:
        try:
            context = ChatContext.model_validate(raw_context).model_dump()
        except ValidationError:
            raise HTTPException(status_code=400, detail="Invalid context.")

    # Reject when no provider is configured BEFORE parsing any file — a keyless
    # instance must not burn CPU extracting arbitrary PDF/DOCX/XLSX content.
    provider = await asyncio.to_thread(_require_provider)

    if len(files) > uploads.MAX_FILES:
        raise HTTPException(status_code=400, detail=f"At most {uploads.MAX_FILES} files per message.")

    blocks: list[str] = []
    for f in files:
        # Read at most MAX_FILE_SIZE+1 so an oversized file can't exhaust memory.
        raw = await f.read(uploads.MAX_FILE_SIZE + 1)
        if len(raw) > uploads.MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"'{f.filename}' exceeds the 10 MB limit.")
        if not raw:
            continue
        try:
            # Offload the (synchronous, CPU-bound) parse so a large PDF/DOCX/XLSX
            # can't block the async event loop / other concurrent SSE streams.
            blocks.append(await asyncio.to_thread(uploads.extract_upload, f.filename or "upload", raw))
        except uploads.UploadError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Title from the user's TYPED text, before we prepend file content.
    original_text = messages[-1].get("content") or ""
    if blocks:
        messages[-1] = {**messages[-1], "content": "\n\n".join(blocks) + "\n\n" + original_text}
        # Uploaded documents are the untrusted-content channel. A prompt-injected
        # file could ask the model to run a destructive write; in power ("Auto")
        # mode that would execute with no human check. What actually forces every
        # write on this turn through the gate — routine ones included — is the
        # engine's own scan of the assembled context (`context_is_untrusted`,
        # issue #180); normal mode alone no longer confirms everything, so this
        # demotion cannot carry that guarantee by itself. It is kept so the mode
        # the client is running under is honest about the risk. (No files → unchanged.)
        if tool_mode == "power":
            tool_mode = "normal"

    registry = await asyncio.to_thread(ToolRegistry, user=user)
    stream = engine.chat(
        provider, registry, messages,
        tool_mode=tool_mode, conversation_id=conversation_id, title_hint=original_text,
        context=context, user=user,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


# ── Confirmation (server-authoritative, idempotent) ────────────────────────

@router.post("/confirm")
def confirm(req: ConfirmRequest, user=Depends(get_current_user)):
    if req.decision not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'deny'.")
    # The APPROVER is the actor a confirmed write is credited to (issue #190) — the
    # person who said yes, not whoever proposed it. That is why threading identity through
    # the registry needs no change to `resolve_confirmation`: the registry it is handed
    # already carries the right seat, and the stored tool arguments (which the DB, not the
    # client, is authoritative for) cannot override it.
    result = engine.resolve_confirmation(
        ToolRegistry(user=user), req.conversation_id, req.tool_use_id, req.decision,
        msg_id=req.msg_id, user=user,
    )
    # Another seat's conversation is refused before the claim (issue #191) and reported
    # exactly as an unknown one is — 404, never 403, so this is not an existence oracle.
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return result


# ── Conversations ──────────────────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(limit: int = Query(50), offset: int = Query(0), user=Depends(get_current_user)):
    return {"conversations": history.list_conversations(user_id=user["id"], limit=limit, offset=offset)}


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, user=Depends(get_current_user)):
    conv = history.get_conversation(conv_id, user_id=user["id"])
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conv["messages"] = [_message_for_ui(m) for m in conv.get("messages", [])]
    return conv


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, user=Depends(get_current_user)):
    if not history.delete_conversation(conv_id, user_id=user["id"]):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True}


@router.patch("/conversations/{conv_id}/title")
def rename_conversation(conv_id: str, req: TitleRequest, user=Depends(get_current_user)):
    # Split the two reasons apart, because #191 gave the second one a rule: a
    # conversation this seat does not own must answer 404, exactly as a missing one does.
    # A blank title is the caller's own input and stays a 400. This is an emptiness test,
    # not a second normalization: `" ".join(x.split())` is empty for precisely the
    # whitespace-only strings `x.strip()` is, so history.rename_conversation remains the
    # single place a title is actually cleaned.
    if not (req.title or "").strip():
        raise HTTPException(status_code=400, detail="Title is empty.")
    new_title = history.rename_conversation(conv_id, req.title, user_id=user["id"])
    if new_title is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"title": new_title}


# ── Identity ───────────────────────────────────────────────────────────────

@router.get("/identity")
def get_identity(user=Depends(get_current_user)):
    return identity.get_identity()


@router.put("/identity")
def update_identity(req: IdentityUpdateRequest, user=Depends(require_admin)):
    if req.personality is not None and len(req.personality) > _PERSONALITY_MAX:
        raise HTTPException(status_code=400, detail=f"Personality must be ≤ {_PERSONALITY_MAX} characters.")
    return identity.update_identity(personality=req.personality)


# ── Helpers ────────────────────────────────────────────────────────────────

def _message_for_ui(msg: dict) -> dict:
    """Fold capped tool-result previews into each tool_call and drop tool_results.

    The frontend renders history from ``tool_calls`` alone (each carrying a
    ``result`` preview), so the raw/uncapped ``tool_results`` never crosses the
    wire. ``tool_calls`` stays a JSON array — the frontend maps it directly.
    """
    calls = msg.get("tool_calls")
    if not calls:
        msg.pop("tool_results", None)
        return msg
    results_by_id = {r.get("tool_use_id"): r for r in (msg.get("tool_results") or [])}
    merged_calls = []
    for c in calls:
        r = results_by_id.get(c.get("tool_use_id"))
        preview = None
        if r is not None:
            content = r.get("content") or ""
            # Cap BEFORE parsing — a valid-JSON crm_list_contacts result can be
            # arbitrarily large; the raw/uncapped result must never cross the wire.
            if len(content) > _UI_RESULT_PREVIEW_CAP:
                preview = content[:_UI_RESULT_PREVIEW_CAP] + "…(truncated)"
            else:
                try:
                    preview = json.loads(content)
                except (ValueError, TypeError):
                    preview = content
        merged_calls.append({**c, "result": preview})
    msg = {**msg, "tool_calls": merged_calls}
    msg.pop("tool_results", None)
    return msg
