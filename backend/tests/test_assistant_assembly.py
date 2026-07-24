"""Server-side assembler — delimiter-safe truncation, coalescing, reconstruction.

Hermetic: exercises the REAL assembly functions (the engine tests monkeypatch
assemble_messages away, so these are its only unit coverage). The truncation-fence
test is the enforcement point for the upload prompt-injection mitigation.
"""

from assistant import assembly

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
