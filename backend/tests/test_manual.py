"""The user manual (docs/manual -> website/manual, #309) stays whole and current.

A guard over docs/manual/pages: every screenshot a page declares or places exists as a committed
webp, no webp is orphaned, every cross-link resolves, and the committed website/manual/index.html
is exactly what docs/manual/build.py renders from the pages today, so a page edit without a
rebuild fails here. The prose being TRUE is convention, not a test: a PR that changes a
user-facing flow updates its manual page (AGENTS.md).
"""
import importlib.util
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("manual_build", ROOT / "docs" / "manual" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

PAGES = build.load_pages()
WEBPS = sorted(build.OUT_SHOTS.glob("*.webp"))


def _used(pages):
    return {n for p in pages for n in p["shots"] + build.shots_in_body(p["body"])}


@pytest.mark.parametrize("page", PAGES, ids=[p["src"] for p in PAGES])
def test_every_shot_a_page_uses_exists_and_is_declared(page):
    assert build.missing_shots(page) == []
    assert set(build.shots_in_body(page["body"])) == set(page["shots"]), "front-matter shots must match the {{shot:}} lines"
    assert page["title"] and page["summary"]


def test_no_orphan_screenshots():
    assert {p.stem for p in WEBPS} == _used(PAGES)


def test_routes_are_unique_and_sections_known():
    routes = [p["route"] for p in PAGES]
    assert len(routes) == len(set(routes))
    assert {p["section"] for p in PAGES if p["section"]} == set(build.SECTION_TITLES)


def test_committed_build_is_current():
    # Renders every page (which also fails on a broken cross-link or missing shot) and compares.
    assert build.build_html(PAGES) == (build.OUT / "index.html").read_text(), "run: python docs/manual/build.py"


def test_screenshot_budget():
    assert sum(p.stat().st_size for p in WEBPS) < 8 * 1024 * 1024
    for p in WEBPS:
        with Image.open(p) as im:
            assert im.width <= build.MAX_WIDTH, p.name


def test_the_guard_reached_the_real_manual():
    assert len(PAGES) >= 40 and len(WEBPS) >= 20
    assert any(p["route"] == "integrations/gmail" for p in PAGES)


# ── self-tests on synthetic input: each detector flags a bad case and passes a good one

def _page(tmp_path, name, text):
    (tmp_path / "pages" / "01-getting-started").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pages" / "01-getting-started" / name).write_text(text)


def test_the_shot_detector_flags_a_missing_shot(tmp_path):
    (tmp_path / "ok.webp").write_bytes(b"")
    good = dict(shots=["ok"], body="{{shot:ok}}\n")
    bad = dict(shots=["ok"], body="{{shot:ok}}\n{{shot:ghost}}\n")
    assert build.missing_shots(good, tmp_path) == []
    assert build.missing_shots(bad, tmp_path) == ["ghost"]


def test_front_matter_parses_lists():
    meta, body = build.parse_front("---\ntitle: T\nsummary: S\nshots: [a, b]\n---\nbody")
    assert meta == {"title": "T", "summary": "S", "shots": ["a", "b"]} and body == "body"


def test_a_broken_cross_link_fails_the_render(tmp_path):
    head = "---\ntitle: A\nsummary: S\nshots: []\n---\n"
    _page(tmp_path, "01-a.md", head + "See [B](02-b.md).\n")
    _page(tmp_path, "02-b.md", head + "See [nowhere](03-missing.md).\n")
    pages = build.load_pages(tmp_path / "pages")
    by_src = {p["src"]: p["route"] for p in pages}
    assert 'href="#/getting-started/b"' in build.render(pages[0], by_src, tmp_path)
    with pytest.raises(ValueError, match="unresolved link"):
        build.render(pages[1], by_src, tmp_path)
