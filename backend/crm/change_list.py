"""One approval card for a list of deal and todo field changes, with batch Undo (#371).

Ports the card + apply + Undo model of upstream a0c03fe2e, not its machinery: one tool,
one table, one transaction.

* ``stage`` runs when the engine is about to raise the card. It validates every row with
  the target write path's own rules, reads the current values itself and saves the batch.
  The card, the apply and the Undo all read that saved batch, never the model's arguments.
* ``apply`` runs on Approve and ``undo`` on the card's Undo button. Each is one transaction
  that locks the batch, then the deals, then the todos (each in id order), skips every row
  whose record no longer holds the value the batch expects, and writes the rest through
  ``service._write_deal_update_cur`` / ``service._apply_todo_update_cur``.
* Titles, notes and descriptions live only in the batch, which the browser reads over
  REST. The pending summary samples up to five other fields; results carry counts, ids
  and reasons. A public-capture todo title never reaches the model through this tool.
* Money is shown per row with that deal's currency and is never added up.

simplification: a deal-then-todo lock order can deadlock against a todo edit that links
the same deal at that moment; Postgres aborts one of the two and nothing is half-applied.
Deadlocks roll back with the same start-over message as a lock timeout; no automatic retry.
simplification: Undo restores field values, not derived stamps: a todo put back to done
gets a new completed_at, and a deal's stage history gains a reverse entry. Upgrade path:
save and restore the stamps with the row.
"""

import json
import logging
import math
import re
import secrets
from collections import Counter
from typing import Literal

import psycopg2

from core.localtime import today_local
from core.postgres import get_connection, pg_execute, pg_fetchone, row_to_dict
from crm import (
    deal_write_guard,
    gtd_common,
    provenance_service,
    scoring_service,
    service,
)

logger = logging.getLogger(__name__)

TOOL = "crm_apply_change_list"
# simplification: one card is one transaction holding every row lock, so it is capped
# like bulk_move_deals (service.BULK_MOVE_MAX); upgrade path: raise the cap after measuring lock time.
MAX_ROWS = 200
REASON_MAX = 300
# The pending placeholder and the result must stay valid JSON under the 2000-character
# preview a reloaded conversation gets (assistant/router._UI_RESULT_PREVIEW_CAP).
SUMMARY_BUDGET = 1300
RESULT_BUDGET = 1800

#: What one change list may set. Every other column keeps its single-record tool; owners
#: stay out because they are assigned by name, one record at a time, through
#: tools._resolve_owner(require_active=True).
FIELDS = {
    "deal": ("title", "stage", "value", "expected_close_date", "probability", "currency",
             "deal_temperature", "notes"),
    "todo": ("title", "status", "due_date", "bring_back_on", "star", "context", "priority",
             "description", "tags"),
}
_READ = {
    "deal": "SELECT id, title, stage, archived_at, value, expected_close_date, probability, "
            "currency, deal_temperature, notes FROM deals WHERE id = ANY(%s) ORDER BY id",
    "todo": "SELECT id, title, status, due_date, bring_back_on, star, context, priority, "
            "repeat, description, tags FROM todos WHERE id = ANY(%s) ORDER BY id",
}

CHANGED = "changed since the card was shown"
NOT_FOUND = ("not found by a CRM search in this chat; find the deal first, then send the "
             "change list in your next step")
NEEDS_USER = "A change list needs a signed-in person in a conversation."
ONLY_FROM_CARD = "A change list is applied only from its approval card."
GONE = "That change list is no longer available. Build it again."
BUSY = ("Those records were busy, so nothing was changed. "
        "Ask Baker to send the change list again.")

_TRANSITIONS = {
    "apply": ("proposed", "applied", "ready", "old", "new", "applied", "skipped"),
    "undo": ("applied", "undone", "applied", "new", "old", "undone", "kept"),
}


def _normalize(kind: str, field: str, value):
    """``value`` as the target write path stores it; ValueError says what is wrong."""
    if field in ("notes", "description"):
        return gtd_common.validate_notes(value)
    if kind == "todo":
        if field == "tags":
            return gtd_common.validate_tags(value)
        if field == "title":
            return gtd_common.validate_title(value)
        if field == "status":
            return gtd_common.validate_status(value)
        if field == "due_date":
            return gtd_common.validate_due(value)
        if field == "bring_back_on":
            return gtd_common.validate_bring_back(value)
        if field == "star":
            return gtd_common.validate_flag(value, "star")
        if field == "context":
            return gtd_common.validate_short(value, "context")
        if value not in service.TODO_PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(service.TODO_PRIORITIES)}")
        return value
    if field == "title":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("title must be text")
        return value.strip()
    if field == "stage":
        if value not in service.OPEN_STAGES:
            raise ValueError(
                f"stage must be one of {', '.join(service.OPEN_STAGES)}; close deals one at "
                "a time with crm_mark_deal_won or crm_mark_deal_lost")
        return value
    if field == "value":
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("value must be a number, zero or more")
        return float(value)
    if field == "expected_close_date":
        return gtd_common.validate_due(value, "expected_close_date")
    if field == "probability":
        if type(value) is not int:
            raise ValueError("probability must be a whole number from 0 to 100")
        return max(0, min(100, value))  # update_deal's clamp
    if field == "currency":
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value.strip()):
            raise ValueError("currency must be a three-letter code such as USD")
        return value.strip().upper()
    return service.normalize_deal_temperature(value)


