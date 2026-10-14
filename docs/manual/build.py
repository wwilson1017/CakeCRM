"""Build the user manual: docs/manual/pages/**/*.md -> website/manual/index.html (+ shots/*.webp).

    pip install -r backend/requirements-dev.txt   # Python-Markdown; Pillow comes from requirements.txt
    python docs/manual/build.py

Screenshots: `node docs/manual/shoot.mjs --seed` writes raw 2x PNGs to the git-ignored docs/manual/shots/;
this script frames each one like the README captures (scripts/frame_readme_shots.py), caps it at
MAX_WIDTH and writes website/manual/shots/<name>.webp. A PNG that isn't on this machine keeps its
committed webp, so a prose-only edit needs no screenshots. The HTML half (build_html) is pure and
deterministic: backend/tests/test_manual.py rebuilds it and compares with the committed file.
"""
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PAGES, SHOTS = HERE / "pages", HERE / "shots"
OUT = ROOT / "website" / "manual"
OUT_SHOTS = OUT / "shots"
MAX_WIDTH, QUALITY = 1400, 80
DARK_SHOTS = {"dark-mode"}  # framed on the dark ground

SECTION_TITLES = {
    "getting-started": "Getting started", "dashboard": "Dashboard", "pipeline": "Pipeline",
    "contacts-and-companies": "Contacts & companies", "todos": "Todos", "reports": "Reports",
    "baker": "Baker, the assistant", "settings": "Settings", "integrations": "Integrations",
    "self-hosting": "Self-hosting",
}
FRONT = re.compile(r"---\n(.*?)\n---\n", re.S)
SHOT = re.compile(r"^\{\{shot:([^}]+)\}\}\s*$", re.M)
LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")


def parse_front(text):
    m = FRONT.match(text)
    if not m:
        raise ValueError("missing --- front matter ---")
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        meta[k.strip()] = [s.strip() for s in v[1:-1].split(",") if s.strip()] if v.startswith("[") else v
    return meta, text[m.end():]


def strip_order(name):
    return re.sub(r"^\d+-", "", name)


def load_pages(pages_dir=PAGES):
    pages = []
    for f in sorted(pages_dir.rglob("*.md")):
        rel = f.relative_to(pages_dir).with_suffix("")
        meta, body = parse_front(f.read_text())
        section = None if rel.name == "00-index" else strip_order(rel.parent.name)
        pages.append(dict(src=rel.as_posix(), route="" if section is None else f"{section}/{strip_order(rel.name)}",
                          section=section, title=meta.get("title", ""), summary=meta.get("summary", ""),
                          shots=meta.get("shots", []), body=body))
    return pages


def shots_in_body(body):
    return [m.strip() for m in SHOT.findall(body)]


def missing_shots(page, shots_dir=OUT_SHOTS):
    return [n for n in dict.fromkeys(page["shots"] + shots_in_body(page["body"])) if not (shots_dir / f"{n}.webp").exists()]


