#!/usr/bin/env python3
"""Write org/next_actions.org FROM the ledger, for spaces in Phase 1 only.

Phase 1 (DIP-0046): the org file is generated and gitignored; the ledger is
the record. A space is in Phase 1 when `.datacore/ledger-phase` in that space
reads `1`. Any other space is left untouched -- this tool never generates
over an authored file.

Run it AFTER an ingest: ingest captures what writers put in the org file since
the last cycle, then the projection re-emits everything the ledger holds.
Projecting without ingesting first is how a hand edit gets lost.

    ledger_project_org.py --space NAME | --all [--root DIR]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger.fold import fold  # noqa: E402
from ledger.log import read_events  # noqa: E402
from ledger.projector import project  # noqa: E402

MARKER = Path(".datacore") / "ledger-phase"
ORG = Path("org") / "next_actions.org"


HEADER_COPY = Path(".datacore") / "ledger-org-header"


def _with_org_header(space: Path, target: Path, text: str) -> str:
    """Keep the authored file's `#+TITLE/#+CATEGORY/#+STARTUP/#+TAGS/...` lines.

    The projector opens with a GENERATED banner and no in-buffer settings; an
    org-mode user's tag completion and startup folding live in those lines.
    They are read from the current file when it has them, else from the copy
    saved at flip time, and the banner is replaced by one honest line.
    """
    header: list[str] = []
    src = target if target.exists() else None
    if src is not None:
        for line in src.read_text(errors="replace").splitlines():
            if line.startswith("#+"):
                header.append(line)
            elif line.strip() and not line.startswith("#"):
                break
    if header:
        (space / HEADER_COPY).write_text("\n".join(header) + "\n")
    elif (space / HEADER_COPY).exists():
        header = (space / HEADER_COPY).read_text().splitlines()
    body = [l for l in text.splitlines() if not l.startswith("# ")]
    note = "# Generated from the ledger (Phase 1, DIP-0046). Edits here are ingested hourly; the ledger is the record."
    return "\n".join(header + [note] + body) + "\n"


def phase(space: Path) -> int:
    try:
        return int((space / MARKER).read_text().strip() or "0")
    except (FileNotFoundError, ValueError):
        return 0


def project_space(space: Path) -> str:
    if phase(space) != 1:
        return "phase 0, authored — not generated"
    text = project(fold(read_events(space)), space=space.name).text
    target = space / ORG
    text = _with_org_header(space, target, text)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".org.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return f"generated {ORG} ({text.count(chr(10))} lines)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data")))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--space")
    g.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    spaces = [a.root / a.space] if a.space else sorted(p for p in a.root.glob("[0-9]-*") if (p / ".datacore" / "events").is_dir())
    for s in spaces:
        print(f"  {s.name:14} {project_space(s)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
