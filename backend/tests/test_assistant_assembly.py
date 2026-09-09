"""Server-side assembler — delimiter-safe truncation, coalescing, reconstruction.

Hermetic: exercises the REAL assembly functions (the engine tests monkeypatch
assemble_messages away, so these are its only unit coverage). The truncation-fence
test is the enforcement point for the upload prompt-injection mitigation.
"""

from assistant import assembly, delimiters

_NONCE = "abcd1234ef567890"  # 16 hex chars, like secrets.token_hex(8)


def _block(body: str) -> str:
    return (
        f'<untrusted_file_content id="{_NONCE}" filename="f.txt">\n'
        f"{body}\n"
        f'</untrusted_file_content id="{_NONCE}">'
    )


def test_truncate_user_content_recloses_open_upload_block():
    """A cut that lands inside an upload block must re-append the close tag so
    the untrusted content can't escape its fence."""
    block = _block("X" * 200)
    # cut short enough to drop the trailing close tag
    out = assembly._truncate_user_content(block, len(block) - 40)
    assert f'</untrusted_file_content id="{_NONCE}">' in out  # fence restored
    assert "[truncated]" in out


def test_truncate_user_content_no_double_close_when_block_intact():
    text = _block("data") + ("Y" * 500)  # block fully within the cut, tail trimmed
    out = assembly._truncate_user_content(text, len(_block("data")) + 100)
    assert out.count(f'</untrusted_file_content id="{_NONCE}">') == 1  # not duplicated


def test_truncate_user_content_short_text_unchanged():
    assert assembly._truncate_user_content("hi", 100) == "hi"


def test_coalesce_merges_consecutive_same_role():
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
    ]
    out = assembly._coalesce_consecutive(msgs)
    assert len(out) == 2
    assert out[0]["content"] == "a\n\nb"


def test_coalesce_leaves_tool_role_untouched():
    msgs = [
        {"role": "assistant", "content": "x"},
        {"role": "tool", "content": "r1"},
        {"role": "tool", "content": "r2"},
    ]
    out = assembly._coalesce_consecutive(msgs)
    assert len(out) == 3  # tool rows are one-per-tool_call, never merged


def test_coalesce_never_drops_tool_calls_openai_shape():
    """A narration assistant row followed by an OpenAI-shaped tool-call assistant
    row must NOT merge (which would drop tool_calls and orphan the tool message →
    OpenAI/Ollama/Together 400 the whole conversation)."""
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "content": "ra", "tool_call_id": "a"},
        {"role": "assistant", "content": "narration"},  # wrap-up narration row
        {"role": "assistant", "content": None, "tool_calls": [{"id": "b"}]},  # continuation tool row
        {"role": "tool", "content": "rb", "tool_call_id": "b"},
    ]
    out = assembly._coalesce_consecutive(msgs)
    # every role:'tool' must be immediately preceded by an assistant carrying tool_calls
    for i, m in enumerate(out):
        if m.get("role") == "tool":
            assert i > 0 and "tool_calls" in out[i - 1], f"orphaned tool message at {i}"


class _Provider:
    context_window = None

    def __init__(self, raise_on_build=False):
        self._raise = raise_on_build

    def build_tool_turn(self, text, tool_calls, results):
        if self._raise:
            raise ValueError("bad tool row")
        return [{"role": "assistant", "content": text or "(tool)"}, {"role": "user", "content": "tool_result"}]


def test_malformed_tool_row_falls_back_to_text(monkeypatch):
    conv = {"messages": [
        {"id": "m1", "role": "assistant", "content": "hello", "seq": 0,
         "tool_calls": [{"tool": "x", "tool_use_id": "t1", "args": {}}], "tool_results": []},
    ]}
    monkeypatch.setattr(assembly.history, "get_conversation", lambda cid: conv)
    out = assembly.assemble_messages(_Provider(raise_on_build=True), "c1")
    assert out == [{"role": "assistant", "content": "hello"}]  # graceful text fallback


def test_assemble_end_to_end(monkeypatch):
    conv = {"messages": [
        {"id": "u1", "role": "user", "content": "hi", "seq": 0, "tool_calls": None, "tool_results": None},
        {"id": "a1", "role": "assistant", "content": "", "seq": 1,
         "tool_calls": [{"tool": "crm_dashboard", "tool_use_id": "t1", "args": {}}],
         "tool_results": [{"tool_use_id": "t1", "tool_name": "crm_dashboard", "content": "{}"}]},
    ]}
    monkeypatch.setattr(assembly.history, "get_conversation", lambda cid: conv)
    out = assembly.assemble_messages(_Provider(), "c1")
    assert out[0] == {"role": "user", "content": "hi"}
    assert out[-1]["role"] == "user"  # ends on the reconstructed tool_result turn


def test_assemble_empty_when_no_conversation(monkeypatch):
    monkeypatch.setattr(assembly.history, "get_conversation", lambda cid: None)
    assert assembly.assemble_messages(_Provider(), "nope") == []


