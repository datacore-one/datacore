#!/usr/bin/env python3
"""reliability_dashboard.py -- one page that says whether the fleet is actually working.

WHY THIS EXISTS. The reliability signal is real but scattered across six commands
that each print a different shape to a different terminal: the scoreboard's day
line, per-principal rows, the job manifest, cadence liveness, principal contracts,
and fleet versions. Nobody reads six commands every morning, so the signal existed
and was not being looked at. DIP-0035 gave every job a contract and DIP-0034 gave
every event a record; this renders both into something a person can take in at a
glance, and something an operator who is not the author can be handed.

THE ONE RULE THIS FILE ENFORCES. A check that could not be run from this host
renders as `unobservable` -- never as ok, never as a blank that reads as fine. The
scoreboard already works this way (`n-a` is not a pass) and the egress report in
datacore-app already distinguishes a wired route from a declared one. A dashboard
that turns "I could not tell" into green is worse than no dashboard, because it
manufactures the confidence it was built to check. Every unobservable cell says
which host would have to run it.

A DAY LINE IS NOT A RECORD. A streak is historical by definition, so a page that
renders only today can never show one: it reports the worst thing about the most
recent 24 hours and calls that the state of the system. The scoreboard log already
holds the history; this reads it from the host that OWNS the check (the box), not
from whichever machine happens to run this script, and shows the run of days. A
condition that has passed for four days straight and a condition that has never
passed look identical in a snapshot and completely different in a record.

Sources, all already on disk or one subprocess away:
  <owner host>:~/.datacore/state/reliability-scoreboard.log   the day-line record
  reliability_scoreboard.py --json   day line, R1-R6, streak, level, principals
  principals_check.py --json         charter / contracts / budget / memory scope
  cadence_liveness.py                overdue cadences, sunset review backlog
  jobs/manifest.yaml                 every scheduled job, its artifact, its alert route
  fleet_status.py --json             per-machine reachability and version drift (SSH)

Usage:
  reliability_dashboard.py [--out PATH] [--no-fleet] [--redact] [--json]

  --no-fleet  skip the SSH round trip (fast, offline; fleet renders unobservable)
  --redact    replace machine identifiers with role labels, for a page that will
              be shown to an audience. Agent names are personas and stay.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
MANIFEST = LIB / "jobs" / "manifest.yaml"
TIMEOUT = 60
OWNER_HOST = os.environ.get("DATACORE_SCOREBOARD_HOST", "winston")
SCOREBOARD_LOG = "~/.datacore/state/reliability-scoreboard.log"
HISTORY_DAYS = 21
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# Machine -> role label, used only under --redact.
ROLES = {
    "box": "chief-of-staff host",
    "winston": "chief-of-staff host",
    "mac": "workstation",
    "nightshift": "execution host",
    "hermes": "gateway host",
    "plur-claw": "comms host",
}

RULES = {
    "R1": ("Delivered", "No scheduled job is stuck in a recurring failure state."),
    "R2": ("Loud", "Every unit that failed today produced an alert that was actually sent."),
    "R3": ("Unattended", "Nobody had to intervene by hand today."),
    "R4": ("Data-safe", "Backup fresh and offsite, restore verified within 8 days, fleet sync healthy."),
    "R5": ("Reachable", "The uptime probe saw the host up for at least 99.5% of samples."),
    "R6": ("Rebuildable", "Today's installer verification logged zero failures."),
}
LEVELS = {3: "under 7 clean days", 4: "7 clean days", 5: "30 clean days"}


# ── collection ───────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = TIMEOUT) -> tuple[bool, str]:
    """Never raises. Returns (ok, output) -- ok False means unobservable, not failing."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "" if not p.stdout else "")
        return True, out
    except Exception as exc:  # noqa: BLE001 -- any failure here is "could not tell"
        return False, f"{type(exc).__name__}: {exc}"


