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
import json
import os
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger.fold import fold  # noqa: E402
from ledger.log import read_events  # noqa: E402
from ledger.genesis import scan  # noqa: E402
from ledger.projector import project  # noqa: E402
from ledger.projection_state import STATE, base_document, guard_projection, ProjectionConflict  # noqa: E402
from org_transaction import serialized, watch_file, write_org_text  # noqa: E402

MARKER = Path(".datacore") / "ledger-phase"
ORG = Path("org") / "next_actions.org"


HEADER_COPY = Path(".datacore") / "ledger-org-header"
#: Directives the projector can render itself; never carried in duplicate.
_PROJECTOR_EMITS = ("#+SEQ_TODO:", "#+TODO:", "#+FILETAGS:")


def _emitted_directives(text: str) -> tuple[str, ...]:
    """Which of `_PROJECTOR_EMITS` THIS projection actually contains.

    Dropping all of them unconditionally is wrong: the projector emits
    `#+FILETAGS:` only when every item carries the same filetag, so in a space
    where it does not, the authored file tag is state the ledger has no copy
    of. Dropping it there loses the tag from the written file -- and since org
    applies a file tag to every heading, the reconciler then reads every item
    as disagreeing with the ledger, which is the failure #198 fixed.

    So dedupe against the projection in hand rather than against a name list.
    """
    found: list[str] = []
    for line in text.splitlines():
        if line.startswith("*"):
            break  # past the directive block; the rest is task data
        for directive in _PROJECTOR_EMITS:
            if line.startswith(directive) and directive not in found:
                found.append(directive)
    return tuple(found)


def _with_org_header(space: Path, target: Path, text: str, *, remember: bool = True) -> str:
    """Keep the authored file's `#+TITLE/#+CATEGORY/#+STARTUP/#+TAGS/...` lines.

    The projector opens with a GENERATED banner and no in-buffer settings; an
    org-mode user's tag completion and startup folding live in those lines.
    They are read from the current file when it has them, else from the copy
    saved at flip time, and the banner is replaced by one honest line.
    """
    header: list[str] = []
    emits = _emitted_directives(text)
    src = target if target.exists() else None
    if src is not None:
        for line in src.read_text(errors="replace").splitlines():
            if line.startswith(emits):
                # The projector emitted this one itself, so carrying the
                # existing file's copy forward duplicates it on EVERY cycle.
                # SEQ_TODO was caught on 2026-09-08 and fixed by name;
                # #+FILETAGS is emitted by projector.py when the items share a
                # common filetag and was not on the list, so it kept growing:
                # 196 copies in 9-practice, 24 in 0-personal. Naming them one
                # at a time is what let the second one through, so this is a
                # tuple and any new projector directive belongs in it.
                continue
            if line.startswith("#+") and line not in header:
                # Defence in depth: never carry a duplicate forward, whatever
                # produced it. Ten days of accumulation should not survive one
                # projection.
                header.append(line)
            elif line.strip() and not line.startswith("#"):
                break
    copy = space / HEADER_COPY
    if header and not copy.exists() and remember:
        # WRITE ONCE. The copy is tracked; rewriting it on every cycle made
        # every host a writer of the same file and the transport conflicted
        # on it within the first hour of Phase 1 (2026-09-05).
        write_org_text(copy, "\n".join(header) + "\n")
    elif not header and copy.exists():
        # The saved copy is subject to the same rule: a directive this
        # projection emits must not come back through the flip-time snapshot.
        header = [l for l in copy.read_text().splitlines()
                  if not (emits and l.startswith(emits))]
    from ledger.projector import GENERATED_HEADER
    # Remove only our generated prefix. Comments in task bodies are data.
    body = text.removeprefix(GENERATED_HEADER).splitlines()
    note = "# Generated from the ledger (Phase 1, DIP-0046). Edits here are ingested hourly; the ledger is the record."
    return "\n".join(header + [note] + body) + "\n"


def phase(space: Path) -> int:
    try:
        value = (space / MARKER).read_text().strip()
    except FileNotFoundError:
        return 0
    if value not in ('0', '1'):
        raise ValueError('invalid ledger phase marker; source-of-truth mode is unverified')
    return int(value)


@serialized
def project_space(space: Path, force: bool = False, adopt_org: bool = False) -> str:
    if phase(space) != 1:
        return "phase 0, authored — not generated"

    target = space / ORG
    if target.is_symlink():
        return "REFUSED — projection target must not be a symbolic link"
    before = watch_file(target)["before"]
    watch_file(space / STATE)
    watch_file(space / HEADER_COPY)

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

    text = project(fold(read_events(space)), space=space.name, as_of=time.time()).text
    target = space / ORG
    text = _with_org_header(space, target, text)
    target.parent.mkdir(parents=True, exist_ok=True)
    if adopt_org and before is not None and not (space / STATE).exists():
        # The deadlock this exists for: with no base, reconcile demands the org
        # file already agree with the ledger, and the base is only written after
        # a projection succeeds. A space that drifted before its first render
        # can therefore never render again -- four of ten spaces here, and the
        # hourly Phase-1 cycle failing since 2026-09-09. Adopting records where
        # reconciliation starts. It writes the base only; the org file and the
        # ledger are both left exactly as they are, so the next cycle performs a
        # real three-way merge instead of refusing.
        write_org_text(space / STATE, base_document(before))
        return "adopted the current org file as the projection base; nothing was rewritten"
    try:
        guard_projection(space, before, text)
    except ProjectionConflict as exc:
        return f"REFUSED — {exc}"
    write_org_text(target, text)
    write_org_text(space / STATE, base_document(text))
    return f"generated {ORG} ({text.count(chr(10))} lines)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data")))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--space")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit a complete versioned result for automation")
    ap.add_argument("--force", action="store_true",
                    help="bypass the legacy import scan; full source-preservation "
                         "checks still apply and cannot be overridden")
    ap.add_argument("--adopt-org", action="store_true",
                    help="establish the FIRST projection base from the org file as it "
                         "stands. Only meaningful when a space has no base and its org "
                         "and ledger disagree: that state otherwise has no exit, because "
                         "the base is written only after a successful projection and "
                         "projection is refused without one. Adopting records where "
                         "reconciliation starts; it rewrites neither the org file nor "
                         "the ledger.")
    a = ap.parse_args(argv)
    from spaces import discover_spaces
    root = a.root.resolve(strict=True)
    if a.space:
        relative = Path(a.space)
        selected = root / relative
        if (relative.is_absolute() or '..' in relative.parts or selected.is_symlink()
                or selected.resolve() != selected or not selected.is_dir()):
            ap.error('space must be an existing directory within the selected root')
        spaces = [selected]
    else:
        spaces = [space.path for space in discover_spaces(root, reject_aliases=True, reject_invalid=True)
                  if (space.path / '.datacore/events').is_dir()]
    refused = 0
    results = []
    for s in spaces:
        line = project_space(s, force=a.force, adopt_org=a.adopt_org)
        if line.startswith("REFUSED"):
            refused += 1
        status = ('refused' if line.startswith('REFUSED') else
                  'generated' if line.startswith('generated ') else 'authored')
        relative = s.relative_to(root).as_posix()
        results.append({'space': relative, 'status': status})
        if not a.json:
            print(f"  {relative:14} {line}")
    if a.json:
        # No task titles, source content or exception details in automation output.
        print(json.dumps({'version': 1, 'spaces': results}))
    # Non-zero so a caller can tell. A refusal that exits 0 is the same silence
    # this guard exists to break.
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
