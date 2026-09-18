#!/usr/bin/env python3
"""Render .datacore/specs markdown into a single self-contained local HTML page.

Why this exists
---------------
`.datacore/specs/` already had one hand-written HTML companion
(`task-lifecycle-and-ledger-coverage.html`) and nothing that could produce the
next one. Hand-writing the second is how a house style becomes two house
styles. This reads the markdown that is already the source of truth and emits
the page, so the page can never drift from the spec — regenerate instead of
editing the HTML.

Local only. It writes a file next to the spec; it publishes nothing.

    python3 .datacore/lib/spec_render.py specs/a.md [specs/b.md ...] \
        --out .datacore/specs/a.html --title "Page title"
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

try:
    import markdown
except ImportError:
    sys.exit("python-markdown required: pip install markdown")

# The palette is lifted from task-lifecycle-and-ledger-coverage.html so the two
# pages read as one set. Light only, deliberately: these are printed and pasted.
CSS = """
:root { color-scheme: light;
  --ink:#2d3142; --slate:#4f5d75; --faint:#8d99ae; --accent:#eb6c36;
  --bg:#f5f5f5; --card:#ffffff; --rule:#d8d8dc; --wash:#ecedf0; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); line-height:1.62;
  font-family: ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:15px; }
.shell { display:grid; grid-template-columns: 250px minmax(0,1fr); gap:44px;
  max-width:1220px; margin:0 auto; padding:34px 22px 90px; }
nav { position:sticky; top:26px; align-self:start; max-height:calc(100vh - 60px);
  overflow-y:auto; font-size:12.6px; line-height:1.5; }
nav .navtitle { font-size:10.5px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--faint); margin-bottom:11px; }
nav a { display:block; color:var(--slate); text-decoration:none;
  padding:3px 0 3px 10px; border-left:2px solid var(--rule); }
nav a:hover { color:var(--accent); border-left-color:var(--accent); }
nav a.part { border-left:none; padding-left:0; margin-top:15px; color:var(--ink);
  font-weight:650; font-size:11px; letter-spacing:.07em; text-transform:uppercase; }
main { min-width:0; }
h1 { font-family:"Iowan Old Style",Palatino,Georgia,serif; font-size:33px;
  line-height:1.2; margin:0 0 6px; font-weight:600; }
h2 { font-family:"Iowan Old Style",Palatino,Georgia,serif; font-size:23px;
  margin:46px 0 12px; padding-top:20px; border-top:1px solid var(--rule);
  font-weight:600; scroll-margin-top:22px; }
h3 { font-size:15.5px; margin:28px 0 8px; font-weight:650; }
p, li { max-width:74ch; }
a { color:var(--accent); }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.6px;
  background:var(--wash); padding:1px 4px; border-radius:3px; }
pre { background:var(--card); border:1px solid var(--rule); border-left:3px solid var(--slate);
  border-radius:5px; padding:13px 15px; overflow-x:auto; }
pre code { background:none; padding:0; font-size:12.2px; line-height:1.55; }
table { border-collapse:collapse; margin:16px 0; font-size:13.4px; width:100%; }
th { text-align:left; border-bottom:1.5px solid var(--ink); padding:7px 11px 7px 0;
  font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--slate); }
td { border-bottom:1px solid var(--rule); padding:7px 11px 7px 0; vertical-align:top; }
tr:hover td { background:var(--card); }
blockquote { margin:18px 0; padding:2px 0 2px 16px; border-left:3px solid var(--accent);
  color:var(--slate); }
hr { border:none; border-top:1px solid var(--rule); margin:38px 0; }
strong { font-weight:650; }
em { color:var(--slate); }
.lede { font-size:14px; color:var(--slate); max-width:74ch; }
.partrule { margin:64px 0 0; padding:26px 0 0; border-top:2px solid var(--ink); }
.partrule h1 { font-size:27px; }
footer { grid-column:1/-1; margin-top:54px; padding-top:15px; border-top:1px solid var(--rule);
  font-size:11.6px; color:var(--faint); }
@media (max-width:900px) { .shell { grid-template-columns:1fr; gap:0; }
  nav { position:static; max-height:none; margin-bottom:30px; } }
@media print { nav { display:none; } .shell { grid-template-columns:1fr; } }
"""

SLUG_STRIP = re.compile(r"[^a-z0-9\s-]")


def slug(text: str) -> str:
    t = SLUG_STRIP.sub("", text.lower()).strip()
    return re.sub(r"[\s-]+", "-", t)


def convert(md_text: str) -> tuple[str, list[tuple[str, str]]]:
    """Markdown to HTML, returning the body and the h2 table of contents."""
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    body = md.convert(md_text)
    toc: list[tuple[str, str]] = []

    def anchor(m: re.Match) -> str:
        raw = re.sub(r"<[^>]+>", "", m.group(1))
        sid = slug(html.unescape(raw))
        toc.append((sid, html.unescape(raw)))
        return f'<h2 id="{sid}">{m.group(1)}</h2>'

    body = re.sub(r"<h2>(.*?)</h2>", anchor, body, flags=re.S)
    return body, toc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+", help="markdown files, rendered in order as parts")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    parts, nav = [], []
    for i, src in enumerate(a.sources):
        text = Path(src).read_text()
        # The first h1 of each part becomes a nav group heading.
        m = re.match(r"#\s+(.+)", text)
        part_title = m.group(1).strip() if m else Path(src).stem
        body, toc = convert(text)
        nav.append(f'<a class="part" href="#part-{i}">{html.escape(part_title)}</a>')
        nav += [f'<a href="#{sid}">{html.escape(t)}</a>' for sid, t in toc]
        cls = "partrule" if i else ""
        parts.append(f'<section id="part-{i}" class="{cls}">{body}</section>')

    title = a.title or re.sub(r"<[^>]+>", "", parts[0].split("</h1>")[0]).lstrip("<h1>")
    out = Path(a.out)
    out.write_text(
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{CSS}</style></head>\n<body><div class=\"shell\">\n"
        f'<nav><div class="navtitle">Contents</div>{"".join(nav)}</nav>\n'
        f'<main>{"".join(parts)}</main>\n'
        f"<footer>Generated from {', '.join(a.sources)} by "
        "<code>.datacore/lib/spec_render.py</code>. Edit the markdown and "
        "regenerate — do not edit this file.</footer>\n"
        "</div></body></html>\n"
    )
    print(f"  wrote  {out}  ({out.stat().st_size // 1024} KB, {len(nav)} nav entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
