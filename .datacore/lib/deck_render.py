#!/usr/bin/env python3
"""Render a markdown file into a single self-contained local HTML slide deck.

Why this exists
---------------
The slides module goes to Gamma (external, needs an API key) or to one-off PIL
scripts written per deck. Neither produces something you can open offline,
diff, or hand to someone without an account. A deck for explaining how the
system works should not depend on a service.

Content stays markdown, so the deck is editable and reviewable; the HTML is
generated. Regenerate, do not edit the HTML.

Conventions in the source markdown:
  ---            on its own line separates slides
  # Heading      slide title
  > quote        rendered as the "in plain words" callout
  ::: kicker     a line starting with "::: " becomes the small label above the title

    python3 .datacore/lib/deck_render.py deck.md --out deck.html --title "..."

Keys in the browser: arrows / space / j / k to move, Home, End, p to print.
"""
from __future__ import annotations

import argparse
import base64
import html
import re
import subprocess
import sys
from pathlib import Path

try:
    import markdown
except ImportError:
    sys.exit("python-markdown required: pip install markdown")



# ── Page sizes ───────────────────────────────────────────────────────────
# The printing box is set in CSS px and the @page in physical units; the two
# must describe the same rectangle (96 CSS px == 1 inch) or Chrome rescales
# and the auto-fit pass is measuring the wrong thing.
PAGES = {
    "a4":   {"px": (1122, 631), "size": "297mm 167mm"},    # A4 width, 16:9 — default
    "a4-full": {"px": (1122, 793), "size": "297mm 210mm"}, # true A4 landscape, 1.41:1
    "wide": {"px": (1920, 1080), "size": "20in 11.25in"},  # full-bleed projection
}

# ── Themes ───────────────────────────────────────────────────────────────
# `spec` matches .datacore/specs house style (light, for reading on screen).
# `datacore` is the product brand, lifted from 2-datacore/2-projects/website
# index.html :root — dark-first, Inter + JetBrains Mono, blue #3b82f6.
# Fonts load from Google at BUILD time; print-to-PDF embeds the glyphs, so the
# PDF stays portable even though the HTML is not.
THEMES = {
    "spec": {
        "font_link": "",
        "vars": """--ink:#2d3142; --slate:#4f5d75; --faint:#8d99ae; --accent:#eb6c36;
  --bg:#f5f5f5; --card:#ffffff; --rule:#d8d8dc; --wash:#ecedf0;
  --sans:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --display:"Iowan Old Style",Palatino,Georgia,serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;""",
        "scheme": "light",
    },
    "datacore-light": {
        # 2-datacore/2-projects/website index.html, [data-theme="light"].
        "font_link": '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
                     '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
                     '&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">',
        "vars": """--ink:#1e293b; --slate:#475569; --faint:#64748b; --accent:#2563eb;
  --bg:#e6f0fa; --card:#f0f7ff; --rule:#c7d9ed; --wash:#dbe8f5;
  --sans:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
  --display:'Inter',-apple-system,sans-serif;
  --mono:'JetBrains Mono',ui-monospace,Menlo,monospace;""",
        "scheme": "light",
    },
    "datacore": {
        "font_link": '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
                     '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
                     '&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">',
        "vars": """--ink:#ffffff; --slate:#a0a0a0; --faint:#666666; --accent:#3b82f6;
  --bg:#0a0a0a; --card:#111111; --rule:#222222; --wash:#1a1a1a;
  --sans:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
  --display:'Inter',-apple-system,sans-serif;
  --mono:'JetBrains Mono',ui-monospace,Menlo,monospace;""",
        "scheme": "dark",
    },
}

