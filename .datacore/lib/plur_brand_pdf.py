#!/usr/bin/env python3
"""Render a markdown file to a PDF in PLUR brand dress.

`make-pdf` (gstack) gives Helvetica and a generic layout — fine for a memo, wrong
for anything that goes to a person outside the company. This renders the same
markdown through the brand's own tokens instead: Outfit, the light palette from
tokens.json, the horizontal lockup on page one, and the four accents as the rule
under it, in mark order (cyan first, emerald last).

Two rules from BRAND.md are enforced here rather than left to whoever writes the
CSS next:

  ACCENTS ARE POSITIONAL, NEVER SEMANTIC. They appear once, as the four segments
  of the opening rule, in path order. They never colour a heading by category or
  a callout by severity — that would overwrite the one thing they encode.

  TEXT ACCENTS ARE NOT DISPLAY ACCENTS. On light ground the display accents fail
  AA badly (cyan 1.73:1), so links use the accent_on_light set. The display set
  stays for decorative fills, where no contrast floor applies.

Pagination is Paged.js (vendored at .datacore/assets/vendor/), because Chrome
alone cannot do @page margin boxes and therefore cannot number pages. Printing is
Chrome headless — no Node, no browse daemon, nothing that has to be running.

Usage:
    plur_brand_pdf.py INPUT.md OUTPUT.pdf [--title T] [--subtitle S]
                      [--page-size a4|letter] [--no-confidential]
                      [--watermark DRAFT] [--html-only]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "5-plur/1-tracks/comms/brand"
FONT = ROOT / "5-plur/2-projects/website/scripts/Outfit.ttf"
PAGEDJS = ROOT / ".datacore/assets/vendor/paged.polyfill.js"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

PAGE_SIZES = {"a4": "A4", "letter": "letter"}


def load_tokens() -> dict:
    """Palette and type come from tokens.json — the file that says it is the source."""
    with open(BRAND / "tokens.json") as fh:
        t = json.load(fh)
    light = t["color"]["light"]
    on_light = t["color"]["accent_on_light"]
    accent = t["color"]["accent"]
    return {
        "bg": light["bg"]["value"],
        "surface": light["surface"]["value"],
        "text": light["text"]["value"],
        "mid": light["mid"]["value"],
        "dim": light["dim"]["value"],
        "hairline": light["hairline"]["value"],
        "link": on_light["cyan"]["value"],
        "path": [accent[k]["value"] for k in ("cyan", "amber", "violet", "emerald")],
        "display": t["type"]["display"]["family"],
        "mono": t["type"]["mono"]["family"],
    }


def font_face() -> str:
    """Outfit is a variable font; embedding it means the PDF renders the same anywhere."""
    if not FONT.exists():
        return ""
    b64 = base64.b64encode(FONT.read_bytes()).decode()
    return (
        "@font-face{font-family:'Outfit';font-style:normal;font-weight:100 900;"
        f"src:url(data:font/ttf;base64,{b64}) format('truetype');font-display:block;}}"
    )


def lockup() -> str:
    svg = BRAND / "assets/lockup-h-light.svg"      # dark ink, for light ground
    return svg.read_text() if svg.exists() else ""


def path_device(tok: dict) -> str:
    """The home path, flattened: four nodes joined by three bars.

    BRAND.md §6b lists this device for exactly two jobs — section dividers and
    page breaks — so the document's structure is carried by the mark's own
    geometry rather than by a line someone drew. Gradients are defined once in a
    hidden defs block; every divider references them.
    """
    x = (25, 75, 125, 175)
    bars = "".join(
        f'<line x1="{x[i]}" y1="13" x2="{x[i+1]}" y2="13" stroke="url(#pb{i})"/>'
        for i in range(3)
    )
    dots = "".join(
        f'<circle cx="{x[i]}" cy="13" r="11" fill="{tok["path"][i]}" opacity=".88"/>'
        for i in range(4)
    )
    return (
        '<svg class="pathrule" viewBox="0 0 200 26" aria-hidden="true">'
        f'<g stroke-width="7" stroke-linecap="round" opacity=".72">{bars}</g>{dots}</svg>'
    )


def gradient_defs(tok: dict) -> str:
    """Bar gradients run between the two node colours the bar joins (BRAND.md §6b)."""
    stops = "".join(
        f'<linearGradient id="pb{i}" gradientUnits="userSpaceOnUse"'
        f' x1="{25 + 50 * i}" y1="13" x2="{75 + 50 * i}" y2="13">'
        f'<stop offset="0" stop-color="{tok["path"][i]}"/>'
        f'<stop offset="1" stop-color="{tok["path"][i+1]}"/></linearGradient>'
        for i in range(3)
    )
    return f'<svg width="0" height="0" style="position:absolute"><defs>{stops}</defs></svg>'


def css(tok: dict, page_size: str, title: str, confidential: bool, watermark: str) -> str:
    """Type scale follows BRAND.md §5: the system runs light.

    300 is the workhorse, 400 is emphasis, 100-200 is display, and 900 is
    reserved for the mark. Hierarchy therefore comes from SIZE and CASE, never
    from reaching for a heavier weight — which is what separates this from a
    generic document template.
    """
    footer_right = (
        f'@bottom-right{{content:"CONFIDENTIAL";font-size:7pt;letter-spacing:.09em;'
        f'color:{tok["dim"]};}}' if confidential else ""
    )
    mark = (
        f'body::before{{content:"{watermark}";position:fixed;top:45%;left:12%;'
        f'font-size:82pt;font-weight:100;color:{tok["path"][0]};opacity:.09;'
        "transform:rotate(-32deg);z-index:0;}" if watermark else ""
    )
    return f"""
{font_face()}
@page {{
  size: {PAGE_SIZES[page_size]};
  margin: 20mm 42mm 16mm 26mm;   /* wide right margin holds the measure near 68ch */
  @top-left {{ content: "{title}"; font-family:'Outfit',sans-serif; font-size:7pt;
               font-weight:400; letter-spacing:.18em; text-transform:uppercase;
               color:{tok['dim']}; padding-bottom:7mm; }}
  @bottom-left {{ content: "PLUR"; font-family:'Outfit',sans-serif; font-size:7pt;
                  font-weight:400; letter-spacing:.22em; color:{tok['dim']}; padding-top:6mm; }}
  @bottom-center {{ content: counter(page) " / " counter(pages); font-family:'Outfit',sans-serif;
                    font-size:7pt; font-weight:300; letter-spacing:.1em;
                    color:{tok['dim']}; padding-top:6mm; }}
  {footer_right}
}}
@page :first {{ @top-left {{ content: none; }} }}

