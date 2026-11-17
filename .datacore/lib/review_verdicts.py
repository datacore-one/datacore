#!/usr/bin/env python3
"""review_verdicts.py: a task in review closes when its own pull request is settled.

WHY. The overnight agent leaves finished code work in REVIEW with the pull
request it opened in :NIGHTSHIFT_PR:. The owner's verdict on that work is what
happens to the pull request: merged means accepted, closed without merging
means rejected. Nothing wrote that verdict back, so the task stayed in review
and the daily briefing kept listing it. On 2026-11-16 43 such tasks were closed
by hand; 40 of them sat in the queue file (org/nightshift.org), which the
weekly GitHub reconciler (gh_reconcile.py) never reads.

The reconciler's rule is also wider on purpose: it closes an open task only
when EVERY reference the task names is settled. On a queue task that includes
the issue behind the pull request, which often stays open after the pull
request is merged or rejected. A review task is narrower (owner, 2026-11-16):
its own pull request decides.

    merged                    -> DONE
    closed without merging    -> CANCELLED
    open, or the lookup fails -> unchanged (a failed lookup proves nothing)

Scope: tasks in state REVIEW that carry :NIGHTSHIFT_PR:, in org/nightshift.org
and org/next_actions.org of every space. Nothing else is read or changed: no
other state, no other reference, no pull request.

Every close goes through org_workspace_adapter.py update, so a space's ledger
records it. The pull request's state comes from gh_reconcile.check_github_ref,
so both tools prove a verdict the same way.

Usage:
    review_verdicts.py [--data-dir DIR] [--dry-run] [--max N]

One status line is printed and written to $DATACORE_STATE/review-verdicts.txt:
    OK review-verdicts: closed 3 (done 2, cancelled 1), still open 12, unverified 1, left for the next run 0
    FAIL review-verdicts: <what could not be done>
Exit 0 on OK, 1 on FAIL. Closed tasks are listed below the line, by id.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

import gh_reconcile  # noqa: E402

ADAPTER = LIB / "org_workspace_adapter.py"
#: The queue file first: it is where the overnight agent leaves its work.
FILES = ("nightshift.org", "next_actions.org")
#: A wrong verdict rule must not close a whole queue in one run.
MAX_CLOSES = 50
RE_PR = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)")


def status_file() -> Path:
    root = os.environ.get("DATACORE_STATE") or Path.home() / ".datacore" / "state"
    return Path(root) / "review-verdicts.txt"


def _gh_ready() -> bool:
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _adapter(*args: str) -> dict:
    """One call of the task tool. Its answer, or {'error': ...}."""
    try:
        r = subprocess.run([sys.executable, str(ADAPTER), *args],
                           capture_output=True, text=True, timeout=900)
        out = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return out if isinstance(out, dict) else {"error": "unexpected answer from the task tool"}


def _spaces(data_dir: Path) -> list[Path]:
    return sorted(d for d in data_dir.iterdir()
                  if d.is_dir() and re.match(r"^\d+-", d.name)
                  and not d.name.endswith(("-archive", ".git")) and (d / "org").is_dir())


def _verdict(url: str) -> tuple[str, str, str]:
    """('DONE' | 'CANCELLED' | 'open' | 'unverified', 'owner/repo#n', date)."""
    m = RE_PR.search(url)
    ref = gh_reconcile.GithubRef(m.group(1), m.group(2), int(m.group(3)), "pr")
    name = f"{ref.full_repo}#{ref.num}"
    found = gh_reconcile.check_github_ref(ref)
    if found is None:
        return "unverified", name, ""
    if not found.get("terminal"):
        return "open", name, ""
    return found.get("close_as") or "DONE", name, (found.get("closed_at") or "")[:10]


def _close(org_file: Path, task_id: str, state: str, name: str, when: str) -> str:
    """'closed', 'changed meanwhile', or the reason it did not take."""
    now = _adapter("show", "--file", str(org_file), "--id", task_id)
    if now.get("state") != "REVIEW":
        return "changed meanwhile"
    on = f" on {when}" if when else ""
    if state == "DONE":
        props = [f"RESULT=Closed by review-verdicts: pull request {name} merged{on}"]
    else:
        props = [f"CANCEL_REASON=pull request {name} was closed without merging{on}",
                 f"RESULT=Closed by review-verdicts: pull request {name} was closed without merging{on}"]
    args = ["update", "--file", str(org_file), "--id", task_id, "--state", state]
    for p in props:
        args += ["--property", p]
    out = _adapter(*args)
    if out.get("updated") and (out.get("observed") or {}).get("STATE") == state:
        return "closed"
    return str(out.get("error") or f"the task reads {(out.get('observed') or {}).get('STATE')} afterwards")


def run(data_dir: Path, dry_run: bool = False, max_closes: int = MAX_CLOSES) -> dict:
    result = {"ok": False, "done": 0, "cancelled": 0, "open": 0, "unverified": 0, "left": 0,
              "closed": [], "line": ""}
    if not _gh_ready():
        result["line"] = "FAIL review-verdicts: the GitHub CLI is not logged in; nothing was checked"
        return result

    unreadable, failed, settled, seen = [], [], [], set()
    for space in _spaces(Path(data_dir)):
        for name in FILES:
            org_file = space / "org" / name
            if not org_file.exists():
                continue
            listed = _adapter("list", "--file", str(org_file), "--states", "REVIEW")
            if "tasks" not in listed:
                unreadable.append(f"{space.name}/org/{name}")
                continue
            for task in listed["tasks"]:
                url = str((task.get("properties") or {}).get("NIGHTSHIFT_PR") or "")
                if not RE_PR.search(url):
                    continue
                state, pr, when = _verdict(url)
                key = (space.name, task["id"])
                if state in ("open", "unverified"):
                    if key not in seen:
                        result[state] += 1
                    seen.add(key)
                    continue
                settled.append((key, org_file, state, pr, when))

    closed_keys: dict[tuple, str] = {}
    for key, org_file, state, pr, when in settled:
        if key not in closed_keys and len(closed_keys) >= max_closes:
            if key not in seen:
                result["left"] += 1
            seen.add(key)
            continue
        if not dry_run:
            took = _close(org_file, key[1], state, pr, when)
            if took == "changed meanwhile":
                continue
            if took != "closed":
                failed.append(f"{key[0]} {key[1]}: {took}")
                continue
        if key not in closed_keys:
            closed_keys[key] = state
            result["done" if state == "DONE" else "cancelled"] += 1
            result["closed"].append(f"{state} {key[0]} {org_file.stem} {key[1]} {pr}")

    n = result["done"] + result["cancelled"]
    counts = (f"{n} (done {result['done']}, cancelled {result['cancelled']}), "
              f"still open {result['open']}, unverified {result['unverified']}, "
              f"left for the next run {result['left']}")
    problems = []
    if unreadable:
        problems.append(f"{len(unreadable)} task file(s) could not be read: {', '.join(unreadable)}")
    if failed:
        problems.append(f"{len(failed)} close(s) did not take: {'; '.join(failed)}")
    if problems:
        result["line"] = f"FAIL review-verdicts: {'; '.join(problems)} (closed {counts})"
        return result
    result["ok"] = True
    result["line"] = (f"OK review-verdicts (dry run): would close {counts}" if dry_run
                      else f"OK review-verdicts: closed {counts}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Data",
                        help="Root data directory (default: ~/Data)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Look everything up and say what would close; change nothing")
    parser.add_argument("--max", type=int, default=MAX_CLOSES, dest="max_closes",
                        help=f"Closes per run (default {MAX_CLOSES}); the rest wait for the next run")
    args = parser.parse_args(argv)
    if not args.data_dir.exists():
        result = {"ok": False, "closed": [],
                  "line": f"FAIL review-verdicts: data directory not found: {args.data_dir}"}
    else:
        result = run(args.data_dir, dry_run=args.dry_run, max_closes=args.max_closes)
    print(result["line"])
    for row in result["closed"]:
        print(f"  {row}")
    if not args.dry_run:
        out = status_file()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result["line"] + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