def scoreboard() -> dict:
    ok, out = _run([sys.executable, str(LIB / "reliability_scoreboard.py"), "--json", "--no-write"])
    if not ok:
        return {"unobservable": out}
    try:
        return json.loads(out[out.index("{"):out.rindex("}") + 1])
    except Exception:
        # Fall back to the human line: it carries the same six flags.
        m = re.search(r"(\d{4}-\d{2}-\d{2}) (PASS|FAIL) streak=(\d+) level=(\d+)", out)
        if not m:
            return {"unobservable": out.strip()[:300] or "no output"}
        checks = {k: {"ok": v == "ok", "note": ""} for k, v in re.findall(r"\b(R[1-6])=(ok|FAIL)\b", out)}
        return {"day": m.group(1), "pass": m.group(2) == "PASS",
                "streak": int(m.group(3)), "level": int(m.group(4)), "checks": checks}


def history(host: str | None) -> dict:
    """The day-line record from the host that owns the check.

    Parsed from the log rather than recomputed, because the log is the record:
    recomputing yesterday from today's filesystem would answer a different
    question and quietly disagree with what was alerted on at the time.
    """
    if not host:
        return {"unobservable": "no owner host configured"}
    local = Path(SCOREBOARD_LOG.replace("~", str(Path.home())))
    if host == "local" and local.exists():
        ok, out = True, local.read_text()
    else:
        ok, out = _run(["ssh", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes",
                        host, f"cat {SCOREBOARD_LOG}"], timeout=25)
    if not ok:
        return {"unobservable": f"{host} unreachable: {out[:120]}"}

    days = []
    for line in out.splitlines():
        m = re.match(r"^(\d{4}-\d{2}-\d{2}) (PASS|FAIL) streak=(\d+) level=(\d+)(.*)$", line.strip())
        if not m:
            continue
        rest = m.group(5)
        rules = dict(re.findall(r"\b(R[1-6])=(ok|FAIL)\b", rest))
        notes = {}
        if "|" in rest:
            for chunk in rest.split("|", 1)[1].split(";"):
                nm = re.match(r"\s*(R[1-6]):\s*(.*)", chunk)
                if nm:
                    notes[nm.group(1)] = nm.group(2).strip()
        days.append({"day": m.group(1), "pass": m.group(2) == "PASS",
                     "streak": int(m.group(3)), "level": int(m.group(4)),
                     "rules": rules, "notes": notes,
                     "failing": [k for k, v in rules.items() if v == "FAIL"]})
    if not days:
        return {"unobservable": "no day lines in the log"}

    days = days[-HISTORY_DAYS:]
    clean = [d for d in days if d["pass"]]
    per_rule = {}
    for r in RULES:
        seen = [d for d in days if r in d["rules"]]
        per_rule[r] = {"ok": sum(1 for d in seen if d["rules"][r] == "ok"), "of": len(seen)}
    return {
        "host": host, "days": days, "clean": len(clean), "total": len(days),
        "first_clean": clean[0]["day"] if clean else None,
        "best_streak": max((d["streak"] for d in days), default=0),
        "per_rule": per_rule,
        "today": days[-1],
    }


def principal_rows() -> list[dict]:
    """Live rows from the scoreboard's own per-principal output."""
    ok, out = _run([sys.executable, str(LIB / "reliability_scoreboard.py"), "--no-write"])
    rows = []
    if ok:
        for name, state, note in re.findall(r"principal=(\S+) (ok|n-a|FAIL) \| (.*)", out):
            rows.append({"principal": name, "state": state, "note": note.strip()})
    return rows


def principals() -> list[dict]:
    ok, out = _run([sys.executable, str(LIB / "principals_check.py"), "--json", "--root", str(ROOT)])
    if not ok:
        return []
    try:
        return json.loads(out[out.index("["):out.rindex("]") + 1])
    except Exception:
        return []


def cadences() -> dict:
    ok, out = _run([sys.executable, str(LIB / "cadence_liveness.py"), "--root", str(ROOT)])
    if not ok:
        return {"unobservable": out}
    overdue = re.search(r"(\d+) cadence\(s\) overdue", out)
    no_review = re.search(r"(\d+) without a review date", out)
    past_due = re.search(r"(\d+) past due", out)
    return {
        "overdue": int(overdue.group(1)) if overdue else None,
        "sunset_past_due": int(past_due.group(1)) if past_due else None,
        "no_review_date": int(no_review.group(1)) if no_review else None,
        "raw": out.strip()[:200],
    }


