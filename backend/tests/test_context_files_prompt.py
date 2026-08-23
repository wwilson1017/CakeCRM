"""context_files/prompt.py — what actually reaches the model, and what must not.

The load-bearing invariants: the soul goes in unfenced, everything else goes in fenced,
the cap is applied BEFORE fencing (truncating after could sever a closing nonce tag and
leave the fence open), and a store outage degrades to a context-less turn rather than a
broken chat.
"""

from context_files import prompt, service


def _stub_store(monkeypatch, *, soul="", memory="", today="", topics=(), dailies=()):
    """Stands in for the real store, MIRRORING read_file's blank-soul fallback — the
    fallback lives in the service so the prompt, the assistant's own read tool and the
    Memory editor all agree, and a stub that skipped it would test a contract nothing
    ships with."""
    from assistant.identity import DEFAULT_SOUL

    files = {service.SOUL_FILE: soul or DEFAULT_SOUL, service.MEMORY_FILE: memory}
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


def test_blank_soul_falls_back_to_the_built_in_default(monkeypatch):
    """The migration seeds soul.md EMPTY so a boot can never overwrite a rewritten soul,
    which means the built-in text has to be applied at READ time. Without this the
    constant is dead code and a fresh install ships with no soul at all."""
    from assistant.identity import DEFAULT_SOUL

    _stub_store(monkeypatch, soul="")
    block = prompt.build_soul_block()
    assert DEFAULT_SOUL[:40] in block


def test_a_written_soul_replaces_the_default(monkeypatch):
    from assistant.identity import DEFAULT_SOUL

    _stub_store(monkeypatch, soul="I have my own words now.")
    block = prompt.build_soul_block()
    assert "I have my own words now." in block
    assert DEFAULT_SOUL[:40] not in block


def test_soul_block_is_capped(monkeypatch):
    _stub_store(monkeypatch, soul="x" * (prompt.MAX_SOUL_CHARS + 5_000))
    block = prompt.build_soul_block()
    assert prompt._TRUNCATED in block
    assert len(block) < prompt.MAX_SOUL_CHARS + 500


def test_soul_block_survives_a_store_outage(monkeypatch):
    """Degrades to the built-in soul rather than to no identity at all."""
    from assistant.identity import DEFAULT_SOUL

    def explode(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prompt.service, "read_file", explode)
    assert DEFAULT_SOUL[:40] in prompt.build_soul_block()


def test_a_huge_memory_cannot_evict_the_manifests(monkeypatch):
    """With one shared budget the first section eats everything: a 20k MEMORY.md would
    truncate both manifests to nothing, silently disabling the mechanism that makes
    un-loaded files discoverable."""
    _stub_store(
        monkeypatch,
        memory="m" * 100_000,
        today="t" * 100_000,
        topics=[{"filename": "topics/pricing.md", "headline": "How we price"}],
        dailies=[{"filename": "daily/2026-08-20.md", "headline": "Closed Acme"}],
    )
    block = prompt.build_knowledge_block()
    assert "topics/pricing.md" in block, "the topic manifest was evicted by a large body"
    assert "2026-08-20" in block, "the daily manifest was evicted by a large body"
    assert prompt._TRUNCATED in block


def test_the_overall_cap_cannot_bite_before_the_section_caps(monkeypatch):
    """Arithmetic, not vibes: if the sum of the section budgets exceeded the overall cap,
    the LAST section (the daily manifest) would be silently evicted whenever every
    earlier section was full — the bug the per-section caps were added to fix."""
    assert (
        prompt.MAX_MEMORY_CHARS + prompt.MAX_TODAY_CHARS
        + prompt.MAX_TOPIC_MANIFEST_CHARS + prompt.MAX_DAILY_MANIFEST_CHARS
    ) <= prompt.MAX_KNOWLEDGE_CHARS


def test_maximum_length_manifests_still_both_appear(monkeypatch):
    """40 topic entries at maximum filename+headline length is ~10k on its own, so an
    entry cap alone does not bound the section."""
    long_name = "t" * 100
    long_headline = "h" * 120
    _stub_store(
        monkeypatch,
        memory="m" * 100_000,
        today="t" * 100_000,
        topics=[{"filename": f"topics/{long_name}{i}.md", "headline": long_headline}
                for i in range(200)],
        dailies=[{"filename": f"daily/2026-01-{i:02d}.md", "headline": long_headline}
                 for i in range(1, 31)],
    )
    block = prompt.build_knowledge_block()
    assert "Recent daily notes" in block, "the daily manifest was evicted"
    assert "2026-01-" in block


def test_manifest_entry_counts_are_bounded(monkeypatch):
    _stub_store(
        monkeypatch,
        topics=[{"filename": f"topics/t{i}.md", "headline": "h"} for i in range(500)],
        dailies=[{"filename": f"daily/2026-01-{i:02d}.md", "headline": "h"} for i in range(1, 32)],
    )
    block = prompt.build_knowledge_block()
    assert block.count("topics/t") <= prompt.MAX_TOPIC_MANIFEST_ENTRIES
    assert block.count("- 2026-01-") <= prompt.MAX_DAILY_MANIFEST_ENTRIES


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
