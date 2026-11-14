#!/usr/bin/env python3
"""process_views — the fleet's processes drawn as readable swimlanes, next to the architecture map.

Owner, 2026-10-30: "overview is becoming more difficult ... expand it so that also
just processes and workflows can be analysed, for example a task's lifecycle ...
make the diagram easily readable, not small fonts."
Second round, same day: a left sidebar with every flow, type one size smaller,
the ledger's own processes (fold, seal, corrections), where signing and
provenance receipts happen, and how cadences and scheduled jobs run.

Each flow below is written from the code and the schedules (manifest.yaml job
names, scripts, times in UTC), not from memory; the overlap view is generated
from processes.yaml (process_inventory.py). Lanes are columns and time runs down
the page, so a diagram stays near screen width and its type stays large: node
names 18px, technical lines 14px.

Diagram style: the diagram-design skill, plur profile (Outfit, Literata,
JetBrains Mono for technical lines only; one or two cyan focal nodes).

    process_views.py [--inventory processes.yaml] [--out processes.html] [--open]
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
from pathlib import Path

import yaml

from process_data import DATA
from process_upgrades import UPGRADES

ROOT = Path(__file__).resolve().parents[2]


def arch_dir(root: Path = ROOT) -> Path:
    """The architecture folder of the space holding the `system` role (install.yaml);
    the personal space when none is declared (INS-3)."""
    from spaces import space_for
    return root / space_for("system", root, "0-personal") / "1-tracks" / "dev" / "architecture"


ARCH = arch_dir()

# -- geometry (4px grid) ------------------------------------------------------------
LANE_W = 336          # one actor column; 4 lanes + padding = 1392px, the page width
BOX_W, BOX_H = 272, 96
MAX_NAME, MAX_SUB = 26, 28   # characters that fit a box at 16px / 13px mono (kept from 18/14: room to spare)
ROW = 176             # step pitch; the 80px gap holds a handoff and its label
TOP = 96              # lane header band
PAD = 24


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


# -- marks: where a step is signed, co-signed or leaves a provenance receipt ---------
# code -> (always|optional, letter, hover text). Lower-case codes are optional and
# drawn dashed; the letter is what the badge shows.
MARKS = {
    "S": ("always", "S", "Signed by the writer's own key, always (since 2026-11-01): only an explicit DATACORE_LEDGER_SIGN=0 turns it off, and never in a hub space, where an unsigned event is not admitted."),
    "O": ("always", "O", "Signed by the owner (the Mac's key): hub.enable, policy.set and forward corrections (ledger.compensate)."),
    "c": ("optional", "C", "Needs your co-sign (approval.grant) only when the task has a side effect such as email.send, payment or prod.deploy (ledger/policy.py)."),
    "r": ("optional", "R", "Provenance receipt on Swarm: fingerprints only, signed by the gateway's notary. Only where the receipt hook (agent turns) or the git commit hook is installed."),
}

# -- flows: this installation's own processes, from its gitignored local file ------
# step: (id, lane, row, name, technical line, state, "focal"|"", marks)
# Each flow is written from the code and the schedules; the data describes this
# install's machines, agents and spaces, so it is not shipped code (process_data.py).
FLOWS = list(DATA["flows"])

# Sidebar and page order: groups by what they do, the ones that break most first.
GROUP_ORDER = ["Work", "Delegation and overnight", "You", "Ledger", "Schedules", "Checks and assurance",
               "Intake and knowledge", "Fleet operations"]
FLOWS.sort(key=lambda f: GROUP_ORDER.index(f["group"]))   # stable: keeps the order within a group


# -- drawing ---------------------------------------------------------------------------

def _elbow(x1, y1, x2, y2, r=8, ym=None) -> str:
    """Down from (x1,y1), across at height ym (default the gap's middle), down into (x2,y2); quarter-arc bends."""
    if x1 == x2:
        return f"M{x1},{y1} V{y2}"
    ym = y1 + (y2 - y1) // 2 if ym is None else ym
    sx = 1 if x2 > x1 else -1
    return (f"M{x1},{y1} V{ym - r} Q{x1},{ym} {x1 + sx * r},{ym} "
            f"H{x2 - sx * r} Q{x2},{ym} {x2},{ym + r} V{y2}")


def svg_flow(f: dict) -> str:
    for st in f["steps"]:
        if len(st[3]) > MAX_NAME or len(st[4]) > MAX_SUB:
            raise ValueError(f"{f['slug']}/{st[0]}: label too long for a box: {st[3]!r} / {st[4]!r}")
        unknown = set(st[7] if len(st) > 7 else "") - set(MARKS)
        if unknown:
            raise ValueError(f"{f['slug']}/{st[0]}: unknown mark(s) {sorted(unknown)}")
    lanes = f["lanes"]
    rows = max(s[2] for s in f["steps"]) + 1
    W = PAD * 2 + LANE_W * len(lanes)
    H = TOP + rows * ROW + 24               # the key is in the sidebar, once for every diagram
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
        es = sorted(es, key=lambda e: pos[e[1]][0])   # leftmost target takes the leftmost attach point: no crossings
        for k, (a, b, label) in enumerate(es):
            la, ra = pos[a]
            lb, rb = pos[b]
            n = len(es)
            x1 = cx(la) + (0 if n == 1 else int(BOX_W * (k + 1) / (n + 1)) - BOX_W // 2)
            x1 -= x1 % 4
            y1 = ty(ra) + BOX_H
            x2, y2 = cx(lb), ty(rb) - 4
            if n > 1 and la == lb:   # a fanned handoff into the box straight below drops straight, label beside it
                x2 = x1
            # fanned handoffs cross at different heights, so two lines never share a run
            ym = y1 + (y2 - y1) // 2 + ((n - 1 - k) * 24 if n > 1 else 0)
            out.append(f'<path d="{_elbow(x1, y1, x2, y2, ym=ym)}" class="edge" marker-end="url(#ar-{f["slug"]})"/>')
            if label:
                tw = 12 + int(len(label) * 8.4)
                tw += (-tw) % 4
                if x1 == x2:   # vertical: beside the line, 8px gap
                    lx, ly = x1 + 8, y1 + (y2 - y1) // 2 - 12
                    out.append(f'<rect x="{lx}" y="{ly}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{lx + 6}" y="{ly + 16}" class="elabel">{esc(label)}</text>')
                else:          # across: centred above the horizontal run, 8px gap
                    mx = (x1 + x2) // 2 if n == 1 or k == 0 else x2 - 8 - int(len(label) * 4.2) - 12
                    mx -= mx % 4
                    ly = ym - 32 if (n == 1 or k == n - 1) else ym + 8   # a lowered fan line carries its label below it
                    out.append(f'<rect x="{mx - tw // 2}" y="{ly}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{mx}" y="{ly + 16}" class="elabel" text-anchor="middle">{esc(label)}</text>')
    # boxes: a hover title carries the full step; marks sit on the top edge, left of the arrow
    for st in f["steps"]:
        sid, lane, row, name, sub, state, kind = st[:7]
        marks = st[7] if len(st) > 7 else ""
        x, y = cx(lane) - BOX_W // 2, ty(row)
        cls = {"focal": "box focal", "new": "box new"}.get(kind, "box")
        tip = f"{name} — {sub}" + "".join(f"\n{MARKS[c][1]}: {MARKS[c][2]}" for c in marks)
        out.append(f'<g class="step"><title>{esc(tip)}</title>'
                   f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" class="paper"/>'
                   f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" class="{cls}"/>'
                   f'<text x="{x + 16}" y="{y + 44}" class="name">{esc(name)}</text>'
                   f'<text x="{x + 16}" y="{y + 72}" class="sub">{esc(sub)}</text></g>')
        for k, c in enumerate(marks):
            how, letter, text = MARKS[c]
            mx = x + 24 + k * 32
            out.append(f'<g class="mark {how}" data-k="{letter}"><title>{esc(text)}</title>'
                       f'<circle cx="{mx}" cy="{y}" r="12"/>'
                       f'<text x="{mx}" y="{y + 5}" text-anchor="middle">{letter}</text></g>')
        if state:
            sw = 16 + len(state) * 9
            sw += (-sw) % 4
            out.append(f'<rect x="{x + BOX_W - sw - 8}" y="{y - 12}" width="{sw}" height="24" rx="2" class="state"/>'
                       f'<text x="{x + BOX_W - sw // 2 - 8}" y="{y + 5}" class="statet" text-anchor="middle">{esc(state)}</text>')
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


def fig(svg: str) -> str:
    """A diagram with its zoom bar: smaller, larger, fit the whole diagram on screen, full width."""
    bar = ('<div class="zoom" role="toolbar" aria-label="Zoom this diagram">'
           '<button type="button" data-zoom="out" title="Smaller" aria-label="Smaller">−</button>'
           '<button type="button" data-zoom="in" title="Larger" aria-label="Larger">+</button>'
           '<button type="button" data-zoom="fit" title="Fit the whole diagram on screen">Fit to screen</button>'
           '<button type="button" data-zoom="full" title="Back to the default size">Reset</button></div>')
    return f'<div class="figwrap">{bar}<div class="fig">{svg}</div></div>'


def upgrade_flow(f: dict) -> dict:
    """The proposed version of a flow, in the same shape svg_flow draws (own slug, so its ids never clash)."""
    up = UPGRADES[f["slug"]]
    return {"slug": f["slug"] + "-up", "title": f["title"] + " (proposed)", "lede": up["summary"],
            "lanes": up["lanes"], "steps": up["steps"], "edges": up["edges"]}


NEED_CLASS = {"your decision": "dec", "DIP amendment": "dip", "code fix": "fix", "install": "ins"}


#: What has already happened to a proposal or a plan phase (2026-11-01).
STATUS_ICON = {"done": "✓", "ratified": "§", "in progress": "◐", "waiting on you": "⏸", "next": "→", "not started": "○"}
STATUS_CLASS = {"done": "sdone", "ratified": "srat", "in progress": "sprog", "waiting on you": "swait", "next": "snext",
                "not started": "snot"}


def status_pill(status: str, note: str = "") -> str:
    return (f'<span class="status {STATUS_CLASS[status]}" title="{esc(note)}">{STATUS_ICON[status]} {esc(status)}</span>')


def rebuild_section() -> str:
    """The rebuild plan's phases and where each stands, above the processes."""
    from process_upgrades import PLAN_PROGRESS
    rows = "".join(f'<li data-phase="{ph}">{status_pill(st, note)}<b>{ph}</b> {esc(title)}'
                   f'<span class="why">{esc(note)}</span></li>' for ph, title, st, note in PLAN_PROGRESS)
    return (f'<section id="rebuild" class="rebuild"><h2>Tasks and ledger rebuild: where it stands</h2>'
            f'<p class="lede">The plan you approved on 31 October, phase by phase, in order. Hover a status for its evidence. '
            + (f'Plan: {esc(DATA["plan_path"])}' if DATA["plan_path"] else "") + '</p>'
            f'<ol class="phases">{rows}</ol></section>')


def previous_pane(f: dict) -> str:
    """How the process ran before the rebuild began (process_previous.py, frozen 2026-10-31)."""
    from process_previous import PREVIOUS, AS_OF, COMMIT
    old = PREVIOUS.get(f["slug"])
    head = f'<p class="proposed prev">Before the rebuild · {AS_OF}</p>'
    if old is None:
        return f'{head}<p class="lede">Not drawn before the rebuild: this process was added to the page later.</p>'
    if all(old[k] == f[k] for k in ("lanes", "steps", "edges")):
        return (f'{head}<p class="lede">Unchanged: before the rebuild it ran as the Current drawing shows.</p>')
    notes = "".join(f"<li>{esc(n)}</li>" for n in old["notes"])
    flow = {"slug": f["slug"] + "-prev", "title": old["title"], "lede": old["lede"],
            "lanes": old["lanes"], "steps": old["steps"], "edges": old["edges"]}
    return (f'{head}<p class="lede">{esc(old["lede"])}</p>{fig(svg_flow(flow))}<ul class="notes">{notes}</ul>'
            f'<p class="src">As drawn from the code on {AS_OF} (commit {COMMIT}): {esc(old["source"])}</p>')


def upgrade_pane(f: dict) -> str:
    up = UPGRADES[f["slug"]]
    head = '<p class="proposed">Desired · Proposed, not running</p>'
    if up.get("keep"):
        return f'{head}<p class="lede"><b>{esc(up["summary"])}</b> {esc(up["keep"])}</p>'
    items = "".join(f'<li><span class="need {NEED_CLASS[c[2]]}">{esc(c[2])}</span>'
                    f'{status_pill(*c[3]) if len(c) == 4 else ""} {esc(c[0])}<br><span class="why">{esc(c[1])}</span></li>'
                    for c in up["changes"])
    return (f'{head}<p class="lede">{esc(up["summary"])}</p>{fig(svg_flow(upgrade_flow(f)))}'
            f'<h3 class="ch">What changes, and what each change needs</h3><ol class="changes">{items}</ol>')


def upgrades_markdown() -> str:
    """The proposals as a report, in page order (process_views.py --report)."""
    out = ["# Process upgrades: proposals for every process (2026-10-31)", "",
           "Owner, 2026-10-31: review the diagrams, suggest improvements in processes, and give each process a "
           "Previous / Current / Desired switch. Each proposal is drawn on the processes page "
           "(`1-tracks/dev/architecture/processes.html`, switch to Desired). Nothing has been changed in the running fleet.", "",
           "Each change names what it needs: **your decision**, a **DIP amendment** for you to ratify, a **code fix**, "
           "or an **install** (the code exists; nothing schedules or wires it).", ""]
    group = None
    for f in FLOWS:
        if f["group"] != group:
            group = f["group"]
            out += [f"## {group}", ""]
        up = UPGRADES[f["slug"]]
        out += [f"### {f['title']}", "", up["summary"], ""]
        if up.get("keep"):
            out += [up["keep"], ""]
            continue
        for c in up["changes"]:
            st = f" ({STATUS_ICON[c[3][0]]} {c[3][0]}: {c[3][1]})" if len(c) == 4 else ""
            out.append(f"- **{c[2]}**{st}: {c[0]} *Why:* {c[1]}")
        out.append("")
    return "\n".join(out)


def overlap_section(inv: dict) -> str:
    groups: dict[str, list] = {}
    for f in inv.get("overlaps") or []:
        groups.setdefault(f["kind"], []).append(f)
    rows = []
    for cells in DATA["overlap_fixed"]:   # overlaps already fixed, as this install recorded them
        rows.append('<tr class="fixed">' + "".join(f"<td>{esc(c)}</td>" for c in cells) + '</tr>')
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
--rule-solid:rgba(26,26,26,.22);--accent:#0e7490;--accent-tint:rgba(14,116,144,.08);--link:#6d4bc7;--sig:#6d4bc7;--prov:#b45309}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--paper:#0e0f14;--paper-2:#16181f;--ink:#f0f0f2;--muted:#c5c5c7;
--soft:#8d8d90;--rule:rgba(240,240,242,.10);--rule-solid:rgba(240,240,242,.22);--accent:#22d3ee;--accent-tint:rgba(34,211,238,.12);
--link:#a78bfa;--sig:#a78bfa;--prov:#f0a050}}
:root[data-theme="dark"]{--paper:#0e0f14;--paper-2:#16181f;--ink:#f0f0f2;--muted:#c5c5c7;--soft:#8d8d90;--rule:rgba(240,240,242,.10);
--rule-solid:rgba(240,240,242,.22);--accent:#22d3ee;--accent-tint:rgba(34,211,238,.12);--link:#a78bfa;--sig:#a78bfa;--prov:#f0a050}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:'Outfit',system-ui,sans-serif;font-size:17px;line-height:1.5}
header.top{position:sticky;top:0;z-index:3;height:56px;background:var(--paper);border-bottom:1px solid var(--rule);padding:0 24px;display:flex;
gap:12px;align-items:center}
header.top a{color:var(--ink);text-decoration:none;font-size:15px;padding:5px 12px;border:1px solid var(--rule-solid);border-radius:6px}
header.top a:hover,header.top a.on{border-color:var(--accent);color:var(--accent)}
header.top .eyebrow{font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.14em;color:var(--soft);margin-right:8px}
.wrap{display:grid;grid-template-columns:272px minmax(0,1fr);max-width:1760px;margin:0 auto}
nav.side{position:sticky;top:56px;height:calc(100vh - 56px);overflow-y:auto;border-right:1px solid var(--rule);padding:20px 16px 32px}
nav.side .g{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--soft);margin:20px 10px 6px}
nav.side .g:first-child{margin-top:0}
nav.side a{display:block;padding:6px 10px;margin:1px 0;border-radius:6px;color:var(--muted);font-size:15px;line-height:1.35;text-decoration:none}
nav.side a:hover{color:var(--ink);background:var(--paper-2)}
nav.side a.on{color:var(--accent);background:var(--accent-tint)}
.key{margin:24px 10px 0;padding-top:16px;border-top:1px solid var(--rule);font-size:13px;color:var(--muted)}
.key div{display:flex;gap:10px;align-items:flex-start;margin:8px 0;line-height:1.35}
.key svg{flex:none}
main{padding:8px 40px 64px;min-width:0}
h1{font-family:'Literata',Georgia,serif;font-weight:300;font-size:36px;margin:24px 0 8px}
h2{font-family:'Literata',Georgia,serif;font-weight:400;font-size:28px;margin:56px 0 8px;scroll-margin-top:72px}
h3{font-size:20px;font-weight:600;margin:28px 0 10px}
.lede{font-size:18px;color:var(--muted);max-width:76ch;margin:0 0 20px}
.src{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--soft);max-width:120ch}
.fig{overflow:auto;margin:0 0 12px}
.figwrap{position:relative;margin:8px 0 0;scroll-margin-top:60px}
.zoom{display:flex;gap:4px;justify-content:flex-end;position:sticky;top:64px;z-index:2;margin:0 0 4px}
.zoom button,.sidebtn{font:inherit;font-size:13px;color:var(--muted);background:var(--paper);border:1px solid var(--rule-solid);
border-radius:6px;padding:3px 10px;cursor:pointer;min-width:32px}
.zoom button:hover,.sidebtn:hover{color:var(--accent);border-color:var(--accent)}
.sidebtn{font-size:14px;color:var(--ink)}
body.side-off .wrap{grid-template-columns:minmax(0,1fr);max-width:none}
body.side-off nav.side{display:none}
ul.notes{max-width:96ch;padding-left:20px;font-size:16px}ul.notes li{margin:6px 0}
svg.flow{max-width:100%;height:auto;display:block}
svg.flow.zoomed{max-width:none}
svg .paper{fill:var(--paper)} svg .box{fill:var(--paper);stroke:var(--ink);stroke-width:1.2}
svg .box.focal{fill:var(--accent-tint);stroke:var(--accent);stroke-width:2}
svg .box.new{stroke:var(--accent);stroke-width:2;stroke-dasharray:7 5}
.switch{display:inline-flex;border:1px solid var(--rule-solid);border-radius:8px;overflow:hidden;margin:4px 0 14px}
.switch input{position:absolute;opacity:0;pointer-events:none}
.switch label{padding:6px 16px;font-size:15px;cursor:pointer;color:var(--muted)}
.switch label+input+label{border-left:1px solid var(--rule-solid)}
.switch input:checked+label{background:var(--accent-tint);color:var(--accent);font-weight:600}
.switch input:focus-visible+label{outline:2px solid var(--accent);outline-offset:-2px}
.pane.up,.pane.prev{display:none}
section.flow-sec[data-view="prev"] .pane.cur{display:none} section.flow-sec[data-view="prev"] .pane.prev{display:block}
section.flow-sec:has(input[value="prev"]:checked) .pane.cur{display:none}
section.flow-sec:has(input[value="prev"]:checked) .pane.prev{display:block}
section.flow-sec:has(input[value="prev"]:checked) .pane.up{display:none}
.proposed.prev{color:var(--muted);border-color:var(--muted)}
section.flow-sec[data-view="up"] .pane.cur{display:none} section.flow-sec[data-view="up"] .pane.up{display:block}
section.flow-sec:has(input[value="up"]:checked) .pane.cur{display:none}
section.flow-sec:has(input[value="up"]:checked) .pane.up{display:block}
.proposed{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
color:var(--prov);border:1px dashed var(--prov);border-radius:4px;padding:2px 8px;margin:0 0 10px}
h3.ch{font-size:18px;margin:18px 0 6px}
ol.changes{max-width:96ch;padding-left:22px;font-size:16px}ol.changes li{margin:10px 0}
ol.changes .why{color:var(--muted);font-size:15px}
.need{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:1px 6px;
border-radius:4px;border:1px solid currentColor;margin-right:6px;white-space:nowrap}
.need.dec{color:var(--sig)} .need.dip{color:var(--prov)} .need.fix{color:var(--accent)} .need.ins{color:var(--soft)}
.status{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:1px 6px;margin:0 6px 0 4px;border:1px solid currentColor;border-radius:4px;white-space:nowrap;cursor:help}
.status.sdone{color:#34d399} .status.srat{color:var(--prov)} .status.sprog{color:#f0a050} .status.swait{color:#e8695f} .status.snext{color:#22d3ee} .status.snot{color:var(--soft)}
.rebuild{margin:8px 0 36px;padding:16px 20px;border:1px solid var(--rule-solid);border-radius:10px;background:var(--paper-2)}
.rebuild h2{margin:0 0 6px;font-size:20px;font-weight:500} .rebuild .lede{font-size:15px;margin-bottom:10px}
.phases{list-style:none;margin:0;padding:0} .phases li{padding:6px 0;border-top:1px solid var(--rule);font-size:15px}
.phases li b{font-family:'JetBrains Mono',monospace;margin-right:6px} .phases .why{display:block;margin-left:2px;font-size:13px;color:var(--soft)}
header.top .all{margin-left:auto;display:flex;gap:6px;align-items:center;font-size:13px;color:var(--soft)}
header.top .all button{font:inherit;font-size:14px;color:var(--ink);background:none;border:1px solid var(--rule-solid);border-radius:6px;
padding:4px 10px;cursor:pointer}
header.top .all button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}
svg .step:hover .box{stroke:var(--accent);stroke-width:2}
svg .name{font-family:'Outfit',sans-serif;font-size:16px;font-weight:600;fill:var(--ink)}
svg .sub{font-family:'JetBrains Mono',monospace;font-size:13px;fill:var(--muted)}
svg .lane{font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.1em;fill:var(--soft)}
svg .lane-rule{stroke:var(--rule-solid);stroke-width:1}
svg .edge{fill:none;stroke:var(--muted);stroke-width:1.6} svg .mk{fill:var(--muted)}
svg .mask{fill:var(--paper)} svg .elabel{font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.04em;fill:var(--soft)}
svg .state{fill:var(--paper);stroke:var(--accent);stroke-width:1.2} svg .statet{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:400;fill:var(--accent)}
.mark circle{fill:var(--paper);stroke:var(--sig);stroke-width:1.8}
.mark text{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;fill:var(--sig)}
.mark.optional circle{stroke-dasharray:3 3}
.mark[data-k="R"] circle{stroke:var(--prov)} .mark[data-k="R"] text{fill:var(--prov)}
.mark{cursor:help}
table{border-collapse:collapse;width:100%;font-size:16px;margin:8px 0}
th{text-align:left;font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--soft);
font-weight:400;border-bottom:1px solid var(--rule-solid);padding:8px 12px}
td{border-bottom:1px solid var(--rule);padding:9px 12px;vertical-align:top}
tr.fixed td{color:var(--soft)} tr.fixed td:last-child{color:var(--accent)}
code{font-family:'JetBrains Mono',monospace;font-size:14px}
details{border-bottom:1px solid var(--rule);padding:10px 0;max-width:120ch}summary{cursor:pointer;font-size:17px}
details .n{display:inline-block;min-width:40px;font-family:'JetBrains Mono',monospace;color:var(--accent)}
details .why{color:var(--soft)} details p{font-size:15px;line-height:1.7}
@media (max-width:900px){.wrap{display:block}nav.side{position:static;height:auto;border-right:0;border-bottom:1px solid var(--rule)}
main{padding:8px 16px 48px}h1{font-size:30px}h2{font-size:24px}header.top{padding:0 16px}}
"""

JS = """
(function(){var links={};document.querySelectorAll('nav.side a').forEach(function(a){links[a.getAttribute('href').slice(1)]=a});
if(!('IntersectionObserver' in window))return;var cur=null;
var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){if(cur)cur.classList.remove('on');
cur=links[e.target.id];if(cur)cur.classList.add('on')}})},{rootMargin:'-64px 0px -70% 0px'});
document.querySelectorAll('main h2[id]').forEach(function(h){io.observe(h)})})();
(function(){var secs=document.querySelectorAll('section.flow-sec'),btns=document.querySelectorAll('[data-all]');
function show(sec,v){sec.dataset.view=v;var r=sec.querySelector('input[value="'+v+'"]');if(r)r.checked=true}
function all(v){secs.forEach(function(s){show(s,v)});btns.forEach(function(b){b.setAttribute('aria-pressed',b.dataset.all===v)});
try{localStorage.setItem('processes-view',v)}catch(e){}}
secs.forEach(function(s){s.querySelectorAll('.switch input').forEach(function(r){r.addEventListener('change',function(){show(s,r.value)})})});
btns.forEach(function(b){b.addEventListener('click',function(){all(b.dataset.all)})});
var saved=null;try{saved=localStorage.getItem('processes-view')}catch(e){}if(saved==='up'||saved==='prev')all(saved)})();
(function(){var b=document.getElementById('side-toggle');if(!b)return;
function set(off){document.body.classList.toggle('side-off',off);b.setAttribute('aria-expanded',String(!off));
try{localStorage.setItem('processes-side',off?'off':'on')}catch(e){}}
b.addEventListener('click',function(){set(!document.body.classList.contains('side-off'))});
var s=null;try{s=localStorage.getItem('processes-side')}catch(e){}if(s==='off')set(true)})();
document.addEventListener('click',function(e){var t=e.target.closest('[data-zoom]');if(!t)return;
var svg=t.closest('.figwrap').querySelector('svg.flow'),vb=svg.viewBox.baseVal,box=svg.parentNode.clientWidth,a=t.dataset.zoom;
var w=svg.getBoundingClientRect().width;
if(a==='full'){svg.classList.remove('zoomed');svg.style.width='';return}
if(a==='fit'){w=Math.min(box,vb.width*(window.innerHeight-150)/vb.height)}
else{w=w*(a==='in'?1.25:0.8)}
w=Math.max(240,Math.min(w,vb.width*3));svg.classList.add('zoomed');svg.style.width=Math.round(w)+'px';
if(a==='fit')svg.closest('.figwrap').scrollIntoView({block:'start'})});
"""


def _key_badge(letter: str, how: str) -> str:
    dash = ' stroke-dasharray="3 3"' if how == "optional" else ""
    return (f'<svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><g class="mark {how}" data-k="{letter}">'
            f'<circle cx="14" cy="14" r="12"{dash}/><text x="14" y="19" text-anchor="middle">{letter}</text></g></svg>')


def sidebar() -> str:
    out, group = [], None
    for f in FLOWS:
        if f["group"] != group:
            group = f["group"]
            out.append(f'<div class="g">{esc(group)}</div>')
        out.append(f'<a href="#{f["slug"]}">{esc(f["title"])}</a>')
        if f["slug"] == "task-lifecycle":
            out.append('<a href="#task-states" style="padding-left:24px;font-size:14px">The seven task states</a>')
    out.append('<div class="g">Fleet</div><a href="#overlaps">Duplicates and overlaps</a>')
    key = ['<div class="key"><div class="g" style="margin:0 0 4px">Key · hover any mark</div>',
           '<div><svg width="28" height="20" aria-hidden="true"><rect x="1" y="1" width="26" height="18" rx="4" '
           'style="fill:var(--accent-tint);stroke:var(--accent);stroke-width:2"/></svg>where it broke or matters most</div>']
    for c, (how, letter, text) in MARKS.items():
        short = {"S": "signed by the writer", "O": "signed by the owner",
                 "C": "your co-sign, only for side effects", "R": "provenance receipt, where the hook is on"}[letter]
        key.append(f'<div title="{esc(text)}">{_key_badge(letter, how)}<span>{esc(short)}'
                   f'{" <i>(optional)</i>" if how == "optional" else ""}</span></div>')
    key.append('<div>Dashed ring: happens only in some cases.</div>')
    key.append('<div><svg width="28" height="20" aria-hidden="true"><rect x="1" y="1" width="26" height="18" rx="4" '
               'style="fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:5 3"/></svg>Desired view: new or changed step</div></div>')
    return f'<nav class="side" id="side" aria-label="Processes">{"".join(out)}{"".join(key)}</nav>'


def page(inv: dict) -> str:
    body = []
    for f in FLOWS:
        notes = "".join(f"<li>{esc(n)}</li>" for n in f["notes"])
        s = f["slug"]
        switch = (f'<div class="switch" role="radiogroup" aria-label="Previous, current or desired flow">'
                  f'<input type="radio" name="view-{s}" id="{s}-prev" value="prev"><label for="{s}-prev">Previous</label>'
                  f'<input type="radio" name="view-{s}" id="{s}-cur" value="cur" checked><label for="{s}-cur">Current</label>'
                  f'<input type="radio" name="view-{s}" id="{s}-up" value="up"><label for="{s}-up">Desired</label></div>')
        body.append(f'<section class="flow-sec" data-view="cur"><h2 id="{s}">{esc(f["title"])}</h2>{switch}'
                    f'<div class="pane cur" id="{s}-cur-pane"><p class="lede">{esc(f["lede"])}</p>'
                    f'{fig(svg_flow(f))}<ul class="notes">{notes}</ul>'
                    f'<p class="src">From the code: {esc(f["source"])}</p></div>'
                    f'<div class="pane prev" id="{s}-prev-pane">{previous_pane(f)}</div>'
                    f'<div class="pane up" id="{s}-up-pane">{upgrade_pane(f)}</div></section>')
        if f["slug"] == "task-lifecycle":
            rows = "".join(f"<tr><td><b>{a}</b></td><td><code>{esc(b)}</code></td><td>{esc(c)}</td><td>{esc(d)}</td></tr>"
                           for a, b, c, d in STATES)
            body.append('<h3 id="task-states" style="scroll-margin-top:72px">The seven task states (DIP-0009 v2.0) and their ledger events</h3>'
                        '<table><thead><tr><th>State</th><th>Ledger</th><th>Means</th><th>Who moves it here</th></tr></thead>'
                        f'<tbody>{rows}</tbody></table>')
    body.append('<h2 id="overlaps">Duplicates and overlaps</h2><p class="lede">Generated from the job contracts and the live '
                'schedules on every machine. An overlap is the same work, or the same shared file, owned twice.</p>'
                + overlap_section(inv))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Datacore processes</title>
<meta name="description" content="How work moves through Datacore: tasks, the morning, the ledger (admit, fold, seal, corrections, daily checks), sync, scheduled jobs, cadences, repairs, and where processes overlap.">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Literata:ital,wght@0,300;0,400;1,300;1,400&family=JetBrains+Mono:wght@300;400;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header class="top"><button type="button" id="side-toggle" class="sidebtn" aria-controls="side" aria-expanded="true" title="Hide or show the list of processes">☰ List</button><span class="eyebrow">DATACORE</span><a href="datacore.html">Architecture map</a><a class="on" href="processes.html">Processes</a><span class="all">Show every process as <button type="button" data-all="prev" aria-pressed="false">Previous</button><button type="button" data-all="cur" aria-pressed="true">Current</button><button type="button" data-all="up" aria-pressed="false">Desired</button></span></header>
<div class="wrap">{sidebar()}
<main><h1>Processes and workflows</h1>
<p class="lede">The architecture map shows what exists. This page shows how work moves through it: who runs each step, on which machine, what it writes, and where a step is signed or leaves a provenance receipt. Drawn from the code and the schedules; the overlap list is generated. Hover a step or a mark for its full text. Each process has three views: Previous is how it ran before the rebuild began on 31 October, Current is how it runs now, and Desired is the target, with what each change needs and what has already happened to it.</p>
{rebuild_section()}
{"".join(body)}
</main></div><script>{JS}</script></body></html>"""


def load_inventory(path=None) -> dict:
    p = Path(path) if path else ARCH / "processes.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inventory", default=str(ARCH / "processes.yaml"))
    ap.add_argument("--out", default=str(ARCH / "processes.html"))
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--report", help="also write the upgrade proposals as a markdown report to this path")
    a = ap.parse_args(argv)
    inv = load_inventory(a.inventory)
    Path(a.out).write_text(page(inv))
    print(a.out)
    if a.report:
        Path(a.report).write_text(upgrades_markdown() + "\n")
        print(a.report)
    if a.open:
        # dayshift has no screen: mac-open shows it on the Mac while `ds` is attached.
        opener = "open" if sys.platform == "darwin" else "mac-open"
        subprocess.run([opener, a.out], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
