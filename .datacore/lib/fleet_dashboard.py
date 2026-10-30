"""The captain's console: one screen, the whole fleet, read live.

    fleet_dashboard.py [--out PATH] [--open]

Reads every place the roster says work runs (fleet_reach), plus the box host's
disk and nightly backup, Winston's app server, and Miles's promise scoreboard,
and writes a single self-contained HTML page in the LCARS style of the
Enterprise-D. Read-only: nothing on any machine is changed.

A value it could not read is shown as UNKNOWN, never as green. The checks this
page summarises have lied before by reporting "ok" over a broken part
(2026-10-30); a silent checker is shown as a fault here, not as a pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_reach  # noqa: E402

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
OUT = ROOT / "2-datacore" / "content" / "fleet-dashboard" / "index.html"
DATACORED = os.environ.get("DATACORED_HEALTH_URL", "http://cos-server:8787/health")

# Who sails where. Roster name -> how the console names it.
VESSELS = [
    ("mac", "MAC", "Owner's workstation", "visitor"),
    ("dayshift", "DAYSHIFT", "Owner's day host", "host"),
    ("box", "BOX", "Cluster host", "host-of-pods"),
    ("box@pod", "WINSTON", "Chief of Staff", "pod"),
    ("hermes", "TRIS", "Research & memory", "pod"),
    ("plur-claw", "DATA", "Operations", "pod"),
    ("nightshift", "MILES", "Engineering", "user"),
]

PROBE = r"""
echo "head=$(git -C ~/Data rev-parse --short HEAD 2>/dev/null)"
echo "runner=$(git -C ~/.datacore/v2-runner rev-parse --short HEAD 2>/dev/null)"
echo "sched=$( ( __SCHED__ ) 2>/dev/null | grep -v '^[[:space:]]*#' | grep -c . )"
L=~/.datacore/state/job_verify.log
if [ -f "$L" ]; then
  echo "jvage=$(( $(date +%s) - $(stat -c %Y "$L" 2>/dev/null || stat -f %m "$L") ))"
  echo "jvlast=$(tail -1 "$L" | cut -c1-160)"
  echo "jverr=$(tail -5 "$L" | grep -m1 -oE 'error: [^(]*' | cut -c1-120)"
  R=~/.datacore/v2-runner; [ -f $R/.datacore/lib/job_verify_history.py ] || R=~/Data
  echo "jv=$(DATACORE_ROOT=$HOME/Data python3 $R/.datacore/lib/job_verify_history.py --since $(date -u +%F) --json 2>/dev/null | tr -d '\n')"
