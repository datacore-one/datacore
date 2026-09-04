#!/usr/bin/env python3
"""Can an agent actually work from this roadmap, unsupervised?

Not "is the roadmap good" — a different question with a different answer. This
asks the five things an agent needs to be true before it can pick work up and
finish it without a human in the loop:

  1. SELECT   — can it find work that is genuinely available?
  2. LOCATE   — does the work say where it happens?
  3. FINISH   — does the TASK say what finishing means (not just its epic)?
  3b. BRIEF   — is there enough written down to act on?
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
    #
    # This check asked the WRONG QUESTION until 2026-09-03: it verified the
    # EPIC had a done_when and reported zero findings. But an epic condition is
    # a milestone test, not a task test. Fourteen tasks shared R-070's "fewer
    # than five PRs are open in core" — an agent that finishes one of them and
    # checks that condition finds it FALSE, because the other thirteen are not
    # done. It cannot tell its own work succeeded.
    #
    # A task needs its own criterion, or it needs to be the only task under its
    # epic. Anything else and completion is unobservable to the agent doing it.
    by_epic = Counter((t.get("properties") or {}).get("ROADMAP") for t in ts
                      if (t.get("properties") or {}).get("ROADMAP"))
    for t in ai:
        p = t.get("properties") or {}
        rid = p.get("ROADMAP")
        if p.get("DONE_WHEN"):
            continue                       # has its own — fine
        epic = items.get(rid) if rid else None
        if not epic:
            continue                       # already caught by SELECT
        if by_epic[rid] > 1:
            findings["finish"].append(
                f"no task-level DONE_WHEN, and epic {rid}'s condition is shared "
                f"by {by_epic[rid]} tasks so it cannot tell them apart: "
                f"{t['heading'][:44]}")
        elif not epic.get("done_when"):
            findings["finish"].append(
                f"neither the task nor epic {rid} says what finishing means: "
                f"{t['heading'][:50]}")

    # 3b BRIEF ---------------------------------------------------------------
    # A heading is a label, not an instruction. "Provenance ladder
    # morning-after: review night report, activate Ed25519, fleet identities"
    # names three things an agent cannot locate.
    # A heading that names a RESOLVABLE reference is a brief: the agent can
    # fetch the issue, open the file, or read the PR. "Rebase plur#919" needs
    # no prose. What fails is a heading naming something the agent cannot
    # locate — a report, a tier, a file that does not exist yet.
    RESOLVABLE = re.compile(
        r"#\d{2,5}|\b[\w.-]+\.(ts|py|md|json|yaml|yml)\b|https?://|"
        r"\b(plur|enterprise|exchange)-?ai?/[\w-]+", re.I)
    for t in ai:
        p = t.get("properties") or {}
        has_prose = bool((t.get("body") or "").strip()) or p.get("CONTEXT") \
            or p.get("NOTE") or p.get("BOOTSTRAP")
        if has_prose or RESOLVABLE.search(t["heading"]):
            continue
        findings["brief"].append(
            f"names nothing an agent can open — no issue, file or URL, and no "
            f"context: {t['heading'][:56]}")

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
             "brief":  "3b BRIEF — is there enough to act on",
             "locate": "2 LOCATE — does the work say where it happens",
             "finish": "3 FINISH — does it say what finishing means",
             "verify": "4 VERIFY — can the agent check that itself",
             "avoid":  "5 AVOID  — is undoable work clearly marked"}

    total = sum(len(v) for v in findings.values())
    print(f"{len(r['items'])} epics · {len(ts)} open tasks · {len(ai)} tagged :AI:")
    print(f"{total} readiness finding(s)\n")
    for k in ("select", "locate", "finish", "brief", "verify", "avoid"):
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
