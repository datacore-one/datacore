#!/usr/bin/env python3
"""Check open tasks against the live state of the GitHub issues/PRs they name.

Staleness by date only tells you the org record was untouched. It cannot tell
you the task is DONE — a task can be date-fresh while describing a world that
no longer exists, because its completion depends on an external system that
changed without anyone touching the org file (ENG-2026-08-10-014).

So this resolves every #NNN reference in a task heading against the repo and
reports tasks whose referent is already closed or merged. Read-only: it
proposes, it does not close anything.
"""
import argparse, json, re, subprocess, sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / ".datacore/lib/org_workspace_adapter.py"
FILES = ["5-plur/org/next_actions.org", "5-plur/org/someday.org",
         "5-plur/org/inbox.org"]
# A bare #NNN in a PLUR task means plur-ai/plur unless the heading says otherwise.
DEFAULT_REPO = "plur-ai/plur"
REPO_HINTS = [(r'enterprise#(\d+)|enterprise\b', 'plur-ai/enterprise'),
              (r'omnigent', 'omnigent-ai/omnigent'),
              (r'hermes-agent', 'NousResearch/hermes-agent'),
              (r'website', 'plur-ai/website')]


def tasks():
    for f in FILES:
        r = subprocess.run(["python3", str(ADAPTER), "list", "--file", f,
                            "--states", "NEXT,TODO,WAITING,REVIEW"],
                           capture_output=True, text=True, cwd=REPO)
        if r.returncode:
            continue
        for t in json.loads(r.stdout)["tasks"]:
            t["_f"] = f
            yield t


def repo_for(heading):
    for pat, repo in REPO_HINTS:
        if re.search(pat, heading, re.I):
            return repo
    return DEFAULT_REPO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    refs = defaultdict(list)          # (repo, num) -> [task, ...]
    for t in tasks():
        for num in set(re.findall(r'#(\d{2,5})\b', t["heading"])):
            refs[(repo_for(t["heading"]), int(num))].append(t)
    print(f"{len(refs)} distinct issue/PR references across "
          f"{len({id(x) for v in refs.values() for x in v})} tasks\n")

    closed = []
    for (repo, num), ts in list(refs.items())[:args.limit]:
        r = subprocess.run(["gh", "api", f"repos/{repo}/issues/{num}",
                            "--jq", "[.state, (.pull_request!=null), .title]"],
                           capture_output=True, text=True)
        if r.returncode:
            continue
        try:
            state, is_pr, title = json.loads(r.stdout)
        except Exception:
            continue
        if state == "closed":
            for t in ts:
                closed.append((repo, num, "PR" if is_pr else "issue", title, t))

    print(f"{len(closed)} open tasks name a CLOSED referent — candidates to close:\n")
    seen = set()
    for repo, num, kind, title, t in sorted(closed, key=lambda x: x[4]["heading"]):
        key = (t["heading"], num)
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{t['state']:7}] {t['heading'][:78]}")
        print(f"            -> {repo}#{num} ({kind}, CLOSED) {title[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