fi
"""

BOX_HOST = r"""
df -P / | tail -1 | awk '{print "disk_pct="$5; print "disk_used="$3; print "disk_size="$2}'
echo "bk_result=$(systemctl show fleet-backup.service -p Result --value 2>/dev/null)"
echo "bk_ran=$(systemctl show fleet-backup.service -p ExecMainExitTimestamp --value 2>/dev/null)"
echo "bk_newest=$(sudo -n find /var/backups/fleet -type f -printf '%TY-%Tm-%Td\n' 2>/dev/null | sort | tail -1)"
echo "bk_why=$(sudo -n journalctl -u fleet-backup.service -n 30 --no-pager -o cat 2>/dev/null | grep -m1 -oE 'SKIPPED, [^;]*')"
"""


def _kv(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.isidentifier():
            out[k] = v.strip()
    return out


def _run(pl, script: str, timeout: int = 120) -> dict:
    try:
        r = fleet_reach.run_at(pl, script, timeout=timeout)
    except Exception as exc:  # unreachable, timeout
        return {"_error": f"{type(exc).__name__}"}
    kv = _kv(r.stdout)
    if r.returncode != 0 and not kv:
        kv["_error"] = (r.stderr or "no output").strip().splitlines()[-1][:90] if r.stderr else "no output"
    return kv


def probe_vessel(key: str) -> dict:
    name = key.split("@")[0]
    try:
        pl = fleet_reach.place(name, host=(name == "box" and "@" not in key))
    except fleet_reach.Unreachable as exc:
        return {"_error": str(exc)[:90]}
    script = PROBE.replace("__SCHED__", fleet_reach.schedule_listing(pl))
    kv = _run(pl, script)
    if name == "box" and "@" not in key:
        kv.update(_run(pl, BOX_HOST, timeout=60))
    if name == "box" and "@" in key:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        kv.update(_run(pl, f"test -d ~/.datacore/cos/briefings/{today} && echo briefing=yes || echo briefing=no", 60))
    if name == "nightshift":
        r = fleet_reach.run_at(pl, "cat ~/.datacore/state/promise-scoreboard/latest.json 2>/dev/null", timeout=60)
        kv["_promises"] = r.stdout
    return kv


def assess(key: str, kind: str, kv: dict) -> dict:
    """One vessel's condition: green / amber / red / unknown, and why."""
    v = {"reachable": "_error" not in kv, "error": kv.get("_error"), "code": kv.get("runner") or kv.get("head") or "?",
         "schedules": int(kv["sched"]) if kv.get("sched", "").isdigit() else None, "faults": [], "checker": "n/a"}
    if not v["reachable"]:
        v.update(status="red", checker="unreachable")
        return v
    if kind == "visitor":
        v.update(status="green", checker="visitor: checks run on arrival")
        return v
    if "jvage" not in kv:
        v.update(status="amber", checker="no job checker here")
        return v
    age = int(kv.get("jvage") or 0)
    last = kv.get("jvlast", "")
    if age > 26 * 3600:
        v.update(status="amber", checker=f"checker silent {age // 3600}h")
        return v
    err = kv.get("jverr") or (last if last.startswith("error:") else "")
    if err:
        v.update(status="red", checker="checker crashes: " + re.sub(r"\s/\S+", "", err[7:80]).strip())
        return v
    try:
        jobs = (json.loads(kv.get("jv") or "{}").get("jobs") or {})
    except ValueError:
        jobs = {}
    v["faults"] = sorted(({"job": j, "runs": d.get("failed_runs", 0), "detail": re.sub(r"/home/[^/]+/", "~/", d.get("last_detail") or "")[:140]}
                          for j, d in jobs.items()), key=lambda x: -x["runs"])
    v["checker"] = "ran today"
    v["status"] = "green" if not v["faults"] else ("amber" if len(v["faults"]) <= 3 else "red")
    return v


def datacored() -> dict:
    try:
        with urllib.request.urlopen(DATACORED, timeout=10) as r:
            d = json.loads(r.read())
    except Exception as exc:
        return {"status": "unreachable", "tools": {}, "error": type(exc).__name__}
    return {"status": d.get("status"), "tools": (d.get("mcp") or {}), "indexed": (d.get("knowledge") or {}).get("indexed_files")}


def promises(raw: str) -> dict:
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    areas: dict = {}
    rows = d.get("promises") or []
    if isinstance(rows, dict):  # {id: status} or {id: {status: ...}}
        rows = [{"id": k, **(v if isinstance(v, dict) else {"status": v})} for k, v in rows.items()]
    for p in rows:
        a = (p.get("id") or "?").split("-")[0]
        st = p.get("status") or p.get("state") or "?"
        areas.setdefault(a, {"green": 0, "red": 0, "could-not-run": 0})
        areas[a][st] = areas[a].get(st, 0) + 1
    return {"date": d.get("date"), "counts": d.get("counts"), "areas": areas}


def stardate(now: datetime) -> str:
    # TNG convention: 41000 in 1987, a thousand units a year.
    day = now.timetuple().tm_yday - 1 + now.hour / 24
    return f"{41000 + (now.year - 1987) * 1000 + day / 365.25 * 1000:.1f}"


