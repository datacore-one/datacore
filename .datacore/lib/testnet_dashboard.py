#!/usr/bin/env python3
"""The Firm, as a public page: testnet.datacore.one.

WHY A SECOND RENDERER AND NOT A FLAG. reliability_dashboard.py already collects
everything this needs, and this reuses every one of its collectors. What it does
NOT reuse is `render()`, because that page is written for the owner and this one
is written for the internet. Threading a `public` flag through four hundred lines
of interleaved conditionals is how a disclosure bug gets written: one branch
forgotten in one table, and a name that was never meant to leave the machine is
on a public URL with a TLS certificate in front of it.

Two views over one data layer. The data layer is shared; the judgement about
what an audience may see is in one place, here.

THE PROJECTION IS AN ALLOWLIST. Nothing reaches the page unless this file puts
it there. A denylist would fail open the first time a collector grew a field --
and collectors grow fields.

THE GUARD IS FAIL-CLOSED. Before writing anything, `audit()` scans the rendered
HTML for the things that must never appear, built from THIS INSTALLATION's real
data rather than a hardcoded list: every principal in principals.yaml, every
host in infrastructure.yaml, every space directory name, any IPv4, any email,
any version string. If one survives, nothing is written and the exit code is
non-zero. A page that cannot be proved clean is not published.

    testnet_dashboard.py --out build/index.html
    testnet_dashboard.py --audit-only        # prove a built page is clean
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import reliability_dashboard as rd  # noqa: E402

ROOT = rd.ROOT
FIRM = "8-firm"

# The three agents, by the handle each already publishes under. Names of people
# are not here and must not be added: see the disclosure rules in MEMORY.md.
AGENTS = [
    ("Mr Data", "@plurclaw_bot", "community and communication"),
    ("Tris", "@TrisHermes_bot", "research and cross-domain analysis"),
    ("Miles", "@datacore_1_bot", "code, deployment, product"),
]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")

#: Terms deliberately published, each with the reason it is safe.
#:
#: These exist because the denylist is built from this installation's registries
#: and the Firm's agents ARE principals -- so the names the owner chose to
#: publish collide with the names the guard exists to keep in. That collision is
#: real and the guard was right to stop: "Miles" is a principal.
#:
#: Masked out of the page BEFORE the denylist runs, rather than dropped from the
#: denylist. Dropping "hermes" to let `@TrisHermes_bot` through would also let a
#: genuine mention of the hermes host through anywhere else on the page. Masking
#: the approved literal removes exactly the approved string and leaves every
#: other occurrence exposed to the check.
PUBLISHED = {
    "Datacore": "the product, and the domain this page is served from",
    "The Firm": "the subject of the page",
    "Mr Data": "agent persona, published under its own bot handle",
    "@plurclaw_bot": "public Telegram bot handle",
    "Tris": "agent persona, published under its own bot handle",
    "@TrisHermes_bot": "public Telegram bot handle",
    "Miles": "agent persona, published under its own bot handle",
    "@datacore_1_bot": "public Telegram bot handle",
}


# ── the projection ───────────────────────────────────────────────────────────

def project(d: dict) -> dict:
    """Everything the public page is allowed to know, and nothing else.

    Each key here is a deliberate decision. Anything absent from this dict cannot
    reach the page, because the renderer below reads only this.
    """
    hist = d.get("history") or {}
    jobs = d.get("jobs") or {}
    ledger = d.get("ledger") or {}
    queue = d.get("queue") or {}

    # Per-space rows are dropped entirely. Space names are venture and client
    # names, and the event counts beside them are a readout of which parts of the
    # business are busy. The aggregate says the same thing about integrity
    # without saying anything about the business.
    firm_row = next((s for s in ledger.get("spaces", []) if s.get("space") == FIRM), None)

    days = []
    for day in hist.get("days", []):
        # The failing RULE IDs only. The free-text note carries systemd unit
        # names, job names and host identifiers -- it is the single richest
        # disclosure surface in the private page and none of it survives here.
        days.append({
            "day": day["day"],
            "pass": day["pass"],
            "failing": list(day.get("failing") or []),
        })

    return {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "agents": AGENTS,
        "today": {
            "pass": (hist.get("today") or {}).get("pass"),
            "streak": (hist.get("today") or {}).get("streak"),
            "level": (hist.get("today") or {}).get("level"),
        } if hist.get("today") else None,
        "days": days,
        "clean": hist.get("clean"),
        "total": hist.get("total"),
        "per_rule": hist.get("per_rule") or {},
        "rules": {k: v for k, v in rd.RULES.items()},
        "today_rules": (hist.get("today") or {}).get("rules") or {},
        # Machine identities are replaced by a count. Even a role label
        # ("execution host") tells a reader the shape of the estate; the number
        # of machines carries the operational point without the map.
        "machines": len(jobs.get("by_machine") or {}) or None,
        # The collector calls these `verified` and `alerting`. Naming them
        # something else here read as "-- / 59" on the page: a projection that
        # invents field names produces a dash, not an error, and a dash looks
        # like missing data rather than a bug.
        "jobs_total": jobs.get("total"),
        "jobs_with_artifact": jobs.get("verified"),
        "jobs_with_route": jobs.get("alerting"),
        "firm_events": (firm_row or {}).get("events"),
        "firm_writers": (firm_row or {}).get("writers"),
        "firm_generated": (firm_row or {}).get("generated"),
        "spaces_total": len(ledger.get("spaces") or []) or None,
        "chains_failing": len(ledger.get("failed") or []),
        "events_total": ledger.get("events"),
        "queue": {
            "committed": queue.get("queued"),
            "fenced": queue.get("fenced"),
            "delivered": queue.get("review"),
        },
    }


# ── the guard ────────────────────────────────────────────────────────────────

def forbidden_terms() -> list[str]:
    """What must never appear, read from this installation rather than guessed.

    Hardcoding a list means the guard protects yesterday's data. Principals get
    added, spaces get created, hosts get renamed -- and a guard that does not
    read them is a guard that passes while the thing it guards against walks
    past it.
    """
    terms: set[str] = set()

    reg = ROOT / ".datacore" / "registry" / "principals.yaml"
    if reg.exists():
        for m in re.finditer(r"^\s{2}([a-z][\w-]*):\s*$", reg.read_text(), re.M):
            terms.add(m.group(1))
        for m in _EMAIL.finditer(reg.read_text()):
            terms.add(m.group(0))

    infra = ROOT / ".datacore" / "registry" / "infrastructure.yaml"
    if infra.exists():
        text = infra.read_text()
        for m in re.finditer(r"hostname:\s*(\S+)", text):
            terms.add(m.group(1))
        for m in re.finditer(r"^\s{2}([a-z][\w-]*):\s*$", text, re.M):
            terms.add(m.group(1))

    for space in ROOT.glob("[0-9]-*"):
        if space.is_dir() and space.name != FIRM:
            terms.add(space.name)
            terms.add(space.name.split("-", 1)[1])

    # Role labels are a map of the estate even without names.
    terms.update(rd.ROLES.values())

    # Never useful on a public page, always useful to an attacker.
    terms.update({"/Users/", "/home/", "/root/", "ssh ", "BatchMode"})

    # Short or generic tokens produce false positives against ordinary prose.
    # "firm" is this page's subject; "data" is a word.
    drop = {"firm", "data", "mac", "box", "one", "org", "ok", "host"}
    return sorted(t for t in terms if len(t) > 3 and t.lower() not in drop)


def audit(page: str) -> list[str]:
    """Every forbidden term that survived into the page. Empty means clean.

    Approved terms are masked first, so `@TrisHermes_bot` cannot shelter the
    hermes host and `datacore.one` cannot shelter the 2-datacore space, while
    either of those words appearing anywhere else is still caught.
    """
    # LONGEST FIRST, and this is not a tidiness preference. Masking "Tris"
    # before "@TrisHermes_bot" rewrites the handle to "@\x00Hermes_bot", the
    # longer approved term then matches nothing, and "hermes" -- a real host
    # name -- survives into the scan. The guard reported a leak that was not in
    # the page, which is the same class of wrong as missing one that is.
    masked = page
    for term in sorted(PUBLISHED, key=len, reverse=True):
        masked = re.sub(re.escape(term), "\x00", masked, flags=re.IGNORECASE)

    hits = []
    low = masked.lower()
    for term in forbidden_terms():
        if term.lower() in low:
            hits.append(term)
    for rx, what in ((rd._IPV4, "IPv4 address"), (_EMAIL, "email address"),
                     (_VERSION, "version string")):
        for m in rx.finditer(page):
            # The generated timestamp is a date, not a version.
            if what == "version string" and re.match(r"\d{4}\.", m.group(0)):
                continue
            hits.append(f"{what}: {m.group(0)}")
    return sorted(set(hits))


# ── rendering ────────────────────────────────────────────────────────────────

def _n(v, dash: str = "&mdash;") -> str:
    return dash if v is None else html.escape(str(v))


def render(p: dict) -> str:
    today = p.get("today") or {}
    verdict = "PASS" if today.get("pass") else ("FAIL" if today else "NO DATA")
    vclass = "ok" if today.get("pass") else ("fail" if today else "unobservable")

    strip = []
    worst = max((len(d["failing"]) for d in p["days"]), default=1) or 1
    for day in p["days"]:
        n = len(day["failing"])
        cls = "ok" if day["pass"] else ("near" if n == 1 else "fail")
        hgt = 4 if day["pass"] else max(8, round(34 * n / worst))
        label = "PASS" if day["pass"] else str(n)
        failing = ", ".join(day["failing"])
        strip.append(
            f'<div class="daycell {cls}" title="{html.escape(day["day"])}'
            f'{(" — failing: " + failing) if failing else " — all six"}">'
            f'<div class="daybar"><i style="height:{hgt}px"></i></div>'
            f'<div class="dayv">{label}</div>'
            f'<div class="dayd">{html.escape(day["day"][5:])}</div>'
            f'<div class="daynote">{html.escape(failing) or "all six conditions"}</div></div>')

    rulestats = []
    for rid, (name, _) in p["rules"].items():
        st = p["per_rule"].get(rid) or {}
        rulestats.append(
            f'<div class="rulestat"><span class="rs-k">{rid}</span>'
            f'<span class="rs-n">{st.get("ok", "&mdash;")}/{st.get("of", "&mdash;")}</span>'
            f'<span class="rs-l">{html.escape(name)}</span></div>')

    ruletable = []
    for rid, (name, meaning) in p["rules"].items():
        state = p["today_rules"].get(rid)
        chip = ('<span class="chip ok">ok</span>' if state == "ok"
                else '<span class="chip fail">fail</span>' if state == "FAIL"
                else '<span class="chip unobservable">not seen</span>')
        ruletable.append(
            f'<tr><td class="mono">{rid}</td><td><strong>{html.escape(name)}</strong>'
            f'<div class="meaning">{html.escape(meaning)}</div></td><td>{chip}</td></tr>')

    agents = []
    for name, handle, does in p["agents"]:
        agents.append(
            f'<tr><td><strong>{html.escape(name)}</strong></td>'
            f'<td class="mono small">{html.escape(handle)}</td>'
            f'<td class="note">{html.escape(does)}</td></tr>')

    q = p["queue"]
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>The Firm — Testnet</title>
<meta name="description" content="Three autonomous agents, running in the open. What was scheduled, what ran, and what the record says.">
<meta name="robots" content="noindex">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@600&display=swap">
<style>
:root {{
  --paper:#edeff3; --surface:#fafbfc; --surface-2:#e4e8ee;
  --ink:#131a28; --ink-2:#4e5868; --ink-3:#6f7889;
  --rule:#ccd3dd; --rule-soft:#dde2e9;
  --brass:#946a1f; --brass-tint:#f2e6cd;
  --teal:#1a5f5c; --teal-tint:#d8e8e6; --brick:#92372a; --brick-tint:#f3ddd9;
  --f-display:"IBM Plex Serif",Georgia,serif;
  --f-body:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  color-scheme:light dark;
  padding-top:env(safe-area-inset-top,0px); padding-bottom:env(safe-area-inset-bottom,0px);
}}
@media (prefers-color-scheme:dark) {{
  :root:not([data-theme="light"]) {{
    --paper:#0d1118; --surface:#151b25; --surface-2:#1d2532;
    --ink:#e7ebf2; --ink-2:#9aa5b6; --ink-3:#7c8798;
    --rule:#2a3444; --rule-soft:#212a37;
    --brass:#d9ab52; --brass-tint:#2e2617;
    --teal:#56b0a8; --teal-tint:#14282a; --brick:#dd7f6e; --brick-tint:#2c1a18;
  }}
}}
:root[data-theme="dark"] {{
  --paper:#0d1118; --surface:#151b25; --surface-2:#1d2532;
  --ink:#e7ebf2; --ink-2:#9aa5b6; --ink-3:#7c8798;
  --rule:#2a3444; --rule-soft:#212a37;
  --brass:#d9ab52; --brass-tint:#2e2617;
  --teal:#56b0a8; --teal-tint:#14282a; --brick:#dd7f6e; --brick-tint:#2c1a18;
}}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);font-family:var(--f-body);font-size:15px;
  line-height:1.55;margin:0;padding-inline:20px;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1000px;margin-inline:auto}}
h1,h2{{font-family:var(--f-display);font-weight:600;margin:0;text-wrap:balance}}
p{{margin:0}}
.eyebrow{{font-family:var(--f-mono);font-size:.7rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-3);font-weight:500}}
.mono{{font-family:var(--f-mono)}} .small{{font-size:.82rem}}
header{{border-bottom:2px solid var(--ink);padding-block:40px 24px}}
header h1{{font-size:clamp(1.9rem,5vw,2.7rem);letter-spacing:-.015em;margin:14px 0 12px}}
.meta{{display:flex;flex-wrap:wrap;gap:6px 20px}}
.lede{{color:var(--ink-2);font-size:.93rem;max-width:66ch}}
.verdict{{display:flex;flex-wrap:wrap;gap:20px 30px;align-items:flex-start;margin-top:24px}}
.fig{{display:flex;flex-direction:column;gap:3px}}
.fig-n,.vbig{{font-family:var(--f-mono);font-variant-numeric:tabular-nums;
  font-size:1.5rem;font-weight:500;line-height:1.1;letter-spacing:-.02em}}
.vbig.ok{{color:var(--teal)}} .vbig.fail{{color:var(--brick)}}
.vbig.unobservable{{color:var(--ink-3)}}
.fig-n.good{{color:var(--teal)}} .fig-n.warn{{color:var(--brass)}}
.fig-l{{font-size:.82rem;color:var(--ink-2)}}
.figs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:18px}}
section{{padding-block:34px;border-top:1px solid var(--rule)}}
section h2{{font-size:1.24rem;margin-bottom:6px}}
section .lede{{margin-bottom:18px}}
.tbl{{overflow-x:auto;border:1px solid var(--rule);border-radius:3px;background:var(--surface)}}
table{{border-collapse:collapse;width:100%;font-size:.87rem;min-width:460px}}
th,td{{text-align:left;padding:9px 13px;vertical-align:top;border-bottom:1px solid var(--rule-soft)}}
thead th{{font-family:var(--f-mono);font-size:.66rem;font-weight:600;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3);background:var(--surface-2);
  border-bottom:1px solid var(--rule)}}
tbody tr:last-child td{{border-bottom:0}}
td.num{{font-family:var(--f-mono);font-variant-numeric:tabular-nums}}
.meaning{{font-size:.78rem;color:var(--ink-3);margin-top:2px}}
.note{{font-size:.82rem;color:var(--ink-2)}}
.chip{{font-family:var(--f-mono);font-size:.66rem;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;padding:.2em .48em;border-radius:2px;white-space:nowrap;
  display:inline-block}}
.chip.ok{{color:var(--teal);background:var(--teal-tint);border:1px solid var(--teal)}}
.chip.fail{{color:var(--brick);background:var(--brick-tint);border:1px solid var(--brick)}}
.chip.unobservable{{color:var(--ink-3);background:transparent;border:1px dashed var(--ink-3)}}
.strip{{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;align-items:stretch}}
.daycell{{flex:1 1 84px;min-width:84px;background:var(--surface);border:1px solid var(--rule);
  border-radius:3px;padding:9px 8px;display:flex;flex-direction:column;gap:5px}}
.daycell.ok{{border-color:var(--teal);background:var(--teal-tint)}}
.daycell.near{{border-color:var(--brass);border-style:dashed}}
.daycell.fail{{border-color:var(--brick);background:var(--brick-tint)}}
.daybar{{height:36px;display:flex;align-items:flex-end}}
.daybar i{{display:block;width:100%;border-radius:2px;background:var(--brick)}}
.daycell.ok .daybar i{{background:var(--teal)}}
.daycell.near .daybar i{{background:var(--brass)}}
.dayv{{font-family:var(--f-mono);font-size:.82rem;font-weight:600;line-height:1}}
.daycell.ok .dayv{{color:var(--teal)}} .daycell.fail .dayv{{color:var(--brick)}}
.daycell.near .dayv{{color:var(--brass)}}
.dayd{{font-family:var(--f-mono);font-size:.68rem;color:var(--ink-3)}}
.daynote{{font-size:.66rem;color:var(--ink-2);line-height:1.3}}
.rsrow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:10px;margin-top:10px}}
.rulestat{{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  padding:9px 11px;display:flex;flex-direction:column;gap:1px}}
.rs-k{{font-family:var(--f-mono);font-size:.66rem;color:var(--brass);font-weight:600}}
.rs-n{{font-family:var(--f-mono);font-variant-numeric:tabular-nums;font-size:1.02rem;font-weight:600}}
.rs-l{{font-size:.72rem;color:var(--ink-2)}}
.legend{{margin-top:18px;padding:14px 16px;background:var(--surface);border:1px solid var(--rule);
  border-radius:3px;font-size:.84rem;color:var(--ink-2);display:flex;flex-direction:column;gap:6px}}
footer{{border-top:2px solid var(--ink);padding-block:22px 48px;margin-top:34px;
  font-size:.8rem;color:var(--ink-2)}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style></head>
<body><div class="wrap">

<header>
  <div class="meta">
    <span class="eyebrow">testnet</span>
    <span class="eyebrow">the firm</span>
    <span class="eyebrow">{_n(p["generated"])}</span>
  </div>
  <h1>Three agents, running in the open</h1>
  <p class="lede">The Firm is a small autonomous organisation: three agents on three
  different models, with their own memory, running scheduled work without anyone
  watching. This page is the part of its operating record that can be shown &mdash;
  whether the work ran, whether failures were noticed, and whether the log they write
  to still verifies. Numbers, never contents.</p>
  <div class="verdict">
    <div class="fig"><span class="vbig {vclass}">{verdict}</span>
      <span class="fig-l">the day line, most recent</span></div>
    <div class="fig"><span class="fig-n">{_n(today.get("streak"))}</span>
      <span class="fig-l">day streak</span></div>
    <div class="fig"><span class="fig-n">{_n(today.get("level"))}</span>
      <span class="fig-l">reliability level</span></div>
    <div class="fig"><span class="fig-n">{_n(p["jobs_with_artifact"])}/{_n(p["jobs_total"])}</span>
      <span class="fig-l">jobs proving they ran</span></div>
  </div>
</header>

<section>
  <h2>The members</h2>
  <p class="lede">Different models on purpose. Three agents that reason the same way are
  one agent with two echoes, and a disagreement is the only thing that catches a
  confident mistake.</p>
  <div class="tbl"><table>
    <thead><tr><th>Member</th><th>Reachable at</th><th>Owns</th></tr></thead>
    <tbody>{''.join(agents)}</tbody>
  </table></div>
</section>

<section>
  <h2>Did the work run?</h2>
  <p class="lede">Six conditions, evaluated once a day. Each is a file and a rule, so two
  people checking get the same answer. Any one failing breaks the streak that day &mdash;
  there is no partial credit and no judgement call.</p>
  <div class="tbl"><table>
    <thead><tr><th>Rule</th><th>Condition</th><th>Latest</th></tr></thead>
    <tbody>{''.join(ruletable)}</tbody>
  </table></div>
</section>

<section>
  <h2>The record</h2>
  <p class="lede">One column per day. Bar height is how many conditions were failing, so a
  tall day is a bad day. A streak is historical by definition &mdash; a single green day
  does not demonstrate anything, which is the point of keeping the bad ones visible.</p>
  <div class="figs">
    <div class="fig"><span class="fig-n good">{_n(p["clean"])} / {_n(p["total"])}</span>
      <span class="fig-l">days with zero failing conditions</span></div>
    <div class="fig"><span class="fig-n">{_n(p["machines"])}</span>
      <span class="fig-l">machines carrying scheduled work</span></div>
    <div class="fig"><span class="fig-n">{_n(p["jobs_with_route"])}/{_n(p["jobs_total"])}</span>
      <span class="fig-l">jobs with somewhere to send a failure</span></div>
  </div>
  <div class="strip">{''.join(strip)}</div>
  <div class="rsrow">{''.join(rulestats)}</div>
</section>

<section>
  <h2>The record it keeps</h2>
  <p class="lede">Every action an agent takes is appended to a hash-chained log. That
  matters more than the six conditions: a broken chain would leave all six green while
  the history underneath them quietly stopped being true.</p>
  <div class="figs">
    <div class="fig"><span class="fig-n">{_n(p["firm_events"])}</span>
      <span class="fig-l">events in the Firm's own log</span></div>
    <div class="fig"><span class="fig-n">{_n(p["firm_writers"])}</span>
      <span class="fig-l">writers, one per agent per machine</span></div>
    <div class="fig"><span class="fig-n good">{_n(p["chains_failing"])}</span>
      <span class="fig-l">chains that do not verify, anywhere</span></div>
    <div class="fig"><span class="fig-n">{_n(p["spaces_total"])}</span>
      <span class="fig-l">independent logs kept in total</span></div>
  </div>
</section>

<section>
  <h2>The queue</h2>
  <p class="lede">What the overnight runner would pick up, and what is deliberately
  fenced until a person decides. Counts only &mdash; a number says whether the queue is
  moving without publishing what is in it.</p>
  <div class="figs">
    <div class="fig"><span class="fig-n">{_n(q["committed"])}</span>
      <span class="fig-l">committed to a run</span></div>
    <div class="fig"><span class="fig-n warn">{_n(q["fenced"])}</span>
      <span class="fig-l">fenced, waiting on a person</span></div>
    <div class="fig"><span class="fig-n">{_n(q["delivered"])}</span>
      <span class="fig-l">delivered, awaiting review</span></div>
  </div>
  <div class="legend">
    <span class="eyebrow">What this page deliberately omits</span>
    <div>No names of people. No machine names, addresses or versions. No task contents,
    client names or amounts. The six conditions and the counts are the whole of it.</div>
    <div>A page that cannot be proved free of those is not published: the generator
    scans its own output against this installation's real registries and refuses to
    write if anything survives.</div>
  </div>
</section>

<footer>
  The Firm &middot; testnet. Generated from the same operating record the team runs on,
  projected to what an audience may see. Read-only: this page computes nothing of its
  own and writes no state.
</footer>

</div></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="build/index.html")
    ap.add_argument("--no-fleet", action="store_true", default=True,
                    help="default: the page shows no per-machine data, so the SSH "
                         "round trip buys nothing")
    ap.add_argument("--audit-only", metavar="PATH",
                    help="scan an already-built page and exit non-zero if unclean")
    ap.add_argument("--json", action="store_true", help="print the projection, not HTML")
    args = ap.parse_args()

    if args.audit_only:
        hits = audit(Path(args.audit_only).read_text())
        if hits:
            print(f"REFUSED: {len(hits)} forbidden term(s) in {args.audit_only}", file=sys.stderr)
            for h in hits:
                print(f"  {h}", file=sys.stderr)
            return 1
        print(f"clean: {args.audit_only}")
        return 0

    data = rd.collect(skip_fleet=True)
    p = project(data)
    if args.json:
        print(json.dumps(p, indent=2, default=str))
        return 0

    page = render(p)
    hits = audit(page)
    if hits:
        # Nothing is written. A page that cannot be proved clean is not published,
        # and leaving a stale-but-clean page up is strictly better than replacing
        # it with a fresh one that leaks.
        print(f"REFUSED to write {args.out}: {len(hits)} forbidden term(s)", file=sys.stderr)
        for h in hits:
            print(f"  {h}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"testnet dashboard: {out} ({len(page):,} bytes), disclosure audit clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
