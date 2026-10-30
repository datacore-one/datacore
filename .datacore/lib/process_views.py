#!/usr/bin/env python3
"""process_views — the fleet's processes drawn as readable swimlanes, next to the architecture map.

Owner, 2026-10-30: "overview is becoming more difficult ... expand it so that also
just processes and workflows can be analysed, for example a task's lifecycle ...
make the diagram easily readable, not small fonts."

Each flow below is written from the code and the schedules (manifest.yaml job
names, scripts, times in UTC), not from memory; the overlap view is generated
from processes.yaml (process_inventory.py). Lanes are columns and time runs down
the page, so a diagram stays near screen width and its type stays large: node
names 20px, technical lines 16px.

Diagram style: the diagram-design skill, plur profile (Outfit, Literata,
JetBrains Mono for technical lines only; one or two cyan focal nodes).

    process_views.py [--inventory processes.yaml] [--out processes.html] [--open]
"""
from __future__ import annotations

import argparse
import html
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "2-datacore" / "1-tracks" / "dev" / "architecture"

# -- geometry (4px grid) ------------------------------------------------------------
LANE_W = 336          # one actor column; 4 lanes + padding = 1392px, the page width
BOX_W, BOX_H = 272, 96
MAX_NAME, MAX_SUB = 24, 24   # characters that fit a box at 20px / 16px mono
ROW = 176             # step pitch; the 80px gap holds a handoff and its label
TOP = 96              # lane header band
PAD = 24


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


