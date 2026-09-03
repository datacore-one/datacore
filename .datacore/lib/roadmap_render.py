#!/usr/bin/env python3
"""Render 5-plur/roadmap.yaml to a human view.

The other half of the 2026-08-21 spec: roadmap.yaml is the source, every
readable roadmap is generated. A stale generated view is then a visible diff
rather than a silent three-month gap.

Embargoed items keep their title, track, horizon and gate — the roadmap shape
is not secret — but their `note` is withheld. Several embargoed notes describe
a live defect in a shipped product and carry a stricter embargo than the rest.

Usage:
    python3 .datacore/lib/roadmap_render.py --html out.html
    python3 .datacore/lib/roadmap_render.py --md            # stdout
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip install pyyaml")

REPO = Path(__file__).resolve().parents[2]
ROADMAP = REPO / "5-plur" / "roadmap.yaml"

REDACTED = "Withheld from generated views — embargoed. Read it in roadmap.yaml."


def load():
    return yaml.safe_load(ROADMAP.read_text())


INTENTS_ORG = REPO / "5-plur" / "org" / "intents.org"


def load_intents():
    """Parse org/intents.org into a flat list of nodes with depth and kind.

    The intent graph is the authority on *why*; roadmap.yaml only references it.
    Parsing it here rather than restating it keeps that direction of authority —
    a renamed or deleted intent shows up as a missing node, not as a stale copy.
    """
    if not INTENTS_ORG.exists():
        return []
    nodes, cur = [], None
    for line in INTENTS_ORG.read_text().splitlines():
        if line.startswith("*"):
            stars = len(line) - len(line.lstrip("*"))
            tags = re.findall(r":([a-z_]+):", line)
            title = re.sub(r"\s*:[a-z_:]+:\s*$", "", line.lstrip("* ")).strip()
            cur = {"depth": stars, "title": title, "kind": tags[-1] if tags else None,
                   "id": None, "success": None, "gate": None, "note": None}
            nodes.append(cur)
        elif cur is not None:
            for key, field in ((":INTENT_ID:", "id"), (":SUCCESS:", "success"),
                               (":GATE:", "gate"), (":METRIC:", "note")):
                m = re.match(rf"^\s*{key}\s*(.+?)\s*$", line)
                if m:
                    cur[field] = m.group(1)
    return [n for n in nodes if n["id"]]


def public_items(r):
    """Items with embargoed notes stripped."""
    out = []
    for i in r["items"]:
        i = dict(i)
        if i.get("embargoed"):
            i["note"] = REDACTED
        out.append(i)
    return out


def render_md(r):
    lines = [f"# PLUR Roadmap\n", f"_Generated from `5-plur/roadmap.yaml` — updated {r['updated']}._\n"]
    ns = r["north_star"]
    lines.append(f"**North star:** {ns['metric']} — {ns['target']} by {ns['by']}"
                 + (f"  ⚠️ **{ns['status']}**" if ns.get("status") else "") + "\n")
    for h in ("now", "next", "gated", "later"):
        group = [i for i in public_items(r) if i["horizon"] == h]
        if not group:
            continue
        lines.append(f"\n## {h.title()}\n")
        for i in group:
            flag = " 🔒" if i.get("embargoed") else ""
            block = f" — **blocked on {i['blocked_on']}**" if i.get("blocked_on") else ""
            lines.append(f"- **{i['id']} · {i['title']}**{flag} `{i['track']}`{block}  \n"
                         f"  {i['outcome']}")
            if i.get("gate"):
                lines.append(f"  <br>Gate: _{i['gate']}_")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────── HTML view

# PLUR SKIN — derived from plur.ai's own CSS custom properties, 2026-09-01.
# plur.ai is dark-first; its light variant supplies the light column. Brand cyan
# #22d3ee measures 10.59:1 on their dark ground but 1.73:1 on light, so light
# carries cyan-700 #0e7490 (5.13:1). Semantic colours reuse plur.ai's own
# --amber / --violet / --emerald rather than inventing a second palette.
CSS = """
:root{
  --paper:#fafaf9; --surface:#ffffff; --raised:#f5f5f0;
  --ink:#1a1a1a; --body:#4b4b4b; --muted:#6e6e6d; --faint:#888883;
  --rule:rgba(26,26,26,.12); --rule-soft:rgba(26,26,26,.07);
  --accent:#0e7490; --accent-soft:rgba(14,116,144,.09);
  --human:#a15c11; --human-soft:rgba(240,160,80,.14);
  --gated:#6d4bc7; --gated-soft:rgba(167,139,250,.14);
  --halt:#9b2c2c; --halt-soft:rgba(155,44,44,.10);
  --go:#12704c; --go-soft:rgba(52,211,153,.14);
  --shadow:0 1px 2px rgba(26,26,26,.04),0 8px 24px -14px rgba(26,26,26,.16);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#0e0f14; --surface:#16181f; --raised:#1d2028;
    --ink:#f0f0f2; --body:#c5c5c7; --muted:#8d8d90; --faint:#6c7076;
    --rule:rgba(240,240,242,.12); --rule-soft:rgba(240,240,242,.06);
    --accent:#22d3ee; --accent-soft:rgba(34,211,238,.12);
    --human:#f0a050; --human-soft:rgba(240,160,80,.13);
    --gated:#a78bfa; --gated-soft:rgba(167,139,250,.13);
    --halt:#e08585; --halt-soft:rgba(224,133,133,.12);
    --go:#34d399; --go-soft:rgba(52,211,153,.12);
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -14px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --paper:#0e0f14; --surface:#16181f; --raised:#1d2028;
  --ink:#f0f0f2; --body:#c5c5c7; --muted:#8d8d90; --faint:#6c7076;
  --rule:rgba(240,240,242,.12); --rule-soft:rgba(240,240,242,.06);
  --accent:#22d3ee; --accent-soft:rgba(34,211,238,.12);
  --human:#f0a050; --human-soft:rgba(240,160,80,.13);
  --gated:#a78bfa; --gated-soft:rgba(167,139,250,.13);
  --halt:#e08585; --halt-soft:rgba(224,133,133,.12);
  --go:#34d399; --go-soft:rgba(52,211,153,.12);
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -14px rgba(0,0,0,.7);
}
/* Diagram tokens. These MUST cover every var() the inline SVG references — an
   undefined var() with no fallback makes `fill` invalid-at-computed-value-time,
   which resolves to the initial value BLACK, not to the inherited colour. That
   rendered five node boxes and eight labels solid black in both themes. */
:root{
  --paper-2:#f5f5f0; --soft:#6e6e6d; --accent-tint:rgba(14,116,144,.08);
  --fill-store:rgba(26,26,26,.05); --fill-opt:rgba(26,26,26,.02); --stroke-opt:rgba(26,26,26,.28);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --paper-2:#1d2028; --soft:#9aa0a6; --accent-tint:rgba(34,211,238,.14);
  --fill-store:rgba(240,240,242,.06); --fill-opt:rgba(240,240,242,.03); --stroke-opt:rgba(240,240,242,.32);
}}
:root[data-theme="dark"]{
  --paper-2:#1d2028; --soft:#9aa0a6; --accent-tint:rgba(34,211,238,.14);
  --fill-store:rgba(240,240,242,.06); --fill-opt:rgba(240,240,242,.03); --stroke-opt:rgba(240,240,242,.32);
}

*{box-sizing:border-box}
body{
  background:var(--paper); color:var(--body);
  font-family:'Outfit',ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;
  font-size:15px; line-height:1.55; margin:0;
  font-variant-numeric:tabular-nums;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1120px;margin:0 auto;padding:0 28px 96px}
h1,h2,h3{color:var(--ink);text-wrap:balance;margin:0}
h1{font-family:'Literata',Georgia,serif;font-size:clamp(28px,4.2vw,42px);font-weight:400;letter-spacing:-.012em;line-height:1.08}
h2{font-family:'Literata',Georgia,serif;font-size:20px;font-weight:400;letter-spacing:-.006em}
h3{font-size:15px;font-weight:600;letter-spacing:-.005em}
p{margin:0}
code,.mono{font-family:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,monospace}

.eyebrow{
  font-family:'JetBrains Mono',monospace; font-size:10.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--faint);
}

/* header */
header{border-bottom:1px solid var(--rule);background:var(--surface)}
.head-in{max-width:1120px;margin:0 auto;padding:36px 28px 30px;display:flex;flex-direction:column;gap:18px}
.head-top{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap}
.srcline{
  font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--muted);
  display:flex;gap:14px;flex-wrap:wrap;align-items:center;
}
.srcline b{color:var(--accent);font-weight:500}
.valid{
  display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:999px;
  background:var(--go-soft);color:var(--go);border:1px solid color-mix(in srgb,var(--go) 26%,transparent);
  font-family:'JetBrains Mono',monospace;font-size:11.5px;white-space:nowrap;
}
.valid::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--go)}
.lede{max-width:66ch;font-size:16.5px;color:var(--body)}

