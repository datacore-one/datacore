#!/usr/bin/env python3
"""Triage a large org backlog: near-duplicates, merge clusters, roadmap coverage.

The org adapter's `duplicates` command checks ONE title against a file. This
one does the all-pairs sweep you need when the backlog is in the hundreds and
nobody has read it end to end.

Three questions it answers:

    --dupes     which tasks say almost the same thing (candidates to close)
    --clusters  which tasks group tightly enough to become one epic
    --coverage  which roadmap item each task serves, and which serve none

The roadmap is not a backlog, so most tasks SHOULD be absent from it. What
matters is whether a task can name the outcome it serves. A task that cannot
is either implementation detail of something already on the roadmap, or work
nobody decided to do.

Usage:
    python3 .datacore/lib/task_triage.py --dupes [--threshold 0.62]
    python3 .datacore/lib/task_triage.py --clusters [--min 4]
    python3 .datacore/lib/task_triage.py --coverage
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required")

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / ".datacore/lib/org_workspace_adapter.py"
ROADMAP = REPO / "5-plur" / "roadmap.yaml"

ORG_FILES = ["5-plur/org/next_actions.org", "5-plur/org/someday.org",
             "5-plur/org/inbox.org", "0-personal/org/next_actions.org",
             "0-personal/org/someday.org"]

# Words that carry no signal for similarity — every task has them.
STOP = set("""the a an and or of for to in on with from into at by is are be
was were this that these those it its as if then than so but not no plur add
new use using make made get set run fix update review check via per each
about after before also just only more most some any all can could should
would will shall may might must task item work do does done""".split())


# Coarse buckets for the inventory. Deliberately overlapping-tolerant: a task
# lands in the first theme that matches, and "unfiled" is a finding rather
# than a failure — it is where work nobody categorised accumulates.
THEMES = {
    "packs / hub": r"\bpack|hub\b|marketplace|listing|seller",
    "provenance / audit": r"provenance|lineage|tamper|signed|attest|audit chain",
    "retrieval / recall": r"recall|retriev|rerank|embed|inject|bm25|vector|hybrid",
    "scopes / permissions": r"scope|permission|acl|multi-tenant|rbac|tenant",
    "enterprise delivery": r"enterprise|customer|deploy|onboard|install|docker|helm|runbook",
    "integrator / channel": r"integrator|channel|partner|reseller|civo|stackit",
    "geo / content": r"\bgeo\b|dev\.to|blog|share of voice|wikidata|seo|content|publish",
    "benchmark": r"benchmark|longmemeval|locomo|bench\b|leaderboard",
    "exchange / token": r"exchange|token|escrow|x402|verity|\bfee\b",
    "spec / standard": r"\bspec\b|standard|capsule|schema|protocol",
    "security / trust": r"security|vulnerab|trust page|soc2|dpa|secret|credential",
    "agents / nightshift": r"nightshift|agent fleet|miles|cadence|prompt|orchestrat",
    "verticals": r"vertical|clinical|medicine|health|legal|law",
    "fundraising": r"fundrais|investor|seed|deck|cap table|round",
    "release / ci": r"\bci\b|workflow|release|version|npm|pypi|publish to",
    "infra / ops": r"backup|monitor|server|dns|smoke|token rotation|systemd",
}


def tokens(s):
    return {w for w in re.findall(r"[a-z0-9#]{3,}", s.lower()) if w not in STOP}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_tasks(plur_only=True):
    out = []
    for f in ORG_FILES:
        path = REPO / f
        if not path.exists():
            continue
        r = subprocess.run(["python3", str(ADAPTER), "list", "--file", str(path),
                            "--states", "NEXT,TODO,WAITING,REVIEW"],
                           capture_output=True, text=True, cwd=REPO)
        if r.returncode:
            continue
        for t in json.loads(r.stdout)["tasks"]:
            tags = set(t.get("tags") or [])
            if plur_only and not ("plur" in tags or f.startswith("5-plur")
                                  or "plur" in t["heading"].lower()):
                continue
            t["_file"] = f
            t["_tok"] = tokens(t["heading"])
            out.append(t)
    return out


def report_dupes(tasks, threshold):
    """All-pairs near-duplicate scan. O(n^2) but n is hundreds, not millions."""
    pairs = []
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            s = jaccard(a["_tok"], b["_tok"])
            if s >= threshold:
                pairs.append((s, a, b))
    pairs.sort(key=lambda x: -x[0])
    print(f"{len(pairs)} near-duplicate pairs at >= {threshold:.2f} "
          f"across {len(tasks)} tasks\n")
    for s, a, b in pairs:
        same = "SAME FILE" if a["_file"] == b["_file"] else "cross-file"
        print(f"{s:.2f}  {same}")
        print(f"      [{a['state']:7}] {a['heading'][:100]}")
        print(f"               {(a.get('properties') or {}).get('ID','')}")
        print(f"      [{b['state']:7}] {b['heading'][:100]}")
        print(f"               {(b.get('properties') or {}).get('ID','')}\n")
    return 0


def report_clusters(tasks, minsize):
    """Single-link clustering — groups that could become one epic."""
    parent = list(range(len(tasks)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(tasks):
        for j in range(i + 1, len(tasks)):
            if jaccard(a["_tok"], tasks[j]["_tok"]) >= 0.34:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups = defaultdict(list)
    for i, t in enumerate(tasks):
        groups[find(i)].append(t)
    big = sorted((g for g in groups.values() if len(g) >= minsize),
                 key=len, reverse=True)
    loose = sum(1 for g in groups.values() if len(g) < minsize)
    print(f"{len(big)} clusters of {minsize}+ tasks · {loose} tasks not clustering\n")
    print("Each of these is a candidate to become ONE epic with sub-issues,\n"
          "rather than N separate tasks nobody schedules.\n")
    for g in big:
        shared = set.intersection(*(t["_tok"] for t in g)) or set()
        label = " ".join(sorted(shared)[:6]) or "—"
        print(f"── {len(g)} tasks · shared: {label}")
        for t in g[:9]:
            print(f"     [{t['state']:7}] {t['heading'][:96]}")
        if len(g) > 9:
            print(f"     … {len(g) - 9} more")
        print()
    return 0


def report_coverage(tasks):
    """Which roadmap item does each task serve? Match on title+note tokens."""
    rm = yaml.safe_load(ROADMAP.read_text())
    items = [(i["id"], tokens(i["title"] + " " + (i.get("note") or "")))
             for i in rm["items"]]
    hit, miss = defaultdict(list), []
    for t in tasks:
        best, score = None, 0.0
        for iid, itok in items:
            s = jaccard(t["_tok"], itok)
            if s > score:
                best, score = iid, s
        if score >= 0.18:
            hit[best].append((score, t))
        else:
            miss.append(t)
    print(f"{len(tasks)} PLUR tasks · {sum(len(v) for v in hit.values())} plausibly "
          f"serve a roadmap item · {len(miss)} serve none\n")
    for iid in sorted(hit, key=lambda k: -len(hit[k]))[:20]:
        title = next(i["title"] for i in rm["items"] if i["id"] == iid)
        print(f"{len(hit[iid]):4}  {iid}  {title[:66]}")
    print(f"\n{len(miss)} tasks match no item. Most are implementation detail, which\n"
          f"is correct. Scan for anything that is an outcome nobody decided on.")
    return 0


def write_doc(tasks, out):
    """A grouped, scannable inventory of the whole pool.

    This is the file agents select work from, so what matters is that a human
    can see its shape in one pass: what is stale, what is unscheduled, what is
    a review wrapper nobody closed, and which theme each bucket belongs to.
    Nothing is discarded — a task nobody can place is a clue, not a mistake.
    """
    import datetime
    now = datetime.datetime.now()

    def age(t):
        c = (t.get("properties") or {}).get("CREATED", "")
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", c)
        return (now - datetime.datetime(*map(int, m.groups()))).days if m else None

    buckets = defaultdict(list)
    for t in tasks:
        hits = [k for k, pat in THEMES.items() if re.search(pat, t["heading"], re.I)]
        buckets[hits[0] if hits else "unfiled"].append(t)

    wrappers = [t for t in tasks if re.match(r"^review:\s", t["heading"], re.I)]
    stale = [t for t in tasks if (age(t) or 0) > 90]
    unsched = [t for t in tasks if not t.get("scheduled")]
    linked = [t for t in tasks if (t.get("properties") or {}).get("ROADMAP")]

    L = [f"# PLUR task pool — {len(tasks)} open", "",
         f"Generated {now:%Y-%m-%d} by `.datacore/lib/task_triage.py --doc`. "
         "Regenerate rather than edit.", "",
         "This is the pool agents select work from. The tighter it is, the more of "
         "what they pick serves the roadmap.", "",
         "| | |", "|---|---|",
         f"| open tasks | {len(tasks)} |",
         f"| unscheduled | {len(unsched)} |",
         f"| older than 90 days | {len(stale)} |",
         f"| `Review:` wrappers | {len(wrappers)} |",
         f"| linked to a roadmap item | {len(linked)} |",
         f"| **orphaned — serve no named outcome** | **{len(tasks)-len(linked)}** |", ""]

    L += ["", "## Orphans by theme", "",
          "These serve no named roadmap outcome. Most are implementation detail, "
          "which is correct. Scan for anything that is an outcome nobody decided on.",
          "", "| theme | orphans |", "|---|---|"]
    orph = defaultdict(int)
    for t in tasks:
        if not (t.get("properties") or {}).get("ROADMAP"):
            hits = [k for k, pat in THEMES.items() if re.search(pat, t["heading"], re.I)]
            orph[hits[0] if hits else "unfiled"] += 1
    for k in sorted(orph, key=lambda k: -orph[k]):
        L.append(f"| {k} | {orph[k]} |")
    L.append("")

    for theme in sorted(buckets, key=lambda k: -len(buckets[k])):
        rows = sorted(buckets[theme], key=lambda t: (t["state"], -(age(t) or 0)))
        L += [f"## {theme} — {len(rows)}", "",
              "| state | age | roadmap | task | id |", "|---|---|---|---|---|"]
        for t in rows:
            a = age(t)
            head = t["heading"].replace("|", "\\|")[:130]
            rm = (t.get("properties") or {}).get("ROADMAP", "")
            L.append(f"| {t['state']} | {str(a)+'d' if a is not None else '—'} | "
                     f"{rm or '—'} | {head} | `{(t.get('properties') or {}).get('ID','')}` |")
        L.append("")
    Path(out).write_text("\n".join(L))
    print(f"wrote {out} — {len(tasks)} tasks in {len(buckets)} groups")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dupes", action="store_true")
    ap.add_argument("--clusters", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--doc", metavar="OUT", help="write a grouped inventory")
    ap.add_argument("--threshold", type=float, default=0.62)
    ap.add_argument("--min", type=int, default=4)
    ap.add_argument("--all-tasks", action="store_true",
                    help="do not restrict to PLUR-scoped")
    args = ap.parse_args()

    tasks = load_tasks(plur_only=not args.all_tasks)
    if args.dupes:
        return report_dupes(tasks, args.threshold)
    if args.clusters:
        return report_clusters(tasks, args.min)
    if args.coverage:
        return report_coverage(tasks)
    if args.doc:
        return write_doc(tasks, args.doc)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
