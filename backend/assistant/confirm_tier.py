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

A leaf with no imports, on purpose. ``assistant.registry`` — where the sibling
``writes`` validation lives — cannot host it: ``crm.tools`` would have to import the
constant back, and the registry already imports ``crm.tools``, so that is a cycle.
``crm.tools`` could host it, but then a memory or Gmail tool source wanting a
tier would import CRM vocabulary to spell an assistant-layer word. So: a small topical
leaf, the shape ``assistant.delimiters`` and ``assistant.write_budget`` already use.
"""

ROUTINE = "routine"