# -- flows (from the code; see each source note) -----------------------------------
FLOWS = [
    {
        "slug": "task-lifecycle",
        "title": "A task's lifecycle",
        "lede": ("From capture to done. Every step is a ledger event; the lists you read "
                 "(inbox.org, next_actions.org) are written from the ledger. The delegation "
                 "review is the step that ran twice until 2026-10-30."),
        "source": "org_workspace_adapter.py add/move · cos_inbox.sh (box-inbox 05:00) · ledger_phase1_cycle.sh (:25 hourly) · "
                  "chief-of-staff lib.cli review (box-delegation-review 23:30) · nightshift run (hourly) · ledger/fold.py",
        "lanes": ["YOU · MAC", "LEDGER · SHARED", "WINSTON · BOX", "MILES · OVERNIGHT"],
        "steps": [
            ("cap", 0, 0, "Capture", "inbox.org · item.create", "TODO", ""),
            ("inb", 2, 1, "Inbox run, 05:00", "cos_inbox.sh", "", ""),
            ("adm", 1, 2, "Admitted to the ledger", "events/<actor>.jsonl", "", ""),
            ("nxt", 0, 3, "Next actions list", "next_actions.org", "NEXT", ""),
            ("rev", 2, 4, "Delegation review, 23:30", "delegation_gate.py", "", "focal"),
            ("run", 3, 5, "Claim and run", "item.claim · grant", "", ""),
            ("res", 1, 6, "Result recorded", "item.complete", "REVIEW", ""),
            ("ok", 0, 7, "You review it", "item.verify", "DONE", ""),
        ],
        "edges": [("cap", "inb", "NEXT 05:00"), ("inb", "adm", "FILE ACTION"), ("adm", "nxt", "PROJECT"),
                  ("nxt", "rev", "AGENT TASKS"), ("rev", "run", "APPROVED"), ("run", "res", "OUTPUT"),
                  ("res", "ok", "TO REVIEW")],
        "notes": [
            "Approval writes one receipt, 2-datacore/.datacore/reviews/cos-lastrun.json; delegation holds when it is missing or stale.",
            "Until 2026-10-30 Miles ran the same review at 05:55 (cos-cadence-triage.timer on dayshift); both wrote the receipt and the merge read as 'hold'. Miles's copy is off.",
            "WAITING, DEFERRED and CANCELLED leave this path: CANCELLED is item.dismiss; DEFERRED keeps the task with a wake date.",
        ],
    },
    {
        "slug": "repair-loop",
        "title": "When a job fails: the repair loop",
        "lede": ("Each machine checks its own jobs. A job that fails three times in a row becomes a "
                 "repair task for that machine's own agent; only a repair the agent gives up on reaches you."),
        "source": "job_verify.py (hourly, every machine) · jobs/autofix.py delegate/escalations · fix_check.py · "
                  "box-autofix-escalation (:25 hourly on Winston)",
        "lanes": ["THE MACHINE'S CHECKER", "THE MACHINE'S AGENT", "WINSTON · ESCALATION", "YOU"],
        "steps": [
            ("chk", 0, 0, "Hourly job check", "job_verify.py", "", ""),
            ("third", 0, 1, "Third failure in a row", "same fingerprint", "", ""),
            ("task", 1, 2, "Repair task", "autofix.delegate", "", ""),
            ("fix", 1, 3, "Fix in a clean copy", "fix_check.py", "", ""),
            ("pass", 0, 4, "Job passes again", "repair closed", "", "focal"),
            ("gave", 2, 5, "Gave up after 3 tries", "escalations, :25", "", ""),
            ("you", 3, 6, "One alert, once", "Telegram · ack", "", ""),
        ],
        "edges": [("chk", "third", ""), ("third", "task", "DELEGATE"), ("task", "fix", ""),
                  ("fix", "pass", "CHECK PASSES"), ("fix", "gave", "3 FAILED"), ("gave", "you", "ALERT")],
        "notes": [
            "The agent is the one whose machine the job runs on (principals.yaml hosts): Winston for the box, Miles for the overnight host. A job on your Mac becomes a task for you, never an agent repair.",
            "A give-up stops alerting when its job passes again or 48 hours after it was closed.",
        ],
    },
    {
        "slug": "sync-cycle",
        "title": "How work moves between machines",
        "lede": ("Every machine keeps its own copy of each space. Writers never share a file: each appends "
                 "to its own event log, so merging is a union and cannot conflict."),
        "source": "ledger_phase1_cycle.sh (:25 hourly on box, overnight host, dayshift) · ledger_transport.py sync (DIP-0046) · ledger/fold.py",
        "lanes": ["THIS MACHINE", "THE SPACE'S GIT REPO", "EVERY OTHER MACHINE"],
        "steps": [
            ("edit", 0, 0, "Work is done", "events/<actor>.jsonl", "", ""),
            ("cyc", 0, 1, "Hourly ledger cycle, :25", "ledger_phase1_cycle.sh", "", ""),
            ("push", 0, 2, "Commit, merge, push", "ledger_transport.py sync", "", ""),
            ("repo", 1, 3, "One log per writer", "events/*.jsonl", "", "focal"),
            ("pull", 2, 4, "Pull and merge", "union, never rebase", "", ""),
            ("proj", 2, 5, "Fold and project", "next_actions.org", "", ""),
        ],
        "edges": [("edit", "cyc", ""), ("cyc", "push", ""), ("push", "repo", "PUSH"),
                  ("repo", "pull", "PULL"), ("pull", "proj", "")],
        "notes": [
            "A sync ends clean, offline (retries by itself), blocked (login or remote gone: tell the owner) or conflict (markers left for a person). Nothing is stashed or reset to make it pass.",
            "Two writers on one shared file breaks this rule: that is what the duplicate delegation review did with its receipt.",
        ],
    },
    {
        "slug": "morning",
        "title": "The morning, on Winston",
        "lede": "Winston's scheduled morning, in UTC. The briefing waits for that morning's sleep reading before it is written.",
        "source": "box crontab: cos_news.sh 03:00 · triage-email.sh 03:30 · cos_inbox.sh 05:00 · cos_research.sh 05:30 · "
                  "cadence_run.py cadence-firm-cos-briefing 06:15 → cos_morning.sh · catch-up 06:45–10:45 · cos_verify_morning.sh 08:45",
        "lanes": ["WINSTON · BOX", "YOU"],
        "steps": [
            ("news", 0, 0, "03:00 News", "cos_news.sh", "", ""),
            ("mail", 0, 1, "03:30 Email triage", "triage-email.sh", "", ""),
            ("inbox", 0, 2, "05:00 Inbox run", "cos_inbox.sh", "", ""),
            ("res", 0, 3, "05:30 Research", "cos_research.sh", "", ""),
            ("brief", 0, 4, "06:15 Morning briefing", "cos_morning.sh", "", "focal"),
            ("read", 1, 5, "Briefing, audio, opener", "## Daily Briefing", "", ""),
            ("ver", 0, 6, "08:45 Verify the morning", "cos_verify_morning.sh", "", ""),
        ],
        "edges": [("news", "mail", ""), ("mail", "inbox", ""), ("inbox", "res", ""), ("res", "brief", ""),
                  ("brief", "read", "JOURNAL"), ("read", "ver", "CHECKED")],
        "notes": [
            "The briefing holds until 08:15 Ljubljana for the sleep reading; a catch-up job reruns a missed morning hourly from 06:45 to 10:45.",
            "One run, two checks: the briefing job (box-briefing) and the briefing cadence (cadence-firm-cos-briefing) both judge the same journal page, so one fault opens two repair tasks.",
        ],
    },
]


