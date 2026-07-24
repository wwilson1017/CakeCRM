"""The single built-in assistant's identity (name + personality).

Replaces Chatty's multi-agent roster / training-mode personality system with one
editable singleton row (``assistant_identity``, seeded id=1) — the same pattern
as ``ai_settings`` / ``crm_meta``. A stored ``personality`` of ``''`` means "use
the built-in default", so the default prompt can improve without a migration and
without overwriting a user's customization.

The system prompt is returned as a ``(static, volatile)`` tuple so providers that
support prompt caching (Anthropic) can cache the large static portion.
"""

from datetime import datetime

from assistant import delimiters
from core.postgres import pg_execute, pg_fetchone

DEFAULT_NAME = "Baker"

# Genericized built-in sales-assistant personality. Written fresh for CakeCRM —
# not ported from any CAKE OS / TN Cheesecake prompt. {name} is interpolated.
DEFAULT_PERSONALITY = """You are {name}, the built-in AI sales assistant for this CRM.

You help the user manage their customer relationships conversationally: finding \
and creating contacts, tracking deals through the pipeline, logging activities, \
and keeping follow-up tasks moving. You are proactive, concise, and honest.

How to work:
- When the user mentions a person or company, look them up with the CRM tools \
before assuming anything. Don't invent contacts, deals, or history — if a tool \
returns nothing, say so.
- When something meaningful happens (a call, an email, a meeting, a note), offer \
to log it. When a next step is implied, offer to create a follow-up task.
- Use the pipeline and dashboard tools to give the user a clear read on where \
things stand.
- Report tool results faithfully. If an action needs the user's confirmation, \
ask plainly and wait — a result of `{"status": "pending_user_approval"}` means \
the user still needs to approve it, not that anything failed.
- Keep replies short and skimmable. Prefer doing the lookup over asking the user \
to repeat what the CRM already knows."""

# Appended to the static system prompt so the model reads the confirmation
# contract consistently regardless of the user's custom personality text.
CONFIRMATION_NOTE = (
    "## Write Confirmations\n"
    "Some actions (creating, updating, or deleting CRM records) may require the "
    "user's approval before they run. When a tool result is "
    '`{"status": "pending_user_approval"}`, the action has NOT happened yet — tell '
    "the user what you're about to do and wait for them to approve. This is normal, "
    "not an error. Read tools never require approval."
)


def get_identity() -> dict:
    """Return the identity singleton, resolving the default personality.

    ``personality`` is the stored custom text, or the built-in default when the
    stored text is blank. ``using_default`` reflects which one is in effect.
    """
    row = pg_fetchone("SELECT name, personality FROM assistant_identity WHERE id = 1")
    name = (row or {}).get("name") or DEFAULT_NAME
    stored = ((row or {}).get("personality") or "").strip()
    using_default = not stored
    personality = DEFAULT_PERSONALITY if using_default else stored
    return {"name": name, "personality": personality, "using_default": using_default}


def update_identity(name: str | None = None, personality: str | None = None) -> dict:
    """Update the singleton's name and/or personality; returns the resolved identity.

    Only provided fields change. A blank ``personality`` (after strip) stores
    ``''`` → reverts to the built-in default.
    """
    sets: list[str] = []
    params: list[object] = []
    if name is not None:
        sets.append("name = %s")
        params.append(name.strip() or DEFAULT_NAME)
    if personality is not None:
        sets.append("personality = %s")
        params.append(personality.strip())
    if sets:
        sets.append("updated_at = now()")
        pg_execute(f"UPDATE assistant_identity SET {', '.join(sets)} WHERE id = 1", tuple(params))
    return get_identity()


def build_system_prompt(identity: dict) -> tuple[str, str]:
    """Build the ``(static, volatile)`` system prompt for stream_turn().

    Static: personality (name-interpolated) + confirmation note + upload-safety
    instruction (cacheable). Volatile: the current date/time (changes every turn).
    """
    name = identity.get("name") or DEFAULT_NAME
    personality = (identity.get("personality") or DEFAULT_PERSONALITY).replace("{name}", name)
    static = "\n\n".join([
        personality,
        CONFIRMATION_NOTE,
        delimiters.UPLOAD_SAFETY_INSTRUCTION,
    ])
    volatile = f"Current date and time: {datetime.now().astimezone().strftime('%A, %B %d, %Y %I:%M %p %Z')}"
    return static, volatile
