#!/usr/bin/env python3
"""Audit where each Datacore module lives, and whether that matches policy.

The policy is stated in the root .gitignore and nowhere enforced:

    a module that declares `repository:` in module.yaml is an independent repo,
    cloned into place, and stays excluded from the root repo;
    a module that declares none is core, and belongs in the .gitignore allowlist.

Nothing checked that either half held. On 2026-08-10 the `github` module turned
out to satisfy neither: no `repository:` field AND no allowlist entry, so it was
invisible to git anywhere. Two bugs fixed in it that day — a failed gh query
reporting as an empty result, and a date-suffixed dedup key duplicating bodies
into DONE tasks — had no history and no backup. It had been in that state since
April.

Verdicts:
  core          no repository:, allowlisted            — tracked in the root repo
  independent   repository: + a real clone             — lives in its own repo
  ORPHAN        no repository:, not allowlisted, no    — in NO repository at all;
                .git                                     one `rm -rf` from gone
  UNCLONED      repository: declared, but not a clone  — local state matches no
                                                         remote; edits are unbacked
  UNDECLARED    a real clone, but no repository:       — nothing records where it
                                                         came from or how to
                                                         restore it
  CONTRADICTORY declares repository: AND is allowlisted — the two rules disagree

Exit codes: 0 clean, 1 problems found. Use --json for machine output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent
MODULES = DATA_DIR / ".datacore" / "modules"
GITIGNORE = DATA_DIR / ".gitignore"

#: Directories under modules/ that are not modules.
NOT_MODULES = {"node_modules", "state", "__pycache__"}

OK_VERDICTS = {"core", "independent"}


def allowlisted_modules(gitignore: Path) -> set[str]:
    """Module names re-included via `!.datacore/modules/<name>/`."""
    if not gitignore.exists():
        return set()
    return set(
        re.findall(r"^!\.datacore/modules/([^/\s]+)/?\s*$", gitignore.read_text(), re.M)
    )


def declared_repository(module_dir: Path) -> str | None:
    """The `repository:` value from module.yaml, if any."""
    manifest = module_dir / "module.yaml"
    if not manifest.exists():
        return None
    for line in manifest.read_text(errors="replace").splitlines():
        m = re.match(r"^repository:\s*(\S+)", line)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def classify(name: str, module_dir: Path, allowlist: set[str]) -> dict:
    repo = declared_repository(module_dir)
    is_clone = (module_dir / ".git").exists()
    listed = name in allowlist

    if repo and listed:
        verdict, detail = "CONTRADICTORY", (
            f"declares repository: {repo} AND is allowlisted for the root repo — "
            "the two rules disagree; pick one")
    elif repo and is_clone:
        verdict, detail = "independent", f"clone of {repo}"
    elif repo and not is_clone:
        verdict, detail = "UNCLONED", (
            f"declares repository: {repo} but is not a clone — local edits match "
            "no remote and are unbacked")
    elif not repo and is_clone:
        verdict, detail = "UNDECLARED", (
            "is a git clone but module.yaml records no repository: — nothing says "
            "where it came from or how to restore it")
    elif not repo and listed:
        verdict, detail = "core", "tracked in the root repo"
    else:
        verdict, detail = "ORPHAN", (
            "no repository:, not allowlisted, not a clone — this module is in NO "
            "repository at all; edits have no history and no backup")

    return {
        "module": name,
        "verdict": verdict,
        "detail": detail,
        "repository": repo,
        "is_clone": is_clone,
        "allowlisted": listed,
        "ok": verdict in OK_VERDICTS,
    }


def audit(modules_dir: Path = MODULES, gitignore: Path = GITIGNORE) -> list[dict]:
    allowlist = allowlisted_modules(gitignore)
    out = []
    if not modules_dir.exists():
        return out
    for d in sorted(modules_dir.iterdir()):
        if not d.is_dir() or d.name in NOT_MODULES or d.name.startswith("."):
            continue
        if not (d / "module.yaml").exists() and not any(d.iterdir()):
            # An empty stub directory left behind by a module that was never
            # cloned here (datacore-campaigns on this Mac, 2026-08-10).
            out.append({
                "module": d.name, "verdict": "EMPTY-STUB",
                "detail": "empty directory — a module that was never cloned onto this host",
                "repository": None, "is_clone": False, "allowlisted": d.name in allowlist,
                "ok": True,
            })
            continue
        out.append(classify(d.name, d, allowlist))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet", action="store_true", help="only report problems")
    args = ap.parse_args()

    rows = audit()
    problems = [r for r in rows if not r["ok"]]

    if args.json:
        print(json.dumps({"modules": rows, "problems": len(problems)}, indent=2))
        return 1 if problems else 0

    if not args.quiet:
        width = max((len(r["module"]) for r in rows), default=10)
        for r in sorted(rows, key=lambda r: (r["ok"], r["module"])):
            mark = " " if r["ok"] else "!"
            print(f" {mark} {r['module']:<{width}}  {r['verdict']:<14} {r['detail']}")
        print()

    if not problems:
        print(f"OK — {len(rows)} module(s), all placed consistently.")
        return 0

    print(f"{len(problems)} module(s) placed inconsistently:")
    for r in problems:
        print(f"  {r['module']}: {r['verdict']} — {r['detail']}")
    print("\nFix: give the module a repository: field and clone it, or add "
          "'!.datacore/modules/<name>/' to the root .gitignore. ORPHAN is the "
          "urgent one — that code exists in no repository at all.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
