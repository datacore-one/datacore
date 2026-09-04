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
# EVERY space, not just 5-plur. nightshift's find_ai_tasks walks the whole data
# directory — it does not know what a roadmap is — so a queue measured in one
# space is not the queue that runs. Scoped to 5-plur this check reported ONE
# finding on 2026-09-04 while the executor was about to select 265 tasks across
# six spaces, 262 of them with no surface and no definition of done. Third time
# the same mistake: measure the set the executor actually selects.
def _org_files() -> list[str]:
    out = []
    for space in sorted(REPO.glob("[0-9]-*")):
        d = space / "org"
        if d.is_dir():
            out += [str(p.relative_to(REPO)) for p in sorted(d.glob("*.org"))]
    return out


ORG = _org_files()
VERIFIABLE = {"test", "metric", "url", "artifact", "merged-pr"}
JUDGEMENT = {"decision", "signed", "screenshot"}


OPEN_STATES = {"NEXT", "TODO", "WAITING", "REVIEW"}

# Only a space that HAS a roadmap can require its tasks to link to one. 5-plur
# has roadmap.yaml; 0-personal, 6-meridian and the rest do not, and demanding a
# ROADMAP property there reported 357 findings for a file that does not exist.
# SURFACE and DONE_WHEN are required everywhere — an agent always needs to know
# where the work lands and how it will know it finished, roadmap or no roadmap.
HAS_ROADMAP = {p.parent.name for p in REPO.glob("[0-9]-*/roadmap.yaml")}


def _space_of(node) -> str:
    try:
        return Path(str(node.file_path)).relative_to(REPO).parts[0]
    except Exception:
        return "?"


def tasks():
    """Read the org files directly rather than through the adapter's `list`.

    `org_workspace_adapter.py list` emits heading/state/tags/properties and NO
    BODY key. The 3b BRIEF check below asks whether there is prose an agent can
    act on — and against the adapter it was reading `t.get("body")` on a dict
    that never has one, so the body half of that test was dead from the day it
    was written and only the property half ever fired. Loading the workspace
    gives the real NodeView, body included. It is also faster: one parse
    instead of three subprocesses.
    """
    import contextlib, io

    from org_workspace import OrgWorkspace

    ws = OrgWorkspace()
    # load() prints a line per duplicate ID it regenerates. Across all spaces
    # that is dozens of lines of unrelated noise on top of this report. The
    # regeneration is IN MEMORY ONLY — this function never calls save_all() —
    # so swallowing the log changes nothing on disk. Verified 2026-09-04: org
    # files are byte-identical after a run.
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        for f in ORG:
            p = REPO / f
            if p.exists():
                ws.load(p)
    out = []
    for n in ws.all_nodes():
        if n.todo not in OPEN_STATES:
            continue
        out.append({
            "heading": n.heading or "",
            "state": n.todo,
            "_space": _space_of(n),
            # SHALLOW, not inherited. org-mode inherits tags from ancestors and
            # org_workspace's `.tags` honours that — but nightshift does not:
            # nightshift_parser matches `(\s+:[\w:]+:)?$` on the heading LINE
            # and never walks parents. So a live child under a DONE parent
            # tagged :AI: looks queued to org_workspace and is invisible to the
            # executor. Four tasks sat in exactly that state on 2026-09-04.
            # Mirror the executor or this check measures a queue nobody runs.
            "tags": list(n.shallow_tags or []),
            "properties": dict(n.properties or {}),
            "body": (n.body or ""),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    r = yaml.safe_load(ROADMAP.read_text())
    items = {i["id"]: i for i in r["items"]}
    ts = tasks()

    # THE QUEUE IS NOT EVERY :AI: TASK. nightshift_parser.find_ai_tasks selects
    # `states = ['TODO', 'NEXT']` and nothing else, so a REVIEW-state task
    # tagged :AI: is work an agent has already DONE and a human has not yet
    # looked at — the tag is provenance there, not a request. Scoring those for
    # "can an agent start this" measures a set the executor will never select,
    # which inflated this report by 52 findings on 2026-09-04 and is the same
    # wrong-question mistake the FINISH check made (see below). Mirror the
    # executor: if find_ai_tasks' state list changes, change this with it.
    QUEUED = ("TODO", "NEXT")
    tagged = [t for t in ts if "AI" in (t.get("tags") or [])]
    ai = [t for t in tagged if t.get("state") in QUEUED]
    findings = defaultdict(list)

    # 1 SELECT ---------------------------------------------------------------
    for t in ai:
        p = t.get("properties") or {}
        rid = p.get("ROADMAP")
        if not rid:
            if t.get("_space") in HAS_ROADMAP:
                findings["select"].append(
                    f"AI task with no epic: {t['heading'][:60]}")
            continue
        epic = items.get(rid)
        if epic and epic.get("blocked_on") == "human":
            findings["select"].append(
                f"AI task under an epic blocked on a human ({rid}): "
                f"{t['heading'][:52]}")

    # 2 LOCATE ---------------------------------------------------------------
    # SURFACE has to be one of the DECLARED surfaces, not merely non-empty.
    # In the wild it carries two incompatible meanings: task_surface.py's
    # vocabulary ("which repo do I work in" — core, enterprise, hub, ops...)
    # and, on GEO tasks, a publication URL like "plur.ai/blog (canonical) +
    # dev.to mirror". The second is useful to a writer and useless to an agent
    # asking which checkout to open, and an emptiness test passes it. Validate
    # against the vocabulary so a URL fails LOCATE the way a blank would.
    sys.path.insert(0, str(REPO / ".datacore/lib"))
    try:
        from task_surface import SURFACES as _S, DEFAULT as _D
        KNOWN = {n for n, _ in _S} | {_D}
    except Exception:                       # never let the check die on import
        KNOWN = set()

    def _bad_surface(t):
        s = (t.get("properties") or {}).get("SURFACE")
        if s in (None, "", "unassigned"):
            return "no surface"
        if KNOWN and s not in KNOWN:
            return f"surface {s[:28]!r} is not one of {sorted(KNOWN - {'unassigned'})[:4]}..."
        return None

    # NB: do not name this walrus `r` — a comprehension's walrus binds in the
    # ENCLOSING scope, and `r` is the parsed roadmap. Doing so replaced the
    # roadmap with a string and killed the VERIFY check thirty lines later.
    # ONE finding per task, and let the printer do the truncating. This block
    # used to append six rows plus a literal "...and N more" row, so the
    # section reported 7 while 357 tasks were affected — every other check
    # counts tasks, so the sections were not comparable and the total meant
    # nothing. Never fold a count into the finding list.
    for t in ai:
        reason = _bad_surface(t)
        if reason:
            findings["locate"].append(
                f"AI task — agent cannot tell which repo ({reason}): "
                f"{t['heading'][:52]}")

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
            # "already caught by SELECT" was true only while SELECT demanded a
            # ROADMAP on every task. Now that it only demands one where a
            # roadmap exists, a task outside 5-plur with neither a DONE_WHEN
            # nor an epic falls through both checks — unfinishable and
            # unreported. It is the most common shape in the pool, so silently
            # skipping it made the fleet look clean.
            findings["finish"].append(
                f"no DONE_WHEN and no epic to inherit one from — nothing "
                f"defines success: {t['heading'][:48]}")
            continue
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
    print(f"{len(r['items'])} epics · {len(ts)} open tasks · "
          f"{len(tagged)} tagged :AI: · {len(ai)} of them QUEUED (TODO/NEXT)")
    print(f"{total} readiness finding(s) — against the queue, not the pool\n")
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