def alerts_and_orders(vessels: list, box: dict, dcd: dict, prom: dict) -> tuple[list, list]:
    alerts, orders = [], []
    newest = box.get("bk_newest") or ""
    if box.get("bk_result") and box.get("bk_result") != "success":
        alerts.append(("red", f"SHIELDS DOWN: no agent backup since {newest or 'unknown'}",
                       box.get("bk_why") or "fleet-backup failed"))
        orders.append("Free box disk, then run the fleet backup by hand")
    pct = int((box.get("disk_pct") or "0%").rstrip("%") or 0)
    if pct >= 85:
        need = re.search(r"under (\d+)% free", box.get("bk_why") or "")
        alerts.append(("red" if pct >= 88 else "amber", f"WARP CORE at {pct}%: box disk",
                       f"the backup needs {need.group(1)}% free" if need else "backup headroom unknown"))
    tools = dcd.get("tools") or {}
    down = [k for k in ("datacore", "plur") if tools.get(k) and tools.get(k) != "connected"]
    if dcd.get("status") == "unreachable":
        alerts.append(("red", "Winston's app server unreachable", dcd.get("error") or ""))
    elif down:
        alerts.append(("red", f"Winston's app server has no {' / '.join(down)} tools", "reports ok while disconnected"))
        orders.append("Repoint Winston's tool config and restart his pod")
    for v in vessels:
        if v["checker"].startswith("checker crashes"):
            alerts.append(("red", f"{v['label']}: job checker crashes", v["checker"][16:]))
            orders.append(f"Repair {v['label']}'s machine list")
        elif not v["reachable"]:
            alerts.append(("red", f"{v['label']} unreachable", v.get("error") or ""))
    worst = sorted(((f["runs"], v["label"], f) for v in vessels for f in v["faults"]), key=lambda x: -x[0])
    for _, label, f in worst[:6]:
        alerts.append(("amber", f"{label}: {f['job']}", f["detail"]))
    c = prom.get("counts") or {}
    if c:
        total = sum(c.values()) or 1
        if c.get("green", 0) / total < 0.9:
            orders.append(f"Raise promises kept from {round(100 * c.get('green', 0) / total)}% toward 90%")
    return alerts[:9], orders[:5]


def collect() -> dict:
    with ThreadPoolExecutor(8) as ex:
        raw = dict(zip([k for k, *_ in VESSELS], ex.map(probe_vessel, [k for k, *_ in VESSELS])))
    vessels = []
    for key, label, role, kind in VESSELS:
        v = assess(key, kind, raw[key])
        v.update(key=key, label=label, role=role, kind=kind)
        vessels.append(v)
    box = raw.get("box") or {}
    dcd = datacored()
    prom = promises((raw.get("nightshift") or {}).get("_promises") or "")
    alerts, orders = alerts_and_orders(vessels, box, dcd, prom)
    now = datetime.now(timezone.utc)
    return {
        "generated": now.strftime("%Y-%m-%d %H:%M UTC"), "stardate": stardate(now),
        "vessels": vessels,
        "shields": {k: box.get(k) for k in ("bk_result", "bk_ran", "bk_newest", "bk_why")},
        "warp": {k: box.get(k) for k in ("disk_pct", "disk_used", "disk_size")},
        "briefing": (raw.get("box@pod") or {}).get("briefing"),
        "datacored": dcd, "promises": prom, "alerts": alerts, "orders": orders,
    }


