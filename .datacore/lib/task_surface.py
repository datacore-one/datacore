#!/usr/bin/env python3
"""Assign SURFACE — WHERE a task lands — as a second axis beside ROADMAP.

ROADMAP says which outcome a task serves. It does not say where the work
happens, and those are different questions: an R-019 task and an R-041 task
serve different outcomes but may both land in core, while two tasks serving the
SAME outcome can land in different repos with different reviewers.

That gap is what makes an AI review batch expensive to read. The reviewer opens
a list ordered by outcome and has to reconstruct, per task, which repo it
touches and who signs it off. SURFACE makes that the grouping key instead.

Deliberately about PLACE, not topic. A task belongs to the surface whose
working copy you would open to do it.
"""
import argparse, json, re, subprocess, sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / ".datacore/lib/org_workspace_adapter.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from spaces import space_for  # noqa: E402

#: The product space's org files (install.yaml roles.product).
PRODUCT = space_for("product", REPO, "0-personal")
FILES = [f"{PRODUCT}/org/{n}.org" for n in ("next_actions", "someday", "inbox")]

# First match wins. Order matters: the more specific surfaces come first,
# because a task can legitimately mention two and only one is where you work.
# The table is this install's own (repos, prospects, products), so it lives in
# the gitignored config/task-themes.local.yaml under `surfaces:`; the shipped
# task-themes.yaml carries a neutral default (INS-3).
from task_triage import themes  # noqa: E402

SURFACES = list(themes("surfaces").items())
DEFAULT = "unassigned"


def surface_for(heading: str) -> str:
    for name, pat in SURFACES:
        if re.search(pat, heading, re.I):
            return name
    return DEFAULT


def load():
    for f in FILES:
        r = subprocess.run(["python3", str(ADAPTER), "list", "--file", f,
                            "--states", "NEXT,TODO,WAITING,REVIEW"],
                           capture_output=True, text=True, cwd=REPO)
        if r.returncode:
            continue
        for t in json.loads(r.stdout)["tasks"]:
            t["_f"] = f
            yield t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ai-only", action="store_true",
                    help="report only :AI: tagged tasks — the review batch")
    args = ap.parse_args()

    tasks = [t for t in load()
             if not args.ai_only or "AI" in (t.get("tags") or [])]
    counts, todo = Counter(), []
    for t in tasks:
        s = surface_for(t["heading"])
        counts[s] += 1
        p = t.get("properties") or {}
        if p.get("SURFACE") != s and p.get("ID"):
            todo.append((t["_f"], p["ID"], s))

    label = "AI-tagged tasks" if args.ai_only else "open tasks"
    print(f"{len(tasks)} {label} by SURFACE — where the work actually lands\n")
    for k, v in counts.most_common():
        print(f"  {v:5}  {k}")

    if args.apply:
        # Direct file edit: one subprocess per task is minutes for a pool this
        # size, and the property is a single line under the drawer.
        by_file = {}
        for f, tid, s in todo:
            by_file.setdefault(f, {})[tid] = s
        written = 0
        for f, mapping in by_file.items():
            path = REPO / f
            lines = path.read_text().split("\n")
            out = []
            for ln in lines:
                if re.match(r"^\s*:SURFACE:", ln):
                    continue                      # rewritten below; idempotent
                out.append(ln)
                m = re.match(r"^(\s*):ID:\s*(\S+)\s*$", ln)
                if m and m.group(2) in mapping:
                    out.append(f"{m.group(1)}:SURFACE: {mapping[m.group(2)]}")
                    written += 1
            path.write_text("\n".join(out))
        print(f"\napplied SURFACE to {written} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
