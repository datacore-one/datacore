#!/usr/bin/env python3
"""Split a multi-diagram HTML document into one file per <svg>, for validation.

Why this exists
---------------
The diagram-design skill ships `verify-geometry.py`, which checks that no arrow
label mask is clipped by a node painted after it. That checker scans **every
<rect> in the file** with no per-<svg> scoping, because its assumed input is a
one-diagram page.

Point it at a document holding several diagrams and it reports cross-diagram
false positives: two <svg> elements have independent coordinate systems, so
their rects can share x/y numbers and never paint over each other. Distorting a
layout to satisfy that is the wrong fix.

Split first, then check each diagram in isolation:

    python3 diagram_split_svgs.py doc.html /tmp/svgs
    python3 <skill>/scripts/verify-geometry.py /tmp/svgs/*.html

Recorded as ENG-2026-09-08-012.

Each output file is named `NN-<slug>.html`, where the slug comes from the SVG's
`aria-labelledby="<slug>-title ..."` — so a finding names the diagram you can
actually find.
"""
import re
import sys
import pathlib


def split(src_path: pathlib.Path, out_dir: pathlib.Path) -> list[pathlib.Path]:
    src = src_path.read_text(encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.html"):
        stale.unlink()

    written = []
    for i, svg in enumerate(re.findall(r"<svg\b.*?</svg>", src, re.S), 1):
        m = re.search(r'aria-labelledby="([\w-]+)-title', svg)
        slug = m.group(1) if m else f"diagram{i}"
        dest = out_dir / f"{i:02d}-{slug}.html"
        dest.write_text(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{slug}</title></head><body>\n{svg}\n</body></html>",
            encoding="utf-8",
        )
        written.append(dest)
    return written


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <document.html> <out-dir>", file=sys.stderr)
        return 2
    written = split(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
    print(f"{len(written)} diagram(s) written to {sys.argv[2]}")
    for p in written:
        print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