def render(data: dict) -> str:
    return TEMPLATE.replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--from-json", help="render a saved collection instead of probing")
    a = ap.parse_args()
    data = json.loads(Path(a.from_json).read_text()) if a.from_json else collect()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data))
    out.with_suffix(".json").write_text(json.dumps(data, indent=1))
    print(out)
    if a.open:
        subprocess.run(["open", str(out)], check=False)
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Captain's Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#000;--orange:#ff9900;--peach:#ffcc99;--lilac:#cc99cc;--peri:#9999ff;--blue:#99ccff;--red:#ff5555;
--amber:#ffbb33;--ok:#66ccff;--dim:#3a3a55;--text:#ffcc99;--ink:#000}
:root[data-theme="light"]{--bg:#f4efe6;--dim:#cfc6d8;--text:#5a3200;--ink:#000}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:Antonio,"Arial Narrow",sans-serif;overflow:hidden}
body{display:grid;grid-template-columns:clamp(70px,9vw,150px) 1fr;grid-template-rows:auto 1fr auto;gap:.6vh .6vw;
height:100vh;padding:1vh 1vw}
.side{grid-row:1/4;display:flex;flex-direction:column;gap:.6vh}
.side .cap{background:var(--orange);border-radius:3vh 0 0 0;height:9vh}
.side .btn{flex:1;background:var(--lilac);color:var(--ink);display:flex;align-items:flex-end;justify-content:flex-end;
padding:.4vh .6vw;font-size:1.5vh;font-weight:600;letter-spacing:.08em}
.side .btn:nth-child(3){background:var(--peri)}.side .btn:nth-child(4){background:var(--peach)}
.side .btn:nth-child(5){background:var(--blue)}.side .btn:nth-child(6){background:var(--orange)}
.side .foot{background:var(--lilac);border-radius:0 0 0 3vh;height:9vh}
header{display:flex;align-items:center;gap:.8vw}
header .bar{flex:1;height:3vh;background:var(--orange);border-radius:0 1.5vh 1.5vh 0}
header h1{font-size:4.2vh;letter-spacing:.12em;color:var(--orange);white-space:nowrap;font-weight:700}
header .sd{font-size:2.2vh;color:var(--peri);white-space:nowrap}
main{display:grid;grid-template-columns:1.25fr 1fr .9fr;grid-template-rows:1.05fr 1fr;gap:.8vh .8vw;min-height:0}
.panel{border-left:1.1vw solid var(--peri);border-top:.5vh solid var(--peri);border-radius:2vh 0 0 0;padding:.6vh .7vw;
display:flex;flex-direction:column;min-height:0;overflow:hidden}
.panel h2{font-size:2vh;letter-spacing:.14em;color:var(--peri);margin-bottom:.5vh;font-weight:600}
.p-fleet{grid-column:1/3;border-color:var(--orange)}.p-fleet h2{color:var(--orange)}
.p-alert{border-color:var(--red)}.p-alert h2{color:var(--red)}
.p-sens{border-color:var(--lilac)}.p-sens h2{color:var(--lilac)}
.fleet{display:grid;grid-template-columns:repeat(7,1fr);gap:.5vw;flex:1;min-height:0}
.ship{background:#0d0d1a;border-radius:1.4vh;padding:.7vh .5vw;display:flex;flex-direction:column;gap:.35vh;min-height:0;overflow:hidden;
border-top:.9vh solid var(--ok)}
:root[data-theme="light"] .ship{background:#fff8ee}
.ship.amber{border-top-color:var(--amber)}.ship.red{border-top-color:var(--red)}
.ship .nm{font-size:2.6vh;font-weight:700;letter-spacing:.06em;color:var(--peach)}
.ship .rl{font-size:1.35vh;color:var(--peri);text-transform:uppercase;letter-spacing:.06em}
.ship .st{font-size:1.5vh;font-weight:700;letter-spacing:.1em}
.ship.green .st{color:var(--ok)}.ship.amber .st{color:var(--amber)}.ship.red .st{color:var(--red)}
.ship .row{display:flex;justify-content:space-between;font-size:1.4vh;gap:.3vw}
.ship .row span:first-child{color:var(--lilac)}
.ship .faults{font-size:1.25vh;color:var(--amber);overflow:hidden;flex:1;line-height:1.3}
.ship .chk{font-size:1.25vh;color:var(--blue);line-height:1.25}
.blink{animation:b 1.2s ease-in-out infinite alternate}@keyframes b{to{opacity:.35}}
@media (prefers-reduced-motion:reduce){.blink{animation:none}}
.side3{display:flex;flex-direction:column;gap:.8vh;min-height:0}
.side3 .panel{flex:1}
.gauge{display:flex;align-items:center;gap:.8vw;flex:1}
.core{width:3.4vw;min-width:28px;height:100%;max-height:22vh;border:2px solid var(--blue);border-radius:1.6vw;position:relative;overflow:hidden}
.core i{position:absolute;bottom:0;left:0;right:0;background:linear-gradient(0deg,var(--peri),var(--blue))}
.core.hot i{background:linear-gradient(0deg,var(--red),var(--amber))}
.big{font-size:5vh;font-weight:700;line-height:1}.lbl{font-size:1.45vh;color:var(--lilac);letter-spacing:.06em}
.alerts{list-style:none;display:flex;flex-direction:column;gap:.45vh;overflow:hidden;flex:1}
.alerts li{display:grid;grid-template-columns:.7vw 1fr;gap:.5vw;font-size:1.55vh;line-height:1.2}
.alerts li b{display:block;border-radius:.3vw;background:var(--amber)}.alerts li.red b{background:var(--red)}
.alerts li small{display:block;color:var(--peri);font-size:1.25vh;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bars{display:flex;flex-direction:column;gap:.38vh;flex:1;min-height:0;overflow:hidden}
.bar-row{display:grid;grid-template-columns:3.2vw 1fr 4.2vw;align-items:center;gap:.4vw;font-size:1.35vh}
.track{display:flex;height:1.35vh;border-radius:.7vh;overflow:hidden;background:var(--dim)}
.track .g{background:var(--ok)}.track .r{background:var(--red)}.track .c{background:var(--dim)}
.sum{display:flex;gap:1vw;font-size:1.6vh;margin-bottom:.5vh}.sum b{font-size:2.6vh}
.kv{display:flex;justify-content:space-between;font-size:1.6vh;padding:.15vh 0;border-bottom:1px dotted var(--dim)}
.kv span:last-child{font-weight:600}.okc{color:var(--ok)}.badc{color:var(--red)}.amc{color:var(--amber)}
.orders{list-style:none;font-size:1.55vh;display:flex;flex-direction:column;gap:.4vh;margin-top:.4vh}
.orders li::before{content:"▸ ";color:var(--orange)}
footer{display:flex;align-items:center;gap:.8vw}
footer .bar{flex:1;height:2.2vh;background:var(--lilac);border-radius:0 1.1vh 1.1vh 0}
footer .q{font-size:2.4vh;color:var(--orange);letter-spacing:.1em;white-space:nowrap}
footer .t{font-size:1.4vh;color:var(--peri);white-space:nowrap}
@media (max-width:760px){html,body{overflow:auto}body{grid-template-columns:1fr;height:auto;padding:12px 16px}
.side{display:none}main{grid-template-columns:1fr;grid-template-rows:auto}.p-fleet{grid-column:auto}
.fleet{grid-template-columns:repeat(2,1fr)}header h1{font-size:22px}}
</style></head><body>
<nav class="side" aria-hidden="true"><div class="cap"></div><div class="btn" id="sd1"></div><div class="btn">FLEET OPS</div>
<div class="btn">LCARS 47</div><div class="btn">NCC-1701-D</div><div class="btn">PICARD-4-7-ALPHA</div><div class="foot"></div></nav>
<header><h1>CAPTAIN'S CONSOLE · DATACORE FLEET</h1><div class="bar"></div><div class="sd" id="sd"></div></header>
<main>
 <section class="panel p-fleet"><h2>FLEET REGISTRY: VESSELS AND CREW</h2><div class="fleet" id="fleet"></div></section>
 <div class="side3" style="grid-row:1/3;grid-column:3">
  <section class="panel"><h2>SHIELDS: NIGHTLY BACKUP</h2><div id="shields"></div></section>
  <section class="panel"><h2>WARP CORE: BOX DISK</h2><div class="gauge" id="warp"></div></section>
  <section class="panel"><h2>SUBSPACE COMMS</h2><div id="comms"></div><ul class="orders" id="orders"></ul></section>
 </div>
 <section class="panel p-sens"><h2>SENSOR GRID: PROMISES KEPT</h2><div class="sum" id="psum"></div><div class="bars" id="bars"></div></section>
 <section class="panel p-alert"><h2 id="ah">ALERT STATUS</h2><ul class="alerts" id="alerts"></ul></section>
</main>
<footer><div class="q">“MAKE IT SO.”</div><div class="bar"></div><div class="t" id="gen"></div>
<div class="t">Dedicated to Sir Patrick Stewart · Jean-Luc Picard</div></footer>
<script>
const D=/*DATA*/null;
const $=id=>document.getElementById(id), esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
try{const t=localStorage.getItem("lcars-theme");if(t)document.documentElement.dataset.theme=t}catch(e){}
$("sd").textContent="STARDATE "+D.stardate; $("sd1").textContent="SD "+D.stardate; $("gen").textContent="SENSOR SWEEP "+D.generated;
const word={green:"NOMINAL",amber:"CAUTION",red:"RED ALERT",unknown:"UNKNOWN"};
$("fleet").innerHTML=D.vessels.map(v=>`<div class="ship ${v.status}" title="${esc(v.error||"")}">
<div class="nm">${esc(v.label)}</div><div class="rl">${esc(v.role)}</div>
<div class="st ${v.status==="red"?"blink":""}">${word[v.status]||"UNKNOWN"}</div>
<div class="row"><span>CODE</span><span>${esc(v.code)}</span></div>
<div class="row"><span>SCHEDULES</span><span>${v.schedules??"?"}</span></div>
<div class="row"><span>FAULTS</span><span>${v.faults.length}</span></div>
<div class="chk">${esc(v.checker)}</div>
<div class="faults">${v.faults.slice(0,4).map(f=>"▪ "+esc(f.job.replace(/^(box|nightshift|hermes|plur-claw|bridge)-/,""))).join("<br>")}</div></div>`).join("");
const S=D.shields||{}, ok=S.bk_result==="success";
$("shields").innerHTML=`<div class="big ${ok?"okc":"badc blink"}">${ok?"UP":"DOWN"}</div>
<div class="kv"><span>Last good copy</span><span>${esc(S.bk_newest||"unknown")}</span></div>
<div class="kv"><span>Last attempt</span><span>${esc((S.bk_ran||"unknown").replace(/^\w+ /,"").replace(/:\d\d UTC/," UTC"))}</span></div>
<div class="lbl" style="margin-top:.4vh">${esc(S.bk_why||"")}</div>`;
const W=D.warp||{}, pct=parseInt(W.disk_pct)||0, gb=k=>W[k]?(W[k]/1048576).toFixed(0):"?";
$("warp").innerHTML=`<div class="core ${pct>=85?"hot":""}"><i style="height:${pct}%"></i></div>
<div><div class="big ${pct>=88?"badc":pct>=80?"amc":"okc"}">${pct||"?"}%</div><div class="lbl">${gb("disk_used")} of ${gb("disk_size")} GB</div>
<div class="lbl">${esc(((D.shields||{}).bk_why||"").match(/under \d+% free/)?"backup needs "+(D.shields.bk_why.match(/under (\d+)%/)[1])+"% free":"")}</div></div>`;
const C=D.datacored||{}, tl=C.tools||{};
const kv=(k,v,c)=>`<div class="kv"><span>${k}</span><span class="${c}">${esc(v)}</span></div>`;
$("comms").innerHTML=kv("Morning briefing",D.briefing==="yes"?"DELIVERED":"NOT FOUND",D.briefing==="yes"?"okc":"badc")
+kv("Winston app server",String(C.status||"?").toUpperCase(),C.status==="ok"?"okc":"badc")
+["datacore","plur"].map(k=>kv("· "+k+" link",String(tl[k]||"?").toUpperCase(),tl[k]==="connected"?"okc":"badc")).join("");
$("orders").innerHTML=(D.orders||[]).map(o=>`<li>${esc(o)}</li>`).join("");
const P=D.promises||{}, c=P.counts||{}, tot=(c.green||0)+(c.red||0)+(c["could-not-run"]||0)||1;
$("psum").innerHTML=`<span><b class="okc">${c.green??"?"}</b> kept</span><span><b class="badc">${c.red??"?"}</b> broken</span>
<span><b>${c["could-not-run"]??"?"}</b> untested</span><span><b class="${(c.green||0)/tot>=.9?"okc":"amc"}">${Math.round(100*(c.green||0)/tot)}%</b> proven</span>`;
const names={AGT:"Agents",AUD:"Audit",CAD:"Cadence",CAP:"Capture",DAY:"Day",INB:"Inbox",INS:"Install",KNW:"Knowledge",LED:"Ledger",
MEM:"Memory",MOD:"Modules",MSG:"Messages",NS:"Night",OPS:"Ops",SPC:"Spaces",SYN:"Sync",TSK:"Tasks"};
$("bars").innerHTML=Object.entries(P.areas||{}).sort((a,b)=>(b[1].red||0)-(a[1].red||0)).map(([a,v])=>{const n=(v.green||0)+(v.red||0)+(v["could-not-run"]||0)||1;
return `<div class="bar-row" title="${a}"><span>${names[a]||a}</span><div class="track"><div class="g" style="width:${100*(v.green||0)/n}%"></div>
<div class="r" style="width:${100*(v.red||0)/n}%"></div><div class="c" style="width:${100*(v["could-not-run"]||0)/n}%"></div></div>
<span>${v.green||0}/${n}</span></div>`}).join("");
const A=D.alerts||[], red=A.some(a=>a[0]==="red");
$("ah").innerHTML=red?'<span class="blink">RED ALERT</span>':A.length?"YELLOW ALERT":"ALL CLEAR";
$("alerts").innerHTML=A.map(([s,t,d])=>`<li class="${s}"><b></b><div>${esc(t)}<small>${esc(d)}</small></div></li>`).join("")||"<li>No alerts. Engage.</li>";
</script></body></html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