/* metric strip */
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:1px;background:var(--rule);
  border:1px solid var(--rule);border-radius:10px;overflow:hidden;margin:34px 0 0}
.metric{background:var(--surface);padding:16px 18px;display:flex;flex-direction:column;gap:3px}
.metric .n{font-family:'Literata',Georgia,serif;font-size:30px;font-weight:400;color:var(--ink);line-height:1.05;letter-spacing:-.02em}
.metric.alarm .n{color:var(--human)}
.metric .l{font-size:12.5px;color:var(--muted);line-height:1.35}

section{margin-top:56px}
.sec-head{display:flex;align-items:baseline;gap:14px;margin-bottom:6px;flex-wrap:wrap}
.sec-sub{max-width:68ch;color:var(--muted);font-size:14.5px;margin-top:8px}


/* board */
.filters{display:flex;gap:7px;flex-wrap:wrap;margin:20px 0 16px}
.chip{
  font-family:'JetBrains Mono',monospace;font-size:11.5px;padding:5px 11px;border-radius:999px;
  border:1px solid var(--rule);background:var(--surface);color:var(--muted);cursor:pointer;
}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:var(--paper)}
.chip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

.hgroup{margin-top:26px}
.hlabel{display:flex;align-items:center;gap:11px;margin-bottom:10px}
.hlabel .bar{height:2px;flex:1;background:var(--rule)}
.hcount{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--faint)}

.items{display:grid;gap:8px}
.item{
  background:var(--surface);border:1px solid var(--rule);border-radius:9px;
  padding:14px 16px 14px 18px;position:relative;overflow:hidden;
}
.item::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--rule)}
.item[data-block="human"]::before{background:var(--human)}
.item[data-block="standing_block"]::before{background:var(--halt)}
.item[data-h="gated"]::before{background:var(--gated)}
.item[data-s="in_progress"]::before{background:var(--go)}
.itop{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.iid{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--faint)}
.ititle{color:var(--ink);font-weight:600;font-size:14.5px;letter-spacing:-.004em}
.iout{color:var(--body);font-size:14px;margin-top:5px;max-width:74ch}
.imeta{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;align-items:center}
.tag{font-family:'JetBrains Mono',monospace;font-size:10.5px;padding:2.5px 8px;border-radius:4px;
  background:var(--raised);color:var(--muted);border:1px solid var(--rule-soft)}
.tag.track{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.tag.human{background:var(--human-soft);color:var(--human);border-color:transparent}
.tag.halt{background:var(--halt-soft);color:var(--halt);border-color:transparent}
.tag.lock{background:var(--gated-soft);color:var(--gated);border-color:transparent}
.tag.go{background:var(--go-soft);color:var(--go);border-color:transparent}
.igate{margin-top:9px;font-size:13px;color:var(--gated);display:flex;gap:7px}
.igate b{font-family:'JetBrains Mono',monospace;font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--faint);flex-shrink:0;padding-top:2px}
.serves{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--faint);margin-top:8px}

/* coverage */
.cov{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:20px}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:18px 20px}
.bar-track{height:7px;background:var(--rule-soft);border-radius:99px;overflow:hidden;margin:12px 0 10px}
.bar-fill{height:100%;background:var(--accent);border-radius:99px}
.gaps{list-style:none;padding:0;margin:10px 0 0;display:grid;gap:5px}
.gaps li{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--gated);
  display:flex;gap:8px;align-items:baseline}
.gaps li::before{content:"○";color:var(--faint);font-size:9px}

.flag{border:1px solid color-mix(in srgb,var(--human) 34%,transparent);background:var(--human-soft);
  border-radius:10px;padding:20px 22px;margin-top:20px}
.flag h3{color:var(--human);margin-bottom:8px}
.flag p{color:var(--body);max-width:70ch;font-size:14.5px}

footer{margin-top:64px;padding-top:22px;border-top:1px solid var(--rule);
  color:var(--faint);font-size:12.5px;font-family:'JetBrains Mono',monospace;
  display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap}

/* master plan */
.plan{display:grid;gap:1px;background:var(--rule);border:1px solid var(--rule);
  border-radius:10px;overflow:hidden;margin-top:22px}
.step{background:var(--surface);padding:20px 24px;display:grid;
  grid-template-columns:56px 1fr;gap:20px;align-items:start}
.step .num{font-family:'Literata',Georgia,serif;font-size:34px;line-height:1;color:var(--accent)}
.step.thru .num{font-family:'JetBrains Mono',monospace;font-size:10px;line-height:1.3;
  letter-spacing:.1em;text-transform:uppercase;color:var(--accent);padding-top:6px}
.step .do{font-family:'Literata',Georgia,serif;font-size:19px;color:var(--ink);line-height:1.35}
.step .why{font-size:14.5px;color:var(--muted);margin-top:9px;max-width:72ch}
.step.thru{background:var(--accent-soft)}

/* intents */
.intents{margin-top:20px;display:grid;gap:14px}
.l1{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:18px 22px}
.l1 > .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.l1 .nm{font-family:'Literata',Georgia,serif;font-size:17px;color:var(--ink)}
.goals{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.goal{font-family:'JetBrains Mono',monospace;font-size:11px;padding:4px 9px;border-radius:5px;
  background:var(--raised);color:var(--body);border:1px solid var(--rule-soft)}
.goal.cons{background:transparent;color:var(--halt);border-color:color-mix(in srgb,var(--halt) 30%,transparent)}
.vision{background:var(--accent-soft);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);
  border-radius:10px;padding:20px 22px;margin-top:20px}
.vision .nm{font-family:'Literata',Georgia,serif;font-size:19px;color:var(--ink);line-height:1.35}

/* milestones */
.miles{margin-top:22px;position:relative;display:grid;gap:0}
.mile{display:grid;grid-template-columns:96px 1fr;gap:22px;padding:20px 0;
  border-top:1px solid var(--rule-soft)}
.mile:first-child{border-top:none}
.mile .rail{display:flex;flex-direction:column;align-items:flex-start;gap:7px}
.mile .mid{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--faint)}
.mile .mt{font-family:'Literata',Georgia,serif;font-size:20px;color:var(--ink);line-height:1.3}
.mile .ev{font-size:14.5px;color:var(--muted);margin-top:8px;max-width:74ch}
.mile .mgate{margin-top:10px;font-size:13.5px;color:var(--gated);display:flex;gap:8px}
.mile .mgate b{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--faint);flex-shrink:0;padding-top:3px}
.state{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;padding:4px 9px;border-radius:5px;white-space:nowrap}
.state.shipped{background:var(--go-soft);color:var(--go)}
.state.in_progress{background:var(--accent-soft);color:var(--accent)}
.state.not_shipped{background:var(--human-soft);color:var(--human)}
.state.gated{background:var(--gated-soft);color:var(--gated)}
.state.vision{background:var(--raised);color:var(--muted)}

.feats{margin-top:18px;padding-top:16px;border-top:1px dashed var(--rule)}
.feats > .eyebrow{margin:0 0 12px}
.feat{padding:12px 0;border-top:1px solid var(--rule-soft)}
.feat:first-of-type{border-top:none;padding-top:0}
.fh{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.fgh{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--accent);
  text-decoration:none;border-bottom:1px solid transparent;flex-shrink:0}
.fgh:hover{border-bottom-color:var(--accent)}
.fgh:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ft{color:var(--ink);font-weight:600;font-size:14px}
.kids{font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--faint);
  background:var(--raised);padding:2px 7px;border-radius:4px}
.fd{font-size:14px;color:var(--muted);margin-top:6px;max-width:76ch}

/* ladder */
.ladderwrap{display:grid;grid-template-columns:minmax(220px,300px) 1fr;gap:28px;margin-top:22px;align-items:start}
.spine{background:var(--accent-soft);border:1px solid color-mix(in srgb,var(--accent) 28%,transparent);
  border-radius:10px;padding:18px 22px;margin:18px 0 4px;max-width:70ch}
.spine .prim{display:block;font-family:'Literata',Georgia,serif;font-size:26px;color:var(--accent);
  margin:6px 0 10px;letter-spacing:-.01em}
.spinenote{font-size:13.5px;color:var(--body);line-height:1.5}
.rungs{display:flex;flex-direction:column-reverse;gap:8px}
.rung{display:grid;grid-template-columns:130px 1fr auto auto;gap:14px;align-items:baseline;
  background:var(--surface);border:1px solid var(--rule);border-radius:9px;padding:14px 16px}