def jobs() -> dict:
    try:
        import yaml
        data = yaml.safe_load(MANIFEST.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"unobservable": f"{type(exc).__name__}: {exc}"}
    js = data.get("jobs") or []
    by_machine: dict[str, dict] = {}
    for j in js:
        m = j.get("machine", "?")
        b = by_machine.setdefault(m, {"total": 0, "verified": 0, "alerting": 0})
        b["total"] += 1
        if j.get("artifacts"):
            b["verified"] += 1
        if j.get("on_fail"):
            b["alerting"] += 1
    return {
        "total": len(js),
        "verified": sum(1 for j in js if j.get("artifacts")),
        "alerting": sum(1 for j in js if j.get("on_fail")),
        "by_machine": by_machine,
    }


def fleet(skip: bool) -> dict:
    if skip:
        return {"unobservable": "skipped (--no-fleet)"}
    ok, out = _run([sys.executable, str(LIB / "fleet_status.py"), "--json"], timeout=90)
    if not ok:
        return {"unobservable": out}
    try:
        return json.loads(out[out.index("{"):out.rindex("}") + 1])
    except Exception:
        return {"unobservable": "unparseable fleet output"}


def collect(skip_fleet: bool = False, owner_host: str | None = OWNER_HOST) -> dict:
    return {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": os.uname().nodename,
        "owner_host": owner_host,
        "history": history(owner_host),
        "scoreboard": scoreboard(),
        "principal_rows": principal_rows(),
        "principals": principals(),
        "cadences": cadences(),
        "jobs": jobs(),
        "fleet": fleet(skip_fleet),
    }


# ── rendering ────────────────────────────────────────────────────────────────

def _label(machine: str, redact: bool) -> str:
    if not redact:
        return machine
    return ROLES.get(machine, "host")


def _scrub(text: str, redact: bool) -> str:
    """Rule notes are free text written by checks and have carried probe IPs.
    Under --redact they are stripped: an address in a page shown to an audience
    is an infrastructure disclosure nobody decided to make."""
    return _IPV4.sub("[address]", text) if redact else text


def _chip(state: str, text: str | None = None) -> str:
    """state: ok | fail | unobservable | idle"""
    return f'<span class="chip {state}">{html.escape(text or state)}</span>'


def _drift(fleet_data: dict) -> list[str]:
    """Version disagreement across machines is a finding, not a column."""
    if "unobservable" in fleet_data:
        return []
    ms = fleet_data.get("machines", [])
    out = []
    for field in ("core", "mcp", "org_workspace", "cli", "python"):
        vals = {m.get(field) for m in ms if m.get(field)}
        if len(vals) > 1:
            out.append(f"{field}: {', '.join(sorted(str(v) for v in vals))}")
    return out


