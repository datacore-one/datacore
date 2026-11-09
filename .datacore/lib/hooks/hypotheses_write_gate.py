#!/usr/bin/env python3
"""Commit gate: a venture's hypotheses board lands only if the heartbeat can read it.

WHY. Agents edit hypotheses.yaml by hand as well as through the ventures store.
On 2026-10-02 a board got a lane the heartbeat does not know; on 2026-10-12
another got unescaped quotes and stopped being YAML. The heartbeat then refused
both ventures on every tick for weeks (ops report 2026-10-23). The store's own
write check (`ventures.lib.hypothesis_store.check_text`) is run on the STAGED
bytes of the board, so nothing that writes it -- editor, agent or script --
can land a board the heartbeat would refuse.

Which file: the repository's `venture.yaml` (as staged) names it with
`hypotheses_file`; without one it is `hypotheses.yaml` at the repository root.
Nothing staged there -> exit 0 at once, without importing anything.

Exit 0 = clean or not concerned, 1 = refused (reason on stderr), 2 = cannot run
(the ventures module or its dependencies are missing: the board is not
committed unchecked).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT = "hypotheses.yaml"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def staged_paths(repo: Path) -> set:
    out = _git(repo, "-c", "core.quotePath=false", "diff", "--cached", "--name-only",
               "--diff-filter=ACMR", "-z").stdout
    return {p for p in out.split("\0") if p}


def board_path(repo: Path, staged: set) -> str:
    """The board the staged venture.yaml names, else the default."""
    shown = _git(repo, "show", ":venture.yaml")
    if shown.returncode:
        return DEFAULT
    import yaml
    try:
        config = yaml.safe_load(shown.stdout) or {}
    except yaml.YAMLError:
        return DEFAULT  # venture.yaml is the loader's business, not this gate's
    named = config.get("hypotheses_file") if isinstance(config, dict) else None
    return named if isinstance(named, str) and named.strip() else DEFAULT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[3]))
    ap.add_argument("--staged", action="store_true", help="check the index (the only mode)")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    staged = staged_paths(repo)
    if not staged or (DEFAULT not in staged and "venture.yaml" not in staged
                      and _git(repo, "cat-file", "-e", ":venture.yaml").returncode):
        return 0
    try:
        board = board_path(repo, staged)
    except ImportError:
        print("pre-commit: BLOCKED — the hypotheses gate needs PyYAML (python3 -m pip install pyyaml)",
              file=sys.stderr)
        return 2
    if board not in staged:
        return 0
    modules = Path(args.root) / ".datacore" / "modules"
    if not (modules / "ventures" / "lib" / "hypothesis_store.py").is_file():
        print(f"pre-commit: BLOCKED — {board} is staged but the ventures module is not installed "
              f"under {modules}, so the board cannot be checked", file=sys.stderr)
        return 2
    sys.path.insert(0, str(modules))
    try:
        from ventures.lib.hypothesis_store import check_text
    except Exception as exc:  # noqa: BLE001 -- any import failure means the gate cannot run
        print(f"pre-commit: BLOCKED — the hypotheses gate cannot load the ventures store "
              f"({type(exc).__name__}: {str(exc)[:160]}); {board} was not committed unchecked",
              file=sys.stderr)
        return 2
    text = _git(repo, "show", f":{board}").stdout
    try:
        check_text(text)
    except ValueError as exc:
        print(f"pre-commit: BLOCKED — {board}: {exc}. The venture heartbeat would refuse this "
              "board; fix the file (or write it through the ventures hypothesis tools) and "
              "commit again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
