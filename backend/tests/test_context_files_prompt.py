"""context_files/prompt.py — what actually reaches the model, and what must not.

The load-bearing invariants: the soul goes in unfenced, everything else goes in fenced,
the cap is applied BEFORE fencing (truncating after could sever a closing nonce tag and
leave the fence open), and a store outage degrades to a context-less turn rather than a
broken chat.
"""

from context_files import prompt, service


def _stub_store(monkeypatch, *, soul="", memory="", today="", topics=(), dailies=()):
    files = {service.SOUL_FILE: soul, service.MEMORY_FILE: memory}
    monkeypatch.setattr(
        prompt.service, "read_file",
        lambda name: {"content": files.get(name, "")} if name in files else None,
    )
    monkeypatch.setattr(prompt.service, "read_daily_note", lambda *a, **k: today)
    monkeypatch.setattr(prompt.service, "topic_manifest", lambda: list(topics))
    monkeypatch.setattr(prompt.service, "daily_manifest", lambda *a, **k: list(dailies))


# ── soul (static, unfenced) ───────────────────────────────────────────────────────

def test_soul_block_is_unfenced(monkeypatch):
    _stub_store(monkeypatch, soul="I am Baker and I keep notes.")
    block = prompt.build_soul_block()
    assert "I am Baker and I keep notes." in block
    assert "recorded_context" not in block, "the soul is the ONE knowledge file loaded unfenced"


def test_soul_block_is_empty_when_unset(monkeypatch):
    """A fresh install seeds soul.md with EMPTY content — the default text lives in
    identity.DEFAULT_SOUL, so the block must not emit a bare heading here."""
    _stub_store(monkeypatch, soul="")
    assert prompt.build_soul_block() == ""


def test_soul_block_is_capped(monkeypatch):
    _stub_store(monkeypatch, soul="x" * (prompt.MAX_SOUL_CHARS + 5_000))
    block = prompt.build_soul_block()
    assert prompt._TRUNCATED in block
    assert len(block) < prompt.MAX_SOUL_CHARS + 500


def test_soul_block_survives_a_store_outage(monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prompt.service, "read_file", explode)
    assert prompt.build_soul_block() == ""


# ── knowledge (volatile, fenced) ──────────────────────────────────────────────────

def test_knowledge_block_is_fenced_and_closed(monkeypatch):
    _stub_store(monkeypatch, memory="Dana Chen runs Acme.")
    block = prompt.build_knowledge_block()
    assert block.startswith("<recorded_context id=")
    assert block.rstrip().endswith('">')
    # The nonce must be repeated in both tags — that is what makes the fence unforgeable.
    nonce = block.split('id="', 1)[1].split('"', 1)[0]
    assert block.count(nonce) == 2
    assert "Dana Chen runs Acme." in block


def test_knowledge_block_is_empty_when_store_is_empty(monkeypatch):
    _stub_store(monkeypatch)
    assert prompt.build_knowledge_block() == ""


def test_knowledge_block_includes_every_section(monkeypatch):
    _stub_store(
        monkeypatch,
        memory="snapshot text",
        today="today text",
        topics=[{"filename": "topics/pricing.md", "headline": "How we price"}],
        dailies=[{"filename": "daily/2026-08-20.md", "headline": "Closed Acme"}],
    )
    block = prompt.build_knowledge_block()
    assert "snapshot text" in block
    assert "today text" in block
    assert "topics/pricing.md · How we price" in block
    assert "2026-08-20 · Closed Acme" in block


def test_manifest_headline_falls_back(monkeypatch):
    _stub_store(monkeypatch, topics=[{"filename": "topics/x.md", "headline": ""}])
    assert "(no summary yet)" in prompt.build_knowledge_block()


def test_truncation_happens_before_fencing(monkeypatch):
    """The finding this test exists for: capping the assembled string AFTER wrapping
    could cut off the closing tag, leaving injected text an open fence to escape
    through."""
    _stub_store(monkeypatch, memory="y" * (prompt.MAX_KNOWLEDGE_CHARS * 3))
    block = prompt.build_knowledge_block()
    nonce = block.split('id="', 1)[1].split('"', 1)[0]
    assert block.count(nonce) == 2, "the closing fence was severed by truncation"
    assert block.rstrip().endswith(f'</recorded_context id="{nonce}">')
    assert prompt._TRUNCATED in block


def test_knowledge_block_survives_a_store_outage(monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prompt.service, "read_file", explode)
    assert prompt.build_knowledge_block() == ""


def test_sections_render_deterministically(monkeypatch):
    """Same data, same rendering — only the nonce may differ turn to turn."""
    _stub_store(
        monkeypatch, memory="m", today="t",
        topics=[{"filename": "topics/a.md", "headline": "A"}],
    )
    first, second = prompt.build_knowledge_block(), prompt.build_knowledge_block()
    strip = lambda b: b.split("\n", 1)[1].rsplit("\n", 1)[0]  # noqa: E731 — drop the nonce lines
    assert strip(first) == strip(second)