html {{ font-size: 10.5pt; }}
body {{ font-family:'Outfit',-apple-system,sans-serif; color:{tok['mid']};
        background:{tok['bg']}; line-height:1.65; font-weight:300;
        hyphens:none; -webkit-font-smoothing:antialiased; }}
{mark}

/* ---- page one -------------------------------------------------------- */
.masthead {{ margin-bottom:14mm; padding:6mm 0 9mm 0;
             background-image:radial-gradient(circle, rgba(0,0,0,.09) .7px, transparent .75px);
             background-size:9mm 9mm;                 /* ghost field, BRAND.md §6b */
             -webkit-mask-image:linear-gradient(115deg, rgba(0,0,0,.5), transparent 62%); }}
.masthead svg.lockup {{ width:38mm; height:auto; display:block; }}
h1 {{ font-size:33pt; font-weight:100; line-height:1.04; letter-spacing:-.035em;
      color:{tok['text']}; margin:0 0 5mm 0; }}
.subtitle {{ font-size:8pt; font-weight:400; text-transform:uppercase; letter-spacing:.2em;
             color:{tok['dim']}; margin:0 0 4mm 0; }}
.opening {{ margin:0 0 12mm 0; }}
.opening svg.pathrule {{ width:44mm; }}

/* ---- headings: size and case carry the hierarchy, not weight ---------- */
h2 {{ font-size:19pt; font-weight:200; letter-spacing:-.028em; line-height:1.14;
      color:{tok['text']}; margin:13mm 0 4.5mm 0; position:relative;
      break-after:avoid; break-inside:avoid; }}
h2::before {{ content:""; position:absolute; left:-9mm; top:.62em; width:7px; height:7px;
              border-radius:999px; background:{tok['path'][0]}; opacity:.88; }}
h3 {{ font-size:8.5pt; font-weight:400; text-transform:uppercase; letter-spacing:.19em;
      color:{tok['text']}; margin:9mm 0 2.5mm 0; break-after:avoid; }}
h2.part-start {{ break-before:page; margin-top:2mm; }}

/* ---- body ------------------------------------------------------------- */
p {{ margin:0 0 3.4mm 0; orphans:3; widows:3; }}
strong {{ font-weight:400; color:{tok['text']}; }}     /* 400 IS the emphasis weight */
em {{ font-style:italic; }}
a {{ color:{tok['link']}; text-decoration:none; border-bottom:1px solid {tok['hairline']}; }}
.block {{ break-inside:avoid; }}
/* A list is one thought with the line that introduces it: structure() wraps the
   two together, because break-after on the paragraph alone was ignored. */
