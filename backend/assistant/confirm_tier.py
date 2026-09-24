"""The confirmation tier a tool def may declare beside ``writes`` (issue #180).

``confirm_tier`` is an INTERNAL def key — stripped before a provider ever sees it,
exactly like ``kind`` and ``writes`` — with exactly ONE legal value. A write that
carries it skips the Approve card in normal mode. Every other write, every read, and
every unknown tool name keeps its card: **absence is the deny state**, so a tool nobody
classified is never silently exempted.

A tool may declare it only when ALL of these hold (the classification rule from #180):
  1. the effect stays in a CakeCRM Postgres record;
  2. nobody is notified (no email draft, no push/Telegram, nothing that fires at someone later);
  3. nothing is removed from view (no delete, archive, merge, cancel);
  4. it is not a bulk write;
  5. nothing leaves the install (no Gmail, no outbound HTTP).

Rule 3 also has an argument-level half, because a value can hide a record the tool is
otherwise free to edit. That is ``removes_from_view()`` at the bottom of this module —
it moved here from ``crm.tools`` in #186, when its keys stopped belonging to one tool
module.

A leaf with no imports, on purpose. ``assistant.registry`` — where the sibling
``writes`` validation lives — cannot host it: ``crm.tools`` would have to import the
constant back, and the registry already imports ``crm.tools``, so that is a cycle.
``crm.tools`` could host it, but then a memory or Gmail tool source wanting a
tier would import CRM vocabulary to spell an assistant-layer word. So: a small topical
leaf, the shape ``assistant.delimiters`` and ``assistant.write_budget`` already use.
"""

ROUTINE = "routine"


# ── Rule 3, at argument level: the "removes from view" carve-out ──────────────
# Some routine tools can take a record OUT of the lists through an argument value.
# The TOOL stays routine — renaming a company or retitling a todo must not raise a
# card — while that one CALL keeps its confirmation. This is rule 3 above, narrowed
# to the argument, and the engine consults it exactly once, as a narrowing of the
# normal-mode term of the gate.
#
# It lives HERE, beside the rule it narrows, because its keys span two tool modules
# (#186): `crm_update_*` are defined in `crm/tools.py` and `todo_update*` in
# `crm/gtd_tools.py`, so a map inside either one would be naming tools it does not
# own. Keeping it in this leaf also lets `assistant/engine.py` off its only import of
# CRM feature code — the confirmation gate is assistant-layer vocabulary end to end.
#
# Why each entry:
#   * `crm_update_contact` / `crm_update_company` — "archived". `crm_update_company`'s
#     own description sells it as the stand-in for the delete tool we deliberately do
#     not expose;
#   * `crm_update_todo` — "dropped", which `list_todos` filters out unconditionally
#     (`crm.service.NOT_DROPPED_TODO_T`), i.e. a soft delete. The def does not
#     advertise `status`, but tool arguments are NOT validated against the schema at
#     runtime and the executor forwards `**kwargs` into `service.update_todo`, whose
#     allow-list accepts `status` — so an undeclared argument really does reach the
#     column;
#   * `todo_update` — "dropped" (#186). Here `status` IS advertised, and
#     `todo_delete`'s own description sends the model this way: "to drop work the
#     user has decided against, set status='dropped' so it stays recoverable". That
#     makes dropping the product's delete gesture, so without this entry classifying
#     `todo_update` routine would hand the model an unconfirmed delete;
#   * `todo_update_project` — "dropped", the same gesture one level up. Dropping a
#     project does NOT cascade to its todos (`gtd_service.update_project` writes only
#     `todo_projects`), so what leaves the view is the project row itself.
#
# Deliberately keyed on the values that HIDE a record, not on every status:
#   * 'done' on a todo and 'completed' on a project are COMPLETION — the user's
#     intended terminal state for work that happened. `crm_complete_todo` is routine
#     by design and these are the same gesture;
#   * 'someday_maybe' / 'someday' is FILING between working lists: GTD renders a page
#     per status, and both `todo_list` and `todo_list_projects` still return the row.
#
# `status` is the only hiding argument these four update tools can reach:
#   * `gtd_service.update_todo` / `update_project` run `_check_fields` FIRST, which
#     RAISES on any key outside `TODO_FIELDS` / `PROJECT_FIELDS` — a rejecting
#     allow-list, unlike `service.update_todo`'s silent filter. `deal_id` (the one
#     other column that hides a todo, via `list_todos`' archived-deal predicate) is
#     not in `TODO_FIELDS`, so it cannot be set through `todo_update` at all;
#   * for contacts/companies/todos the same conclusion was reached in #180.
# `tests/test_confirm_tier.py` pins both halves of that.
#
# Named the same shape as `context_files.tools.requires_confirmation`: the engine's
# routine predicate is name-keyed, and this is the hook that makes one tool
# "routine sometimes". It only ever ADDS a confirmation, so it is safe to fail closed.
_HIDING_STATUS: dict[str, frozenset[str]] = {
    "crm_update_contact": frozenset({"archived"}),
    "crm_update_company": frozenset({"archived"}),
    "crm_update_todo": frozenset({"dropped"}),
    "todo_update": frozenset({"dropped"}),
    "todo_update_project": frozenset({"dropped"}),
}


def removes_from_view(tool_name: str, args: dict | None) -> bool:
    """True when this specific call would take the record it edits out of the lists.

    Fails CLOSED: a provider can decode malformed tool JSON to a list, string or
    number, and an unreadable argument set on a hide-capable tool is treated as a hide.
    The cost of being wrong is one Approve card.
    """
    hiding = _HIDING_STATUS.get(tool_name)
    if hiding is None:
        return False
    if not isinstance(args, dict):
        return True
    status = args.get("status")
    if status is None:
        return False
    if not isinstance(status, str):
        return True
    return status.strip().lower() in hiding
