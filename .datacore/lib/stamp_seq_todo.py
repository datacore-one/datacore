#!/usr/bin/env python3
"""Stamp the canonical DIP-0009 v1.1 #+SEQ_TODO header on GTD task files.

Why: four disagreeing state vocabularies stalled ~80 tasks invisibly
(2026-07-24 audit). org-workspace ≥0.5.0 seeds the canonical set at parse
time, so this stamp is belt-and-suspenders for every OTHER consumer: Emacs
org-mode UI, raw-regex tools, and humans reading the file.

Scope: inbox.org, next_actions.org, someday.org, nightshift.org, ai.org,
research_learning.org in every space's org/ dir. Deliberately skipped:
projects.org (own PROJECT/ACTIVE/PAUSED sequence), habits.org (custom ASSIGN
keyword), archive files (historical snapshots).

Idempotent. Usage:
    python3 .datacore/lib/stamp_seq_todo.py [--dry-run] [--data-dir PATH]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spaces import discover_spaces  # noqa: E402

# DIP-0009 v2.0 canon (2026-08-29) — must match projector.SEQ_TODO and
# state_loop_rollout.CANON, or this stamper silently reverts the rollout.
CANONICAL =("#+SEQ_TODO: TODO(t) NEXT(n!) WAITING(w!) REVIEW(r!) "
            "| DONE(d!) DEFERRED(f!) CANCELLED(c!)")

SCOPE = {"inbox.org", "next_actions.org", "someday.org", "nightshift.org",
         "ai.org", "research_learning.org"}


def stamp(path: Path, dry_run: bool) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    seq_idx = next((i for i, l in enumerate(lines)
                    if l.startswith("#+SEQ_TODO:") or l.startswith("#+TODO:")), None)

    if seq_idx is not None:
        if lines[seq_idx].rstrip("\n") == CANONICAL:
            return "ok"
        action = f"replace ({lines[seq_idx].strip()[:60]}...)"
        if not dry_run:
            lines[seq_idx] = CANONICAL + "\n"
    else:
        # Insert after the leading #+ directive block (or at top).
        insert_at = 0
        for i, l in enumerate(lines):
            if l.startswith("#+"):
                insert_at = i + 1
            elif l.strip():
                break
        action = f"insert at line {insert_at + 1}"
        if not dry_run:
            lines.insert(insert_at, CANONICAL + "\n")

    if not dry_run:
        path.write_text("".join(lines), encoding="utf-8")
    return action


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data-dir", default=str(Path.home() / "Data"))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    changed = 0
    for discovered in discover_spaces(data_dir):
        space = discovered.path / "org"
        for f in sorted(space.glob("*.org")):
            if f.name not in SCOPE or "archive" in f.name.lower():
                continue
            result = stamp(f, args.dry_run)
            rel = f.relative_to(data_dir)
            if result != "ok":
                changed += 1
                print(f"{'DRY ' if args.dry_run else ''}{rel}: {result}")
    print(f"{'Would change' if args.dry_run else 'Changed'}: {changed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