def _blocked(kind: str, record: dict | None, field: str, value) -> str | None:
    """Why ``record`` cannot take ``field = value`` right now, else None.

    Asked at stage, again under the row lock at apply, and again at Undo.
    """
    if record is None:
        return "no such record"
    if kind == "deal":
        if record["archived_at"] is not None:
            return "archived deal; restore it first"
        if field == "stage" and record["stage"] in service.CLOSED_STAGES:
            return f"{record['stage']} deal; reopen it by itself first"
    elif field == "status" and record["repeat"] and "done" in (record["status"], value):
        return ("repeating todo; complete or reopen it by itself, because that creates "
                "its next occurrence")
    return None


def _read(cur, kind: str, ids, *, lock: bool) -> dict[int, dict]:
    """Current rows by id. ``lock`` takes them FOR UPDATE in id order (bulk_move_deals' rule)."""
    if not ids:
        return {}
    cur.execute(_READ[kind] + (" FOR UPDATE" if lock else ""), (sorted(ids),))
    return {row[0]: row_to_dict(cur, row) for row in cur.fetchall()}


def _label(kind: str, record_id: int, record: dict | None) -> str:
    title = (record or {}).get("title") or f"{kind} {record_id}"
    return f"{title} (lost)" if (record or {}).get("stage") == "lost" else title


def _line(row: dict) -> str:
    target = (row["label"] if row["record_type"] == "row"
              else f"{row['record_type']} {row['id']} {row['field']}")
    return f"{target}: {row['reason']}"


def _bounded(base: dict, key: str, lines: list[str], budget: int, *, suffix: str = "") -> dict:
    """``base`` plus as many ``lines`` under ``key`` as keep its JSON within ``budget``."""
    if not lines:
        return base
    kept: list[str] = []
    for line in lines:
        more = f"…and {len(lines) - len(kept) - 1} more{suffix}"
        if len(json.dumps({**base, key: [*kept, line, more]})) > budget:
            break
        kept.append(line)
    if len(kept) < len(lines):
        kept.append(f"…and {len(lines) - len(kept)} more{suffix}")
    return {**base, key: kept}


def _sample(row: dict) -> str:
    def show(value, unit=None):
        if value is None or value == "":
            return "(empty)"
        if isinstance(value, bool):
            text = "yes" if value else "no"
        elif isinstance(value, (int, float)):
            text = f"{value:,.3f}".rstrip("0").rstrip(".")
        elif isinstance(value, list):
            text = ",".join(value)
        else:
            text = str(value)
        text = f"{text} {unit}" if unit else text
        return text if len(text) <= 40 else text[:39] + "…"

    currency = row.get("currency", [None, None])
    return (f"{row['record_type']} {row['id']} {row['field']}: "
            f"{show(row['old'], currency[0])} → {show(row['new'], currency[1])}")


def _headline(rows: list[dict]) -> str:
    ready = [r for r in rows if r["state"] == "ready"]
    records = [
        f"{n} {kind}{'' if n == 1 else 's'}" for kind in FIELDS
        if (n := len({r["id"] for r in ready if r["record_type"] == kind}))
    ]
    text = f"Change {len(ready)} field{'' if len(ready) == 1 else 's'} on {' and '.join(records)}"
    left = len(rows) - len(ready)
    return text + (f"; {left} row{'' if left == 1 else 's'} will not change" if left else "")


def stage(args, registry) -> dict:
    """Validate a change list and save it for one approval card. Never raises.

    ``{"error": …}`` refuses the call before any card (nothing is saved). Otherwise the
    pending-placeholder binding: ``{"summary": {headline, lines}, "change_batch_id": id}``.
    """
    try:
        return _stage(args, registry)
    except Exception:
        logger.exception("change list: staging failed")
        return {"error": "Could not prepare that change list. Try again."}