# -- drawing ---------------------------------------------------------------------------

def _elbow(x1, y1, x2, y2, r=8) -> str:
    """Down from (x1,y1), across at the gap's middle, down into (x2,y2); quarter-arc bends."""
    if x1 == x2:
        return f"M{x1},{y1} V{y2}"
    ym = y1 + (y2 - y1) // 2
    sx = 1 if x2 > x1 else -1
    return (f"M{x1},{y1} V{ym - r} Q{x1},{ym} {x1 + sx * r},{ym} "
            f"H{x2 - sx * r} Q{x2},{ym} {x2},{ym + r} V{y2}")


def svg_flow(f: dict) -> str:
    for st in f["steps"]:
        if len(st[3]) > MAX_NAME or len(st[4]) > MAX_SUB:
            raise ValueError(f"{f['slug']}/{st[0]}: label too long for a box: {st[3]!r} / {st[4]!r}")
    lanes = f["lanes"]
    rows = max(s[2] for s in f["steps"]) + 1
    W = PAD * 2 + LANE_W * len(lanes)
    H = TOP + rows * ROW + 80 + 64          # + legend strip
    cx = lambda lane: PAD + lane * LANE_W + LANE_W // 2  # noqa: E731
    ty = lambda row: TOP + 32 + row * ROW  # noqa: E731
    pos = {s[0]: (s[1], s[2]) for s in f["steps"]}
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" role="img" aria-labelledby="{f["slug"]}-title {f["slug"]}-desc" '
           f'class="flow" xmlns="http://www.w3.org/2000/svg">',
           f'<title id="{f["slug"]}-title">{esc(f["title"])}</title>',
           f'<desc id="{f["slug"]}-desc">{esc(f["lede"])}</desc>',
           '<defs><marker id="ar-' + f["slug"] + '" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">'
           '<polygon points="0 0, 10 4, 0 8" class="mk"/></marker></defs>']
    # lanes
    for i, name in enumerate(lanes):
        x = PAD + i * LANE_W
        if i:
            out.append(f'<line x1="{x}" y1="{TOP - 40}" x2="{x}" y2="{TOP + rows * ROW}" class="lane-rule"/>')
        out.append(f'<text x="{x + LANE_W // 2}" y="{TOP - 48}" class="lane" text-anchor="middle">{esc(name)}</text>')
    out.append(f'<line x1="{PAD}" y1="{TOP - 32}" x2="{W - PAD}" y2="{TOP - 32}" class="lane-rule"/>')
    # edges first (z-order): fan attach points when a box has two outgoing handoffs
    outs: dict[str, list] = {}
    for e in f["edges"]:
        outs.setdefault(e[0], []).append(e)
    for src, es in outs.items():
        for k, (a, b, label) in enumerate(es):
            la, ra = pos[a]
            lb, rb = pos[b]
            n = len(es)
            x1 = cx(la) + (0 if n == 1 else int(BOX_W * (k + 1) / (n + 1)) - BOX_W // 2)
            x1 -= x1 % 4
            y1 = ty(ra) + BOX_H
            x2, y2 = cx(lb), ty(rb) - 4
            out.append(f'<path d="{_elbow(x1, y1, x2, y2)}" class="edge" marker-end="url(#ar-{f["slug"]})"/>')
            if label:
                tw = 12 + int(len(label) * 9.6)
                tw += (-tw) % 4
                if x1 == x2:   # vertical: beside the line, 8px gap
                    lx, ly = x1 + 8, y1 + (y2 - y1) // 2 - 12
                    out.append(f'<rect x="{lx}" y="{ly}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{lx + 6}" y="{ly + 17}" class="elabel">{esc(label)}</text>')
                else:          # across: centred above the horizontal run, 8px gap
                    ym = y1 + (y2 - y1) // 2
                    mx = (x1 + x2) // 2
                    mx -= mx % 4
                    out.append(f'<rect x="{mx - tw // 2}" y="{ym - 32}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{mx}" y="{ym - 15}" class="elabel" text-anchor="middle">{esc(label)}</text>')
    # boxes
    for sid, lane, row, name, sub, state, kind in f["steps"]:
        x, y = cx(lane) - BOX_W // 2, ty(row)
        cls = "box focal" if kind == "focal" else "box"
        out.append(f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" class="paper"/>'
                   f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" class="{cls}"/>')
        out.append(f'<text x="{x + 16}" y="{y + 40}" class="name">{esc(name)}</text>')
        out.append(f'<text x="{x + 16}" y="{y + 72}" class="sub">{esc(sub)}</text>')
        if state:
            sw = 16 + len(state) * 10
            sw += (-sw) % 4
            out.append(f'<rect x="{x + BOX_W - sw - 8}" y="{y - 12}" width="{sw}" height="24" rx="2" class="state"/>'
                       f'<text x="{x + BOX_W - sw // 2 - 8}" y="{y + 5}" class="statet" text-anchor="middle">{esc(state)}</text>')
    # legend strip
    ly = TOP + rows * ROW + 40
    out.append(f'<line x1="{PAD}" y1="{ly}" x2="{W - PAD}" y2="{ly}" class="lane-rule"/>')
    out.append(f'<text x="{PAD}" y="{ly + 36}" class="leg">LEGEND</text>'
               f'<rect x="{PAD + 112}" y="{ly + 16}" width="32" height="24" rx="4" class="box focal"/>'
               f'<text x="{PAD + 156}" y="{ly + 36}" class="legt">where it broke or matters most</text>'
               f'<rect x="{PAD + 492}" y="{ly + 16}" width="64" height="24" rx="2" class="state"/>'
               f'<text x="{PAD + 524}" y="{ly + 33}" class="statet" text-anchor="middle">STATE</text>'
               f'<text x="{PAD + 568}" y="{ly + 36}" class="legt">task state in your lists</text>')
    out.append("</svg>")
    return "\n".join(out)


STATES = [
    ("TODO", "item.create → created", "Captured; in an inbox or a list", "You, an agent's capture, a cadence"),
    ("NEXT", "created (approved)", "Committed work; agents may take it", "You, or the morning delegation review"),
    ("WAITING", "created (blocked)", "Waits on someone or a date", "You"),
    ("REVIEW", "item.complete → completed", "Done by an agent; waits for your check", "The agent that ran it"),
    ("DONE", "item.verify → verified", "Finished and checked", "You"),
    ("DEFERRED", "kept, with a wake date", "Not now; comes back on its date", "You"),
    ("CANCELLED", "item.dismiss → dismissed", "Dropped, or superseded", "You, or housekeeping"),
]

KIND_TEXT = {
    "same-output": ("Two jobs write one shared file", "Risk: they overwrite each other, or one fault opens two repairs."),
    "same-process": ("One process scheduled on two machines", "Risk: the work runs twice (the delegation review case)."),
    "no-contract": ("Scheduled with no job contract", "Nothing checks that it ran; a silent failure stays silent."),
    "contract-not-in-shared-manifest": ("Contract only in a host-local manifest", "Checked on that host, invisible here."),
    "cadence-not-in-shared-manifest": ("Venture cadence (declared in venture.yaml)", "Judged by cadence liveness, not a job contract."),
}


def overlap_section(inv: dict) -> str:
    groups: dict[str, list] = {}
    for f in inv.get("overlaps") or []:
        groups.setdefault(f["kind"], []).append(f)
    rows = []
    rows.append('<tr class="fixed"><td>Morning delegation review</td><td>chief-of-staff review</td>'
                '<td>Winston 23:30 and Miles 05:55 (cos-cadence-triage.timer)</td>'
                '<td>Fixed 2026-10-30: Miles\'s timer off, Winston owns it</td></tr>')
    for kind in ("same-process", "same-output"):
        for f in groups.get(kind, []):
            why = KIND_TEXT[kind][1]
            owners = {b.rsplit("(", 1)[-1] for b in f["by"]}
            if kind == "same-output" and any("-pull" in b for b in f["by"]):
                why = "A one-way copy pulled onto another machine; expected, not two writers."
            elif kind == "same-output" and len(owners) == 1:
                why = "One run judged by two job contracts: a single fault opens two repair tasks."
            rows.append(f"<tr><td>{esc(KIND_TEXT[kind][0])}</td><td><code>{esc(f['what'])}</code></td>"
                        f"<td>{esc(', '.join(f['by']))}</td><td>{esc(why)}</td></tr>")
    gaps = []
    for kind in ("no-contract", "contract-not-in-shared-manifest", "cadence-not-in-shared-manifest"):
        items = groups.get(kind, [])
        if not items:
            continue
        where: dict[str, list] = {}
        for f in items:
            where.setdefault(", ".join(f["by"]), []).append(f["what"])
        detail = "; ".join(f"<b>{esc(w)}</b>: " + ", ".join(f"<code>{esc(x)}</code>" for x in sorted(v)) for w, v in where.items())
        gaps.append(f'<details><summary><span class="n">{len(items)}</span> {esc(KIND_TEXT[kind][0])}'
                    f' <span class="why">— {esc(KIND_TEXT[kind][1])}</span></summary><p>{detail}</p></details>')
    return (f'<table class="ov"><thead><tr><th>Overlap</th><th>What</th><th>Who runs or writes it</th><th>Why it matters</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<h3>Gaps: processes nothing checks</h3>{"".join(gaps)}'
            f'<p class="src">Generated {esc(inv.get("generated", ""))} from {esc(inv.get("source", ""))}. '
            f'Every machine also runs its own ledger cycle, job checker, promise scoreboard and syncs: those copies are by design and not listed.</p>')


CSS = """
:root{--paper:#fafaf9;--paper-2:#f5f5f0;--ink:#1a1a1a;--muted:#4b4b4b;--soft:#6e6e6d;--rule:rgba(26,26,26,.10);
--rule-solid:rgba(26,26,26,.22);--accent:#0e7490;--accent-tint:rgba(14,116,144,.08);--link:#6d4bc7}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--paper:#0e0f14;--paper-2:#16181f;--ink:#f0f0f2;--muted:#c5c5c7;
--soft:#8d8d90;--rule:rgba(240,240,242,.10);--rule-solid:rgba(240,240,242,.22);--accent:#22d3ee;--accent-tint:rgba(34,211,238,.12);--link:#a78bfa}}
:root[data-theme="dark"]{--paper:#0e0f14;--paper-2:#16181f;--ink:#f0f0f2;--muted:#c5c5c7;--soft:#8d8d90;--rule:rgba(240,240,242,.10);
--rule-solid:rgba(240,240,242,.22);--accent:#22d3ee;--accent-tint:rgba(34,211,238,.12);--link:#a78bfa}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:'Outfit',system-ui,sans-serif;font-size:18px;line-height:1.5}
header.top{position:sticky;top:0;z-index:2;background:var(--paper);border-bottom:1px solid var(--rule);padding:12px 24px;display:flex;
gap:16px;align-items:center;flex-wrap:wrap}
header.top a{color:var(--ink);text-decoration:none;font-size:16px;padding:6px 12px;border:1px solid var(--rule-solid);border-radius:6px}
header.top a:hover,header.top a.on{border-color:var(--accent);color:var(--accent)}
header.top .eyebrow{font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.14em;color:var(--soft);margin-right:8px}
main{max-width:1440px;margin:0 auto;padding:24px 24px 64px}
h1{font-family:'Literata',Georgia,serif;font-weight:300;font-size:40px;margin:24px 0 8px}
h2{font-family:'Literata',Georgia,serif;font-weight:400;font-size:32px;margin:64px 0 8px;scroll-margin-top:72px}
h3{font-size:22px;font-weight:600;margin:32px 0 12px}
.lede{font-size:20px;color:var(--muted);max-width:72ch;margin:0 0 24px}
.src{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--soft);max-width:110ch}
.fig{overflow-x:auto;margin:8px 0 16px}
ul.notes{max-width:90ch;padding-left:20px}ul.notes li{margin:6px 0}
svg.flow{max-width:100%;height:auto;display:block}
svg .paper{fill:var(--paper)} svg .box{fill:var(--paper);stroke:var(--ink);stroke-width:1.2}
svg .box.focal{fill:var(--accent-tint);stroke:var(--accent);stroke-width:2}
svg .name{font-family:'Outfit',sans-serif;font-size:20px;font-weight:600;fill:var(--ink)}
svg .sub{font-family:'JetBrains Mono',monospace;font-size:16px;fill:var(--muted)}
svg .lane{font-family:'JetBrains Mono',monospace;font-size:16px;letter-spacing:.1em;fill:var(--soft)}
svg .lane-rule{stroke:var(--rule-solid);stroke-width:1}
svg .edge{fill:none;stroke:var(--muted);stroke-width:1.6} svg .mk{fill:var(--muted)}
svg .mask{fill:var(--paper)} svg .elabel{font-family:'JetBrains Mono',monospace;font-size:16px;letter-spacing:.04em;fill:var(--soft)}
svg .state{fill:var(--paper);stroke:var(--accent);stroke-width:1.2} svg .statet{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:400;fill:var(--accent)}
svg .leg{font-family:'JetBrains Mono',monospace;font-size:16px;letter-spacing:.14em;fill:var(--soft)}
svg .legt{font-family:'Outfit',sans-serif;font-size:16px;fill:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:17px;margin:8px 0}
th{text-align:left;font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--soft);
font-weight:400;border-bottom:1px solid var(--rule-solid);padding:8px 12px}
td{border-bottom:1px solid var(--rule);padding:10px 12px;vertical-align:top}
tr.fixed td{color:var(--soft)} tr.fixed td:last-child{color:var(--accent)}
code{font-family:'JetBrains Mono',monospace;font-size:15px}
details{border-bottom:1px solid var(--rule);padding:10px 0;max-width:110ch}summary{cursor:pointer;font-size:18px}
details .n{display:inline-block;min-width:40px;font-family:'JetBrains Mono',monospace;color:var(--accent)}
details .why{color:var(--soft)} details p{font-size:16px;line-height:1.7}
nav.toc{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 0}
@media (max-width:640px){main{padding:16px}h1{font-size:32px}h2{font-size:26px}}
"""


def page(inv: dict) -> str:
    links = "".join(f'<a href="#{f["slug"]}">{esc(f["title"])}</a>' for f in FLOWS)
    body = []
    for f in FLOWS:
        notes = "".join(f"<li>{esc(n)}</li>" for n in f["notes"])
        body.append(f'<h2 id="{f["slug"]}">{esc(f["title"])}</h2><p class="lede">{esc(f["lede"])}</p>'
                    f'<div class="fig">{svg_flow(f)}</div><ul class="notes">{notes}</ul>'
                    f'<p class="src">From the code: {esc(f["source"])}</p>')
        if f["slug"] == "task-lifecycle":
            rows = "".join(f"<tr><td><b>{a}</b></td><td><code>{esc(b)}</code></td><td>{esc(c)}</td><td>{esc(d)}</td></tr>"
                           for a, b, c, d in STATES)
            body.append('<h3>The seven task states (DIP-0009 v2.0) and their ledger events</h3>'
                        '<table><thead><tr><th>State</th><th>Ledger</th><th>Means</th><th>Who moves it here</th></tr></thead>'
                        f'<tbody>{rows}</tbody></table>')
    body.append('<h2 id="overlaps">Duplicates and overlaps</h2><p class="lede">Generated from the job contracts and the live '
                'schedules on every machine. An overlap is the same work, or the same shared file, owned twice.</p>'
                + overlap_section(inv))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Datacore processes</title>
<meta name="description" content="How work moves through Datacore: a task's lifecycle, the repair loop, the sync cycle, the morning run, and where processes overlap.">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Literata:ital,wght@0,300;0,400;1,300;1,400&family=JetBrains+Mono:wght@300;400&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header class="top"><span class="eyebrow">DATACORE</span><a href="datacore.html">Architecture map</a><a class="on" href="processes.html">Processes</a>
<a href="#overlaps">Duplicates and overlaps</a></header>
<main><h1>Processes and workflows</h1>
<p class="lede">The architecture map shows what exists. This page shows how work moves through it: who runs each step, on which machine, and what it writes. Drawn from the code and the schedules; the overlap list is generated.</p>
<nav class="toc">{links}</nav>
{"".join(body)}
</main></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inventory", default=str(ARCH / "processes.yaml"))
    ap.add_argument("--out", default=str(ARCH / "processes.html"))
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)
    inv = yaml.safe_load(Path(a.inventory).read_text()) if Path(a.inventory).exists() else {}
    Path(a.out).write_text(page(inv))
    print(a.out)
    if a.open:
        subprocess.run(["open", a.out], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
