"""Baker's context-file store (issue #72) — soul.md, MEMORY.md, topic files, daily notes.

Ported from chatty's ``core/agents/context_manager.py``, with the filesystem swapped for
one Postgres table. See ``service.py`` for the translation notes.
"""