def _stage(args, registry) -> dict:
    user_id = (registry.user or {}).get("id")
    if user_id is None or not registry.conversation_id:
        return {"error": NEEDS_USER}
    if not isinstance(args, dict) or set(args) - {"changes", "reason"}:
        return {"error": f"{TOOL} takes only `changes` and `reason`."}
    changes = args.get("changes")
    if not isinstance(changes, list) or not changes:
        return {"error": "changes must be a non-empty list of {record_type, id, field, value}."}
    if len(changes) > MAX_ROWS:
        return {"error": f"Too many changes ({len(changes)}); a change list takes at most "
                         f"{MAX_ROWS}. Split it."}
    def well_formed(change):
        return (isinstance(change, dict)
                and set(change) == {"record_type", "id", "field", "value"}
                and isinstance(change["record_type"], str)
                and isinstance(change["field"], str)
                and type(change["id"]) is int and change["id"] > 0)

    valid = [c for c in changes if well_formed(c)]
    ids = {kind: {c["id"] for c in valid if c["record_type"] == kind} for kind in FIELDS}
    with get_connection() as conn:
        cur = conn.cursor()
        current = {kind: _read(cur, kind, ids[kind], lock=False) for kind in FIELDS}
    # The #332 rule per ROW: a deal no earlier batch of this chat found is a refused row,
    # not a refused call (the guard's own check ignores this tool's nested ids).
    unfound = set(deal_write_guard.unfound(registry, sorted(ids["deal"]))) if ids["deal"] else set()
    seen = Counter((c["record_type"], c["id"], c["field"]) for c in valid)

    rows: list[dict] = []
    for i, change in enumerate(changes, 1):
        if not well_formed(change):
            rows.append({"record_type": "row", "id": i, "field": "", "label": f"row {i}",
                         "old": None, "new": None, "state": "refused",
                         "reason": "not a {record_type, id, field, value} row"})
            continue
        kind, record_id, field = change["record_type"], change["id"], change["field"]
        record = current.get(kind, {}).get(record_id)
        row = {"record_type": kind, "id": record_id, "field": field,
               "label": _label(kind, record_id, record), "old": None, "new": change["value"],
               "state": "refused"}
        rows.append(row)
        if kind not in FIELDS:
            row["reason"] = "only deals and todos can be in a change list"
        elif field not in FIELDS[kind]:
            row["reason"] = (f"{field} cannot be set in a change list; "
                             f"a {kind} row takes {', '.join(FIELDS[kind])}")
        elif seen[(kind, record_id, field)] > 1:
            row["reason"] = "listed more than once"
        elif kind == "deal" and record_id in unfound:
            row["reason"] = NOT_FOUND
        else:
            try:
                new = _normalize(kind, field, change["value"])
            except ValueError as e:
                row["reason"] = str(e)[:200]
                continue
            reason = _blocked(kind, record, field, new)
            if record is not None:
                row["old"], row["new"] = record[field], new
                if reason is None and record[field] == new:
                    reason = "already matches"
            if reason:
                row["reason"] = reason
            else:
                row["state"] = "ready"

    # A value row names its deal's currency on each side; nothing is ever totalled.
    recoded = {r["id"]: r["new"] for r in rows
               if r["state"] == "ready" and (r["record_type"], r["field"]) == ("deal", "currency")}
    for r in rows:
        if (r["record_type"], r["field"]) == ("deal", "value") and r["id"] in current["deal"]:
            was = current["deal"][r["id"]]["currency"]
            r["currency"] = [was, recoded.get(r["id"], was)]

    left = [_line(r) for r in rows if r["state"] != "ready"]
    if len(left) == len(rows):
        return _bounded({"error": "Nothing to change: no row can be applied."},
                        "refused", left, RESULT_BUDGET)
    batch_id = "chg_" + secrets.token_hex(8)
    pg_execute(
        "INSERT INTO assistant_change_batches (id, user_id, conversation_id, reason, rows) "
        "VALUES (%s, %s, %s, %s, %s::jsonb)",
        (batch_id, user_id, registry.conversation_id,
         str(args.get("reason") or "").strip()[:REASON_MAX], json.dumps(rows)),
    )
    samples = [_sample(r) for r in rows if r["state"] == "ready"
               and r["field"] not in {"title", "notes", "description"}][:5]
    return {"summary": _bounded({"headline": _headline(rows)}, "lines", samples + left,
                                SUMMARY_BUDGET, suffix="; the card lists every row"),
            "change_batch_id": batch_id}


def view(batch_id: str, *, user_id: int) -> dict | None:
    """The batch as its card shows it, or None when it is not this seat's."""
    return pg_fetchone(
        "SELECT id, status, reason, rows FROM assistant_change_batches "
        "WHERE id = %s AND user_id = %s",
        (batch_id, user_id),
    )