def _record_block(h: dict, redact: bool) -> tuple[str, str]:
    """Returns (milestones_html, strip_html). Empty strings when unobservable."""
    if "unobservable" in h:
        return ("", f"<p class='note'>Record unobservable: {html.escape(str(h['unobservable']))}</p>")

    days = h["days"]
    worst = max((len(x["failing"]) for x in days), default=0) or 1
    cells = []
    for x in days:
        n = len(x["failing"])
        state = "ok" if x["pass"] else ("fail" if n > 1 else "near")
        detail = "all six conditions" if x["pass"] else "failing: " + ", ".join(
            f"{r} {RULES[r][0]}" for r in x["failing"])
        note = "; ".join(_scrub(v, redact) for v in x["notes"].values())
        bar_h = max(6, round(34 * n / worst)) if n else 4
        cells.append(
            f"<div class='daycell {state}' title='{html.escape(x['day'] + ' — ' + detail)}'>"
            f"<div class='daybar'><i style='height:{bar_h}px'></i></div>"
            f"<div class='dayv'>{'PASS' if x['pass'] else str(n)}</div>"
            f"<div class='dayd'>{html.escape(x['day'][5:])}</div>"
            f"<div class='daynote'>{html.escape(detail)}"
            + (f"<span class='dn2'>{html.escape(note)}</span>" if note else "") + "</div></div>")

    first = h.get("first_clean")
    pr = h["per_rule"]
    rule_bits = "".join(
        f"<div class='rulestat'><span class='rs-k'>{k}</span>"
        f"<span class='rs-n'>{v['ok']}/{v['of']}</span>"
        f"<span class='rs-l'>{html.escape(RULES[k][0])}</span></div>"
        for k, v in pr.items())

    milestones = (
        "<div class='figs'>"
        + (f"<div class='fig'><span class='fig-n good'>{html.escape(first)}</span>"
           f"<span class='fig-l'>first day every condition passed</span></div>" if first else
           "<div class='fig'><span class='fig-n bad'>none yet</span>"
           "<span class='fig-l'>no fully clean day recorded</span></div>")
        + f"<div class='fig'><span class='fig-n {'good' if h['clean'] else 'bad'}'>{h['clean']} / {h['total']}</span>"
          f"<span class='fig-l'>days with zero failing conditions</span></div>"
        + f"<div class='fig'><span class='fig-n'>{len(days[-1]['rules']) - len(days[-1]['failing'])} / 6</span>"
          f"<span class='fig-l'>conditions passing on the latest day</span></div>"
        + "</div>")

    strip = (f"<div class='strip'>{''.join(cells)}</div>"
             f"<div class='rulestats'><span class='eyebrow'>Per condition, across these days</span>"
             f"<div class='rsrow'>{rule_bits}</div></div>")
    return milestones, strip


