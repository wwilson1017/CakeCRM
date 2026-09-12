"""The confirmation tier a tool def may declare beside ``writes`` (issue #180).

``confirm_tier`` is an INTERNAL def key — stripped before a provider ever sees it,
exactly like ``kind`` and ``writes`` — with exactly ONE legal value. A write that
carries it skips the Approve card in normal mode. Every other write, every read, and
every unknown tool name keeps its card: **absence is the deny state**, so a tool nobody
classified is never silently exempted.

A tool may declare it only when ALL of these hold (the classification rule from #180):
  1. the effect stays in a CakeCRM Postgres record;
  2. nobody is notified (no email draft, no push/Telegram, no reminder that fires);
  3. nothing is removed from view (no delete, archive, merge, cancel);
  4. it is not a bulk write;
  5. nothing leaves the install (no Gmail, no outbound HTTP).

A leaf with no imports, on purpose: ``crm.tools`` DECLARES the tier and
``assistant.registry`` VALIDATES it, and the registry already imports ``crm.tools`` — so
the constant can live in neither without a cycle. ``assistant.delimiters`` is the
precedent for an assistant leaf imported from another package.
"""

ROUTINE = "routine"
