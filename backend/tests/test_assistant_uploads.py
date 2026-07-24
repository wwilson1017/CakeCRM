"""Assistant upload extraction — text/pdf/docx/xlsx, guards, delimiter fencing.

Heavy parsers are faked via ``sys.modules`` so the lazy-import seam is exercised
without the real libraries. A separate AST tripwire proves nothing in the
assistant package imports those parsers at module top level.
"""

import ast
from pathlib import Path

import pytest

from assistant import uploads
from assistant.uploads import UploadError, extract_upload


def test_plain_text_wrapped_with_matched_nonce():
    out = extract_upload("notes.txt", b"hello world")
    assert "hello world" in out
    # nonce appears in BOTH the open and close tag (forgery-proof fence)
    open_id = out.split('id="', 1)[1].split('"', 1)[0]
    assert f'<untrusted_file_content id="{open_id}"' in out
    assert f'</untrusted_file_content id="{open_id}">' in out


def test_latin1_fallback():
    out = extract_upload("n.txt", "café".encode("latin-1"))
    assert "caf" in out  # decoded, not crashed


def test_binary_content_rejected():
    with pytest.raises(UploadError):
        extract_upload("n.txt", b"real\x00binary")


def test_disallowed_extension_rejected():
    with pytest.raises(UploadError):
        extract_upload("virus.exe", b"data")


def test_empty_extraction_placeholder():
    out = extract_upload("blank.txt", b"   ")
    assert "No extractable text found" in out


def test_truncation_footer_inside_wrapper():
    big = ("A" * (uploads.MAX_CHARS + 100)).encode()
    out = extract_upload("big.txt", big)
    assert "(truncated at 50,000 characters)" in out
    assert out.rstrip().endswith('">')  # footer sits INSIDE the close tag


def test_filename_is_escaped():
    out = extract_upload('a".txt', b"x")
    assert 'a".txt' not in out  # quote escaped, can't break out of the attribute


# ── Lazy-parser branches via faked sys.modules ────────────────────────────────

class _FakePage:
    def __init__(self, text):
        self._t = text

    def extract_text(self):
        return self._t


class _FakePdf:
    def __init__(self, pages):
        self.pages = [_FakePage(p) for p in pages]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_pdf_branch(monkeypatch):
    fake = type("m", (), {"open": staticmethod(lambda _f: _FakePdf(["page one", "page two"]))})
    monkeypatch.setitem(__import__("sys").modules, "pdfplumber", fake)
    out = extract_upload("doc.pdf", b"%PDF-fake")
    assert "page one" in out and "page two" in out


def test_docx_branch(monkeypatch):
    class _P:
        def __init__(self, t):
            self.text = t

    class _Doc:
        paragraphs = [_P("first line"), _P("second line")]
        tables = []

    fake = type("m", (), {"Document": staticmethod(lambda _f: _Doc())})
    monkeypatch.setitem(__import__("sys").modules, "docx", fake)
    out = extract_upload("d.docx", b"PKfake")
    assert "first line" in out and "second line" in out


def test_xlsx_branch(monkeypatch):
    class _WS:
        def iter_rows(self, values_only=True):
            yield ("a", 1, None)
            yield ("b", 2, None)

    class _WB:
        sheetnames = ["Sheet1"]

        def __getitem__(self, _k):
            return _WS()

        def close(self):
            pass

    fake = type("m", (), {"load_workbook": staticmethod(lambda *a, **k: _WB())})
    monkeypatch.setitem(__import__("sys").modules, "openpyxl", fake)
    out = extract_upload("s.xlsx", b"PKfake")
    assert "a\t1" in out and "b\t2" in out


def test_corrupt_pdf_raises_upload_error(monkeypatch):
    def _boom(_f):
        raise ValueError("not a pdf")

    fake = type("m", (), {"open": staticmethod(_boom)})
    monkeypatch.setitem(__import__("sys").modules, "pdfplumber", fake)
    with pytest.raises(UploadError):
        extract_upload("x.pdf", b"garbage")


def test_no_top_level_parser_imports():
    """pdfplumber / docx / openpyxl must be imported lazily inside functions only."""
    roots = {"pdfplumber", "docx", "openpyxl"}
    assistant_dir = Path(__file__).resolve().parent.parent / "assistant"
    offenders = []
    for path in sorted(assistant_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # top-level only; function-nested imports excluded
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            offenders += [f"{path.name}:{node.lineno} {n}" for n in names if n.split(".")[0] in roots]
    assert not offenders, f"parsers must be lazy-imported: {offenders}"
