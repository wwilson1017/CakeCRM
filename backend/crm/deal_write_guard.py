"""Deal writes require a CRM find from an earlier batch in the same chat (#332).

A guessed id or a search sent alongside a write never licenses that write. Only CRM
reads and crm_create_deal establish finds; write echoes cannot validate their targets.
Approval dispatch checks again, so a stale card after a reset does not bypass the rule.

Conversationless runs remember finds for that run only. Finds survive compaction and
are saved before the transcript, so they can outlive the visible search. Similar names
can still identify the wrong deal: the closed-deal warning and write target echo are
checks for that remaining ambiguity, not proof of the user's intent.

A CRM reset restarts deal ids, so every find is stamped with the CRM epoch it was made
in and only counts while that epoch is current. The epoch is read BEFORE the CRM read
runs, so a reset landing between the read and its saved find leaves that find stale.
simplification: the epoch is ``crm_meta.updated_at``, which every reset (``clear_all``,
``clear_demo_data``, a sample load) bumps in its own transaction; the few settings writes
that also bump it only make Baker search again. Upgrade path: a dedicated counter.
simplification: the check and the write are separate transactions, so a reset that
also recreates the same deal id in the milliseconds between them is not caught. This is
a guard against the model guessing ids, not access control; holding the epoch through
the write would mean threading it into every deal-writing service call.
"""

import logging

from core.postgres import pg_execute, pg_fetchall, pg_fetchone
from crm.links import deal_records

logger = logging.getLogger(__name__)
_PRIOR = -1
#: ``epoch_before`` when no find may be recorded (not a finding tool, or the epoch is unknown).
NO_EPOCH = object()
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


def _current_epoch():
    row = pg_fetchone("SELECT updated_at FROM crm_meta WHERE id = 1")
    return row["updated_at"] if row else None


def _records_finds(name: str, is_write: bool) -> bool:
    return name.startswith("crm_") and (not is_write or name == "crm_create_deal")


def epoch_before(name: str, is_write: bool):
    """The CRM epoch a finding tool runs in, read before it runs; ``NO_EPOCH`` otherwise."""
    if not _records_finds(name, is_write):
        return NO_EPOCH
    try:
        return _current_epoch()
    except Exception:
        logger.warning("deal write guard: could not read the CRM epoch", exc_info=True)
        return NO_EPOCH


def _found(state, epoch) -> dict[int, int]:
    """This run's finds for ``epoch``; a cache from an older epoch is dropped and reloaded."""
    if state.found_deals is None or state.found_epoch != epoch:
        state.found_deals, state.found_epoch = {}, epoch
        if state.conversation_id:
            try:
                rows = pg_fetchall(
                    "SELECT deal_id FROM assistant_deal_finds "
                    "WHERE conversation_id = %s AND crm_epoch IS NOT DISTINCT FROM %s "
                    "ORDER BY deal_id",
                    (state.conversation_id, epoch),
                )
                state.found_deals.update((row["deal_id"], _PRIOR) for row in rows)
            except Exception:
                logger.warning("deal write guard: could not load finds", exc_info=True)
    return state.found_deals


def note_found(state, result, epoch) -> None:
    """Record the deals a finding tool returned, in the epoch read before it ran."""
    if epoch is NO_EPOCH:
        return
    ids = deal_records(result)
    if not ids:
        return
    found = _found(state, epoch)
    new = [deal_id for deal_id in ids if deal_id not in found]
    found.update((deal_id, state.tool_batch) for deal_id in new)
    if new and state.conversation_id:
        try:
            pg_execute(
                "INSERT INTO assistant_deal_finds (conversation_id, deal_id, crm_epoch) "
                "SELECT %s, unnest(%s::int[]), %s ON CONFLICT (conversation_id, deal_id) "
                "DO UPDATE SET crm_epoch = EXCLUDED.crm_epoch, found_at = now()",
                (state.conversation_id, new, epoch),
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
    found = _found(state, _current_epoch())
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
