#!/usr/bin/env python3
"""Rehearse the Phase 1 flip AND its reversal on a scratch space (DIP-0046 F2a).

Phase 1 is the migration: `org/next_actions.org` stops being authored and
becomes generated from the ledger, and is gitignored. The DIP calls that
reversible. Until this script ran, nobody had ever reversed it — and a phase
billed as reversible that has never been reversed is a claim, not a property.
This installation's history is 610 stranded commits, 1,204 rewritten ids and
110 wiped files; claims are not enough.

The drill runs on a throwaway space, never a real one, and asserts on state
rather than on the absence of errors:

  FLIP      org file becomes generated + gitignored; the ledger drives it.
            Assert: a task added to the LEDGER appears in the generated file.

  REVERSE   un-gitignore, commit the current projection as the authored file,
            resume hand-authoring.
            Assert: every task survives, and a task added BY HAND afterwards
            persists across a regeneration — which is the real question, since
            a reversal that leaves the generator still overwriting the file has
            reverted nothing.

WHAT THE REVERSAL GENUINELY COSTS, stated because the drill would otherwise
imply it is free: the git history of that file for the period it was untracked
is gone. `git log next_actions.org` shows a gap. The facts survive in the
ledger; the file's own history does not.

    phase1_drill.py [--keep]     --keep leaves the scratch space for inspection
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))


def git(repo: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class Drill:
    def __init__(self, root: Path):
        self.root = root
        self.space = root / "9-drill"
        self.org = self.space / "org" / "next_actions.org"
        self.failures: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if not cond else ""))
        if not cond:
            self.failures.append(name)

    # ---- setup ---------------------------------------------------------
    def setup(self) -> None:
        (self.space / "org").mkdir(parents=True)
        (self.space / ".datacore" / "events").mkdir(parents=True)
        git(self.space, "init", "-q")
        git(self.space, "config", "user.email", "drill@datacore")
        git(self.space, "config", "user.name", "drill")
        git(self.space, "config", "core.hooksPath", str(self.space / ".git" / "hooks"))
        self.org.write_text(
            "* Focus\n"
            "** TODO Authored before the flip\n"
            "   :PROPERTIES:\n"
            "   :ID: drill-authored-1\n"
            "   :END:\n"
        )
        (self.space / ".gitignore").write_text("*.projected.org\n")
        git(self.space, "add", "-A")
        git(self.space, "commit", "-qm", "drill: authored state")

        from ledger.genesis import import_space
        import_space(self.space)
        print(f"  setup: scratch space at {self.space}")

    # ---- phase 1 -------------------------------------------------------
    def flip(self) -> None:
        print("\nFLIP -> Phase 1 (org generated, gitignored)")
        from ledger.fold import fold
        from ledger.log import EventLog, read_events
        from ledger.projector import project

        # A fact that exists ONLY in the ledger. If the generated file shows it,
        # the ledger is genuinely driving the file.
        EventLog(self.space, "mac").append("item.create", {
            "id": "drill-ledger-only", "title": "Created in the ledger after the flip",
            "state": "TODO", "tags": ["drill"]})

        gi = self.space / ".gitignore"
        gi.write_text(gi.read_text() + "org/next_actions.org\n")
        git(self.space, "rm", "-q", "--cached", "org/next_actions.org")

        self.org.write_text(project(fold(read_events(self.space)), space=self.space.name).text)
        git(self.space, "add", "-A")
        git(self.space, "commit", "-qm", "drill: flip to Phase 1")

        text = self.org.read_text()
        self.check("ledger-only task appears in the generated file",
                   "Created in the ledger after the flip" in text)
        self.check("pre-existing authored task survives the flip",
                   "Authored before the flip" in text)
        rc, out = git(self.space, "ls-files", "--error-unmatch", "org/next_actions.org")
        self.check("org file is untracked while generated", rc != 0)

    # ---- reversal ------------------------------------------------------
    def reverse(self) -> None:
        print("\nREVERSE -> Phase 0 (org authored again)")
        gi = self.space / ".gitignore"
        gi.write_text("".join(l for l in gi.read_text().splitlines(keepends=True)
                              if l.strip() != "org/next_actions.org"))
        # The current projection becomes the authored file — this is the
        # documented procedure, and it is what makes the facts survive.
        git(self.space, "add", "-f", "org/next_actions.org")
        git(self.space, "add", "-A")
        git(self.space, "commit", "-qm", "drill: revert to Phase 0 (projection becomes authored)")

        rc, _ = git(self.space, "ls-files", "--error-unmatch", "org/next_actions.org")
        self.check("org file is tracked again", rc == 0)
        text = self.org.read_text()
        self.check("nothing was lost in the reversal",
                   "Authored before the flip" in text
                   and "Created in the ledger after the flip" in text)

        # THE REAL TEST. Reverting is meaningless if the generator still owns
        # the file: a hand edit must survive, which it only does if nothing
        # regenerates over it.
        self.org.write_text(text + "\n** TODO Hand-authored after the reversal\n"
                                   "   :PROPERTIES:\n   :ID: drill-hand-1\n   :END:\n")
        git(self.space, "add", "-A")
        git(self.space, "commit", "-qm", "drill: hand-authored after reversal")

        from ledger.shadow import compare
        compare(self.space)          # regenerate the shadow, as the daily job does
        self.check("hand edit survives a projection run",
                   "Hand-authored after the reversal" in self.org.read_text())
        stray = list((self.space / "org").glob("*.projected.org"))
        self.check("projection did not reappear inside org/", not stray, str(stray))

    def run(self) -> int:
        self.setup()
        self.flip()
        self.reverse()
        print(f"\nphase-1 drill: "
              f"{'FAILED — ' + ', '.join(self.failures) if self.failures else 'flip and reversal both verified'}")
        return 1 if self.failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="leave the scratch space in place")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="phase1-drill-")
    try:
        return Drill(Path(tmp)).run()
    finally:
        if a.keep:
            print(f"  scratch space kept at {tmp}")
        else:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