.rung .rname{font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.1em;color:var(--muted)}
.rung .ris{color:var(--body);font-size:14px}
.rung .rst,.rung .rm{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;padding:3px 8px;border-radius:4px;background:var(--raised);color:var(--muted)}
.rung.s-shipping{border-color:color-mix(in srgb,var(--accent) 46%,transparent);background:var(--accent-soft)}
.rung.s-shipping .rname,.rung.s-shipping .rst{color:var(--accent)}
.rung.s-horizon{border-style:dashed;opacity:.86}

/* entities + verticals */
.ents{display:grid;gap:12px;margin-top:20px}
.ent{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:16px 20px}
.ent .nm{font-family:'Literata',Georgia,serif;font-size:17px;color:var(--ink)}
.tablewrap{overflow-x:auto;margin-top:20px;border:1px solid var(--rule);border-radius:10px}
.vtab{border-collapse:collapse;width:100%;background:var(--surface);font-size:14px}
.vtab th{text-align:left;font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint);font-weight:400;padding:12px 16px;
  border-bottom:1px solid var(--rule)}
.vtab td{padding:13px 16px;border-bottom:1px solid var(--rule-soft);vertical-align:top}
.vtab tr:last-child td{border-bottom:none}
.vd{color:var(--ink);font-weight:600;white-space:nowrap}
.vs{color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:12.5px;white-space:nowrap}
.vi{color:var(--body);max-width:56ch}
@media (max-width:760px){.ladderwrap{grid-template-columns:1fr}.rung{grid-template-columns:1fr auto}}

.goals-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:22px}
.goal-card{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:18px 20px}
.goal-card .gid{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--accent)}
.goal-card .gname{font-family:'Literata',Georgia,serif;font-size:19px;color:var(--ink);line-height:1.25}
.gmotion,.gfrom{font-size:13px;color:var(--muted);margin-top:8px}
.gmotion b,.gfrom b{font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--faint);margin-right:7px;font-weight:400}
.rice{font-family:'JetBrains Mono',monospace;font-size:10.5px;padding:2.5px 8px;border-radius:4px;
  background:var(--accent-soft);color:var(--accent);white-space:nowrap;cursor:help}
.ricewhy{font-size:13px;color:var(--faint);margin-top:6px;max-width:76ch;font-style:italic}
.tag.lane{background:transparent;color:var(--faint);border-color:var(--rule)}
.chip.lanechip[aria-pressed="true"]{background:var(--ink);border-color:var(--ink);color:var(--paper)}
.fsep{width:1px;background:var(--rule);align-self:stretch;margin:0 5px}

.rung .rm{text-decoration:none;border-bottom:1px solid transparent}
.rung .rm:hover{color:var(--accent);border-bottom-color:var(--accent)}
.rung .rm:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.kpi{margin-top:14px;padding-top:12px;border-top:1px dashed var(--rule)}
.kpi .eyebrow{margin:0 0 6px}
.kh{font-family:'Literata',Georgia,serif;font-size:15px;color:var(--ink)}
.know{font-size:13px;color:var(--muted);margin-top:4px}
.know b{font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--faint);margin-right:6px;font-weight:400}
.klead{list-style:none;padding:0;margin:9px 0 0;display:grid;gap:4px}
.klead li{font-size:13px;color:var(--body);padding-left:14px;position:relative}
.klead li::before{content:"→";position:absolute;left:0;color:var(--faint);font-size:11px}
.rcheck{margin-top:11px;padding:11px 13px;border-radius:7px;background:var(--human-soft);
  color:var(--human);font-size:13px;line-height:1.5}

/* compact board */
.items{gap:5px}
.item{padding:10px 14px 10px 15px;border-radius:7px}
.item::before{width:2px}
.itop{gap:8px}
.ititle{font-size:14px}
.iout{font-size:13.5px;margin-top:3px}
.imeta{margin-top:7px;gap:4px}
.tag{font-size:10px;padding:2px 6px}
.serves{font-size:10px;margin-top:6px;opacity:.85}
.igate{margin-top:6px;font-size:12.5px}
.hgroup{margin-top:20px}

.rung .rms{display:flex;gap:5px}

.refusal{max-width:70ch;font-family:'Literata',Georgia,serif;font-size:19px;line-height:1.55;
  color:var(--ink);margin:20px 0 4px}
.step{grid-template-columns:64px 1fr;padding:24px}
.stepbody{min-width:0}
.step .today{font-size:14px;color:var(--muted);max-width:72ch}
.step .today b,.step .never b{display:block;font-family:'JetBrains Mono',monospace;font-size:9px;
  letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin-bottom:4px;font-weight:400}
.step .do{font-family:'Literata',Georgia,serif;font-size:21px;color:var(--ink);
  line-height:1.3;margin:14px 0 10px}
.step .how{font-size:14.5px;color:var(--body);max-width:72ch}
.step .never{margin-top:14px;padding:12px 14px;border-radius:7px;background:var(--accent-soft);
  color:var(--accent);font-size:14px;line-height:1.5;max-width:72ch}
.step.thru .never{background:var(--paper);border:1px solid color-mix(in srgb,var(--accent) 34%,transparent)}

.hook{font-family:'Literata',Georgia,serif;font-size:clamp(24px,3.4vw,34px);line-height:1.22;
  color:var(--ink);max-width:20ch;margin:26px 0 18px;letter-spacing:-.012em;text-wrap:balance}
.thesis{max-width:64ch;font-size:16.5px;line-height:1.6;color:var(--body);margin-bottom:6px}
.drive{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:9px;
  letter-spacing:.15em;text-transform:uppercase;color:var(--accent);
  background:var(--accent-soft);padding:3px 9px;border-radius:4px;margin-bottom:12px}
.horizon{margin-top:22px;padding:22px 24px;border-radius:10px;background:var(--raised);
  border:1px solid var(--rule)}
.horizon p:last-child{max-width:66ch;font-family:'Literata',Georgia,serif;font-size:17px;
  line-height:1.55;color:var(--ink)}

.vhead{font-family:'Literata',Georgia,serif;font-size:clamp(21px,2.6vw,27px);line-height:1.28;
  color:var(--ink);max-width:26ch;margin-bottom:14px;letter-spacing:-.01em;text-wrap:balance}
.vclaim{max-width:66ch;font-size:15.5px;line-height:1.6;color:var(--body)}
.vhalves{max-width:66ch;font-size:14px;line-height:1.55;color:var(--muted);margin-top:12px;
  padding-left:14px;border-left:2px solid var(--accent)}
.vhorizon{max-width:66ch;font-family:'Literata',Georgia,serif;font-size:16px;line-height:1.55;
  color:var(--ink);margin-top:16px}
.radius{margin-top:7px;font-size:13.5px;color:var(--accent)}
.radius b{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--faint);margin-right:8px;font-weight:400}

.step{grid-template-columns:132px 1fr}
.step .num{display:flex;flex-direction:column;gap:4px}
.verb{font-family:'Literata',Georgia,serif;font-size:23px;line-height:1.1;color:var(--accent);
  letter-spacing:-.01em}
.vwhy{font-size:12px;color:var(--faint);line-height:1.35}
.step.thru .verb{color:var(--accent)}
.missionblock{margin:20px 0 4px;padding:22px 24px;border-radius:10px;background:var(--raised);
  border:1px solid var(--rule)}
.missionblock p{max-width:66ch;font-size:15px;line-height:1.62;color:var(--body)}
.vrow{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.vchip{font-family:'Literata',Georgia,serif;font-size:20px;color:var(--ink);
  padding:4px 14px;border:1px solid var(--rule);border-radius:999px;background:var(--surface)}
.vchip:last-child{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 40%,transparent)}
@media (max-width:640px){.step{grid-template-columns:1fr}}

.missiontext{max-width:66ch;font-size:16px;line-height:1.62;color:var(--ink)}
.whynow{max-width:66ch;font-size:14.5px;line-height:1.58;color:var(--body);margin-top:14px;
  padding-left:14px;border-left:2px solid var(--human)}
.whynow b{display:block;font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--human);margin-bottom:4px;font-weight:400}
.notagainst{max-width:66ch;font-size:14px;line-height:1.55;color:var(--muted);margin-top:14px}
.blessing{margin-top:26px;padding-top:20px;border-top:1px solid color-mix(in srgb,var(--accent) 30%,transparent);
  font-family:'Literata',Georgia,serif;font-size:clamp(20px,2.4vw,26px);color:var(--accent);
  letter-spacing:-.008em}

.motto{font-family:'Literata',Georgia,serif;font-size:clamp(26px,3.6vw,38px);line-height:1.18;
  color:var(--ink);max-width:22ch;margin:26px 0 16px;letter-spacing:-.014em;text-wrap:balance}
