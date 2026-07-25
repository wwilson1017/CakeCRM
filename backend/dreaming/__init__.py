"""Dreaming — the assistant's nightly, pure-algorithmic memory self-maintenance.

Adapted from Chatty's ``core/agents/dreaming/`` (which scored and archived dormant
markdown *context files*) to CakeCRM's single-assistant, Postgres-only layout: the unit
is the memory *fact*, and "archival" is a soft-archive (``memory_facts.archived_at``),
never a delete. Scoring uses only usage signals already on the fact row
(retrieval recency/frequency, age, confidence) — NO AI calls, no provider SDKs.

``processor.run_dreaming_if_due()`` is the scheduler-agnostic entrypoint (advisory-lock
+ due-guard) that issue #6's background loop consumes; an interim guarded lifespan task
(``schedule.py``) runs it until then.
"""