CSS = """
:root { color-scheme: __SCHEME__;
  __VARS__
}
* { box-sizing:border-box; }
html,body { margin:0; height:100%; background:var(--bg); color:var(--ink);
  font-family: var(--sans); }
#deck { height:100%; display:flex; align-items:center; justify-content:center; }
.slide { display:none; width:min(1120px,94vw); aspect-ratio:16/9; background:var(--card);
  border:1px solid var(--rule); border-radius:10px; padding:2.6em 3.1em;
  box-shadow:0 2px 20px rgba(45,49,66,.07); overflow:hidden;
  flex-direction:column; justify-content:flex-start;
  /* Every size below is em-relative, so the auto-fit pass can shrink a dense
     slide by changing this one value instead of clipping its content. */
  font-size:20px; }
.slide.on { display:flex; }
.slide { background-repeat:no-repeat; background-position:center;
  background-size:cover; }
.kicker { font-size:.6em; letter-spacing:.13em; text-transform:uppercase;
  color:var(--accent); font-weight:650; margin-bottom:.7em; }
.slide h1 { font-family:var(--display); font-weight:600;
  font-size:2em; line-height:1.15; margin:0 0 .5em; letter-spacing:-.01em; }
.slide.title h1 { font-size:2.7em; margin-top:auto; }
.slide.title .sub { font-size:1.05em; color:var(--slate); max-width:40ch; margin-bottom:auto; }
.slide h2 { font-size:.95em; margin:1.1em 0 .4em; font-weight:650; color:var(--slate); }
.slide p, .slide li { font-size:1em; line-height:1.52; max-width:62ch; }
.slide li { margin-bottom:.45em; }
.slide ul, .slide ol { padding-left:1.2em; margin:.5em 0; }
.slide li::marker { color:var(--faint); }
blockquote { margin:1em 0 0; padding:.8em 1em; background:var(--wash);
  border-left:4px solid var(--accent); border-radius:0 6px 6px 0; }
blockquote p { font-size:.93em; color:var(--ink); margin:0; max-width:64ch; }
blockquote strong { color:var(--accent); }
table { border-collapse:collapse; margin:.8em 0; width:100%; font-size:.83em; }
th { text-align:left; padding:.45em .8em .45em 0; border-bottom:1.5px solid var(--ink);
  font-size:.7em; letter-spacing:.07em; text-transform:uppercase; color:var(--slate); }
td { padding:.45em .8em .45em 0; border-bottom:1px solid var(--rule); vertical-align:top; }
code { font-family:var(--mono); font-size:.8em;
  background:var(--wash); padding:1px 5px; border-radius:3px; }
strong { font-weight:650; }
em { color:var(--slate); font-style:normal; }
hr { border:none; border-top:1px solid var(--rule); margin:.9em 0; }
#bar { position:fixed; left:0; top:0; height:3px; background:var(--accent);
  transition:width .18s ease; }
#hud { position:fixed; right:18px; bottom:14px; font-size:12px; color:var(--faint);
  font-family:var(--mono); user-select:none; }
#hud b { color:var(--slate); font-weight:650; }
/* Print geometry, also applied on screen under ?print=1 so the auto-fit pass
   measures the page it will actually be printed onto. */
html.printing, html.printing body { height:auto; background:var(--bg); }
html.printing #deck { display:block; height:auto; }
html.printing #bar, html.printing #hud { display:none; }
html.printing .slide { display:flex !important; width:__PW__px; height:__PH__px;
  aspect-ratio:auto; border:none; border-radius:0; box-shadow:none;
  padding:5% 6.25%; page-break-after:always; }
@media print {
  @page { size:__PSIZE__; margin:0; }
  html,body { height:auto; background:var(--bg); }
  #deck { display:block; height:auto; }
  #bar,#hud { display:none; }
  .slide { display:flex !important; width:100%; aspect-ratio:auto; height:__PH__px;
    border:none; border-radius:0; box-shadow:none; page-break-after:always; }
}
"""

JS = """
var s=[].slice.call(document.querySelectorAll('.slide')),i=0;
function fit(el){var px=el.clientWidth/56;el.style.fontSize=px+'px';
  while(el.scrollHeight>el.clientHeight+1&&px>8){px-=0.5;el.style.fontSize=px+'px';}}
window.addEventListener('resize',function(){fit(s[i]);});
/* Cmd+P must work as well as the p key: show and fit every slide first. */
window.addEventListener('beforeprint',function(){s.forEach(function(e){e.classList.add('on');fit(e);});});
window.addEventListener('afterprint',function(){s.forEach(function(e,k){e.classList.toggle('on',k===i);});});
function go(n){i=Math.max(0,Math.min(s.length-1,n));
  s.forEach(function(e,k){e.classList.toggle('on',k===i)});
  fit(s[i]);
  document.getElementById('bar').style.width=((i+1)/s.length*100)+'%';
  document.getElementById('cur').textContent=i+1;
  if(location.hash!=='#'+(i+1))history.replaceState(null,'','#'+(i+1));}
document.addEventListener('keydown',function(e){
  var k=e.key;
  if(k==='ArrowRight'||k==='ArrowDown'||k===' '||k==='j'||k==='PageDown')
    {e.preventDefault();go(i+1);}
  else if(k==='ArrowLeft'||k==='ArrowUp'||k==='k'||k==='PageUp'){e.preventDefault();go(i-1);}
  else if(k==='Home'){go(0);} else if(k==='End'){go(s.length-1);}
  else if(k==='p'){s.forEach(function(e){e.classList.add('on');fit(e);});
    window.print();s.forEach(function(e,k2){e.classList.toggle('on',k2===i);});}});
document.getElementById('deck').addEventListener('click',function(e){
  go(e.clientX < window.innerWidth/2 ? i-1 : i+1);});
/* Print mode (?print=1) — headless Chrome never fires beforeprint, so the
   deck must arrive already expanded and fitted, or slides spill onto extra pages. */
if(location.search.indexOf('print')>=0){
  document.documentElement.classList.add('printing');
  s.forEach(function(e){e.classList.add('on');});
  s.forEach(function(e){fit(e);});
  document.getElementById('bar').style.display='none';
  document.getElementById('hud').style.display='none';
}else{ go(parseInt((location.hash||'#1').slice(1),10)-1||0); }
"""

BG_DIR = None

MD = markdown.Markdown(extensions=["tables", "sane_lists", "attr_list"])


