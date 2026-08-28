#!/usr/bin/env python3
"""Find org headings whose tags do not parse, across every space.

Org tags may only contain [a-zA-Z0-9_@#%]. A hyphen makes the tag invalid and
voids the ENTIRE trailing tag string — siblings included — so the heading drops
out of every tag query while still looking correctly tagged in the file. grep
finds it; org-workspace does not.

Reports two defects:

  invalid-tag   trailing tag string contains a character org rejects
  stray-tags    a tag-shaped string sits in the heading TEXT with another tag
                string after it — what a search-and-replace migration leaves
                behind when it appends the corrected tags instead of replacing
                (seen 2026-07-27 on the wrap-up-extracted migration)

    python3 .datacore/lib/org_tag_audit.py                 # audit all spaces
    python3 .datacore/lib/org_tag_audit.py --root ~/Data --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spaces import discover_spaces  # noqa: E402

HEADING = re.compile(r"^(\*+)\s+(.*)$")
# A trailing tag string: colon-delimited run at end of line.
TRAILING_TAGS = re.compile(r"(:[^\s:]+(?::[^\s:]+)*:)\s*$")
# A tag-shaped run anywhere in the text.
ANY_TAGS = re.compile(r":[^\s:]+(?::[^\s:]+)*:")
VALID_TAG_CHARS = re.compile(r"^[A-Za-z0-9_@#%]+$")
# Org links — [[target][description]] or [[target]]. Their URLs are full of
# colons and otherwise register as tag-shaped runs on every link heading.
LINK = re.compile(r"\[\[[^\]]*\](?:\[[^\]]*\])?\]")


def audit_file(path: Path) -> list[dict]:
    findings = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return findings

    for n, line in enumerate(lines, 1):
        m = HEADING.match(line)
        if not m:
            continue
        text = m.group(2)
        tm = TRAILING_TAGS.search(text)
        if not tm:
            continue

        tags = [t for t in tm.group(1).strip(":").split(":") if t]
        bad = [t for t in tags if not VALID_TAG_CHARS.match(t)]
        if bad:
            findings.append({
                "file": str(path), "line": n, "defect": "invalid-tag",
                "detail": ",".join(bad), "heading": text[:100],
            })
            continue

        # Tag-shaped leftovers sitting in the heading text before the real tags.
        # Strip org links first: [[https://host/path][title]] is full of colons
        # and produced tag-shaped false positives on every link heading.
        before = LINK.sub(" ", text[: tm.start()])
        for stray in ANY_TAGS.findall(before):
            inner = [t for t in stray.strip(":").split(":") if t]
            # Only flag runs that look like a tag string (2+ segments), so
            # prose like "the :foo: tag" is not reported.
            if len(inner) >= 2:
                findings.append({
                    "file": str(path), "line": n, "defect": "stray-tags",
                    "detail": stray, "heading": text[:100],
                })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    # Collect *.org files from every discovered space — consistent with how
    # other lib scripts enumerate spaces (see dedup_tasks.py, tag_validator.py).
    # tag_validator.py uses per-space org/ glob; here we scan all *.org files
    # under each space root (not only org/) to catch non-standard locations.
    files = [
        p
        for space in discover_spaces(root)
        for p in space.path.rglob("*.org")
        if ".git" not in p.parts
    ]
    findings: list[dict] = []
    for f in files:
        findings.extend(audit_file(f))

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        print(f"scanned {len(files)} org files")
        for defect in ("invalid-tag", "stray-tags"):
            hits = [f for f in findings if f["defect"] == defect]
            print(f"\n{defect}: {len(hits)}")
            for h in hits:
                rel = Path(h["file"]).relative_to(root)
                print(f"  {rel}:{h['line']}  [{h['detail']}]")
                print(f"    {h['heading']}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
