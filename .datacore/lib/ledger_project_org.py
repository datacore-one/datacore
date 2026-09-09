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
from ledger.genesis import scan  # noqa: E402
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
            if line.startswith("#+SEQ_TODO:") or line.startswith("#+TODO:"):
                # Projector emits the canonical SEQ_TODO; carrying these from the
                # existing file duplicates them by 1 on every cycle (2026-09-08).
                continue
            if line.startswith("#+"):
                header.append(line)
            elif line.strip() and not line.startswith("#"):
                break
    copy = space / HEADER_COPY
    if header and not copy.exists():
        # WRITE ONCE. The copy is tracked; rewriting it on every cycle made
        # every host a writer of the same file and the transport conflicted
        # on it within the first hour of Phase 1 (2026-09-05).
        copy.write_text("\n".join(header) + "\n")
    elif not header and copy.exists():
        header = copy.read_text().splitlines()
    body = [l for l in text.splitlines() if not l.startswith("# ")]
    note = "# Generated from the ledger (Phase 1, DIP-0046). Edits here are ingested hourly; the ledger is the record."
    return "\n".join(header + [note] + body) + "\n"


def phase(space: Path) -> int:
    try:
        return int((space / MARKER).read_text().strip() or "0")
    except (FileNotFoundError, ValueError):
        return 0


def project_space(space: Path, force: bool = False) -> str:
    if phase(space) != 1:
        return "phase 0, authored — not generated"

    # REFUSE TO PROJECT OVER CONTENT THE LEDGER HAS NEVER SEEN.
    #
    # This file's own docstring has always said "Projecting without ingesting
    # first is how a hand edit gets lost", and nothing enforced it. DIP-0043
    # calls the refuse-to-overwrite guard the most load-bearing mechanism in
    # Phase 1; the guard exists as `projector.write(last_written_sha=...)` and
    # `ledger/phase1.py` uses it, but production reached the file through a bare
    # write and so had no guard at all. Measured 2026-09-08: only that module's
    # own test imports it.
    #
    # A sha comparison is the wrong shape HERE. The hourly cycle ingests
    # immediately before projecting, so the file legitimately differs from what
    # the projector last wrote on every cycle where anyone edited anything, and
    # a guard that fires every hour is a guard someone switches off.
    #
    # `scan()` asks the precise question instead: which headings in the org file
    # is the ledger missing? It is the same call the ingest makes, folding the
    # ledger once and returning only what it has never seen. After a successful
    # ingest that set is empty and this costs nothing. When the ingest failed,
    # skipped a heading with no :ID:, or never ran, it is exactly the content a
    # projection would destroy.
    try:
        pending = list(scan(space).importable)
    except Exception as exc:  # noqa: BLE001
        # Never fail OPEN: if the question cannot be answered, do not overwrite.
        if not force:
            return f"REFUSED — cannot verify against the ledger ({type(exc).__name__}: {exc})"
        pending = []
    if pending and not force:
        titles = "; ".join(str(getattr(i, "title", i))[:40] for i in pending[:3])
        return (f"REFUSED — {len(pending)} heading(s) in {ORG} are not in the "
                f"ledger; ingest first, then project ({titles})")

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
    ap.add_argument("--force", action="store_true",
                    help="project even when the org file holds headings the ledger "
                         "has never seen. This DESTROYS them. Operator override, "
                         "never a routine run.")
    a = ap.parse_args(argv)
    spaces = [a.root / a.space] if a.space else sorted(p for p in a.root.glob("[0-9]-*") if (p / ".datacore" / "events").is_dir())
    refused = 0
    for s in spaces:
        line = project_space(s, force=a.force)
        if line.startswith("REFUSED"):
            refused += 1
        print(f"  {s.name:14} {line}")
    # Non-zero so a caller can tell. A refusal that exits 0 is the same silence
    # this guard exists to break.
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
