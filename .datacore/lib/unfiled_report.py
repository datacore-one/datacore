#!/usr/bin/env python3
"""The unfiled bucket, and where each task should live.

`task_triage.py --doc` groups the whole pool by theme and sorts the groups by
size, which puts "unfiled" — the group that is a finding rather than a
category — first and 200 rows deep. This report does the one thing that
bucket needs: propose a HOME for every task in it, and separate the tasks
that are stale or already answered from the ones that are real.

A home is either a milestone (M1..M5 — the product ladder) or a non-product
lane. Company formation, tax filings and content are not rungs on
USABLE->GOVERNABLE->OWNABLE->PUBLISHABLE->TRADEABLE, and forcing them onto
one would make the ladder mean nothing. They get lanes instead.
"""
import datetime, json, re, subprocess, sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / ".datacore/lib/org_workspace_adapter.py"
FILES = ["5-plur/org/next_actions.org", "5-plur/org/someday.org",
         "5-plur/org/inbox.org"]

sys.path.insert(0, str(REPO / ".datacore/lib"))
from task_triage import THEMES

# Ordered: first match wins. Product rungs first, then the lanes.
HOMES = [
 # ── process first: it is the smallest bucket that is also the most urgent,
 # and its members otherwise scatter into build/system and disappear.
 ("process", "Process — sprints, review flow, issue hygiene",
  r"sprint|retro\b|ceremony|planning ceremony|claim\.py|auto-close|"
  r"close.*issue|issue.*leak|review polic|required status check|branch protection|"
  r"triage|backlog|canvas|extras_delivered|rollover"),
 ("M1", "Usable — recall that returns the right thing",
  r"recall|retriev|rerank|embed|inject|contradict|tension|dedup|decay|salience|"
  r"hyde|activation|search quality|memory-stream|metacognition|consolidat|"
  r"learning-classifier|cognitive_level|temporal default|always-on rule|"
  r"engram documentation|apache age|withlock"),
 ("M2", "Governable — one person's memory becomes a team's",
  r"scope|permission|acl|tenant|promote|approval|team store|"
  r"remote store|sync|access control|plur-admin|group:"),
 ("M3", "Ownable — memory you can prove",
  r"provenance|lineage|signed|signature|attest|tamper|audit|prov-o|chain|"
  r"revocation|rotation|scitt|worm|anchor|non-suppressib|sovereign-memory|"
  r"fds-identity"),
 ("M4", "Publishable — other agents learn to work with you",
  r"\bpack\b|capsule|\.plur\b|hub\b|byline|author|install count|public index|"
  r"standard|spec\b|wikidata|directory|listing|atlas|registry|registries|"
  r"mcp\.so|pulsemcp|smithery|awesome-|skill for claude|connector|"
  r"gemini-cli|composio|omnigent|integration target|ecosystem integration"),
 ("M5", "Tradeable — the knowledge economy",
  r"exchange|escrow|x402|paid pack|stripe|payment|pricing|metering|earn model|"
  r"marketplace|token(?!\s*balance)"),
 ("company", "Company — legal, finance, the entity itself",
  r"plur ltd|hmrc|corporation tax|\bico\b|companies house|confirmation statement|"
  r"annual accounts|cap table|incorporat|fundrais|investor|seed round|data room|"
  r"diligence|contract|invoice|\bvat\b|house of lords"),
 ("channel", "Channel — customers, integrators, prospects, verticals",
  r"integrator|channel|partner|reseller|prospect|buying centre|warm intro|"
  r"adacta|halcom|igea|marand|better split|track a|track b|vertical|clinical|"
  r"health|legal sector|sales plan|customer|debrief|persona|keynote"),
 ("reach", "Reach — GEO, content, launch, outreach, community",
  r"show hn|ask hn|hacker news|blog|dev\.to|\bpost\b|tweet|announce|"
  r"share of voice|\bgeo\b|\bseo\b|content|comms|newsletter|discord|launch|demo|"
  r"outreach|follow up|follow-up|reach out|article|irishtechnews|anthropic|"
  r"data-olympus|sitemap|resend\.com|non-technical-users"),
 ("bench", "Bench — benchmarks, competitive, research, telemetry",
  r"benchmark|longmemeval|locomo|bench\b|competitor|competitive|comparison|"
  r"vs-|alliance|vestige|mem0|letta|\bzep\b|weaviate|caura|paper|research|"
  r"telemetry|north-star|hypothes|h00\d|readout|silent rotation"),
 ("system", "System — Datacore, nightshift, agent tooling",
  r"nightshift|wrap-up|/today|/tomorrow|cadence|org-mode|org file|"
  r"datacore|journal|hook|scaffold|chief-of-staff|outbox|distribution-check|"
  r"auto-default|roadmap"),
 ("build", "Build — CI, release, defects with no rung of their own",
  r"\bci\b|workflow|release|version|npm|pypi|flaky|test|crash|bug|\bfix\b|"
  r"rebase|merge|\bpr #|land pr|dist\b|rebuild|manifest gate|repo for|"
  r"credit balance|top up|spike|prototype|verify|stale status"),
]

