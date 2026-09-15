"""CakeCRM — the assistant's long-term memory (temporal facts + full-text search).

Ported and slimmed from Chatty's ``core/agents/memory/``: the temporal-facts store
(subject/predicate/object triples with validity windows, confidence, and a
memory-type taxonomy) reimplemented on Postgres, plus full-text search over those
facts. Issue #5 scoped this package to "facts + search", pure-algorithmic, no AI.

Issue #72 Phase 4 added exactly one AI member, ``observer.py``, and collapsed chatty's
THREE pipelines into it: chatty's observer, its per-fourth-message extractor and its
581-line commitments store all become one scheduler pass writing two row shapes — facts
here, and GTD inbox tasks in ``crm.gtd_service``. Chatty's ``observations`` table, its
``complete_commitment`` lifecycle and its embeddings/vector search are still NOT ported;
the embeddings half was designed as #72 Phase 5 and declined on 2026-09-14.

Nothing here imports a provider SDK at module level or touches the DB at import time —
the FastAPI app imports ``memory.tools`` (via the assistant registry) with no
DATABASE_URL during the CI import check, and ``observer.py`` resolves its provider
through the ABC inside the run. All DB access goes through ``core/postgres.py`` helpers.
"""
