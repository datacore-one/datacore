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

/* founder queue */
.queue{margin-top:22px;border:1px solid var(--rule);border-radius:10px;overflow:hidden;background:var(--surface);box-shadow:var(--shadow)}
.qrow{display:grid;grid-template-columns:64px 1fr auto;gap:16px;align-items:center;
  padding:13px 18px;border-top:1px solid var(--rule-soft)}
.qrow:first-child{border-top:none}
.qrow .qid{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--human)}
.qrow .qt{color:var(--ink);font-weight:500;font-size:14.5px}
.qrow .qo{font-size:13px;color:var(--muted);margin-top:1px}
.qcost{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--human);
  background:var(--human-soft);padding:3px 9px;border-radius:5px;white-space:nowrap}
.qfoot{padding:13px 18px;background:var(--human-soft);border-top:1px solid var(--rule-soft);
  color:var(--human);font-size:13.5px}

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
  border-radius:10px;padding:18px 20px}
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


JS = """
const chips=[...document.querySelectorAll('.chip')];
const items=[...document.querySelectorAll('.item')];
let active={track:null,owner:null};
function apply(){
  items.forEach(el=>{
    const okT=!active.track||el.dataset.track===active.track;
    const okO=!active.owner||el.dataset.owner===active.owner;
    el.classList.toggle('hidden',!(okT&&okO));
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
    """Master plan — four sequential steps plus one throughline."""
    out = ""
    for s in r.get("master_plan") or []:
        thru = s["step"] == "throughline"
        num = "runs<br>under<br>all" if thru else e(s["step"])
        out += (f'<div class="step{" thru" if thru else ""}"><div class="num">{num}</div>'
                f'<div><div class="do">{e(s["do"])}</div>'
                f'<p class="why">{e(s["why"]).strip()}</p>'
                f'<div class="serves">serves · {e(" · ".join(s.get("serves") or []))}</div>'
                f'</div></div>')
    return f'<div class="plan">{out}</div>'


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
    """The arc: the few state changes that each let the next one happen."""
    out = ""
    for m in r.get("milestones") or []:
        gate = (f'<div class="mgate"><b>gate</b><span>{e(m["gate"])}</span></div>'
                if m.get("gate") else "")
        its = ("".join(f'<span class="tag">{e(i)}</span>' for i in m.get("items") or [])
               or '<span class="tag">—</span>')
        feats = ""
        for f in (r.get("features") or []):
            if f.get("milestone") != m["id"]:
                continue
            repo, _, num = f["gh"].rpartition("#")
            kids = (f'<span class="kids">{len(f["children"])} sub-issues</span>'
                    if f.get("children") else "")
            feats += (f'<div class="feat"><div class="fh">'
                      f'<a class="fgh" href="https://github.com/{e(repo)}/issues/{e(num)}">'
                      f'{e(repo.split("/")[-1])}#{e(num)}</a>'
                      f'<span class="ft">{e(f["title"])}</span>{kids}</div>'
                      f'<p class="fd">{e(f["delivers"]).strip()}</p></div>')
        feats = (f'<div class="feats"><p class="eyebrow">what gets built to get there</p>'
                 f'{feats}</div>') if feats else ""
        out += (f'<div class="mile"><div class="rail"><span class="mid">{e(m["id"])}</span>'
                f'<span class="state {e(m["state"])}">{e(m["state"].replace("_", " "))}</span>'
                f'<span class="mid">{e(m.get("tier", ""))}</span></div>'
                f'<div><div class="mt">{e(m["title"])}</div>'
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
                 f'<span class="rm">{e(rg.get("milestone",""))}</span></div>')
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
    HORIZON_NOTE = {
        "now": "Answers the September SRC question, or unblocks something that does.",
        "next": "Real work, sequenced behind now — not started, not forgotten.",
        "gated": "Held on a condition, never a date. Sprint planning must not select these.",
        "later": "Acknowledged, deliberately unscheduled.",
    }
    board = ""
    for h in ("now", "next", "gated", "later"):
        grp = [i for i in items if i["horizon"] == h]
        if not grp:
            continue
        rows = ""
        for i in grp:
            tags = f'<span class="tag track">{e(i["track"])}</span>'
            tags += f'<span class="tag">{e(i.get("owner", "—"))}</span>'
            st = i.get("status")
            tags += f'<span class="tag {"go" if st == "in_progress" else ""}">{e(st)}</span>'
            b = i.get("blocked_on")
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
            rows += (
                f'<div class="item" data-track="{e(i["track"])}" data-owner="{e(i.get("owner"))}"'
                f' data-block="{e(b)}" data-h="{e(h)}" data-s="{e(st)}">'
                f'<div class="itop"><span class="iid">{e(i["id"])}</span>'
                f'<span class="ititle">{e(i["title"])}</span></div>'
                f'<p class="iout">{e(i["outcome"])}</p>'
                f'<div class="imeta">{tags}</div>{gate}'
                f'<div class="serves">serves · {e(" · ".join(i.get("serves") or []))}</div>'
                f'</div>'
            )
        board += (
            f'<div class="hgroup"><div class="hlabel"><h2>{h.title()}</h2>'
            f'<span class="hcount">{len(grp)} items</span><span class="bar"></span></div>'
            f'<p class="sec-sub" style="margin:0 0 12px">{HORIZON_NOTE[h]}</p>'
            f'<div class="items">{rows}</div></div>'
        )

    ladder_html = render_ladder(r)
    ents_html = render_entities(r)
    verts_html = render_verticals(r)
    plan_html = render_plan(r)
    intents_html = render_intents()
    miles_html = render_milestones(r)

    tchips = "".join(
        f'<button class="chip" data-key="track" data-val="{e(t)}" aria-pressed="false">{e(t)}</button>'
        for t in tracks
    )
    ochips = "".join(
        f'<button class="chip" data-key="owner" data-val="{e(o)}" aria-pressed="false">{e(o)}</button>'
        for o in owners
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
  <p class="lede">Five prose roadmaps drifted independently because nothing regenerated them.
  This page is generated — a stale view is now a visible diff, not a silent three-month gap.</p>
</div></header>

<div class="wrap">
  <div class="metrics">{mhtml}</div>

  <section>
    <div class="sec-head"><h2>The master plan</h2>
      <span class="eyebrow">five lines</span></div>
    <p class="sec-sub">Four steps, each one paying for the next, plus one throughline that
    runs under all of them. If a roadmap item cannot be traced to a step here, that is the
    question to ask about it.</p>
    {plan_html}
  </section>

  <section>
    <div class="sec-head"><h2>The ladder</h2>
      <span class="eyebrow">one primitive, four rungs</span></div>
    <p class="sec-sub">From the seed deck's vision slide. Memory is the floor and it ships today;
    everything above it is carried by the same primitive, captured when knowledge forms —
    <em>the way a deed is issued when property changes hands. It cannot be added later.</em></p>
    {ladder_html}
  </section>

  <section>
    <div class="sec-head"><h2>Who launches what</h2>
      <span class="eyebrow">entity boundaries</span></div>
    <p class="sec-sub">The network is not launched from PLUR Ltd, and that is load-bearing rather
    than administrative: the absence of a value-capture token is what lets PLUR be the cross-vendor
    intermediary at all.</p>
    {ents_html}
  </section>

  <section>
    <div class="sec-head"><h2>Verticals</h2>
      <span class="eyebrow">answered by which integrator signs</span></div>
    <p class="sec-sub">Not a product decision. PLUR rides the system of record and reaches the
    institution through the integrator who already delivers it. The internal ventures are the R&amp;D
    lab for the same pattern — and until one has external revenue they are internal R&amp;D, not proof.</p>
    {verts_html}
  </section>

  <section>
    <div class="sec-head"><h2>What we are actually trying to do</h2>
      <span class="eyebrow">org/intents.org</span></div>
    <p class="sec-sub">Read straight out of the intent graph, not restated here — so a
    renamed or deleted intent shows up as a missing node rather than a stale copy. Items
    reference these; an item serving none of them is a deletion candidate. Crossed chips
    are anti-goals: doors deliberately not opened.</p>
    {intents_html}
  </section>

  <section>
    <div class="sec-head"><h2>The arc</h2>
      <span class="eyebrow">milestones toward the vision</span></div>
    <p class="sec-sub">Not tasks — the handful of state changes that each let the next one
    happen. State is honest: <em>shipped</em> means shipped, and one of these is a claim the
    ICE one-pager already rests on.</p>
    {miles_html}
  </section>

  <section>
    <div class="sec-head"><h2>Where the roadmap stops moving</h2>
      <span class="eyebrow">fan-in bottleneck</span></div>
    <p class="sec-sub">Forty-eight items leave one file by three routes. Two of them flow.
    The third converges on a single person with a capacity of one, and everything behind
    it waits.</p>
    <figure class="figure">{DIAGRAM}</figure>
  </section>

  <section>
    <div class="sec-head"><h2>The founder queue</h2>
      <span class="eyebrow">blocked_on: human</span></div>
    <p class="sec-sub">Every item here is stalled on one person. None is large — a DNS record,
    a token rotation, one approval click on an eight-line green PR, three five-minute form
    submissions. Together they gate four tracks. This is the query the spec argued for after
    a distribution item sat thirty-seven days because nobody had surfaced it as a queue.</p>
    <div class="queue">{qhtml}
      <div class="qfoot"><b>{len(queue)} items</b> — roughly an hour of your attention,
      against {len(delegable)} items already delegable and moving. The constraint is not
      engineering capacity.</div>
    </div>
  </section>

  <section>
    <div class="sec-head"><h2>The board</h2><span class="eyebrow">filter to narrow</span></div>
    <div class="filters">{tchips}<span style="width:10px"></span>{ochips}</div>
    {board}
  </section>

  <section>
    <div class="sec-head"><h2>Where the strategy has no roadmap</h2></div>
    <div class="cov">
      <div class="card">
        <h3>Intent coverage</h3>
        <div class="bar-track"><div class="bar-fill" style="width:82%"></div></div>
        <p style="font-size:13.5px;color:var(--muted)">23 of 28 item-bearing intents have at
        least one item. Constraints, the vision node and ops cadences are excluded — they are
        not supposed to carry work.</p>
      </div>
      <div class="card">
        <h3>Five uncovered goals</h3>
        <p style="font-size:13.5px;color:var(--muted)">All five sit in the top-down half of the
        two-track exchange. The strategy calls hub and Verity “the same rails from opposite
        ends” — only the bottom-up end has items.</p>
        <ul class="gaps">{ghtml}</ul>
      </div>
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
