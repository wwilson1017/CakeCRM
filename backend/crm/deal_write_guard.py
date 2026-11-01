"""Deal writes require a CRM find from an earlier batch in the same chat (#332).

A guessed id or a search sent alongside a write never licenses that write. Only CRM
reads and crm_create_deal establish finds; write echoes cannot validate their targets.
Approval dispatch checks again, so a stale card after a reset does not bypass the rule.

Conversationless runs remember finds for that run only. Finds survive compaction and
are saved before the transcript, so they can outlive the visible search. Similar names
can still identify the wrong deal: the closed-deal warning and write target echo are
checks for that remaining ambiguity, not proof of the user's intent.
"""

import logging

from core.postgres import pg_execute, pg_fetchall
from crm.links import deal_records

logger = logging.getLogger(__name__)
_PRIOR = -1
_DEAL_ARGS = ("deal_id", "target_deal_id", "source_deal_id")


def target_deal_ids(args: dict) -> list[int]:
    """Validate every named deal slot; optional null links carry no target."""
    ids = [args.get(key) for key in _DEAL_ARGS]
    if "deal_ids" in args:
        if not isinstance(args["deal_ids"], list):
            raise ValueError("deal_ids must be a list")
        ids.extend(args["deal_ids"])
    if args.get("entity_type") == "deal":
        ids.append(args.get("entity_id"))
    if any(value is not None and type(value) is not int for value in ids):
        raise ValueError("deal ids must be integers")
    return list(dict.fromkeys(value for value in ids if value is not None))


def _found(state) -> dict[int, int]:
    if state.found_deals is None:
        state.found_deals = {}
        if state.conversation_id:
            try:
                rows = pg_fetchall(
                    "SELECT deal_id FROM assistant_deal_finds "
                    "WHERE conversation_id = %s ORDER BY deal_id",
                    (state.conversation_id,),
                )
                state.found_deals.update((row["deal_id"], _PRIOR) for row in rows)
            except Exception:
                logger.warning("deal write guard: could not load finds", exc_info=True)
    return state.found_deals


def note_found(state, name: str, is_write: bool, result) -> None:
    """Only CRM reads and a newly created deal count as finds."""
    if not name.startswith("crm_") or (is_write and name != "crm_create_deal"):
        return
    ids = deal_records(result)
    if not ids:
        return
    found = _found(state)
    new = [deal_id for deal_id in ids if deal_id not in found]
    found.update((deal_id, state.tool_batch) for deal_id in new)
    if new and state.conversation_id:
        try:
            pg_execute(
                "INSERT INTO assistant_deal_finds (conversation_id, deal_id) "
                "SELECT %s, unnest(%s::int[]) ON CONFLICT DO NOTHING",
                (state.conversation_id, new),
            )
        except Exception:
            logger.warning("deal write guard: could not save finds", exc_info=True)


def refusal(state, args: dict) -> str | None:
    try:
        ids = target_deal_ids(args)
    except ValueError:
        return "Not written: deal ids must be integers from a CRM search; deal_ids must be a list."
    if not ids:
        return None
    found = _found(state)
    missing = [deal_id for deal_id in ids if deal_id not in found or found[deal_id] == state.tool_batch]
    if missing:
        named = ", ".join(map(str, missing))
        return (
            f"Not written: deal {named} was not found by a CRM search in this chat. "
            "Find the deal with crm_search_deals or crm_get_deal, check its title is the one "
            "the user named, then make this write in your next step. A search sent in the "
            "same batch as the write does not count."
        )
    return None


def closed_warning(ids: list[int]) -> str | None:
    if not ids:
        return None
    try:
        rows = pg_fetchall(
            "SELECT id, title, stage, archived_at FROM deals "
            "WHERE id = ANY(%s) AND (stage = 'lost' OR archived_at IS NOT NULL) ORDER BY id",
            (ids,),
        )
    except Exception:
        logger.warning("deal write guard: could not read closed deals", exc_info=True)
        return None
    if not rows:
        return None
    named = "; ".join(
        f"deal {row['id']} '{row['title']}' is "
        + "/".join(label for label, applies in (
            ("lost", row["stage"] == "lost"), ("archived", row["archived_at"] is not None)
        ) if applies)
        for row in rows
    )
    return (
        f"Written to a closed deal: {named}. Lost and archived deals can have an open "
        "duplicate with the same name. Tell the user which deal you wrote to and check "
        "it is the one they meant."
    )