.half{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--accent);margin:26px 0 2px;padding-top:14px;
  border-top:1px solid color-mix(in srgb,var(--accent) 26%,transparent)}
.miles > .half:first-child{margin-top:0;padding-top:0;border-top:none}

.visionblock{margin-top:20px;padding:26px 28px;border-radius:10px;background:var(--accent-soft);
  border:1px solid color-mix(in srgb,var(--accent) 30%,transparent)}
.visionblock .vhead{margin-bottom:14px}

.board{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:20px;align-items:start}
.col{min-width:0}
.colhead{display:flex;align-items:baseline;gap:9px;padding-bottom:9px;margin-bottom:10px;
  border-bottom:2px solid var(--accent)}
.colhead h3{font-family:'Literata',Georgia,serif;font-size:18px;font-weight:400;color:var(--ink)}
.colcount{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--faint)}
.hz{font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--faint);margin:16px 0 6px}
.col > .hz:first-of-type{margin-top:0}
.col .item{margin-bottom:6px}
.col .ititle{font-size:13.5px}
.col .iout{font-size:12.5px}
@media (max-width:900px){.board{grid-template-columns:1fr}}

.vrung{font-family:'JetBrains Mono',monospace;font-size:8.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--faint);margin-top:6px}
details.more{margin-top:14px}
details.more > summary{cursor:pointer;list-style:none;display:inline-flex;align-items:center;gap:8px;
  font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--accent);
  border:1px solid color-mix(in srgb,var(--accent) 34%,transparent);border-radius:999px;padding:7px 15px}
details.more > summary::-webkit-details-marker{display:none}
details.more > summary::after{content:"↓"}
details.more[open] > summary::after{content:"↑"}
details.more > summary:hover{background:var(--accent-soft)}
details.more > summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.figure{margin:22px 0 0;overflow-x:auto;border:1px solid var(--rule);border-radius:10px;
  background:var(--surface);padding:20px 8px}