.keep {{ break-inside:avoid; }}
.keep h2 {{ margin-top:0; }}

/* Dots and numbered dots, never a glyph bullet (BRAND.md §6b). */
ul {{ list-style:none; margin:0 0 3.4mm 0; padding-left:6mm; }}
ul li {{ position:relative; margin-bottom:1.8mm; }}
ul li::before {{ content:""; position:absolute; left:-5mm; top:.62em; width:5px; height:5px;
                 border-radius:999px; background:{tok['dim']}; }}
ol {{ list-style:none; counter-reset:step; margin:0 0 3.4mm 0; padding-left:8mm; }}
ol li {{ position:relative; margin-bottom:2.4mm; counter-increment:step; }}
ol li::before {{ content:counter(step); position:absolute; left:-8mm; top:.12em;
                 width:4.6mm; height:4.6mm; border-radius:999px; background:{tok['text']};
                 color:{tok['bg']}; font-weight:900; font-size:6.5pt; line-height:4.6mm;
                 text-align:center; }}   /* a node that has fired */

code {{ font-family:'{tok['mono']}','SF Mono',Menlo,monospace; font-size:8.4pt;
        font-weight:400; background:{tok['surface']}; border:1px solid {tok['hairline']};
        border-radius:3px; padding:.3mm 1.1mm; color:{tok['text']}; }}
blockquote {{ margin:0 0 3.4mm 0; padding-left:4mm; border-left:1px solid {tok['hairline']};
              color:{tok['dim']}; }}
table {{ border-collapse:collapse; width:100%; margin:0 0 4mm 0; font-size:9.5pt; }}
th {{ text-align:left; font-weight:400; color:{tok['dim']}; font-size:7.5pt;
      letter-spacing:.16em; text-transform:uppercase;
      border-bottom:1px solid {tok['hairline']}; padding:1.6mm 2mm 1.6mm 0; }}
td {{ border-top:1px solid {tok['hairline']}; border-bottom:0; padding:1.8mm 2mm 1.8mm 0;
      vertical-align:top; }}

/* ---- dividers --------------------------------------------------------- */
hr {{ display:none; }}                      /* replaced by the path device */
/* A divider opening a page is a line with nothing above it to divide. Glue it to
   the content it closes, so it falls at the foot of a page or not at all. */
.divider {{ display:flex; align-items:center; gap:7mm; margin:13mm 0 13mm 0;
             break-inside:avoid; break-before:avoid; }}
.divider::before, .divider::after {{ content:""; flex:1; height:1px;
                                     background:{tok['hairline']}; }}
.divider svg.pathrule {{ width:23mm; display:block; flex:none; }}
svg.pathrule {{ height:auto; }}
"""


def build_html(md_text: str, tok: dict, title: str, subtitle: str,
               break_before: list[str], **kw) -> str:
    import markdown

    # The document's own H1 becomes the masthead title, so it is not repeated in the body.
    body_md = re.sub(r"\A#\s+(.+?)\n", "", md_text, count=1)
    html = markdown.markdown(
        body_md, extensions=["extra", "sane_lists", "smarty", "attr_list"]
    )
    html = structure(html, tok, break_before)
    sub = f'<p class="subtitle">{subtitle}</p>' if subtitle else ""
    mark = lockup().replace("<svg ", '<svg class="lockup" ', 1)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>{css(tok, title=title, **kw)}</style></head>
<body>
{gradient_defs(tok)}
<header class="masthead">{mark}</header>
<h1>{title}</h1>
{sub}
<div class="opening">{path_device(tok)}</div>
{html}
<script src="file://{PAGEDJS}"></script>
</body></html>
"""


