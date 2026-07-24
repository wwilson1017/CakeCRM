"""Smart-import: deterministic (keyless) paths + AI fallback + graceful no-key.

The acceptance split lives here: CSV/vCard parse with ZERO AI keys, the AI
branch only runs when a provider is configured, and a missing provider yields a
clean warning (never an exception).
"""

from crm import smart_import


class FakeProvider:
    """Streams canned text events then _turn_complete, mirroring the real
    stream_turn contract _call_ai consumes."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_turn(self, messages, tools, system_prompt):
        for c in self._chunks:
            yield {"type": "text", "text": c}
        yield {"type": "_turn_complete", "tool_calls": [], "stop_reason": "stop"}


async def test_csv_deterministic_is_keyless(monkeypatch):
    # If the deterministic CSV branch ran, get_ai_provider must never be touched.
    monkeypatch.setattr(smart_import, "get_ai_provider",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI called")))
    result = await smart_import.parse_contacts("Name,Email\nAda,ada@x.io\n", "contacts.csv")
    assert result.ai_used is False
    assert any(c["name"] == "Ada" for c in result.contacts)


async def test_vcard_is_keyless(monkeypatch):
    monkeypatch.setattr(smart_import, "get_ai_provider",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI called")))
    vcf = "BEGIN:VCARD\nVERSION:3.0\nFN:Grace Hopper\nEMAIL:grace@navy.mil\nEND:VCARD\n"
    result = await smart_import.parse_contacts(vcf, "card.vcf")
    assert result.ai_used is False
    assert result.contacts and result.contacts[0]["name"] == "Grace Hopper"


async def test_no_provider_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(smart_import, "get_ai_provider", lambda *a, **k: None)
    result = await smart_import.parse_contacts("just some freeform text, no structure", "notes.txt")
    assert result.ai_used is False
    assert result.contacts == []
    assert result.warnings and "No AI provider configured" in result.warnings[0]


async def test_ai_path_parses_json_array(monkeypatch):
    provider = FakeProvider(['[{"name": "Ada Lovelace", "email": "ada@x.io"}]'])
    monkeypatch.setattr(smart_import, "get_ai_provider", lambda *a, **k: provider)
    result = await smart_import.parse_contacts("weird proprietary format", "data.dat")
    assert result.ai_used is True
    assert result.contacts[0]["name"] == "Ada Lovelace"
    assert result.contacts[0]["email"] == "ada@x.io"


async def test_ai_path_handles_fenced_json(monkeypatch):
    provider = FakeProvider(['```json\n[{"name": "Bob"}]\n```'])
    monkeypatch.setattr(smart_import, "get_ai_provider", lambda *a, **k: provider)
    result = await smart_import.parse_contacts("blob", "x.dat")
    assert result.ai_used is True
    assert result.contacts[0]["name"] == "Bob"


async def test_csv_branch_never_calls_ai(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("_parse_with_ai must not run for a .csv with a name column")

    monkeypatch.setattr(smart_import, "_parse_with_ai", _boom)
    result = await smart_import.parse_contacts("Name,Company\nZaha,ZHA\n", "firms.csv")
    assert result.ai_used is False
    assert result.contacts[0]["name"] == "Zaha"


async def test_empty_content_warns(monkeypatch):
    result = await smart_import.parse_contacts("   ", "empty.txt")
    assert result.contacts == [] and result.warnings


# ── vCard edge cases (keyless path used by real address-book exports) ──────────

async def test_vcard_n_fallback_when_no_fn():
    vcf = "BEGIN:VCARD\nVERSION:3.0\nN:Hopper;Grace;;;\nEMAIL:grace@navy.mil\nEND:VCARD\n"
    result = await smart_import.parse_contacts(vcf, "c.vcf")
    assert result.contacts[0]["name"] == "Grace Hopper"


async def test_vcard_multiple_entries():
    vcf = (
        "BEGIN:VCARD\nFN:Ada Lovelace\nEMAIL:ada@x.io\nEND:VCARD\n"
        "BEGIN:VCARD\nFN:Alan Turing\nEMAIL:alan@x.io\nEND:VCARD\n"
    )
    result = await smart_import.parse_contacts(vcf, "c.vcf")
    assert {c["name"] for c in result.contacts} == {"Ada Lovelace", "Alan Turing"}


async def test_vcard_line_folding_reconstructs_value():
    # RFC 6350 folding: a continuation line begins with a space; unfolding joins
    # it back (the fold marker space is consumed), so "Gr\n ace" -> "Grace".
    vcf = "BEGIN:VCARD\nFN:Gr\n ace Hopper\nEMAIL:g@x.io\nEND:VCARD\n"
    result = await smart_import.parse_contacts(vcf, "c.vcf")
    assert result.contacts[0]["name"] == "Grace Hopper"


async def test_vcard_quoted_printable_decodes():
    vcf = ("BEGIN:VCARD\nFN:Test\nNOTE;ENCODING=QUOTED-PRINTABLE:caf=C3=A9\n"
           "EMAIL:t@x.io\nEND:VCARD\n")
    result = await smart_import.parse_contacts(vcf, "c.vcf")
    assert result.contacts[0]["notes"] == "café"


async def test_vcard_grouped_property_captured():
    # Apple/iOS exports emit grouped props like "item1.EMAIL" — the group prefix
    # must be stripped so the value isn't silently dropped.
    vcf = "BEGIN:VCARD\nFN:Ada\nitem1.EMAIL;type=INTERNET:ada@x.io\nEND:VCARD\n"
    result = await smart_import.parse_contacts(vcf, "c.vcf")
    assert result.contacts[0]["email"] == "ada@x.io"


async def test_vcard_malformed_line_skipped_not_raised():
    vcf = "BEGIN:VCARD\nthislinehasnocolon\nFN:Ada\nEMAIL:ada@x.io\nEND:VCARD\n"
    result = await smart_import.parse_contacts(vcf, "c.vcf")
    assert result.contacts[0]["name"] == "Ada"  # bad line ignored, contact still parsed


async def test_ai_failure_degrades_to_warning(monkeypatch):
    class BoomProvider:
        async def stream_turn(self, messages, tools, system_prompt):
            raise RuntimeError("provider exploded")
            yield  # pragma: no cover  (make it an async generator)

    monkeypatch.setattr(smart_import, "get_ai_provider", lambda *a, **k: BoomProvider())
    # A configured-but-failing provider must NOT raise → clean warning result.
    result = await smart_import.parse_contacts("some unknown format", "data.dat")
    assert result.contacts == []
    assert result.warnings and "AI parsing failed" in result.warnings[0]
