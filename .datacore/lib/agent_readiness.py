#!/usr/bin/env python3
"""Can an agent actually work from this roadmap, unsupervised?

Not "is the roadmap good" — a different question with a different answer. This
asks the five things an agent needs to be true before it can pick work up and
finish it without a human in the loop:

  1. SELECT   — can it find work that is genuinely available?
  2. LOCATE   — does the work say where it happens?
  3. FINISH   — does it say what finishing means?
  4. VERIFY   — can the agent check that itself?
  5. AVOID    — is work it cannot do clearly marked?

Every check is mechanical, so this is re-runnable rather than re-arguable. A
finding is a defect in the ROADMAP or the TASK POOL, never in the agent.

    python3 .datacore/lib/agent_readiness.py [--strict]
"""
import argparse, json, re, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ROADMAP = REPO / "5-plur" / "roadmap.yaml"
ADAPTER = REPO / ".datacore/lib/org_workspace_adapter.py"
ORG = ["5-plur/org/next_actions.org", "5-plur/org/someday.org",
       "5-plur/org/inbox.org"]
VERIFIABLE = {"test", "metric", "url", "artifact", "merged-pr"}
JUDGEMENT = {"decision", "signed", "screenshot"}


def tasks():
    out = []
    for f in ORG:
        r = subprocess.run(["python3", str(ADAPTER), "list", "--file", f,
                            "--states", "NEXT,TODO,WAITING,REVIEW"],
                           capture_output=True, text=True, cwd=REPO)
        if r.returncode:
            continue
        for t in json.loads(r.stdout)["tasks"]:
            t["_f"] = f
            out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    r = yaml.safe_load(ROADMAP.read_text())
    items = {i["id"]: i for i in r["items"]}
    ts = tasks()
    ai = [t for t in ts if "AI" in (t.get("tags") or [])]
    findings = defaultdict(list)

    # 1 SELECT ---------------------------------------------------------------
    for t in ai:
        p = t.get("properties") or {}
        rid = p.get("ROADMAP")
        if not rid:
            findings["select"].append(
                f"AI task with no epic: {t['heading'][:60]}")
            continue
        epic = items.get(rid)
        if epic and epic.get("blocked_on") == "human":
            findings["select"].append(
                f"AI task under an epic blocked on a human ({rid}): "
                f"{t['heading'][:52]}")

    # 2 LOCATE ---------------------------------------------------------------
    unassigned = [t for t in ai
                  if (t.get("properties") or {}).get("SURFACE") in (None, "unassigned")]
    for t in unassigned[:6]:
        findings["locate"].append(
            f"AI task with no surface — agent cannot tell which repo: "
            f"{t['heading'][:56]}")
    if len(unassigned) > 6:
        findings["locate"].append(
            f"...and {len(unassigned) - 6} more AI tasks with SURFACE unassigned")

    # 3 FINISH ---------------------------------------------------------------
    for t in ai:
        p = t.get("properties") or {}
        rid = p.get("ROADMAP")
        epic = items.get(rid) if rid else None
        if epic and not epic.get("done_when") and not p.get("DONE_WHEN"):
            findings["finish"].append(
                f"AI task whose epic {rid} has no definition of done: "
                f"{t['heading'][:50]}")

    # 4 VERIFY ---------------------------------------------------------------
    for i in r["items"]:
        dw = i.get("done_when") or {}
        ev = dw.get("evidence")
        if not ev:
            continue
        if i.get("delegable") and ev in JUDGEMENT:
            findings["verify"].append(
                f"{i['id']} is delegable but its evidence is {ev!r} — an agent "
                f"cannot produce a {ev}; either it is not delegable, or the "
                f"condition is wrong")

    # 5 AVOID ----------------------------------------------------------------
    for i in r["items"]:
        if i.get("horizon") == "now" and i.get("blocked_on") == "human" \
                and i.get("delegable") is not False:
            findings["avoid"].append(
                f"{i['id']} is now + blocked on a human but delegable is not "
                f"explicitly false — an agent may try it")
    # delegability that has never been demonstrated
    done_by_epic = Counter()
    for t in ts:
        rid = (t.get("properties") or {}).get("ROADMAP")
        if rid and t["state"] == "REVIEW":
            done_by_epic[rid] += 1
    unproven = [i["id"] for i in r["items"]
                if i.get("delegable") and i.get("horizon") in ("now", "next")
                and not done_by_epic.get(i["id"])]
    if unproven:
        findings["avoid"].append(
            f"{len(unproven)} epics are marked delegable with no agent work yet "
            f"in review — delegability is asserted, not demonstrated: "
            f"{', '.join(unproven[:8])}"
            + (" ..." if len(unproven) > 8 else ""))

    LABEL = {"select": "1 SELECT — can an agent find available work",
             "locate": "2 LOCATE — does the work say where it happens",
             "finish": "3 FINISH — does it say what finishing means",
             "verify": "4 VERIFY — can the agent check that itself",
             "avoid":  "5 AVOID  — is undoable work clearly marked"}

    total = sum(len(v) for v in findings.values())
    print(f"{len(r['items'])} epics · {len(ts)} open tasks · {len(ai)} tagged :AI:")
    print(f"{total} readiness finding(s)\n")
    for k in ("select", "locate", "finish", "verify", "avoid"):
        rows = findings.get(k) or []
        print(f"── {LABEL[k]} — {len(rows)}")
        for line in rows[:8]:
            print(f"   {line}")
        if len(rows) > 8:
            print(f"   ...and {len(rows) - 8} more")
        print()
    return 1 if (args.strict and total) else 0


if __name__ == "__main__":
    sys.exit(main())