def _size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def render(page, by_src, shots_dir=OUT_SHOTS):
    import markdown

    def link(m):
        text, target = m.group(1), m.group(2)
        path, _, anchor = target.partition("#")
        if re.match(r"https?:|mailto:", target) or not path:
            return m.group(0)
        key = re.sub(r"[^/]+/\.\./", "", (Path(page["src"]).parent / path).as_posix()).removesuffix(".md")
        if key not in by_src:
            raise ValueError(f"unresolved link in {page['src']}: {target}")
        return f"[{text}](#/{by_src[key]})"

    def shot(m):
        name = m.group(1).strip()
        f = shots_dir / f"{name}.webp"
        if not f.exists():
            raise ValueError(f"missing screenshot in {page['src']}: {name} (run shoot.mjs, then build.py)")
        w, h = _size(f)
        return f'\n<figure class="shot"><img data-shot="{name}" width="{w}" height="{h}" alt="{html.escape(page["title"])} screenshot"></figure>\n'

    body = LINK.sub(link, page["body"])
    body = SHOT.sub(shot, body)
    out = markdown.markdown(body, extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    return out.replace("<table>", '<div class="wrap"><table>').replace("</table>", "</table></div>")


def build_html(pages, shots_dir=OUT_SHOTS, template=HERE / "template.html"):
    by_src = {p["src"]: p["route"] for p in pages}
    sections = []
    for p in pages:
        p["html"] = render(p, by_src, shots_dir)
        if p["section"]:
            if not sections or sections[-1]["id"] != p["section"]:
                sections.append(dict(id=p["section"], title=SECTION_TITLES[p["section"]], pages=[]))
            sections[-1]["pages"].append(p)
    esc = html.escape
    nav = "".join(
        f'<details data-sec="{s["id"]}"><summary><span class="n">{i}</span>{esc(s["title"])}</summary><ul>'
        + "".join(f'<li><a href="#/{q["route"]}" data-route="{q["route"]}">{esc(q["title"])}</a></li>' for q in s["pages"])
        + "</ul></details>" for i, s in enumerate(sections, 1))
    cards = "".join(
        f'<a class="card" href="#/{s["pages"][0]["route"]}"><span class="n">{i}</span><strong>{esc(s["title"])}</strong>'
        f'<span class="count">{len(s["pages"])} page{"s" if len(s["pages"]) > 1 else ""}</span>'
        f'<span class="list">{" · ".join(esc(q["title"]) for q in s["pages"][:6])}{" …" if len(s["pages"]) > 6 else ""}</span></a>'
        for i, s in enumerate(sections, 1))
    home = next(p for p in pages if not p["route"])
    articles = [f'<article data-route="" data-title="{esc(home["title"])}"><p class="eyebrow">Manual</p><h1>{esc(home["title"])}</h1>'
                f'<p class="lead">{esc(home["summary"])}</p><div class="cards">{cards}</div>{home["html"]}</article>']
    order = [p for s in sections for p in s["pages"]]
    for i, p in enumerate(order):
        sec = next(s for s in sections if s["id"] == p["section"])
        prev_p, next_p = (order[i - 1] if i else None), (order[i + 1] if i + 1 < len(order) else None)
        pn = ('<nav class="pn">' + (f'<a href="#/{prev_p["route"]}"><small>Previous</small>{esc(prev_p["title"])}</a>' if prev_p else "<span></span>")
              + (f'<a class="next" href="#/{next_p["route"]}"><small>Next</small>{esc(next_p["title"])}</a>' if next_p else "<span></span>") + "</nav>")
        articles.append(
            f'<article data-route="{p["route"]}" data-title="{esc(p["title"])}" hidden>'
            f'<p class="crumb"><a href="#/">Manual</a> › <a href="#/{sec["pages"][0]["route"]}">{esc(sec["title"])}</a></p>'
            f'<h1>{esc(p["title"])}</h1><p class="lead">{esc(p["summary"])}</p>{p["html"]}{pn}</article>')
    used = sorted({n for p in pages for n in p["shots"] + shots_in_body(p["body"])})
    shots = {n: f"shots/{n}.webp" for n in used}
    return (template.read_text().replace("{{NAV}}", nav).replace("{{ARTICLES}}", "\n".join(articles))
            .replace("{{SHOTS}}", json.dumps(shots, sort_keys=True)))


def encode_webp(name):
    """Frame a raw 2x PNG and write the committed webp; keeps the committed webp when the PNG isn't here."""
    src, out = SHOTS / f"{name}.png", OUT_SHOTS / f"{name}.webp"
    if not src.exists() or (out.exists() and out.stat().st_mtime >= src.stat().st_mtime):
        return
    from PIL import Image
    sys.path.insert(0, str(ROOT / "scripts"))
    from frame_readme_shots import frame
    img = frame(Image.open(src), "dark" if name in DARK_SHOTS else "light")
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    img.save(out, "WEBP", quality=QUALITY, method=6)


def main():
    pages = load_pages()
    used = sorted({n for p in pages for n in p["shots"] + shots_in_body(p["body"])})
    OUT_SHOTS.mkdir(parents=True, exist_ok=True)
    for n in used:
        encode_webp(n)
    for stale in sorted(OUT_SHOTS.glob("*.webp")):
        if stale.stem not in used:
            stale.unlink()
    (OUT / "index.html").write_text(build_html(pages), newline="\n")
    print(f"website/manual/index.html: {len(pages)} pages, {len(used)} screenshots")


if __name__ == "__main__":
    main()
