"""CakeCRM — the assistant's long-term memory (temporal facts + full-text search).

Ported and slimmed from Chatty's ``core/agents/memory/``: the temporal-facts store
(subject/predicate/object triples with validity windows, confidence, and a
memory-type taxonomy) reimplemented on Postgres, plus full-text search over those
facts. Chatty's AI/document layers — embeddings/vector search, the daily-note
summarizer, the MEMORY.md consolidator, observer/extractor/commitments — are
deliberately NOT ported (issue #5 scopes memory to "facts + search", pure-algorithmic,
no AI calls).

Nothing here imports a provider SDK or touches the DB at import time — the FastAPI
app imports ``memory.tools`` (via the assistant registry) with no DATABASE_URL during
the CI import check. All DB access goes through ``core/postgres.py`` helpers.
"""
