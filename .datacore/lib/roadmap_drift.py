#!/usr/bin/env python3
"""Reconcile roadmap claims against what the repos actually say.

Every claim in roadmap.yaml is hand-maintained, which means every claim decays.
On 2026-09-03 a single afternoon of reading repos found five epics whose state
the roadmap had wrong — provenance work shipped in the hub, eleven meta-engram
modules with no item, forty-nine admin routes with no coverage, an epic marked
ready whose PR was already open, and one marked in_progress that had shipped.

None of that was discoverable from the file. This makes it discoverable.

    python3 .datacore/lib/roadmap_drift.py           # report
    python3 .datacore/lib/roadmap_drift.py --strict  # exit 1 on drift, for CI

Checks, and what each one catches:

  gh-rejected    an epic points at an issue closed as NOT PLANNED — a dead
                 reference, which reads as progress if you only check `closed`
  gh-closed      an epic is not `done` but its issue closed as COMPLETED
  gh-open        an epic is `done` but its issue is still open
  no-tasks       a `now` epic that no task points at — nobody has started it,
                 and nothing distinguishes that from finished
  stale-note     a note asserting a state ("NOT SHIPPED", "as of") older than
                 45 days, which is long enough for the assertion to have rotted
"""
import argparse, json, re, subprocess, sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ROADMAP = REPO / "5-plur" / "roadmap.yaml"
ADAPTER = REPO / ".datacore/lib/org_workspace_adapter.py"
ORG = ["5-plur/org/next_actions.org", "5-plur/org/someday.org",
       "5-plur/org/inbox.org"]
STALE_DAYS = 45


def gh_state(ref):
    """`plur-ai/plur#123` -> (state, reason).

    The reason matters more than the state. A CLOSED issue can mean the work
    landed (COMPLETED) or that it was rejected (NOT_PLANNED), and those imply
    OPPOSITE things about the epic pointing at it: the first says the epic may
    be done, the second says the epic references a dead issue and needs a new
    one. Reporting only `closed` conflates them, which is how #445 — retired,
    not delivered — would read as progress.
    """
    m = re.match(r"([\w-]+/[\w-]+)#(\d+)$", str(ref).strip())
    if not m:
        return None, None
    r = subprocess.run(
        ["gh", "api", f"repos/{m.group(1)}/issues/{m.group(2)}",
         "--jq", "[.state, (.state_reason // \"-\")] | @tsv"],
        capture_output=True, text=True)
    parts = r.stdout.strip().split("\t")
    if len(parts) != 2:
        return None, None
    return parts[0].upper(), parts[1].upper()


def linked_epics():
    seen = set()
    for f in ORG:
        r = subprocess.run(["python3", str(ADAPTER), "list", "--file", f,
                            "--states", "NEXT,TODO,WAITING,REVIEW"],
                           capture_output=True, text=True, cwd=REPO)
        if r.returncode:
            continue
        for t in json.loads(r.stdout)["tasks"]:
            rid = (t.get("properties") or {}).get("ROADMAP")
            if rid:
                seen.add(rid)
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when drift is found (for CI)")
    ap.add_argument("--no-github", action="store_true",
                    help="skip the GitHub checks (offline)")
    args = ap.parse_args()

    r = yaml.safe_load(ROADMAP.read_text())
    items = r["items"]
    have_tasks = linked_epics()
    findings = defaultdict(list)

    for i in items:
        ref, status = i.get("gh"), i.get("status")
        if ref and not args.no_github:
            st, why = gh_state(ref)
            if st == "CLOSED" and status != "done" and why == "NOT_PLANNED":
                findings["gh-rejected"].append(
                    f"{i['id']} is {status!r} and {ref} was closed as NOT PLANNED — "
                    f"the epic points at a rejected issue and needs a live one")
            elif st == "CLOSED" and status != "done":
                findings["gh-closed"].append(
                    f"{i['id']} is {status!r} but {ref} is CLOSED as completed — "
                    f"the epic may be done, or narrower than the issue it names")
            elif st == "OPEN" and status == "done":
                findings["gh-open"].append(
                    f"{i['id']} is done but {ref} is still OPEN")

        if i.get("horizon") == "now" and i["id"] not in have_tasks:
            findings["no-tasks"].append(
                f"{i['id']} is on the now horizon and no task points at it — "
                f"nothing distinguishes 'not started' from 'finished'")

        # Only flag a date that sits NEXT TO a state claim. Searching the whole
        # note matched any note that happened to contain both a date and the
        # word "today" somewhere, which fired on citations — a recorded design
        # session is not a claim about the present, and flagging it teaches the
        # reader to ignore the check.
        note = str(i.get("note", "")) + " " + str(i.get("evidence") or "")
        for m in re.finditer(r"(20\d\d)-(\d\d)-(\d\d)", note):
            try:
                when = date(*map(int, m.groups()))
            except ValueError:
                continue
            if (date.today() - when).days <= STALE_DAYS:
                continue
            near = note[max(0, m.start() - 90):m.end() + 90]
            if re.search(r"NOT SHIPPED|as of\b|currently|today|still\b|"
                         r"remains|unchanged", near, re.I):
                findings["stale-note"].append(
                    f"{i['id']} asserts a state dated {when} "
                    f"({(date.today() - when).days}d old) — re-check or restate")
                break

    total = sum(len(v) for v in findings.values())
    print(f"{len(items)} epics checked · {total} drift finding(s)\n")
    for kind in ("gh-rejected", "gh-closed", "gh-open", "no-tasks", "stale-note"):
        rows = findings.get(kind) or []
        if not rows:
            continue
        print(f"── {kind} ({len(rows)})")
        for line in rows:
            print(f"   {line}")
        print()
    if not total:
        print("No drift. The roadmap and the repos agree.")
    return 1 if (args.strict and total) else 0


if __name__ == "__main__":
    sys.exit(main())