# ── Compaction (issue #72 Phase 3) ────────────────────────────────────────────

_SUMMARY_NONCE = "0123456789abcdef"


def _gist(body: str = "what happened earlier") -> str:
    return (
        f'<conversation_summary id="{_SUMMARY_NONCE}" reference_only="true">\n'
        f"{body}\n"
        f'</conversation_summary id="{_SUMMARY_NONCE}">'
    )


def _rows(n=6):
    return [
        {"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant",
         "content": f"row{i}", "seq": i, "tool_calls": None, "tool_results": None}
        for i in range(n)
    ]


def test_apply_compaction_is_a_no_op_without_a_stored_boundary():
    rows = _rows()
    assert assembly._apply_compaction(rows, None, None) is rows
    assert assembly._apply_compaction(rows, _gist(), None) is rows
    assert assembly._apply_compaction(rows, None, 4) is rows


def test_apply_compaction_declines_a_boundary_inside_the_head():
    """Nothing has aged past the head, so folding a gist on would duplicate context
    that is still present verbatim."""
    rows = _rows()
    assert assembly._apply_compaction(rows, _gist(), 1) is rows


def test_apply_compaction_keeps_the_head_and_folds_the_gist_onto_the_first_kept_turn():
    out = assembly._apply_compaction(_rows(6), _gist(), 4)
    assert [r["seq"] for r in out] == [0, 1, 4, 5]     # seqs 2,3 replaced by the gist
    assert out[0]["content"] == "row0"                  # head verbatim
    assert out[2]["content"].startswith(_gist())        # gist rides a REAL user turn
    assert out[2]["content"].endswith("row4")           # …and keeps what it said


def test_apply_compaction_adds_no_synthetic_turn_when_the_boundary_is_a_user_row():
    """Folding onto a real user row is what keeps role alternation valid on every
    provider — a standalone gist turn would be a second consecutive user message."""
    out = assembly._apply_compaction(_rows(6), _gist(), 4)
    assert sum(1 for r in out if r["role"] == "user") == 2


def test_apply_compaction_falls_back_to_a_standalone_turn_off_a_user_boundary():
    """Defensive: compaction snaps its boundary to a user row, so reaching this needs a
    hand-edited boundary — but the gist must still survive it."""
    out = assembly._apply_compaction(_rows(6), _gist(), 3)
    assert out[2]["role"] == "user"
    assert out[2]["content"] == _gist()
    assert out[3]["seq"] == 3


def test_truncation_recloses_a_summary_fence_a_cut_left_open():
    text = _gist("Z" * 400)
    out = assembly._truncate_user_content(text, len(text) - 40)
    assert f'</conversation_summary id="{_SUMMARY_NONCE}">' in out


def test_truncation_keeps_the_gist_when_it_fits_in_half_the_row():
    gist = _gist()
    merged = gist + "\n\n" + ("U" * 5000)
    out = assembly._truncate_user_content(merged, len(gist) * 4)
    assert out.startswith(gist)                       # gist intact, fence unbroken
    assert "UUUU" in out                              # and the request survives
    assert "[truncated]" in out


def test_an_oversized_gist_is_dropped_whole_so_the_request_survives():
    """The case a plain end-cut gets wrong: once the gist alone fills the row, cutting
    from the end leaves a truncated summary and NONE of what the user typed, and
    `_last_user_text` then strips the gist and finds only a truncation marker."""
    gist = _gist("G" * 400)
    merged = gist + "\n\n" + ("U" * 500)
    out = assembly._truncate_user_content(merged, len(gist))
    assert "conversation_summary" not in out          # dropped whole, never sliced
    assert out.startswith("UUUU")                     # the request got the whole budget
    assert delimiters.strip_conversation_summary(out).startswith("UUUU")


def test_truncation_of_a_row_with_no_gist_is_unchanged():
    assert assembly._truncate_user_content("plain", 100) == "plain"
    assert "[truncated]" in assembly._truncate_user_content("P" * 200, 50)


class _EchoProvider:
    context_window = 1000

    def build_tool_turn(self, text, calls, results):
        return [{"role": "assistant", "content": text}]


def test_the_assembled_gist_is_byte_identical_across_turns(monkeypatch):
    """The Anthropic provider applies cache_control to the conversation PREFIX, and the
    gist rides an early user turn. Re-wrapping per assembly would mint a fresh nonce and
    re-key that cache every single turn — so the fence is minted once, at write time."""
    conv = {
        "id": "c1", "messages": _rows(6),
        "compaction_summary": _gist(), "compaction_first_kept_seq": 4,
    }
    monkeypatch.setattr(assembly.history, "get_conversation", lambda cid: conv)
    first = assembly.assemble_messages(_EchoProvider(), "c1")
    second = assembly.assemble_messages(_EchoProvider(), "c1")
    assert first == second
    assert _SUMMARY_NONCE in str(first)