def _transition(batch_id: str, user_id: int, *, direction: Literal["apply", "undo"],
                unfound: frozenset = frozenset()) -> dict | None:
    """Apply or undo in one transaction; every taken row of a record succeeds or misses.

    None when the batch is not this seat's; ValueError for an invalid state or busy rows.
    """
    before, after, take, expect, write, done, missed = _TRANSITIONS[direction]
    written: dict[tuple[str, int], dict] = {}
    contact_ids: list[int | None] = []
    today = today_local()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            # Bound the wait: this holds up to MAX_ROWS row locks (bulk_move_deals' idiom).
            cur.execute("SET LOCAL lock_timeout = '10s'")
            cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute(
                "SELECT status, reason, rows FROM assistant_change_batches "
                "WHERE id = %s AND user_id = %s FOR UPDATE",
                (batch_id, user_id),
            )
            batch = cur.fetchone()
            if batch is None:
                return None
            status, reason, rows = batch
            if status != before:
                raise ValueError(f"This change list is {status}, so it cannot be {after}.")
            taken = [r for r in rows if r["state"] == take]
            current = {
                kind: _read(cur, kind, {r["id"] for r in taken if r["record_type"] == kind}, lock=True)
                for kind in FIELDS  # deals, then todos
            }
            grouped: dict[tuple[str, int], list[dict]] = {}
            for r in taken:
                grouped.setdefault((r["record_type"], r["id"]), []).append(r)
            for (kind, record_id), record_rows in grouped.items():
                record = current[kind].get(record_id)
                why = None
                for r in record_rows:
                    field = r["field"]
                    why = (NOT_FOUND if kind == "deal" and record_id in unfound
                           else _blocked(kind, record, field, r[write]))
                    if why is None and record[field] != r[expect]:
                        why = CHANGED
                    if (why is None and kind == "deal" and field == "value"
                            and record["currency"] != r["currency"][0 if direction == "apply" else 1]):
                        why = CHANGED
                    if why:
                        break
                for r in record_rows:
                    if why:
                        r["state"], r["reason"] = missed, why
                    else:
                        r["state"] = done
                if why is None:
                    written[(kind, record_id)] = {r["field"]: r[write] for r in record_rows}
            for (kind, record_id), fields in sorted(written.items()):
                if kind == "deal":
                    _, contact_id, _ = service._write_deal_update_cur(
                        cur, record_id, fields, closed_on_day=None, today=today, actor_id=user_id)
                    contact_ids.append(contact_id)
                else:
                    service._apply_todo_update_cur(cur, record_id, fields)
            cur.execute(
                "UPDATE assistant_change_batches SET status = %s, rows = %s::jsonb WHERE id = %s",
                (after, json.dumps(rows), batch_id),
            )
    except (psycopg2.errors.LockNotAvailable, psycopg2.errors.QueryCanceled,
            psycopg2.errors.DeadlockDetected):
        # Rolled back: nothing was written and the batch is still ``before``.
        raise ValueError(BUSY) from None
    deals = {record_id: fields for (kind, record_id), fields in written.items() if kind == "deal"}
    # After commit, like every deal write: score_on_event never raises.
    scoring_service.score_on_event(deal_ids=list(deals), contact_ids=contact_ids)
    if after == "applied":
        for deal_id, fields in deals.items():
            try:  # the "an AI wrote this" badge; never costs the write its result
                provenance_service.record_fields("deal", deal_id, fields)
            except Exception:
                logger.warning("change list: provenance failed for deal %s", deal_id, exc_info=True)
    return {"id": batch_id, "status": after, "reason": reason, "rows": rows}


def apply(registry) -> dict:
    """Apply the batch the approved card showed (``registry.change_batch_id``)."""
    batch_id = getattr(registry, "change_batch_id", None)
    user_id = (getattr(registry, "user", None) or {}).get("id")
    if not batch_id or user_id is None:
        return {"error": ONLY_FROM_CARD}
    staged = view(batch_id, user_id=user_id)
    if staged is None:
        return {"error": GONE}
    # Approval checks the #332 rule again, as dispatch does for every other deal write.
    deal_ids = sorted({r["id"] for r in staged["rows"]
                       if r["record_type"] == "deal" and r["state"] == "ready"})
    try:
        unfound = frozenset(deal_write_guard.unfound(registry, deal_ids)) if deal_ids else frozenset()
        batch = _transition(batch_id, user_id, direction="apply",
                            unfound=unfound)
    except ValueError as e:
        return {"error": str(e)}
    if batch is None:
        return {"error": GONE}
    counts = Counter(r["state"] for r in batch["rows"])
    result = {"ok": True, "change_batch_id": batch_id, "applied": counts["applied"],
              "skipped": counts["skipped"], "refused": counts["refused"],
              "undo": "The card has an Undo button that restores every applied row."}
    return _bounded(result, "not_applied",
                    [_line(r) for r in batch["rows"] if r["state"] in ("skipped", "refused")],
                    RESULT_BUDGET, suffix="; the card lists every row")


def undo(batch_id: str, *, user_id: int) -> dict | None:
    """Restore every applied row that still holds what the batch wrote. One Undo per batch."""
    return _transition(batch_id, user_id, direction="undo")
