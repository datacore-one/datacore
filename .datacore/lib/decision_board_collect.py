#!/usr/bin/env python3
"""File saved decision-board answers into the default private directory.

A board page can only download its answers (<slug>.decisions.json) to the
browser's download folder. This moves them next to the boards themselves, in
$DATACORE_STATE/decision-boards/answers/ (default ~/.datacore/state/...), so
every board and every answer lives in one owner-only place that the machine's
backup already covers. The Downloads copy is removed only after the filed copy
has been written and read back identical.

Usage:
    decision_board_collect.py                  # file every *.decisions.json in ~/Downloads
    decision_board_collect.py --dry-run        # show what would be filed
    decision_board_collect.py --only <slug>    # file just that board's answers
    decision_board_collect.py --latest <slug>  # print the path of the newest filed answers
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_utils import atomic_write_text, private_state_directory  # noqa: E402


def answers_dir() -> Path:
    d = private_state_directory("decision-boards") / "answers"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def target_for(data: dict, src: Path, dest_dir: Path) -> Path:
    date = str(data.get("savedAt", ""))[:10] or "undated"
    stamp = str(data.get("savedAt", "")).replace(":", "").replace("-", "")[:15]
    base = dest_dir / f"{date}-{data['board']}.decisions.json"
    if not base.exists() or base.read_text() == src.read_text():
        return base
    return dest_dir / f"{date}-{data['board']}.{stamp or 'x'}.decisions.json"


def collect(downloads: Path, only: str | None, dry: bool) -> int:
    dest_dir = answers_dir()
    filed = 0
    for src in sorted(downloads.glob("*.decisions.json")):
        try:
            data = json.loads(src.read_text())
        except (OSError, ValueError) as e:
            print(f"skip {src.name}: not readable JSON ({e})")
            continue
        if not isinstance(data, dict) or not data.get("board"):
            print(f"skip {src.name}: no 'board' field")
            continue
        if only and data["board"] != only:
            continue
        dest = target_for(data, src, dest_dir)
        print(f"{'would file' if dry else 'filed'} {src.name} -> {dest}")
        if dry:
            continue
        text = src.read_text()
        atomic_write_text(dest, text)
        dest.chmod(0o600)
        if dest.read_text() != text:
            print(f"  copy differs; kept {src}")
            continue
        src.unlink()
        filed += 1
    return filed


def latest(slug: str) -> None:
    hits = sorted(answers_dir().glob(f"*-{slug}*.decisions.json"))
    if not hits:
        raise SystemExit(f"no filed answers for {slug}")
    print(hits[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--downloads", default=str(Path.home() / "Downloads"))
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--latest")
    a = ap.parse_args()
    if a.latest:
        latest(a.latest)
        return 0
    collect(Path(a.downloads), a.only, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