def render(d: dict, redact: bool = False) -> str:
    hist = d.get("history", {})
    sb = d["scoreboard"]
    # The day line belongs to the host that owns the check. Prefer it; fall back
    # to the local computation only when the owner is unreachable, and say so.
    if "unobservable" not in hist:
        t = hist["today"]
        sb = {"day": t["day"], "pass": t["pass"], "streak": t["streak"], "level": t["level"],
              "checks": {k: {"ok": v == "ok", "note": _scrub(t["notes"].get(k, ""), redact)}
                         for k, v in t["rules"].items()}}
        source = f"day line from {_label(hist['host'], redact)}, the host that owns these checks"
    else:
        source = f"day line computed locally — {_label(d['host'], redact)} does not own every check"
    day_known = "unobservable" not in sb
    passed = sb.get("pass") if day_known else None
    streak = sb.get("streak") if day_known else None
    level = sb.get("level") if day_known else None
    checks = sb.get("checks", {}) if day_known else {}

    # R1-R6
    rule_rows = []
    for key, (name, meaning) in RULES.items():
        c = checks.get(key)
        if c is None:
            state, note = "unobservable", "not computed on this host"
        elif c.get("ok"):
            state, note = "ok", c.get("note") or ""
        else:
            state, note = "fail", c.get("note") or ""
        rule_rows.append(
            f"<tr><td class='mono'>{key}</td><td><strong>{html.escape(name)}</strong>"
            f"<div class='meaning'>{html.escape(meaning)}</div></td>"
            f"<td>{_chip(state)}</td><td class='note'>{html.escape(note)}</td></tr>")

    # principals: merge live state with declared contract data
    live = {r["principal"]: r for r in d["principal_rows"]}
    p_rows = []
    for p in d["principals"]:
        name = p["principal"]
        lr = live.get(name)
        if lr is None:
            state, note = "idle", "no scoreboard row"
        else:
            state = {"ok": "ok", "FAIL": "fail", "n-a": "unobservable"}.get(lr["state"], "unobservable")
            note = lr["note"]
        missing = p.get("missing") or []
        p_rows.append(
            f"<tr><td><strong>{html.escape(name)}</strong>"
            f"<div class='meaning'>{html.escape(p.get('kind',''))}</div></td>"
            f"<td>{_chip(state)}</td>"
            f"<td class='num'>{p.get('contracts', 0)}</td>"
            f"<td>{'yes' if p.get('charter') else _chip('fail','none')}</td>"
            f"<td>{html.escape(str(p.get('budget')) if p.get('budget') else '') or _chip('unobservable','undeclared')}</td>"
            f"<td class='mono small'>{html.escape(p.get('memory_scope') or '—')}</td>"
            f"<td class='note'>{html.escape(note)}"
            + (f"<div class='miss'>{html.escape('; '.join(missing))}</div>" if missing else "")
            + "</td></tr>")

    # jobs
    j = d["jobs"]
    if "unobservable" in j:
        job_block = f"<p class='note'>Job manifest unreadable: {html.escape(j['unobservable'])}</p>"
    else:
        rows = "".join(
            f"<tr><td>{html.escape(_label(m, redact))}</td>"
            f"<td class='num'>{v['total']}</td>"
            f"<td class='num'>{v['verified']}</td>"
            f"<td class='num'>{v['alerting']}</td></tr>"
            for m, v in sorted(j["by_machine"].items(), key=lambda kv: -kv[1]["total"]))
        job_block = (
            "<div class='tbl'><table><thead><tr><th>Machine</th><th>Jobs</th>"
            "<th>With a verified artifact</th><th>With a failure route</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")

    # cadences
    c = d["cadences"]
    if "unobservable" in c:
        cad_block = f"<p class='note'>Unobservable: {html.escape(c['unobservable'])}</p>"
    else:
        overdue = c.get("overdue")
        cad_block = (
            f"<div class='figs'>"
            f"<div class='fig'><span class='fig-n {'bad' if overdue else 'good'}'>{overdue if overdue is not None else '—'}</span>"
            f"<span class='fig-l'>cadences overdue</span></div>"
            f"<div class='fig'><span class='fig-n {'warn' if c.get('sunset_past_due') else 'good'}'>{c.get('sunset_past_due', '—')}</span>"
            f"<span class='fig-l'>past their sunset review</span></div>"
            f"<div class='fig'><span class='fig-n warn'>{c.get('no_review_date', '—')}</span>"
            f"<span class='fig-l'>with no review date set</span></div>"
            f"</div>")

    # fleet
    f = d["fleet"]
    if "unobservable" in f:
        fleet_block = (f"<p class='note'>Unobservable from this host: "
                       f"{html.escape(str(f['unobservable'])[:200])}</p>")
    else:
        rows = "".join(
            f"<tr><td>{html.escape(_label(m.get('machine','?'), redact))}</td>"
            f"<td>{_chip('ok','reachable') if m.get('reachable') else _chip('fail','unreachable')}</td>"
            f"<td class='mono small'>{html.escape(str(m.get('core','—')))}</td>"
            f"<td class='mono small'>{html.escape(str(m.get('mcp','—')))}</td>"
            f"<td class='mono small'>{html.escape(str(m.get('org_workspace','—')))}</td>"
            f"<td class='mono small'>{html.escape(str(m.get('python','—')))}</td></tr>"
            for m in f.get("machines", []))
        drift = _drift(f)
        drift_block = ("<div class='drift'><span class='eyebrow'>Version drift</span><ul>"
                       + "".join(f"<li>{html.escape(x)}</li>" for x in drift)
                       + "</ul></div>") if drift else ""
        fleet_block = ("<div class='tbl'><table><thead><tr><th>Machine</th><th>State</th>"
                       "<th>Core</th><th>MCP</th><th>org-workspace</th><th>Python</th>"
                       f"</tr></thead><tbody>{rows}</tbody></table></div>{drift_block}")

    verdict = ("PASS" if passed else "FAIL") if day_known else "UNOBSERVABLE"
    vstate = ("ok" if passed else "fail") if day_known else "unobservable"
    host = _label(d["host"], redact)

    return _PAGE.format(
        generated=html.escape(d["generated"]),
        host=html.escape(host),
        day=html.escape(str(sb.get("day", "—"))),
        verdict=verdict, vstate=vstate,
        streak=streak if streak is not None else "—",
        level=level if level is not None else "—",
        level_meaning=html.escape(LEVELS.get(level, "not computed")),
        rule_rows="".join(rule_rows), source=html.escape(source),
        milestones=_record_block(hist, redact)[0], strip=_record_block(hist, redact)[1],
        principal_rows="".join(p_rows),
        job_total=j.get("total", "—"), job_verified=j.get("verified", "—"),
        job_alerting=j.get("alerting", "—"),
        job_block=job_block, cad_block=cad_block, fleet_block=fleet_block,
    )