STALE_DAYS = 90
now = datetime.datetime.now()


def age(t):
    c = (t.get("properties") or {}).get("CREATED", "")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", c)
    return (now - datetime.datetime(*map(int, m.groups()))).days if m else None


def load():
    out = []
    for f in FILES:
        r = subprocess.run(["python3", str(ADAPTER), "list", "--file", f,
                            "--states", "NEXT,TODO,WAITING,REVIEW"],
                           capture_output=True, text=True, cwd=REPO)
        if r.returncode:
            continue
        for t in json.loads(r.stdout)["tasks"]:
            if [k for k, p in THEMES.items() if re.search(p, t["heading"], re.I)]:
                continue                      # has a theme — not our problem here
            t["_f"] = f.split("/")[-1]
            t["_a"] = age(t)
            t["_rm"] = (t.get("properties") or {}).get("ROADMAP", "")
            out.append(t)
    return out


def home(t):
    for key, _, pat in HOMES:
        if re.search(pat, t["heading"], re.I):
            return key
    return None


def main():
    tasks = load()
    for t in tasks:
        t["_h"] = home(t)

    groups = defaultdict(list)
    for t in tasks:
        groups[t["_h"] or "VARIOUS"].append(t)

    stale = [t for t in tasks if (t["_a"] or 0) > STALE_DAYS]
    wrappers = [t for t in tasks if re.match(r"^review:\s", t["heading"], re.I)]

    L = [f"# Unfiled — {len(tasks)} tasks, and where each one goes", "",
         f"Generated {now:%Y-%m-%d} by `.datacore/lib/unfiled_report.py`. "
         "Regenerate rather than edit.", "",
         "These are the tasks that matched no theme in the pool report. "
         "A **home** is either a rung on the product ladder (M1–M5) or a lane. "
         "Company formation, tax and content are not rungs — forcing them onto "
         "a milestone would make the ladder mean nothing, so they get lanes.", "",
         "| | |", "|---|---|",
         f"| unfiled tasks | {len(tasks)} |",
         f"| already carry a roadmap link | {sum(1 for t in tasks if t['_rm'])} |",
         f"| older than {STALE_DAYS} days — **check these first** | {len(stale)} |",
         f"| `Review:` wrappers | {len(wrappers)} |",
         f"| still VARIOUS after classifying | {len(groups['VARIOUS'])} |", ""]

    order = [k for k, _, _ in HOMES] + ["VARIOUS"]
    titles = {k: d for k, d, _ in HOMES}
    titles["VARIOUS"] = "Various — no home proposed, needs your call"

    L += ["## Proposed split", "", "| home | tasks |", "|---|---|"]
    for k in order:
        if groups[k]:
            L.append(f"| **{k}** — {titles[k]} | {len(groups[k])} |")
    L.append("")

    for k in order:
        rows = sorted(groups[k], key=lambda t: -(t["_a"] or 0))
        if not rows:
            continue
        L += [f"## {k} — {titles[k]} ({len(rows)})", "",
              "| state | age | roadmap | task | id |", "|---|---|---|---|---|"]
        for t in rows:
            a = t["_a"]
            flag = " ⚠️" if (a or 0) > STALE_DAYS else ""
            head = t["heading"].replace("|", "\\|")[:120] + flag
            L.append(f"| {t['state']} | {str(a)+'d' if a is not None else '—'} | "
                     f"{t['_rm'] or '—'} | {head} | `{(t.get('properties') or {}).get('ID','')}` |")
        L.append("")

    out = REPO / "5-plur/1-tracks/ops/unfiled-2026-09-02.md"
    out.write_text("\n".join(L))
    print(f"wrote {out.relative_to(REPO)} — {len(tasks)} tasks")
    for k in order:
        if groups[k]:
            print(f"  {len(groups[k]):4}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