def structure(html: str, tok: dict, break_before: list[str]) -> str:
    """Turn a flat run of markdown into something that paginates on purpose.

    Three passes, each fixing a way a long document goes wrong in print:
      1. h3 and the paragraphs under it become one unbreakable block, so a rung
         never gets its name on one page and its content on the next;
      2. headings named in --break-before start a page, so the document breaks
         where its argument turns rather than wherever the text happened to run out;
      3. every other `---` becomes the path device. A divider immediately before a
         forced break is dropped — the break already says what it said.
    """
    def h2_id(tag: str) -> str:
        return re.sub(r"<[^>]+>", "", tag).strip()

    # 2 — part starts
    def mark_h2(m: re.Match) -> str:
        text = h2_id(m.group(0))
        if any(text.lower().startswith(b.strip().lower()) for b in break_before if b.strip()):
            return m.group(0).replace("<h2>", '<h2 class="part-start">', 1)
        return m.group(0)

    html = re.sub(r"<h2>.*?</h2>", mark_h2, html, flags=re.S)

    # 3 — dividers; the leading one would duplicate the opening device on page one
    html = re.sub(r"\A\s*<hr\s*/?>", "", html)
    # drop the one that only introduces a forced break
    html = re.sub(r"<hr\s*/?>\s*(?=<h2 class=\"part-start\">)", "", html)
    html = re.sub(r"<hr\s*/?>", f'<div class="divider">{path_device(tok)}</div>', html)

    # 4 — a list, the line that introduces it, and the heading above that, are one
    #     unit. CSS `break-after:avoid` on the lead-in paragraph is advisory and
    #     Paged.js ignored it: a page ended on "Both count. Neither is the junior
    #     one:" with the two outputs overleaf. Grouping in the markup is not
    #     advisory.
    html = re.sub(
        # every group tempered: a lazy .*? would happily span to a LATER closing
        # tag and swallow whole sections into one unbreakable block.
        r"(?:(<h2[^>]*>(?:(?!</h2>).)*</h2>)\s*)?(<p>(?:(?!</p>).)*</p>)\s*"
        r"(<(ul|ol)>(?:(?!</\4>).)*</\4>)",
        lambda m: f'<div class="keep">{m.group(1) or ""}{m.group(2)}{m.group(3)}</div>',
        html,
        flags=re.S,
    )

    # 1 — keep each h3 with the prose that belongs to it
    parts = re.split(r"(?=<h3>)", html)
    out = [parts[0]]
    for chunk in parts[1:]:
        m = re.search(r"(?=<h2|<div class=\"divider\")", chunk)
        cut = m.start() if m else len(chunk)
        out.append(f'<section class="block">{chunk[:cut]}</section>{chunk[cut:]}')
    return "".join(out)


def node_modules() -> Path:
    """Playwright lives wherever it lives; look, do not assume."""
    for cand in (Path.home() / ".claude/skills/gstack/node_modules",
                 ROOT / "node_modules"):
        if (cand / "playwright").exists():
            return cand
    sys.exit("playwright not found — install it, or point node_modules() at an install")


def print_pdf(html_path: Path, out: Path) -> None:
    if not Path(CHROME).exists():
        sys.exit(f"Chrome not found at {CHROME}")
    helper = Path(__file__).resolve().parent / "print_paged_pdf.cjs"
    proc = subprocess.run(
        ["node", str(helper), str(html_path), str(out)],
        capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "NODE_PATH": str(node_modules())},
    )
    if not out.exists():
        sys.exit(f"no PDF produced.\n{proc.stderr[-2000:]}")
    sys.stderr.write(proc.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input"), ap.add_argument("output")
    ap.add_argument("--title"), ap.add_argument("--subtitle", default="")
    ap.add_argument("--page-size", default="a4", choices=sorted(PAGE_SIZES))
    ap.add_argument("--no-confidential", action="store_true")
    ap.add_argument("--watermark", default="")
    ap.add_argument("--break-before", default="",
                    help="comma-separated H2 headings that should start a new page")
    ap.add_argument("--html-only", action="store_true",
                    help="write the HTML beside the output and stop (for iterating on the CSS)")
    a = ap.parse_args()

    src = Path(a.input).resolve()
    md_text = src.read_text()
    m = re.match(r"\A#\s+(.+?)\n", md_text)
    title = a.title or (m.group(1) if m else src.stem)

    html = build_html(md_text, load_tokens(), title, a.subtitle,
                      break_before=a.break_before.split(","),
                      page_size=a.page_size, confidential=not a.no_confidential,
                      watermark=a.watermark)

    out = Path(a.output).resolve()
    html_path = out.with_suffix(".html")
    html_path.write_text(html)
    if a.html_only:
        print(html_path)
        return
    print_pdf(html_path, out)
    html_path.unlink(missing_ok=True)
    print(out)


if __name__ == "__main__":
    if not PAGEDJS.exists():
        sys.exit(f"Paged.js missing: {PAGEDJS}\n  curl -L -o {PAGEDJS} "
                 "https://unpkg.com/pagedjs@0.4.3/dist/paged.polyfill.js")
    if shutil.which("python3") is None:
        sys.exit("python3 required")
    main()