_PAGE = """<title>Fleet Reliability</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@600&display=swap">
<style>
:root {{
  --paper:#edeff3; --surface:#fafbfc; --surface-2:#e4e8ee;
  --ink:#131a28; --ink-2:#4e5868; --ink-3:#6f7889;
  --rule:#ccd3dd; --rule-soft:#dde2e9;
  --navy:#1f3455; --brass:#946a1f; --brass-tint:#f2e6cd;
  --teal:#1a5f5c; --teal-tint:#d8e8e6; --brick:#92372a; --brick-tint:#f3ddd9;
  --f-display:"IBM Plex Serif",Georgia,serif;
  --f-body:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper:#0d1118; --surface:#151b25; --surface-2:#1d2532;
    --ink:#e7ebf2; --ink-2:#9aa5b6; --ink-3:#7c8798;
    --rule:#2a3444; --rule-soft:#212a37;
    --navy:#8fabd8; --brass:#d9ab52; --brass-tint:#2e2617;
    --teal:#56b0a8; --teal-tint:#14282a; --brick:#dd7f6e; --brick-tint:#2c1a18;
  }}
}}
:root[data-theme="dark"] {{
  --paper:#0d1118; --surface:#151b25; --surface-2:#1d2532;
  --ink:#e7ebf2; --ink-2:#9aa5b6; --ink-3:#7c8798;
  --rule:#2a3444; --rule-soft:#212a37;
  --navy:#8fabd8; --brass:#d9ab52; --brass-tint:#2e2617;
  --teal:#56b0a8; --teal-tint:#14282a; --brick:#dd7f6e; --brick-tint:#2c1a18;
}}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);font-family:var(--f-body);font-size:15px;
  line-height:1.55;padding-inline:20px;padding-block:0;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin-inline:auto}}
h1,h2{{font-family:var(--f-display);font-weight:600;margin:0;text-wrap:balance}}
p{{margin:0}}
.eyebrow{{font-family:var(--f-mono);font-size:.7rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-3);font-weight:500}}
.mono{{font-family:var(--f-mono)}} .small{{font-size:.82rem}}
header{{border-bottom:2px solid var(--ink);padding-block:36px 22px;margin-bottom:8px}}
header h1{{font-size:clamp(1.8rem,4.4vw,2.5rem);letter-spacing:-.015em;margin:14px 0 10px}}
.meta{{display:flex;flex-wrap:wrap;gap:6px 20px}}
.verdict{{display:flex;flex-wrap:wrap;gap:18px 26px;align-items:flex-start;margin-top:22px}}
.vbig{{font-family:var(--f-mono);font-variant-numeric:tabular-nums;font-size:1.5rem;font-weight:500;line-height:1.1;letter-spacing:-.02em}}
.vbig.ok{{color:var(--teal)}} .vbig.fail{{color:var(--brick)}} .vbig.unobservable{{color:var(--ink-3)}}
.vside{{display:flex;gap:18px 26px;flex-wrap:wrap}}
.fig{{display:flex;flex-direction:column;gap:3px}}
.fig-n{{font-family:var(--f-mono);font-variant-numeric:tabular-nums;font-size:1.5rem;
  font-weight:500;line-height:1.1}}
.fig-n.good{{color:var(--teal)}} .fig-n.warn{{color:var(--brass)}} .fig-n.bad{{color:var(--brick)}}
.fig-l{{font-size:.82rem;color:var(--ink-2)}}
.figs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:18px}}
section{{padding-block:34px;border-top:1px solid var(--rule)}}
section h2{{font-size:1.22rem;margin-bottom:6px}}
.lede{{color:var(--ink-2);font-size:.9rem;max-width:70ch;margin-bottom:18px}}
.tbl{{overflow-x:auto;border:1px solid var(--rule);border-radius:3px;background:var(--surface)}}
table{{border-collapse:collapse;width:100%;font-size:.86rem;min-width:540px}}
th,td{{text-align:left;padding:9px 13px;vertical-align:top;border-bottom:1px solid var(--rule-soft)}}
thead th{{font-family:var(--f-mono);font-size:.66rem;font-weight:600;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3);background:var(--surface-2);border-bottom:1px solid var(--rule)}}
tbody tr:last-child td{{border-bottom:0}}
td.num{{font-family:var(--f-mono);font-variant-numeric:tabular-nums}}
.meaning{{font-size:.78rem;color:var(--ink-3);margin-top:2px}}
.note{{font-size:.8rem;color:var(--ink-2)}}
.miss{{font-family:var(--f-mono);font-size:.72rem;color:var(--brass);margin-top:3px}}
.chip{{font-family:var(--f-mono);font-size:.66rem;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;padding:.2em .48em;border-radius:2px;white-space:nowrap;display:inline-block}}
.chip.ok{{color:var(--teal);background:var(--teal-tint);border:1px solid var(--teal)}}
.chip.fail{{color:var(--brick);background:var(--brick-tint);border:1px solid var(--brick)}}
.chip.unobservable{{color:var(--ink-3);background:transparent;border:1px dashed var(--ink-3)}}
.chip.idle{{color:var(--ink-3);background:transparent;border:1px dotted var(--ink-3)}}
.drift{{margin-top:12px;padding:12px 16px;background:var(--brass-tint);
  border:1px dashed var(--brass);border-radius:3px}}
.drift ul{{margin:6px 0 0;padding-left:1.1em;font-family:var(--f-mono);font-size:.78rem}}
.strip{{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;align-items:stretch}}
.daycell{{flex:1 1 96px;min-width:96px;background:var(--surface);border:1px solid var(--rule);
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
.dn2{{display:block;color:var(--ink-3);margin-top:2px}}
.rulestats{{margin-top:16px}}
.rsrow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:10px;margin-top:8px}}
.rulestat{{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  padding:9px 11px;display:flex;flex-direction:column;gap:1px}}
.rs-k{{font-family:var(--f-mono);font-size:.66rem;color:var(--brass);font-weight:600}}
.rs-n{{font-family:var(--f-mono);font-variant-numeric:tabular-nums;font-size:1.02rem;font-weight:600}}
.rs-l{{font-size:.72rem;color:var(--ink-2)}}
.source{{font-family:var(--f-mono);font-size:.7rem;color:var(--ink-3);margin-top:10px}}
.legend{{margin-top:18px;padding:14px 16px;background:var(--surface);border:1px solid var(--rule);
  border-radius:3px;font-size:.84rem;color:var(--ink-2);display:flex;flex-direction:column;gap:6px}}
footer{{border-top:2px solid var(--ink);padding-block:20px 46px;margin-top:34px;
  font-size:.8rem;color:var(--ink-2)}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style>
<div class="wrap">
<header>
  <div class="meta">
    <span class="eyebrow">Fleet reliability</span>
    <span class="eyebrow">observed from {host}</span>
    <span class="eyebrow">{generated}</span>
  </div>
  <h1>Did the fleet do what it was scheduled to do?</h1>
  <p class="lede">Six conditions, one line per day, a streak, a level. Every condition is a
  file and a rule, so two people running this get the same answer. A condition this host
  cannot see reads <em>unobservable</em> &mdash; never ok.</p>
  <div class="verdict">
    <div class="fig"><span class="vbig {vstate}">{verdict}</span><span class="fig-l">day line for {day}</span></div>
    <div class="vside">
      <div class="fig"><span class="fig-n">{streak}</span><span class="fig-l">day streak</span></div>
      <div class="fig"><span class="fig-n">{level}</span><span class="fig-l">level &mdash; {level_meaning}</span></div>
      <div class="fig"><span class="fig-n">{job_verified}/{job_total}</span><span class="fig-l">jobs with a verified artifact</span></div>
      <div class="fig"><span class="fig-n">{job_alerting}/{job_total}</span><span class="fig-l">jobs with a failure route</span></div>
    </div>
  </div>
</header>

<section>
  <h2>The record</h2>
  <p class="lede">A streak is historical by definition &mdash; one day cannot show it. Bar
  height is the number of conditions failing that day; a full-height day is the worst day
  on record here.</p>
  {milestones}
  {strip}
</section>

<section>
  <h2>The six conditions</h2>
  <p class="lede">A failing condition breaks the streak the same day. Nothing here is a
  judgement call.</p>
  <p class="source">{source}</p>
  <div class="tbl"><table>
    <thead><tr><th>Rule</th><th>Condition</th><th>State</th><th>Detail</th></tr></thead>
    <tbody>{rule_rows}</tbody>
  </table></div>
</section>

<section>
  <h2>Principals</h2>
  <p class="lede">Every actor that writes here, human or agent, with the contracts it owns
  and what it is still missing. An agent with no charter or no declared budget is reported
  as such every day rather than quietly assumed fine.</p>
  <div class="tbl"><table>
    <thead><tr><th>Principal</th><th>Today</th><th>Contracts</th><th>Charter</th>
    <th>Budget</th><th>Memory scope</th><th>Detail</th></tr></thead>
    <tbody>{principal_rows}</tbody>
  </table></div>
</section>

<section>
  <h2>Scheduled work</h2>
  <p class="lede">Every job declares what must be true after it runs and where a failure
  goes. A job without both is a job that can fail silently.</p>
  {job_block}
</section>

<section>
  <h2>Cadences</h2>
  <p class="lede">Recurring duties declared in configuration or prose. A cadence with no
  review date is one nobody has decided to keep.</p>
  {cad_block}
</section>

<section>
  <h2>Fleet</h2>
  <p class="lede">Reachability and versions. Disagreement between machines is called out,
  because a version gap is how a fix that shipped stops applying.</p>
  {fleet_block}
  <div class="legend">
    <span class="eyebrow">Reading this page</span>
    <div><span class="chip ok">ok</span> checked and passing &nbsp;
    <span class="chip fail">fail</span> checked and failing &nbsp;
    <span class="chip unobservable">unobservable</span> could not be checked from here &nbsp;
    <span class="chip idle">idle</span> nothing reported today</div>
    <div><em>Unobservable is not a pass.</em> It means the check needs a different host to
    run it, and the number beside it should not be trusted until it does.</div>
  </div>
</section>

<footer>
  Generated by <span class="mono">.datacore/lib/reliability_dashboard.py</span> from the
  reliability scoreboard, the principals registry, the job manifest, cadence liveness and
  fleet status. Read-only: it computes nothing of its own and writes no state.
</footer>
</div>
"""


# ── entry ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path.home() / ".datacore" / "state" / "reliability-dashboard.html"))
    ap.add_argument("--no-fleet", action="store_true", help="skip the SSH round trip")
    ap.add_argument("--redact", action="store_true", help="replace machine names with role labels")
    ap.add_argument("--json", action="store_true", help="print collected data instead of HTML")
    ap.add_argument("--owner-host", default=OWNER_HOST,
                    help="host whose scoreboard log is the record ('local' to read this machine's)")
    a = ap.parse_args()

    data = collect(skip_fleet=a.no_fleet, owner_host=a.owner_host)
    if a.json:
        print(json.dumps(data, indent=2))
        return 0

    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data, redact=a.redact))
    print(out)

    sb = data["scoreboard"]
    # Exit 1 on a failing day so a job contract can watch this artifact, matching
    # reliability_scoreboard.py's own convention. Unobservable exits 2: not a pass.
    if "unobservable" in sb:
        return 2
    return 0 if sb.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
