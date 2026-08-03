#!/usr/bin/env python3
"""Render DIPs that live on feature branches into one browsable HTML page.

Draft DIPs sit on their own branches (per the DIP workflow), so they are not in
the working tree and cannot simply be opened. This extracts them with
``git show <branch>:<file>``, renders the markdown, and writes a single
self-contained page with a sticky index — for review before ratification.

Usage:
    python3 .datacore/lib/dip_reader.py                      # all dip/* branches
    python3 .datacore/lib/dip_reader.py --branches dip/0034-event-ledger dip/0035-job-contracts
    python3 .datacore/lib/dip_reader.py --out ~/somewhere.html --open

Reading-order and framing notes are optional: pass --intro <markdown file> to
prepend one (used for the v2 review page).
"""

from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import markdown
except ImportError:  # pragma: no cover - environment guard
    sys.exit("python-markdown required: pip3 install markdown")

DEFAULT_REPO = Path.home() / "Data" / ".datacore" / "dips"
DEFAULT_OUT = Path.home() / ".datacore" / "dip-review" / "dips.html"
BRANCH_RE = re.compile(r"^dip/(\d{4})-")
STATUS_RE = re.compile(r"status\W+(draft|accepted|implemented|rejected|superseded)", re.I)


def run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def discover_branches(repo: Path) -> list[str]:
    out = run(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/dip")
    return sorted({b.strip() for b in out.splitlines() if BRANCH_RE.match(b.strip())})


def dip_file_for(repo: Path, branch: str) -> str | None:
    """The DIP file whose number matches the branch number (branches also carry
    every previously-merged DIP, so a plain listing is not enough)."""
    match = BRANCH_RE.match(branch)
    if not match:
        return None
    number = match.group(1)
    listing = run(repo, "ls-tree", "--name-only", branch).splitlines()
    for name in listing:
        if name.startswith(f"DIP-{number}-") and name.endswith(".md"):
            return name
    return None


def extract(repo: Path, branch: str, path: str) -> str:
    return run(repo, "show", f"{branch}:{path}")


def title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def status_of(text: str) -> str:
    head = "\n".join(text.splitlines()[:40])
    found = STATUS_RE.search(head)
    return found.group(1).capitalize() if found else "—"


def section_links(text: str) -> list[tuple[str, str]]:
    """Second-level headings, for the per-DIP mini index."""
    links = []
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
            links.append((heading, slug))
    return links


CSS = """
:root { --bg:#fbfaf8; --fg:#1b1a17; --muted:#6b675f; --rule:#e2ddd4; --accent:#7a5c2e;
        --code-bg:#f2efe9; --card:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16151a; --fg:#e8e6e1; --muted:#9d988e; --rule:#2f2d33; --accent:#d7b071;
          --code-bg:#1f1e24; --card:#1b1a20; } }
:root[data-theme="dark"] { --bg:#16151a; --fg:#e8e6e1; --muted:#9d988e; --rule:#2f2d33;
          --accent:#d7b071; --code-bg:#1f1e24; --card:#1b1a20; }
:root[data-theme="light"] { --bg:#fbfaf8; --fg:#1b1a17; --muted:#6b675f; --rule:#e2ddd4;
          --accent:#7a5c2e; --code-bg:#f2efe9; --card:#fff; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }
.wrap { display:grid; grid-template-columns:290px minmax(0,1fr); gap:0; align-items:start; }
nav { position:sticky; top:0; max-height:100vh; overflow-y:auto; padding:26px 20px 60px;
  border-right:1px solid var(--rule); background:var(--card); }
nav h2 { font-size:12px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted);
  margin:0 0 14px; }
nav ol { list-style:none; margin:0; padding:0; }
nav li { margin:0 0 4px; }
nav a { display:block; padding:7px 9px; border-radius:7px; color:var(--fg);
  text-decoration:none; font-size:14px; line-height:1.35; }
nav a:hover { background:var(--code-bg); }
nav .num { color:var(--accent); font-weight:600; font-variant-numeric:tabular-nums; }
nav .sub { display:block; color:var(--muted); font-size:12px; margin-top:2px; }
main { padding:40px 46px 120px; max-width:80ch; }
article { padding-top:26px; margin-top:34px; border-top:1px solid var(--rule); }
article:first-of-type { border-top:none; margin-top:0; }
h1 { font-size:30px; line-height:1.2; margin:.2em 0 .1em; }
h2 { font-size:22px; margin:1.8em 0 .5em; padding-bottom:.25em; border-bottom:1px solid var(--rule); }
h3 { font-size:17px; margin:1.5em 0 .4em; }
p, li { font-size:15.5px; }
a { color:var(--accent); }
code { background:var(--code-bg); padding:.15em .38em; border-radius:4px; font-size:13.5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
pre { background:var(--code-bg); padding:14px 16px; border-radius:9px; overflow-x:auto;
  border:1px solid var(--rule); }
pre code { background:none; padding:0; font-size:13px; line-height:1.55; }
table { border-collapse:collapse; width:100%; margin:1em 0; display:block; overflow-x:auto; }
th, td { border:1px solid var(--rule); padding:7px 11px; text-align:left; font-size:14.5px;
  vertical-align:top; }
th { background:var(--code-bg); }
blockquote { margin:1em 0; padding:.4em 1em; border-left:3px solid var(--accent);
  color:var(--muted); }
hr { border:none; border-top:1px solid var(--rule); margin:2em 0; }
.badge { display:inline-block; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  padding:3px 9px; border-radius:99px; border:1px solid var(--accent); color:var(--accent);
  vertical-align:middle; margin-left:10px; }
.meta { color:var(--muted); font-size:13px; margin:.2em 0 1.4em; }
.meta code { font-size:12.5px; }
.intro { background:var(--card); border:1px solid var(--rule); border-radius:12px;
  padding:22px 26px; margin-bottom:14px; }
.intro h1 { margin-top:0; }
.mini { font-size:13px; color:var(--muted); margin:-.6em 0 1.6em; }
.mini a { color:var(--muted); text-decoration:none; border-bottom:1px dotted var(--rule); }
.mini a:hover { color:var(--accent); }
.top { position:fixed; right:18px; bottom:18px; background:var(--card); border:1px solid var(--rule);
  border-radius:99px; padding:9px 15px; font-size:13px; text-decoration:none; color:var(--fg); }
@media (max-width:900px) { .wrap { grid-template-columns:1fr; } nav { position:static; max-height:none;
  border-right:none; border-bottom:1px solid var(--rule); } main { padding:26px 20px 90px; } }
"""


def build(docs: list[dict], intro_html: str) -> str:
    nav_items = []
    bodies = []
    for doc in docs:
        nav_items.append(
            f'<li><a href="#{doc["anchor"]}"><span class="num">{doc["number"]}</span> '
            f'{html.escape(doc["short"])}<span class="sub">{doc["status"]}</span></a></li>'
        )
        mini = " · ".join(
            f'<a href="#{doc["anchor"]}-{slug}">{html.escape(name)}</a>'
            for name, slug in doc["sections"][:9]
        )
        bodies.append(
            f'<article id="{doc["anchor"]}">'
            f'<div class="meta">branch <code>{html.escape(doc["branch"])}</code> · '
            f'file <code>{html.escape(doc["file"])}</code> · '
            f'{doc["words"]:,} words<span class="badge">{doc["status"]}</span></div>'
            f'{doc["html"]}'
            + (f'<p class="mini">Sections: {mini}</p>' if mini else "")
            + "</article>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Datacore v2 — DIPs for review</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<nav><h2>Datacore v2 DIPs</h2><ol>{''.join(nav_items)}</ol></nav>
<main><div class="intro">{intro_html}</div>{''.join(bodies)}</main>
</div><a class="top" href="#">↑ top</a></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--branches", nargs="*", default=None)
    parser.add_argument(
        "--files", nargs="*", default=None,
        help="render these DIP files directly (working tree) instead of extracting from branches",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--intro", type=Path, default=None)
    parser.add_argument("--open", action="store_true", help="open in Brave when done")
    args = parser.parse_args()

    if not args.repo.exists():
        print(f"error: dips repo not found at {args.repo}", file=sys.stderr)
        return 1

    renderer = markdown.Markdown(extensions=["extra", "sane_lists", "toc"])
    docs = []

    if args.files:
        sources = []
        for raw in sorted(args.files):
            path = Path(raw)
            found = re.search(r"DIP-(\d{4})", path.name)
            if not found:
                print(f"skip {path}: no DIP number in filename", file=sys.stderr)
                continue
            sources.append((f"(working tree) {path.parent.name}", path.name,
                            found.group(1), path.read_text()))
    else:
        branches = args.branches or discover_branches(args.repo)
        if not branches:
            print("error: no dip/* branches found", file=sys.stderr)
            return 1
        sources = []
        for branch in branches:
            path = dip_file_for(args.repo, branch)
            if not path:
                print(f"skip {branch}: no matching DIP file", file=sys.stderr)
                continue
            sources.append((branch, path, BRANCH_RE.match(branch).group(1),
                            extract(args.repo, branch, path)))

    for branch, path, number, text in sources:
        anchor = f"dip-{number}"
        renderer.reset()
        # Namespace heading ids per DIP so anchors stay unique across the page.
        body = renderer.convert(text)
        body = re.sub(r'(<h[1-6][^>]*) id="([^"]+)"', rf'\1 id="{anchor}-\2"', body)
        title = title_of(text, path)
        docs.append({
            "branch": branch, "file": path, "number": number, "anchor": anchor,
            "title": title,
            "short": re.sub(r"^DIP-\d+[:\s—-]*", "", title).strip() or title,
            "status": status_of(text), "sections": section_links(text),
            "words": len(text.split()), "html": body,
        })

    intro_html = ""
    if args.intro and args.intro.exists():
        renderer.reset()
        intro_html = renderer.convert(args.intro.read_text())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(docs, intro_html))
    total = sum(d["words"] for d in docs)
    print(f"wrote {args.out} — {len(docs)} DIPs, {total:,} words")

    if args.open:
        subprocess.run(["open", "-a", "Brave Browser", str(args.out)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