def render_slide(raw: str, index: int) -> str:
    lines = raw.strip("\n").split("\n")
    kicker, bg = "", ""
    while lines and (lines[0].startswith("::: ") or lines[0].startswith("!!! bg ")):
        if lines[0].startswith("::: "):
            kicker = lines.pop(0)[4:].strip()
        else:
            bg = lines.pop(0)[7:].strip()
    body_md = "\n".join(lines).strip()

    # A title slide is the first one, and its second paragraph is the standfirst.
    cls = "slide title" if index == 0 else "slide"
    MD.reset()
    body = MD.convert(body_md)
    if index == 0:
        body = body.replace("<p>", '<p class="sub">', 1) if "<p>" in body else body
    k = f'<div class="kicker">{html.escape(kicker)}</div>' if kicker else ""
    style = ""
    if bg:
        src = (BG_DIR / bg) if BG_DIR else Path(bg)
        if src.exists():
            # Inlined so the HTML is self-contained and the PDF needs no file access.
            uri = base64.b64encode(src.read_bytes()).decode()
            style = f' style="background-image:url(data:image/jpeg;base64,{uri})"'
        else:
            print(f"  WARN   background not found: {src}", file=sys.stderr)
    return f'<section class="{cls}"{style}>{k}{body}</section>'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Deck")
    ap.add_argument("--theme", default="spec", choices=sorted(THEMES))
    ap.add_argument("--page", default="a4", choices=sorted(PAGES),
                    help="printed page size (default a4: 297x167mm, 16:9)")
    ap.add_argument("--bg-dir", default="",
                    help="directory the per-slide `!!! bg <file>` names resolve against")
    ap.add_argument("--pdf", action="store_true",
                    help="also print to PDF via headless Chrome (1920x1080 pages)")
    a = ap.parse_args()
    theme = THEMES[a.theme]
    global BG_DIR
    BG_DIR = Path(a.bg_dir).expanduser() if a.bg_dir else None

    text = Path(a.source).read_text()
    chunks = [c for c in re.split(r"(?m)^---\s*$", text) if c.strip()]
    slides = [render_slide(c, n) for n, c in enumerate(chunks)]

    page = PAGES[a.page]
    css = (CSS.replace('__SCHEME__', theme['scheme'])
              .replace('__VARS__', theme['vars'])
              .replace('__PW__', str(page['px'][0]))
              .replace('__PH__', str(page['px'][1]))
              .replace('__PSIZE__', page['size']))
    out = Path(a.out)
    out.write_text(
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html.escape(a.title)}</title>\n"
        f"{theme['font_link']}\n"
        f"<style>{css}</style></head>\n<body>\n"
        '<div id="bar"></div>\n'
        f'<div id="deck">{"".join(slides)}</div>\n'
        f'<div id="hud"><b id="cur">1</b> / {len(slides)} &nbsp;·&nbsp; ← → &nbsp;·&nbsp; p to print</div>\n'
        f"<script>{JS}</script>\n</body></html>\n"
    )
    print(f"  wrote  {out}  ({len(slides)} slides, {out.stat().st_size // 1024} KB)")

    if a.pdf:
        pdf = out.with_suffix(".pdf")
        chrome = next((c for c in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/usr/bin/google-chrome", "/usr/bin/chromium") if Path(c).exists()), None)
        if not chrome:
            print("  SKIP   no Chrome found — HTML written, PDF not built", file=sys.stderr)
            return 1
        # --no-pdf-header-footer keeps the page edge-to-edge; the deck supplies
        # its own margins. Fonts are fetched here and embedded into the PDF.
        cmd = [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
               "--virtual-time-budget=10000",
               f"--print-to-pdf={pdf}", out.resolve().as_uri() + "?print=1"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not pdf.exists():
            print(f"  FAIL   chrome exit {r.returncode}: {r.stderr[-400:]}", file=sys.stderr)
            return 1
        print(f"  wrote  {pdf}  ({pdf.stat().st_size // 1024} KB)")
        add_outline(pdf, chunks)
    return 0


def add_outline(pdf: Path, chunks: list[str]) -> None:
    """Bookmark every slide by its title, so PDF viewers show a clickable
    table of contents (Preview: View > Table of Contents). Chrome's print
    path writes no outline of its own. Optional: skipped without PyMuPDF."""
    try:
        import fitz
    except ImportError:
        print("  SKIP   outline: PyMuPDF not installed", file=sys.stderr)
        return
    doc = fitz.open(pdf)
    if doc.page_count != len(chunks):
        # A slide that overflowed onto two pages would shift every bookmark.
        print(f"  SKIP   outline: {doc.page_count} pages for {len(chunks)} slides", file=sys.stderr)
        return
    toc = []
    for n, c in enumerate(chunks, 1):
        m = re.search(r"(?m)^#\s+(.+)$", c)
        toc.append([1, f"{n}. {m.group(1).strip() if m else f'Slide {n}'}".replace("**", ""), n])
    doc.set_toc(toc)
    tmp = pdf.with_suffix(".outline.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    tmp.replace(pdf)
    print(f"  wrote  outline ({len(toc)} bookmarks)")


if __name__ == "__main__":
    raise SystemExit(main())