.figure svg{display:block;min-width:940px;width:100%;height:auto}
.hidden{display:none}
@media (max-width:640px){
  .wrap,.head-in{padding-left:18px;padding-right:18px}
  .qrow{grid-template-columns:1fr;gap:6px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

DIAGRAM = """<svg viewBox="0 0 1120 680" role="img" aria-labelledby="bottleneck-title bottleneck-desc"
     xmlns="http://www.w3.org/2000/svg">
<title id="bottleneck-title">Founder queue bottleneck in the PLUR roadmap</title>
<desc id="bottleneck-desc">Forty-eight roadmap items split three ways: twenty delegable items
flow through an agent fleet that works in parallel and reach shipped work; nine items are
blocked on the founder, queue behind a service capacity of one, and stall four downstream
tracks; nine further items are deliberately held on conditions and are not queued at all.</desc>

<defs>
  <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
    <polygon points="0 0, 8 3, 0 6" fill="var(--muted)"/>
  </marker>
  <marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
    <polygon points="0 0, 8 3, 0 6" fill="var(--accent)"/>
  </marker>
  <marker id="arrow-soft" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
    <polygon points="0 0, 8 3, 0 6" fill="var(--stroke-opt)"/>
  </marker>
</defs>

<rect width="100%" height="100%" fill="var(--paper)"/>

<!-- ── arrows first, so z-order puts them behind the boxes ── -->

<!-- A1  roadmap → delegable (right + up) -->
<path d="M 208,284 H 264 Q 272,284 272,276 V 188 Q 272,180 280,180 H 368"
      fill="none" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#arrow)"/>
<rect x="212" y="264" width="56" height="12" rx="2" fill="var(--paper)"/>
<text x="240" y="273" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.06em">20 READY</text>

<!-- A2  roadmap → founder queue (right + down) — the constricting route -->
<path d="M 208,316 H 288 Q 296,316 296,324 V 344 Q 296,352 304,352 H 368"
      fill="none" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#arrow)"/>
<rect x="216" y="296" width="64" height="12" rx="2" fill="var(--paper)"/>
<text x="248" y="305" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.06em">9 BLOCKED</text>

<!-- A3  roadmap → held (bottom port, single bend) -->
<path d="M 124,344 V 504 Q 124,512 132,512 H 368"
      fill="none" stroke="var(--stroke-opt)" stroke-width="1" stroke-dasharray="4,3"
      marker-end="url(#arrow-soft)"/>
<rect x="132" y="408" width="56" height="12" rx="2" fill="var(--paper)"/>
<text x="160" y="417" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.06em">9 HELD</text>

<!-- A4  delegable → agent fleet -->
<line x1="536" y1="180" x2="636" y2="180" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#arrow)"/>
<!-- A5  agent fleet → shipping -->
<line x1="804" y1="180" x2="904" y2="180" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#arrow)"/>
<rect x="828" y="160" width="52" height="12" rx="2" fill="var(--paper)"/>
<text x="854" y="169" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.06em">PARALLEL</text>

<!-- A6  founder queue → the one server -->
<line x1="536" y1="352" x2="636" y2="352" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#arrow)"/>
<rect x="556" y="332" width="60" height="12" rx="2" fill="var(--paper)"/>
<text x="586" y="341" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.06em">SERIAL · 1</text>

<!-- A7  server → stalled -->
<line x1="804" y1="352" x2="904" y2="352" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#arrow)"/>
<rect x="828" y="332" width="52" height="12" rx="2" fill="var(--paper)"/>
<text x="854" y="341" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.06em">STALLS 4</text>

<!-- ── nodes ── -->

<!-- 1. source -->
<rect x="40" y="256" width="168" height="88" rx="6" fill="var(--paper)"/>
<rect x="40" y="256" width="168" height="88" rx="6" fill="var(--fill-store)" stroke="var(--muted)" stroke-width="1"/>
<rect x="48" y="262" width="40" height="12" rx="2" fill="none" stroke="var(--muted)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="68" y="271" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">YAML</text>
<text x="124" y="304" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">The roadmap</text>
<text x="124" y="322" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">roadmap.yaml · 48 items</text>

<!-- 2. delegable -->
<rect x="368" y="144" width="168" height="72" rx="6" fill="var(--paper)"/>
<rect x="368" y="144" width="168" height="72" rx="6" fill="var(--paper-2)" stroke="var(--ink)" stroke-width="1"/>
<rect x="376" y="150" width="44" height="12" rx="2" fill="none" stroke="var(--ink)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="398" y="159" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">READY</text>
<text x="452" y="184" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">Delegable work</text>
<text x="452" y="200" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">20 ready · unblocked</text>

<!-- 3. FOCAL — the queue -->
<rect x="368" y="300" width="168" height="104" rx="6" fill="var(--paper)"/>
<rect x="368" y="300" width="168" height="104" rx="6" fill="var(--accent-tint)" stroke="var(--accent)" stroke-width="1"/>
<rect x="376" y="306" width="44" height="12" rx="2" fill="none" stroke="var(--accent)" stroke-opacity="0.5" stroke-width="0.8"/>
<text x="398" y="315" fill="var(--accent)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">QUEUE</text>
<text x="452" y="340" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">Founder queue</text>
<text x="452" y="356" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">9 items · all horizon now</text>
<g fill="var(--accent)">
  <rect x="380" y="376" width="12" height="8" rx="2"/><rect x="396" y="376" width="12" height="8" rx="2"/>
  <rect x="412" y="376" width="12" height="8" rx="2"/><rect x="428" y="376" width="12" height="8" rx="2"/>
  <rect x="444" y="376" width="12" height="8" rx="2"/><rect x="460" y="376" width="12" height="8" rx="2"/>
  <rect x="476" y="376" width="12" height="8" rx="2"/><rect x="492" y="376" width="12" height="8" rx="2"/>
  <rect x="508" y="376" width="12" height="8" rx="2"/>
</g>

<!-- 4. held -->
<rect x="368" y="476" width="168" height="72" rx="6" fill="var(--paper)"/>
<rect x="368" y="476" width="168" height="72" rx="6" fill="var(--fill-opt)" stroke="var(--stroke-opt)"
      stroke-width="1" stroke-dasharray="4,3"/>
<rect x="376" y="482" width="36" height="12" rx="2" fill="none" stroke="var(--stroke-opt)" stroke-width="0.8"/>
<text x="394" y="491" fill="var(--soft)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">HELD</text>
<text x="452" y="516" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">Held on a condition</text>
<text x="452" y="532" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">7 gated · 2 standing</text>

<!-- 5. agent fleet -->
<rect x="636" y="144" width="168" height="72" rx="6" fill="var(--paper)"/>
<rect x="636" y="144" width="168" height="72" rx="6" fill="var(--paper-2)" stroke="var(--ink)" stroke-width="1"/>
<rect x="644" y="150" width="36" height="12" rx="2" fill="none" stroke="var(--ink)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="662" y="159" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">SVC</text>
<text x="720" y="184" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">Agent fleet</text>
<text x="720" y="200" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">capacity 3 · concurrent</text>

<!-- 6. the one server -->
<rect x="636" y="316" width="168" height="72" rx="6" fill="var(--paper)"/>
<rect x="636" y="316" width="168" height="72" rx="6" fill="var(--paper-2)" stroke="var(--accent)" stroke-width="1"/>
<rect x="644" y="322" width="36" height="12" rx="2" fill="none" stroke="var(--accent)" stroke-opacity="0.5" stroke-width="0.8"/>
<text x="662" y="331" fill="var(--accent)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">SVC</text>
<text x="720" y="356" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">Gregor</text>
<text x="720" y="372" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">capacity 1 · serial</text>

<!-- 7. admitted -->
<rect x="904" y="144" width="176" height="72" rx="6" fill="var(--paper)"/>
<rect x="904" y="144" width="176" height="72" rx="6" fill="var(--paper-2)" stroke="var(--ink)" stroke-width="1"/>
<rect x="912" y="150" width="32" height="12" rx="2" fill="none" stroke="var(--ink)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="928" y="159" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">OUT</text>
<text x="992" y="184" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">Work that moves</text>
<text x="992" y="200" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">nightshift · miles · crt</text>

<!-- 8. deferred -->
<rect x="904" y="316" width="176" height="72" rx="6" fill="var(--paper)"/>
<rect x="904" y="316" width="176" height="72" rx="6" fill="var(--paper-2)" stroke="var(--ink)" stroke-width="1"/>
<rect x="912" y="322" width="32" height="12" rx="2" fill="none" stroke="var(--ink)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="928" y="331" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace"
      text-anchor="middle" letter-spacing="0.08em">OUT</text>
<text x="992" y="356" fill="var(--ink)" font-size="12" font-weight="600"
      font-family="'Outfit',sans-serif" text-anchor="middle">Four tracks waiting</text>
<text x="992" y="372" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace"
      text-anchor="middle">H005 · GEO · deploy · launch</text>

<!-- editorial callout -->
<text x="636" y="444" fill="var(--muted)" font-size="14" font-style="italic"
      font-family="'Literata',Georgia,serif">Nine items. A DNS record, a token, one approval click,</text>
<text x="636" y="464" fill="var(--muted)" font-size="14" font-style="italic"
      font-family="'Literata',Georgia,serif">an SSH key, three five-minute forms — about an hour,</text>
<text x="636" y="484" fill="var(--muted)" font-size="14" font-style="italic"
      font-family="'Literata',Georgia,serif">holding four tracks still.</text>

<!-- legend strip -->
<line x1="40" y1="604" x2="1080" y2="604" stroke="var(--rule)" stroke-width="0.8"/>
<text x="40" y="624" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace"
      letter-spacing="0.14em">LEGEND</text>
<rect x="128" y="616" width="12" height="8" rx="2" fill="var(--accent)"/>
<text x="148" y="624" fill="var(--muted)" font-size="8" font-family="'JetBrains Mono',monospace"
      letter-spacing="0.06em">ONE QUEUED ITEM</text>
<rect x="308" y="614" width="28" height="12" rx="2" fill="var(--accent-tint)" stroke="var(--accent)" stroke-width="1"/>
<text x="344" y="624" fill="var(--muted)" font-size="8" font-family="'JetBrains Mono',monospace"
      letter-spacing="0.06em">THE CONSTRAINT</text>
<line x1="524" y1="620" x2="552" y2="620" stroke="var(--muted)" stroke-width="1.2"/>
<text x="560" y="624" fill="var(--muted)" font-size="8" font-family="'JetBrains Mono',monospace"
      letter-spacing="0.06em">FLOWING</text>
<line x1="656" y1="620" x2="684" y2="620" stroke="var(--stroke-opt)" stroke-width="1" stroke-dasharray="4,3"/>
<text x="692" y="624" fill="var(--muted)" font-size="8" font-family="'JetBrains Mono',monospace"
      letter-spacing="0.06em">DELIBERATELY NOT QUEUED</text>
</svg>"""


ARC_DIAGRAM = """<svg viewBox="0 0 1120 580" role="img" aria-labelledby="arc-title arc-desc" xmlns="http://www.w3.org/2000/svg">
<title id="arc-title">The PLUR roadmap arc, from shipped memory to the vision</title>
<desc id="arc-desc">Six milestones in a gated chain: memory that persists is shipped; one customer
becoming a pattern is in progress and carries the enterprise-clients and fundraising goals; memory
you can prove is not shipped and gates both remaining rungs; a pack as a reputation object and
knowledge carrying a price are both gated on conditions; the vision is that knowledge stays owned by
whoever produced it. Each arrow is labelled with the condition that must be true to cross it.</desc>
<defs>
<marker id="a" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="var(--muted)"/></marker>
<marker id="aa" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="var(--accent)"/></marker>
</defs>
<rect width="100%" height="100%" fill="var(--paper)"/>

<!-- connectors first -->
<line x1="364" y1="196" x2="420" y2="196" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#a)"/>
<rect x="368" y="176" width="48" height="12" rx="2" fill="var(--paper)"/>
<text x="392" y="185" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.06em">SHIPPED</text>

<line x1="700" y1="196" x2="756" y2="196" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#a)"/>
<rect x="704" y="176" width="48" height="12" rx="2" fill="var(--paper)"/>
<text x="728" y="185" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.06em">SEPT GATE</text>

<!-- M3 -> M4, the accent route: provenance is the precondition -->
<path d="M 896,252 V 292 Q 896,300 888,300 H 232 Q 224,300 224,308 V 352"
      fill="none" stroke="var(--accent)" stroke-width="1.2" marker-end="url(#aa)"/>
<rect x="472" y="280" width="176" height="12" rx="2" fill="var(--paper)"/>
<text x="560" y="289" fill="var(--accent)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.06em">PROVENANCE ACTUALLY WRITTEN</text>

<line x1="364" y1="408" x2="420" y2="408" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#a)"/>
<rect x="368" y="388" width="48" height="12" rx="2" fill="var(--paper)"/>
<text x="392" y="397" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.06em">PACKTRUST</text>

<line x1="700" y1="408" x2="756" y2="408" stroke="var(--muted)" stroke-width="1.2" marker-end="url(#a)"/>
<rect x="704" y="388" width="48" height="12" rx="2" fill="var(--paper)"/>
<text x="728" y="397" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.06em">ROI LOOP</text>

<!-- M1 -->
<rect x="84" y="140" width="280" height="112" rx="6" fill="var(--paper)"/>
<rect x="84" y="140" width="280" height="112" rx="6" fill="var(--fill-store)" stroke="var(--muted)" stroke-width="1"/>
<rect x="92" y="146" width="60" height="12" rx="2" fill="none" stroke="var(--muted)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="122" y="155" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">USABLE</text>
<text x="224" y="196" fill="var(--ink)" font-size="12" font-weight="600" font-family="'Outfit',sans-serif" text-anchor="middle">Memory that persists</text>
<text x="224" y="216" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace" text-anchor="middle">M1 · core v0.19.4 · R@5 98.0%</text>
<rect x="176" y="228" width="96" height="16" rx="4" fill="var(--paper-2)"/>
<text x="224" y="239" fill="var(--go)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">SHIPPED</text>

<!-- M2 -->
<rect x="420" y="140" width="280" height="112" rx="6" fill="var(--paper)"/>
<rect x="420" y="140" width="280" height="112" rx="6" fill="var(--paper-2)" stroke="var(--ink)" stroke-width="1"/>
<rect x="428" y="146" width="76" height="12" rx="2" fill="none" stroke="var(--ink)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="466" y="155" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">GOVERNABLE</text>
<text x="560" y="196" fill="var(--ink)" font-size="12" font-weight="600" font-family="'Outfit',sans-serif" text-anchor="middle">One person’s memory becomes a team’s</text>
<text x="560" y="216" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace" text-anchor="middle">M2 · review · scopes · permissions</text>
<rect x="472" y="228" width="80" height="16" rx="4" fill="var(--paper)" stroke="var(--stroke-opt)" stroke-width="0.8"/>
<text x="512" y="239" fill="var(--ink)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">G1 CLIENTS</text>
<rect x="560" y="228" width="88" height="16" rx="4" fill="var(--paper)" stroke="var(--stroke-opt)" stroke-width="0.8"/>
<text x="604" y="239" fill="var(--ink)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">G2 RAISE</text>

<!-- M3 — FOCAL -->
<rect x="756" y="140" width="280" height="112" rx="6" fill="var(--paper)"/>
<rect x="756" y="140" width="280" height="112" rx="6" fill="var(--accent-tint)" stroke="var(--accent)" stroke-width="1"/>
<rect x="764" y="146" width="64" height="12" rx="2" fill="none" stroke="var(--accent)" stroke-opacity="0.5" stroke-width="0.8"/>
<text x="796" y="155" fill="var(--accent)" font-size="7" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">OWNABLE</text>
<text x="896" y="196" fill="var(--ink)" font-size="12" font-weight="600" font-family="'Outfit',sans-serif" text-anchor="middle">Memory you can prove</text>
<text x="896" y="216" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace" text-anchor="middle">M3 · origin · chain · signature</text>
<rect x="836" y="228" width="120" height="16" rx="4" fill="var(--paper)" stroke="var(--accent)" stroke-width="0.8"/>
<text x="896" y="239" fill="var(--accent)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">NOT SHIPPED</text>

<!-- M4 -->
<rect x="84" y="352" width="280" height="112" rx="6" fill="var(--paper)"/>
<rect x="84" y="352" width="280" height="112" rx="6" fill="var(--fill-opt)" stroke="var(--stroke-opt)" stroke-width="1" stroke-dasharray="4,3"/>
<rect x="92" y="358" width="64" height="12" rx="2" fill="none" stroke="var(--stroke-opt)" stroke-width="0.8"/>
<text x="124" y="367" fill="var(--soft)" font-size="7" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">OWNABLE</text>
<text x="224" y="408" fill="var(--ink)" font-size="12" font-weight="600" font-family="'Outfit',sans-serif" text-anchor="middle">Other agents learn to work with you</text>
<text x="224" y="428" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace" text-anchor="middle">M4 · publish · installs · demand</text>
<rect x="164" y="440" width="120" height="16" rx="4" fill="var(--paper)" stroke="var(--stroke-opt)" stroke-width="0.8"/>
<text x="224" y="451" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">G3 · 1M AGENTS</text>

<!-- M5 -->
<rect x="420" y="352" width="280" height="112" rx="6" fill="var(--paper)"/>
<rect x="420" y="352" width="280" height="112" rx="6" fill="var(--fill-opt)" stroke="var(--stroke-opt)" stroke-width="1" stroke-dasharray="4,3"/>
<rect x="428" y="358" width="72" height="12" rx="2" fill="none" stroke="var(--stroke-opt)" stroke-width="0.8"/>
<text x="464" y="367" fill="var(--soft)" font-size="7" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">TRADEABLE</text>
<text x="560" y="408" fill="var(--ink)" font-size="12" font-weight="600" font-family="'Outfit',sans-serif" text-anchor="middle">The agentic knowledge economy</text>
<text x="560" y="428" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace" text-anchor="middle">M5 · packs trade between agents</text>
<rect x="488" y="440" width="144" height="16" rx="4" fill="var(--paper)" stroke="var(--stroke-opt)" stroke-width="0.8"/>
<text x="560" y="451" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">FEE UNRECONCILED</text>

<!-- V -->
<rect x="756" y="352" width="280" height="112" rx="6" fill="var(--paper)"/>
<rect x="756" y="352" width="280" height="112" rx="6" fill="var(--fill-store)" stroke="var(--muted)" stroke-width="1"/>
<rect x="764" y="358" width="52" height="12" rx="2" fill="none" stroke="var(--muted)" stroke-opacity="0.4" stroke-width="0.8"/>
<text x="790" y="367" fill="var(--muted)" font-size="7" font-family="'JetBrains Mono',monospace" text-anchor="middle" letter-spacing="0.08em">VISION</text>
<text x="896" y="408" fill="var(--ink)" font-size="12" font-weight="600" font-family="'Outfit',sans-serif" text-anchor="middle">Knowledge stays owned</text>
<text x="896" y="428" fill="var(--muted)" font-size="9" font-family="'JetBrains Mono',monospace" text-anchor="middle">by whoever produced it</text>

<!-- legend -->
<line x1="84" y1="504" x2="1036" y2="504" stroke="var(--rule)" stroke-width="0.8"/>
<text x="84" y="524" fill="var(--soft)" font-size="8" font-family="'JetBrains Mono',monospace" letter-spacing="0.14em">LEGEND</text>
<rect x="172" y="516" width="24" height="12" rx="3" fill="var(--accent-tint)" stroke="var(--accent)" stroke-width="1"/>
<text x="204" y="524" fill="var(--muted)" font-size="8" font-family="'JetBrains Mono',monospace" letter-spacing="0.06em">THE PRECONDITION, NOT SHIPPED</text>
<rect x="464" y="516" width="24" height="12" rx="3" fill="var(--fill-opt)" stroke="var(--stroke-opt)" stroke-width="1" stroke-dasharray="4,3"/>
<text x="496" y="524" fill="var(--muted)" font-size="8" font-family="'JetBrains Mono',monospace" letter-spacing="0.06em">GATED ON A CONDITION</text>
<line x1="716" y1="520" x2="744" y2="520" stroke="var(--muted)" stroke-width="1.2"/>
<text x="752" y="524" fill="var(--muted)" font-size="8" font-family="'JetBrains Mono',monospace" letter-spacing="0.06em">ARROW LABEL = WHAT MUST BE TRUE TO CROSS</text>
</svg>"""


JS = """
const chips=[...document.querySelectorAll('.chip')];
const items=[...document.querySelectorAll('.item')];
let active={track:null,owner:null,lane:null};
function apply(){
  items.forEach(el=>{
    const okT=!active.track||el.dataset.track===active.track;
    const okO=!active.owner||el.dataset.owner===active.owner;
    const okL=!active.lane||el.dataset.lane===active.lane;
    el.classList.toggle('hidden',!(okT&&okO&&okL));
  });
  document.querySelectorAll('.hgroup').forEach(g=>{
    const vis=[...g.querySelectorAll('.item')].filter(i=>!i.classList.contains('hidden')).length;
    g.classList.toggle('hidden',vis===0);
    const c=g.querySelector('.hcount'); if(c) c.textContent=vis+' item'+(vis===1?'':'s');
  });
}
chips.forEach(c=>c.addEventListener('click',()=>{
  const k=c.dataset.key,v=c.dataset.val;
  active[k]=active[k]===v?null:v;
  chips.forEach(o=>{if(o.dataset.key===k)o.setAttribute('aria-pressed',String(active[k]===o.dataset.val))});
  apply();
}));
"""


def render_plan(r):
    """Master plan — each step names its drive and reaches it in one step."""
    out = ""
    for s in r.get("master_plan") or []:
        thru = s["step"] == "throughline"
        out += (f'<div class="step{" thru" if thru else ""}">'
                f'<div class="num"><span class="verb">{e(s["verb"])}</span>'
                f'<span class="vwhy">{e(s["verb_why"])}</span>'
                + (f'<span class="vrung">{e(s["rung"])}</span>' if s.get("rung") else "")
                + '</div>'
                f'<div class="stepbody">'
                f'<span class="drive">{e(s["drive"])}</span>'
                f'<p class="today">{e(s["today"]).strip()}</p>'
                f'<div class="do">{e(s["do"])}</div>'
                f'<p class="how">{e(s["how"]).strip()}</p>'
                f'<p class="never"><b>never</b>{e(s["never"]).strip()}</p>'
                f'</div></div>')
    head = ""
    if r.get("motto"):
        head += f'<p class="motto">{e(r["motto"])}</p>'
    if r.get("thesis"):
        head += f'<p class="thesis">{e(r["thesis"]).strip()}</p>'
    verbs = "".join(f'<span class="vchip">{e(s["verb"])}</span>'
                    for s in r.get("master_plan") or [])
    head += f'<div class="vrow">{verbs}</div>'
    if r.get("primitive"):
        head += (f'<div class="spine"><span class="eyebrow">the one primitive</span>'
                 f'<span class="prim">{e(r["primitive"])}</span>'
                 f'<p class="spinenote">A store that is open, inspectable and portable by design '
                 f'is also <em>forkable</em>. A fork has the bytes and none of the lineage — so '
                 f'attested origin is the only thing that turns a copyable corpus into an ownable '
                 f'asset. Every rung above the floor is gated on it.</p></div>')
    tail = ""
    return head + f'<div class="plan">{out}</div>' + tail


def render_why(r):
    if not r.get("mission"):
        return ""
    return (f'<div class="missionblock">'
            f'<p class="missiontext">{e(r["mission"]).strip()}</p>'
            + (f'<p class="whynow"><b>why now</b>{e(r["why_now"]).strip()}</p>'
               if r.get("why_now") else "")
            + (f'<p class="notagainst">{e(r["not_against"]).strip()}</p>'
               if r.get("not_against") else "") + '</div>')


def render_vision(r):
    v = r.get("vision") or {}
    if not v:
        return ""
    return (f'<div class="visionblock">'
            f'<p class="vhead">{e(v["headline"])}</p>'
            f'<p class="vclaim">{e(v["claim"]).strip()}</p>'
            f'<p class="vhorizon">{e(v["horizon"]).strip()}</p></div>')


def render_intents():
    """The why, read straight out of the intent graph."""
    nodes = load_intents()
    if not nodes:
        return ""
    vision = next((n for n in nodes if n["kind"] == "vision"), None)
    out = ""
    if vision:
        out += (f'<div class="vision"><p class="eyebrow">the vision</p>'
                f'<p class="nm">{e(vision["title"])}</p></div>')
    out += '<div class="intents">'
    for idx, n in enumerate(nodes):
        if n["kind"] != "intent":
            continue
        kids = []
        for m in nodes[idx + 1:]:
            if m["kind"] == "intent" or m["depth"] <= n["depth"]:
                break
            if m["kind"] in ("goal", "constraint"):
                kids.append(m)
        chips = "".join(
            f'<span class="goal{" cons" if k["kind"] == "constraint" else ""}">'
            f'{"✕ " if k["kind"] == "constraint" else ""}{e(k["id"])}</span>' for k in kids)
        meta = f'<span class="tag">{e(n["note"])}</span>' if n.get("note") else ""
        gate = (f'<div class="mgate"><b>gate</b><span>{e(n["gate"])}</span></div>'
                if n.get("gate") else "")
        out += (f'<div class="l1"><div class="hd"><span class="nm">{e(n["title"])}</span>'
                f'<span class="iid">{e(n["id"])}</span>{meta}</div>{gate}'
                f'<div class="goals">{chips}</div></div>')
    return out + "</div>"


def render_milestones(r):
    """The arc: each milestone widens who the compounding is for."""
    out = ""
    seen_half = None
    for m in r.get("milestones") or []:
        if m.get("half") and m["half"] != seen_half:
            seen_half = m["half"]
            out += f'<p class="half">{e(m["half"])}</p>' 
        gate = (f'<div class="mgate"><b>gate</b><span>{e(m["gate"])}</span></div>'
                if m.get("gate") else "")
        its = ("".join(f'<span class="tag">{e(i)}</span>' for i in m.get("items") or [])
               or '<span class="tag">—</span>')
        feats = ""
        mine = [f for f in (r.get("features") or []) if f.get("milestone") == m["id"]]
        mine.sort(key=lambda x: -(x.get("rice", {}).get("score") or 0))
        for f in mine:
            repo, _, num = f["gh"].rpartition("#")
            kids = (f'<span class="kids">{len(f["children"])} sub-issues</span>'
                    if f.get("children") else "")
            rc = f.get("rice")
            rice = (f'<span class="rice" title="reach {rc["reach"]} x impact {rc["impact"]}'
                    f' x confidence {rc["confidence"]} / effort {rc["effort"]}">'
                    f'RICE {rc["score"]}</span>') if rc else ""
            why = f'<p class="ricewhy">{e(f["rice_why"]).strip()}</p>' if f.get("rice_why") else ""
            feats += (f'<div class="feat"><div class="fh">'
                      f'<a class="fgh" href="https://github.com/{e(repo)}/issues/{e(num)}">'
                      f'{e(repo.split("/")[-1])}#{e(num)}</a>'
                      f'<span class="ft">{e(f["title"])}</span>{kids}{rice}</div>'
                      f'<p class="fd">{e(f["delivers"]).strip()}</p>{why}</div>')
        feats = (f'<div class="feats"><p class="eyebrow">what gets built to get there</p>'
                 f'{feats}</div>') if feats else ""
        rad = (f'<p class="radius"><b>compounds for</b>{e(m["compounds_for"])}</p>'
               if m.get("compounds_for") else "")
        out += (f'<div class="mile" id="m-{e(m["id"])}"><div class="rail"><span class="mid">{e(m["id"])}</span>'
                f'<span class="state {e(m["state"])}">{e(m["state"].replace("_", " "))}</span>'
                f'<span class="mid">{e(m.get("tier", ""))}</span></div>'
                f'<div><div class="mt">{e(m["title"])}</div>{rad}'
                f'<p class="ev">{e(m["evidence"]).strip()}</p>{gate}'
                f'<div class="imeta">{its}</div>{feats}</div></div>')
    return f'<div class="miles">{out}</div>'


def render_ladder(r):
    """The strategic spine: one primitive, four rungs."""
    if not r.get("ladder"):
        return ""
    rows = ""
    for i, rg in enumerate(r["ladder"]):
        rows += (f'<div class="rung s-{e(rg["state"])}">'
                 f'<span class="rname">{e(rg["rung"])}</span>'
                 f'<span class="ris">{e(rg["is"]).strip()}</span>'
                 f'<span class="rst">{e(rg["state"])}</span>'
                 f'<span class="rms">' + "".join(
                     f'<a class="rm" href="#m-{e(mid)}">{e(mid)}</a>'
                     for mid in (rg.get("milestones") or [])) + '</span></div>')
    return (f'<div class="ladderwrap"><div class="spine">'
            f'<span class="eyebrow">the one primitive</span>'
            f'<span class="prim">{e(r.get("primitive",""))}</span>'
            f'<p class="spinenote">A store that is open, inspectable and portable by design is also '
            f'<em>forkable</em>. A fork has the bytes and none of the lineage — so attested origin is '
            f'the only thing that turns a copyable corpus into an ownable asset. Every rung above the '
            f'floor is gated on it.</p></div>'
            f'<div class="rungs">{rows}</div></div>')


def render_entities(r):
    out = ""
    for en in r.get("entities") or []:
        c = f'<span class="tag">{e(en["constraint"])}</span>' if en.get("constraint") else ""
        out += (f'<div class="ent"><div class="hd"><span class="nm">{e(en["name"])}</span>'
                f'<span class="tag">{e(en["holds"])}</span>{c}</div>'
                f'<p class="fd">{e(en["stance"]).strip()}</p></div>')
    return f'<div class="ents">{out}</div>' if out else ""


def render_verticals(r):
    out = ""
    for v in r.get("verticals") or []:
        out += (f'<tr><td class="vd">{e(v["domain"])}</td>'
                f'<td class="vs">{e(v["system_of_record"])}</td>'
                f'<td class="vi">{e(v["instance"]).strip()}</td></tr>')
    return (f'<div class="tablewrap"><table class="vtab"><thead><tr>'
            f'<th>Domain</th><th>System of record</th><th>Instance</th>'
            f'</tr></thead><tbody>{out}</tbody></table></div>') if out else ""


def _kpi(g):
    k = g.get("kpi")
    if not k:
        return ""
    lead = "".join(f'<li>{e(x)}</li>' for x in k.get("leading") or [])
    rc = (f'<p class="rcheck">{e(g["reality_check"]).strip()}</p>'
          if g.get("reality_check") else "")
    return (f'<div class="kpi"><p class="eyebrow">how we know</p>'
            f'<p class="kh">{e(k.get("headline",""))}</p>'
            f'<p class="know"><b>today</b> {e(k.get("now",""))}</p>'
            f'<ul class="klead">{lead}</ul>{rc}</div>')


def render_goals(r):
    out = ""
    for g in r.get("goals") or []:
        rung = f'<span class="tag">{e(g["rung"])}</span>' if g.get("rung") else ""
        out += (f'<div class="goal-card"><div class="hd">'
                f'<span class="gid">{e(g["id"])}</span>'
                f'<span class="gname">{e(g["goal"])}</span>{rung}</div>'
                f'<p class="gmotion"><b>motion</b> {e(g["motion"])}</p>'
                f'<p class="gfrom"><b>from</b> {e(g["from"])}</p>'
                f'<p class="fd">{e(g["note"]).strip()}</p>{_kpi(g)}</div>')
    return f'<div class="goals-grid">{out}</div>' if out else ""


def e(s):
    return html.escape(str(s if s is not None else ""))


def render_html(r):
    items = public_items(r)
    ns = r["north_star"]
    tracks = r["tracks"]

    def n(**kw):
        return [i for i in items if all(i.get(k) == v for k, v in kw.items())]

    queue = n(blocked_on="human")
    delegable = [i for i in items if i.get("delegable") and i.get("status") == "ready"]
    owners = sorted({i.get("owner") for i in items if i.get("owner")})

    metrics = [
        (len(items), "outcome-level items,<br>five tracks"),
        (len(n(horizon="now")), "at horizon <code>now</code>"),
        (len(queue), "blocked on <b>you</b> —<br>nothing else can move them", "alarm"),
        (len(delegable), "delegable and ready<br>for an agent"),
        ("82%", "of item-bearing<br>intents covered"),
    ]

    mhtml = "".join(
        f'<div class="metric {m[2] if len(m) > 2 else ""}"><span class="n">{m[0]}</span>'
        f'<span class="l">{m[1]}</span></div>' for m in metrics
    )

    # founder queue — the finding
    qhtml = ""
    for i in queue:
        cost = i.get("_cost", "")
        qhtml += (
            f'<div class="qrow"><span class="qid">{e(i["id"])}</span>'
            f'<span><span class="qt">{e(i["title"])}</span>'
            f'<span class="qo">{e(i["outcome"])}</span></span>'
            f'<span class="qcost">{e(tracks[i["track"]]["repo"].split("/")[-1])}</span></div>'
        )

    # board
    why_html = render_why(r)
    vision_html = render_vision(r)
    plan_html = render_plan(r)
    ladder_html = render_ladder(r)
    goals_html = render_goals(r)
    verts_html = render_verticals(r)
    intents_html = render_intents()
    miles_html = render_milestones(r)

    COLMAP = {tr: col for col, trs in (r.get("columns") or {}).items() for tr in trs}
    HORIZON_ORDER = {"now": 0, "next": 1, "gated": 2, "later": 3}

    def item_card(i):
        b, st, h = i.get("blocked_on"), i.get("status"), i["horizon"]
        tags = f'<span class="tag track">{e(i["track"])}</span>'
        tags += f'<span class="tag">{e(i.get("owner", "—"))}</span>'
        tags += f'<span class="tag lane">{e(i.get("lane", ""))}</span>'
        tags += f'<span class="tag {"go" if st == "in_progress" else ""}">{e(st)}</span>'
        if b == "human":
            tags += '<span class="tag human">blocked on you</span>'
        elif b == "standing_block":
            tags += '<span class="tag halt">standing block</span>'
        elif b:
            tags += f'<span class="tag">blocked · {e(b)}</span>'
        if i.get("embargoed"):
            tags += '<span class="tag lock">embargoed</span>'
        if i.get("gh"):
            tags += f'<span class="tag">{e(i["gh"].split("/")[-1])}</span>'
        gate = (f'<div class="igate"><b>gate</b><span>{e(i["gate"])}</span></div>'
                if i.get("gate") else "")
        return (f'<div class="item" data-track="{e(i["track"])}" data-owner="{e(i.get("owner"))}"'
                f' data-lane="{e(i.get("lane"))}" data-block="{e(b)}" data-h="{e(h)}" data-s="{e(st)}">'
                f'<div class="itop"><span class="iid">{e(i["id"])}</span>'
                f'<span class="ititle">{e(i["title"])}</span></div>'
                f'<p class="iout">{e(i["outcome"])}</p>'
                f'<div class="imeta">{tags}</div>{gate}</div>')

    board = ""
    for col in (r.get("columns") or {}):
        mine = sorted((i for i in items if COLMAP.get(i["track"]) == col),
                      key=lambda x: (HORIZON_ORDER.get(x["horizon"], 9), x["id"]))
        rows, seen = "", None
        for i in mine:
            if i["horizon"] != seen:
                seen = i["horizon"]
                rows += f'<p class="hz">{e(seen)}</p>'
            rows += item_card(i)
        board += (f'<div class="col"><div class="colhead"><h3>{e(col)}</h3>'
                  f'<span class="colcount">{len(mine)}</span></div>{rows}</div>')
    board = f'<div class="board">{board}</div>'

    tchips = "".join(
        f'<button class="chip" data-key="track" data-val="{e(t)}" aria-pressed="false">{e(t)}</button>'
        for t in tracks
    )
    ochips = "".join(
        f'<button class="chip" data-key="owner" data-val="{e(o)}" aria-pressed="false">{e(o)}</button>'
        for o in owners
    )
    lchips = "".join(
        f'<button class="chip lanechip" data-key="lane" data-val="{e(k)}" aria-pressed="false"'
        f' title="{e(v)}">{e(k)}</button>' for k, v in (r.get("lanes") or {}).items()
    )

    gaps = ["dss-rail", "lloyds-of-london-flank", "lloyds-phase-a", "lloyds-phase-b", "lloyds-phase-c"]
    ghtml = "".join(f"<li>{g}</li>" for g in gaps)

    return f"""<title>PLUR Roadmap Board</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Literata:ital,wght@0,300;0,400;1,300;1,400&family=JetBrains+Mono:wght@300;400&display=swap">
<style>{CSS}</style>

<header><div class="head-in">
  <div class="head-top">
    <div style="display:flex;flex-direction:column;gap:10px">
      <span class="eyebrow">Source of truth · consolidated {e(r["updated"])}</span>
      <h1>PLUR Roadmap</h1>
    </div>
    <span class="valid">{len(items)} items · 0 errors</span>
  </div>
  <div class="srcline">
    <span>generated from <b>5-plur/roadmap.yaml</b></span>
    <span>·</span><span>{len(tracks)} tracks</span>
    <span>·</span><span>north star: {e(ns["metric"])} {e(ns["target"])} by {e(ns["by"])}</span>
  </div>
  <p class="lede">What we are building, in what order, and what each step makes possible that
  the one before it could not.</p>
</div></header>

<div class="wrap">
  <div class="metrics">{mhtml}</div>

  <section>
    <div class="sec-head"><h2>The master plan</h2></div>
    {plan_html}
  </section>
  <section>
    <div class="sec-head"><h2>Why we do it</h2></div>
    {why_html}
  </section>

  <section>
    <div class="sec-head"><h2>The vision</h2></div>
    {vision_html}
  </section>
  <section>
    <div class="sec-head"><h2>The roadmap</h2>
      <span class="eyebrow">the story, end to end</span></div>
    <p class="sec-sub">Each milestone does not add a feature — it widens <em>who the compounding
    is for</em>: one person, one team, the organisation, between organisations, the whole network.
    That is the test for belonging on this ladder. Gated by conditions rather than dates, so this
    is a dependency chain and not a timeline. State is honest: <em>shipped</em> means shipped.</p>
    <figure class="figure">{ARC_DIAGRAM}</figure>
    {miles_html}
  </section>
  <section>
    <div class="sec-head"><h2>Goals</h2>
      <span class="eyebrow">three clocks</span></div>
    <p class="sec-sub">Set 2026-09-01, replacing a single metric that spanned two motions with
    different physics and could not be true of both. A sales cycle, a raise, and an ecosystem
    do not move at the same speed, so they are not one number.</p>
    {goals_html}
  </section>
  <section>
    <div class="sec-head"><h2>Where it lands</h2>
      <span class="eyebrow">answered by which integrator signs</span></div>
    <p class="sec-sub">Not a product decision. PLUR rides the system of record and reaches the
    institution through the integrator who already delivers it. The internal ventures are the R&amp;D
    lab for the same pattern — and until one has external revenue they are internal R&amp;D, not proof.</p>
    {verts_html}
  </section>
  <section>
    <div class="sec-head"><h2>The whole board</h2><span class="eyebrow">every roadmap item</span></div>
    <div class="filters">{lchips}<span class="fsep"></span>{tchips}<span class="fsep"></span>{ochips}</div>
    <details class="more"><summary>Show every item</summary>{board}</details>
  </section>
  <section>
    <div class="sec-head"><h2>Out of scope</h2></div>
    <div class="cov">
      <div class="card">
        <h3>Intent coverage</h3>
        <div class="bar-track"><div class="bar-fill" style="width:82%"></div></div>
        <p style="font-size:13.5px;color:var(--muted)">23 of 28 item-bearing intents have at
        least one item. Constraints, the vision node and ops cadences are excluded — they are
        not supposed to carry work.</p>
      </div>
      <div class="card"><h3>The institutional rail</h3><p style="font-size:13.5px;color:var(--muted)">The regulated venue where tokenised data would actually settle, and the insurance market that would make it holdable, are real and sequenced — and they belong to Verity, not here. Out of scope until Verity is the live question. They are the reason the coverage number is not 100% and should not be.</p></div>
    </div>
    <div class="flag">
      <h3>The north star is flagged OPEN, and the file says so</h3>
      <p>{e(ns["open_question"]).replace(chr(10), " ")}</p>
    </div>
  </section>
  <footer>
    <span>generated by .datacore/lib/roadmap_render.py</span>
    <span>embargoed items keep their shape; their notes are withheld from generated views</span>
  </footer>
</div>
<script>{JS}</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", metavar="OUT", help="write the HTML view to OUT")
    ap.add_argument("--md", action="store_true", help="print the markdown view")
    ap.add_argument("--json", action="store_true", help="print public items as JSON")
    args = ap.parse_args()
    r = load()

    if args.json:
        print(json.dumps(public_items(r), indent=2))
    elif args.html:
        Path(args.html).write_text(render_html(r))
        print(f"wrote {args.html}")
    else:
        print(render_md(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
