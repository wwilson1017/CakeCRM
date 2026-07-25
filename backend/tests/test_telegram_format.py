"""Telegram markdown→HTML conversion and source-markdown chunking (pure, no I/O)."""

from telegram.format import chunk_text, escape_html, markdown_to_telegram_html


def test_bold_italic_code():
    html = markdown_to_telegram_html("**bold** and *italic* and `code`")
    assert "<b>bold</b>" in html
    assert "<i>italic</i>" in html
    assert "<code>code</code>" in html


def test_escapes_html_special_chars_in_plain_text():
    html = markdown_to_telegram_html("a < b & c > d")
    assert "&lt;" in html and "&amp;" in html and "&gt;" in html
    assert "<b>" not in html  # nothing was bolded


def test_fenced_code_block_preserved_and_escaped():
    html = markdown_to_telegram_html("```python\nx = a < b\n```")
    assert "<pre>" in html and "language-python" in html
    assert "&lt;" in html  # the < inside code is escaped, not a tag


def test_link_rendered():
    html = markdown_to_telegram_html("[CakeCRM](https://example.com)")
    assert '<a href="https://example.com">CakeCRM</a>' in html


def test_empty_string_passthrough():
    assert markdown_to_telegram_html("") == ""


def test_escape_html_helper():
    assert escape_html("<script>&</script>") == "&lt;script&gt;&amp;&lt;/script&gt;"


def test_chunk_short_text_single_chunk():
    assert chunk_text("hello") == ["hello"]


def test_chunk_splits_on_boundaries_and_stays_within_limit():
    para = "word " * 400  # ~2000 chars
    text = para + "\n\n" + para + "\n\n" + para  # ~6000 chars
    chunks = chunk_text(text, max_length=2500)
    assert len(chunks) > 1
    assert all(len(c) <= 2500 for c in chunks)
    # No content lost (modulo whitespace trimming at split points).
    assert "".join(c.replace(" ", "") for c in chunks) == text.replace(" ", "").replace("\n", "")


def test_chunk_hard_split_when_no_boundary():
    text = "x" * 5000  # no spaces/newlines at all
    chunks = chunk_text(text, max_length=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_never_emits_empty_chunk_on_leading_boundary():
    # A leading blank line before an unbroken run longer than max_length used to yield
    # an empty first chunk (split_at == 0) → an empty Telegram message → non-parse 400 →
    # aborted send loop → truncated reply. Guarded now.
    text = "\n" + ("x" * 3000)
    chunks = chunk_text(text, max_length=1000)
    assert all(c for c in chunks)  # no empty chunks
    assert all(len(c) <= 1000 for c in chunks)


def test_link_href_double_quote_is_escaped():
    # A double-quote in the URL must be encoded so it can't break out of the href="".
    html = markdown_to_telegram_html('[x](http://e.com/a"onmouseover=1)')
    assert "&quot;" in html
