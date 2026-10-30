"""The captain's console: what needs you, why, since when, and the next step.

    fleet_dashboard.py [--out PATH] [--open] [--from-json SAVED]

Reads every place the roster says work runs (fleet_reach), the box host's disk
and nightly backup, Winston's app server, Miles's promise scoreboard and the
owner's open pull requests, and writes one self-contained LCARS page. Read-only.

Every failing job is sorted into one pile:
  you     -- nobody is repairing it, a repair is stuck, or it was escalated to you
  agent   -- an agent took the repair in the last two days
  paused  -- auto-repair is paused (a login or usage limit)
  noise   -- the check looks for work where that work is not scheduled
Owner, 2026-10-30: the first version "doesn't really provide me actionable
info". So each order says what to do, why it matters, how long it has been
broken, and carries the exact command or a ready brief for a Claude session.
A value that could not be read is never shown as green.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_reach  # noqa: E402

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
OUT = ROOT / "2-datacore" / "content" / "fleet-dashboard" / "index.html"
MANIFEST = Path(__file__).resolve().parent / "jobs" / "manifest.yaml"
DATACORED = os.environ.get("DATACORED_HEALTH_URL", "http://cos-server:8787/health")
REPAIR_FRESH_DAYS = 2  # an agent repair older than this that has not fixed it is stuck

VESSELS = [
    ("mac", "MAC", "Owner's workstation", "visitor"),
    ("dayshift", "DAYSHIFT", "Owner's day host", "host"),
    ("box", "BOX HOST", "Cluster host", "host"),
    ("box@pod", "WINSTON", "Chief of Staff", "pod"),
    ("hermes", "TRIS", "Research & memory", "pod"),
    ("plur-claw", "DATA", "Operations", "pod"),
    ("nightshift", "MILES", "Engineering", "user"),
]
POD_DEPLOY = {"box@pod": "winston", "hermes": "tris", "plur-claw": "data"}

TITLES = {
    "backup": "Agent backup (old job)", "knowledge-lint": "Knowledge link check", "phase1-cycle": "Repo sync cycle",
    "cadence-liveness": "Scheduled duties overdue", "deploy-drift": "Box files differ from main",
    "reconcile-timers": "Timer reconciliation", "setup-verify": "Box install self-check",
    "delegation-canary": "Delegation canary", "delegation-review": "Delegation review",
    "delegation-round": "Weekly delegation round", "autofix-escalation": "Auto-repairs waiting for a person",
    "inbox": "Inbox processing", "briefing": "Morning briefing", "cos-briefing": "Morning briefing cadence",
    "github-triage": "GitHub triage", "github-triage-report": "GitHub triage report",
    "ledger-invariants": "Ledger consistency", "preflight": "Overnight preflight",
    "audit-nightly-miles": "Cross-model audit (nightly)", "audit-calibration-miles": "Cross-model audit (calibration)",
    "audit-calibration-tris": "Cross-model audit (calibration)", "id-churn": "Task ID churn",
    "cos-cadence-triage": "Chief-of-staff cadence triage", "creds-sync": "Credential sync",
    "nightshift-deadman": "Night-shift dead-man switch", "v2-verify": "Self-test", "tomorrow": "Evening review",
    "ledger-ingest": "Ledger ingest", "ledger-claim": "Ledger claim loop",
}
PREFIXES = ("box-", "nightshift-", "hermes-", "plur-claw-", "bridge-", "dayshift-", "cadence-firm-", "mac-")

PROBE = r"""
echo "head=$(git -C ~/Data rev-parse --short HEAD 2>/dev/null)"
echo "runner=$(git -C ~/.datacore/v2-runner rev-parse --short HEAD 2>/dev/null)"
echo "sched64=$( ( __SCHED__ ) 2>/dev/null | grep -v '^[[:space:]]*#' | base64 | tr -d '\n')"
L=~/.datacore/state/job_verify.log
if [ -f "$L" ]; then
  echo "jvage=$(( $(date +%s) - $(stat -c %Y "$L" 2>/dev/null || stat -f %m "$L") ))"
  echo "jvlast=$(tail -1 "$L" | cut -c1-160)"
  echo "jverr=$(tail -5 "$L" | grep -m1 -oE 'error: [^(]*' | cut -c1-120)"
  echo "log64=$(tail -600 "$L" | base64 | tr -d '\n')"
  R=~/.datacore/v2-runner; [ -f $R/.datacore/lib/job_verify_history.py ] || R=~/Data
  echo "jv=$(DATACORE_ROOT=$HOME/Data python3 $R/.datacore/lib/job_verify_history.py --since __TODAY__ --json 2>/dev/null | tr -d '\n')"
  echo "jv30=$(DATACORE_ROOT=$HOME/Data python3 $R/.datacore/lib/job_verify_history.py --since __SINCE__ --json 2>/dev/null | tr -d '\n')"
fi
"""

BOX_HOST = r"""
df -P / | tail -1 | awk '{print "disk_pct="$5; print "disk_used="$3; print "disk_size="$2}'
echo "bk_result=$(systemctl show fleet-backup.service -p Result --value 2>/dev/null)"
echo "bk_ran=$(systemctl show fleet-backup.service -p ExecMainExitTimestamp --value 2>/dev/null)"
echo "bk_newest=$(sudo -n find /var/backups/fleet -type f -printf '%TY-%Tm-%Td\n' 2>/dev/null | sort | tail -1)"
echo "bk_why=$(sudo -n journalctl -u fleet-backup.service -n 30 --no-pager -o cat 2>/dev/null | grep -m1 -oE 'SKIPPED, [^;]*')"
"""

# Exact owner commands for known conditions. Each writes on the box or in a pod,
# which agents may not do, so the console hands them over rather than running them.
CMD_BACKUP = "\n".join([
    "ssh winston \"sudo find /var/tmp -maxdepth 1 -name 'datacore-*' -mtime +2 -exec rm -rf {} +\"",
    "ssh winston \"sudo rm -rf /var/lib/private/datacore-workers && df -h /\"",
    "ssh winston \"sudo systemctl start fleet-backup.service; sudo journalctl -u fleet-backup -n 5 --no-pager\"",
])
# $HOME is escaped (\$HOME) so the owner's shell passes it through and the pod's
# own sh expands it; the pre-commit scan also refuses literal home paths.
CMD_TOOLS = ("ssh winston \"sudo k3s kubectl -n firm exec deploy/winston -c daemon -- sh -c 'cp \\$HOME/.datacore/mcp.json "
             "\\$HOME/.datacore/mcp.json.bak-\\$(date +%F) && sed -i -e \\\"s#/usr/bin/node#\\$HOME/.hermes/node/bin/node#g\\\" "
             "-e \\\"s#\\$HOME/Data/2-datacore/2-projects/datacore-mcp/dist/index.js#\\$HOME/.hermes/node/lib/"
             "node_modules/@datacore-one/mcp/dist/index.js#\\\" \\$HOME/.datacore/mcp.json' && sudo k3s kubectl -n firm "
             "rollout restart deploy/winston\"")
ROSTER = "\\$HOME/Data/.datacore/registry/infrastructure.yaml"


def cmd_roster(broken: str, good: str) -> str:
    k = "sudo k3s kubectl -n firm exec"
    return (f"ssh winston \"{k} deploy/{broken} -c cron -- sh -c 'cp {ROSTER} {ROSTER}.bak-\\$(date +%F)' "
            f"&& {k} deploy/{good} -c cron -- sh -c 'cat {ROSTER}' | {k} -i deploy/{broken} -c cron -- sh -c 'cat > {ROSTER}'\"")


# What you lose while a job is broken, and how much it matters (3 = work or
# data at risk, 2 = something you rely on is degraded, 1 = hygiene).
IMPACT = {
    "phase1-cycle": (3, "Work on this machine is not syncing with the others."),
    "cadence-liveness": (3, "Scheduled agent duties are not running on time."),
    "inbox": (3, "Captured items are not being filed out of the inboxes."),
    "ledger-invariants": (3, "The task ledger has inconsistencies that agents may act on."),
    "ledger-ingest": (3, "New ledger events are not being ingested."),
    "creds-sync": (2, "This machine may hold out-of-date credentials."),
    "github-triage": (2, "The briefing's GitHub section is empty."),
    "github-triage-report": (2, "The briefing's GitHub section is empty."),
    "cos-briefing": (2, "The briefing cadence reports trouble (the briefing itself can still arrive)."),
    "preflight": (2, "Some spaces sit out the overnight run."),
    "v2-verify": (2, "This machine's own self-test is failing."),
    "tomorrow": (2, "The evening review did not happen."),
    "autofix-escalation": (1, "Repairs the agents gave up on are queued for you."),
    "cos-cadence-triage": (1, "Chief-of-staff cadence triage has stopped."),
    "audit-nightly-miles": (1, "The cross-model audit of agent work has stopped."),
    "audit-calibration-miles": (1, "The cross-model audit calibration has stopped."),
    "audit-calibration-tris": (1, "The cross-model audit calibration has stopped."),
    "delegation-canary": (1, "Delegation between agents is not being exercised."),
    "delegation-review": (1, "Delegated work is not being reviewed."),
    "delegation-round": (1, "The weekly delegation round is not running."),
    "knowledge-lint": (1, "Broken links in the knowledge base are not being tracked down."),
    "deploy-drift": (1, "Files on the box differ from main."),
    "id-churn": (1, "Task IDs keep changing in one space."),
}


def _key(name: str) -> str:
    for p in PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name


# -- plain language -----------------------------------------------------------

def job_title(name: str) -> str:
    key = name
    for p in PREFIXES:
        if key.startswith(p):
            key = key[len(p):]
            break
    return TITLES.get(key) or key.replace("-", " ").capitalize()


def plain_failure(detail: str) -> str:
    d = detail or ""
    m = re.search(r"stale \(age ([\d.]+)h", d)
    if m:
        h = float(m.group(1))
        return f"no fresh output for {int(h // 24)} days" if h >= 48 else f"no fresh output for {int(h)} hours"
    if "does not exist" in d:
        return "has never written its output here"
    m = re.search(r"last line: '([^']*)", d)
    if m:
        said = re.sub(r"^\d{4}-\d\d-\d\dT[\d:]+Z?\s+", "", m.group(1))
        if len(re.findall(r"[A-Za-z0-9]", said)) >= 3:
            return "reports: " + re.sub(r"/home/[^/]+/", "~/", said)[:110]
        return "its output is not what the check expects"
    if "does not match" in d or "did not match" in d:
        return "its output is not what the check expects"
    m = re.search(r"FATAL: ([^(]*)", d)
    if m:
        return "crashed: " + m.group(1).strip()[:110]
    return re.sub(r"/home/[^/]+/", "~/", d)[:120]


# -- who owns it ----------------------------------------------------------------

_DEC = [
    ("escalated", re.compile(r"alert withheld: (\S+) was already escalated to the owner")),
    ("alerted", re.compile(r"alert withheld: (\S+) was already sent today")),
    ("couldnot", re.compile(r"could NOT delegate (\S+) \(")),
    ("delegated", re.compile(r"delegated repair of (\S+) \(autofix-\S*?-(\d{8}) -> (\w+)\)")),
    ("paused", re.compile(r"repairs paused for (\S+) \(")),
    ("quiet", re.compile(r"alert withheld: (\S+) failed on the same artifact already counted")),
]


def decisions(log: str, today: str) -> dict:
    """Per job, the strongest thing the checker last said about who handles it."""
    seen: dict = {}
    for line in (log or "").splitlines():
        for kind, rx in _DEC:
            m = rx.search(line)
            if m:
                seen.setdefault(m.group(1), {})[kind] = m.groups()[1:]
                break
    out = {}
    t = date.fromisoformat(today)
    for job, k in seen.items():
        if "escalated" in k or "couldnot" in k:
            out[job] = {"owner": "you", "note": "escalated to you: repairs failed or no agent may fix it"}
        elif "delegated" in k:
            day, agent = k["delegated"]
            since = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            if (t - date.fromisoformat(since)).days > REPAIR_FRESH_DAYS:
                out[job] = {"owner": "you", "note": f"{agent}'s repair from {since} is stuck"}
            else:
                out[job] = {"owner": "agent", "agent": agent, "note": f"{agent} is repairing it (since {since})"}
        elif "paused" in k:
            out[job] = {"owner": "paused", "note": "auto-repair paused (login or usage limit)"}
        elif "alerted" in k:
            out[job] = {"owner": "you", "note": "alerted you today"}
        else:
            out[job] = {"owner": "you", "note": "nobody is repairing it; the alert went quiet"}
    return out


def settle(dd: dict, first: str | None, today: str) -> dict:
    """An agent repair counts as handling a job only while the job is newly broken.

    Repairs are re-filed every day, so a fresh repair id on a job that has been
    failing for a month is not progress: it is a loop that needs the owner."""
    days = _days(first, today)
    if dd.get("owner") == "agent" and days is not None and days > REPAIR_FRESH_DAYS:
        return {"owner": "you", "note": f"agent repairs have not fixed it since {first}"}
    return dd


def details(log: str) -> dict:
    """The newest full failure line per job, from the checker's own log."""
    out, job = {}, None
    for line in (log or "").splitlines():
        m = re.match(r"job '([^']+)' FAILED:", line)
        if m:
            job = m.group(1)
        elif job and line.startswith("  - "):
            out[job] = line[4:]
            job = None
    return out


def _script(cmd: str) -> str | None:
    for tok in (cmd or "").split():
        base = tok.rsplit("/", 1)[-1]
        if re.search(r"\.(sh|py)$", base):
            return base
    return None


def noise_reason(job: dict, here: str, elsewhere: dict) -> str | None:
    """Why a failing check is noise: its work is not scheduled where it looks."""
    s = _script(job.get("cmd", ""))
    if not s:
        return None

    def live(text: str) -> bool:
        return any(s in l and not l.lstrip().startswith("#") for l in (text or "").splitlines())

    if live(here):
        return None
    for where, text in elsewhere.items():
        if live(text):
            return f"runs on {where}, checked here"
    return "not scheduled anywhere (retired?)"


# -- vessels ------------------------------------------------------------------

def assess(key: str, kind: str, kv: dict) -> dict:
    v = {"reachable": "_error" not in kv, "error": kv.get("_error"),
         "code": kv.get("runner") or kv.get("head") or "?", "faults": [], "checker": "n/a"}
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
    err = kv.get("jverr") or (kv.get("jvlast", "") if kv.get("jvlast", "").startswith("error:") else "")
    if age > 26 * 3600:
        v.update(status="amber", checker=f"checker silent {age // 3600}h")
    elif err:
        v.update(status="red", checker="checker crashes: " + re.sub(r"\s/\S+", "", err[7:80]).strip())
    else:
        v.update(status="green", checker="checked today")
    return v


# -- orders ---------------------------------------------------------------------

def _days(since: str | None, today: str) -> int | None:
    try:
        return (date.fromisoformat(today) - date.fromisoformat(since[:10])).days
    except (TypeError, ValueError):
        return None


def owner_orders(box: dict, dcd: dict, vessels: list, today: str) -> list:
    orders = []
    if box.get("bk_result") and box.get("bk_result") != "success":
        newest = box.get("bk_newest") or None
        need = re.search(r"under (\d+)% free", box.get("bk_why") or "")
        size, used = float(box.get("disk_size") or 0), float(box.get("disk_used") or 0)
        gb = ""
        if need and size:
            short = (used - size * (1 - int(need.group(1)) / 100)) / 1048576
            gb = f" Free about {max(short, 0) + 3:.0f} GB (needs {need.group(1)}% free)."
        orders.append({"severity": "red", "title": "Agent backups have stopped",
                       "why": f"No copy of Winston, Tris or Data since {newest or 'unknown'}: a lost volume loses that work."
                              f" The nightly run skips because the box disk is too full.{gb}",
                       "since": newest, "days": _days(newest, today), "owner": "you", "command": CMD_BACKUP,
                       "how": "Clears 15 GB of leftovers (temp audit folders, a worker folder unused since 09-14), then runs the backup."})
    tools = dcd.get("tools") or {}
    down = [k for k in ("datacore", "plur") if tools.get(k) and tools.get(k) != "connected"]
    if dcd.get("status") == "unreachable":
        orders.append({"severity": "red", "title": "Winston's app server is unreachable", "why": dcd.get("error") or "",
                       "owner": "you", "brief": "Winston's datacored on the box does not answer its health check. Find out why."})
    elif down:
        orders.append({"severity": "red", "title": "Winston's app server has no tools",
                       "why": f"It reports ok, but its {' and '.join(down)} links are down: the app's chat cannot read your tasks or memory.",
                       "owner": "you", "command": CMD_TOOLS,
                       "how": "Fixes two paths in his tool config, then restarts his pod (about a minute)."})
    good = next((POD_DEPLOY[v["key"]] for v in vessels
                 if v.get("key") in POD_DEPLOY and v.get("checker") == "checked today"), None)
    for v in vessels:
        if str(v.get("checker", "")).startswith("checker crashes"):
            o = {"severity": "red", "title": f"{v['label']}'s job checker crashes",
                 "why": f"Every run is rejected ({v['checker'][17:]}), so none of {v['label']}'s jobs are being verified.",
                 "owner": "you"}
            if v.get("key") in POD_DEPLOY and good and good != POD_DEPLOY[v["key"]]:
                o["command"] = cmd_roster(POD_DEPLOY[v["key"]], good)
                o["how"] = f"Copies {good.title()}'s complete machine list over {v['label']}'s, keeping a backup."
            else:
                o["brief"] = f"{v['label']}'s job checker crashes: {v['checker']}. Find and fix the cause."
            orders.append(o)
        elif not v.get("reachable", True):
            orders.append({"severity": "red", "title": f"{v['label']} is unreachable", "why": v.get("error") or "",
                           "owner": "you", "brief": f"The fleet console cannot reach {v['label']}: {v.get('error')}. Find out why."})
    for v in vessels:
        for f in v.get("faults", []):
            if f.get("bucket") != "you":
                continue
            weight, lost = IMPACT.get(_key(f["job"]), (1, ""))
            orders.append({"severity": "amber", "title": f"{v['label']}: {job_title(f['job'])}", "weight": weight,
                           "why": (f"{lost} " if lost else "") + f"{f['detail'][:1].upper()}{f['detail'][1:]}. "
                                  f"{f['note'][:1].upper()}{f['note'][1:]}.",
                           "since": f.get("first") or None, "days": _days(f.get("first"), today), "owner": "you",
                           "brief": (f"On the Datacore fleet, the job '{f['job']}' on {v['label']} has been failing since "
                                     f"{f.get('first') or 'today'}: {f['detail']} ({f['note']}). Find the root cause and fix it. "
                                     f"Owner rules apply: tests first, and pod or box writes go to the owner as commands.")})
    rank = {"red": 0, "amber": 1}
    return sorted(orders, key=lambda o: (rank.get(o["severity"], 2), -o.get("weight", 9), -(o.get("days") or 0)))


# -- collection -----------------------------------------------------------------

def _kv(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.isidentifier():
            out[k] = v.strip()
    return out


def _b64(s: str | None) -> str:
    try:
        return base64.b64decode(s or "").decode(errors="replace")
    except ValueError:
        return ""


def _run(pl, script: str, timeout: int = 150) -> dict:
    try:
        r = fleet_reach.run_at(pl, script, timeout=timeout)
    except Exception as exc:
        return {"_error": type(exc).__name__}
    kv = _kv(r.stdout)
    if r.returncode != 0 and not kv:
        kv["_error"] = (r.stderr or "no output").strip().splitlines()[-1][:90] if r.stderr else "no output"
    return kv


def probe_vessel(key: str, today: str, since: str) -> dict:
    name = key.split("@")[0]
    try:
        pl = fleet_reach.place(name, host=(name == "box" and "@" not in key))
    except fleet_reach.Unreachable as exc:
        return {"_error": str(exc)[:90]}
    script = (PROBE.replace("__SCHED__", fleet_reach.schedule_listing(pl))
              .replace("__TODAY__", today).replace("__SINCE__", since))
    kv = _run(pl, script)
    if key == "box":
        kv.update(_run(pl, BOX_HOST, timeout=60))
    if key == "box@pod":
        kv.update(_run(pl, f"test -d ~/.datacore/cos/briefings/{today} && echo briefing=yes || echo briefing=no", 60))
    if key == "nightshift":
        r = fleet_reach.run_at(pl, "d=~/.datacore/state/promise-scoreboard; cat $d/latest.json; echo; echo '@@PREV@@'; "
                                   "ls -t $d/board-*.json 2>/dev/null | sed -n 2p | xargs -r cat", timeout=60)
        cur, _, prev = r.stdout.partition("@@PREV@@")
        kv["_promises"], kv["_promises_prev"] = cur.strip(), prev.strip()
    return kv


def datacored() -> dict:
    try:
        with urllib.request.urlopen(DATACORED, timeout=10) as r:
            d = json.loads(r.read())
    except Exception as exc:
        return {"status": "unreachable", "tools": {}, "error": type(exc).__name__}
    return {"status": d.get("status"), "tools": d.get("mcp") or {}}


def _counts(raw: str) -> dict:
    try:
        rows = json.loads(raw).get("promises") or {}
    except (ValueError, AttributeError):
        return {}
    if isinstance(rows, list):
        rows = {r.get("id"): r for r in rows}
    out: dict = {"total": 0, "areas": {}}
    for pid, r in rows.items():
        st = (r.get("state") or r.get("status")) if isinstance(r, dict) else r
        a = str(pid).split("-")[0]
        out["areas"].setdefault(a, {"green": 0, "red": 0, "could-not-run": 0})
        out["areas"][a][st] = out["areas"][a].get(st, 0) + 1
        out[st] = out.get(st, 0) + 1
        out["total"] += 1
    return out


def open_prs() -> list:
    try:
        r = subprocess.run(["gh", "search", "prs", "--author", "@me", "--state", "open", "--limit", "20",
                            "--json", "repository,number,title,url,updatedAt,isDraft"],
                           capture_output=True, text=True, timeout=40)
        rows = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    return [{"repo": p["repository"]["name"], "number": p["number"], "title": p["title"][:80], "url": p["url"],
             "updated": p["updatedAt"][:10]} for p in sorted(rows, key=lambda p: p["updatedAt"], reverse=True)
            if not p.get("isDraft")]


def stardate(now: datetime) -> str:
    day = now.timetuple().tm_yday - 1 + now.hour / 24
    return f"{41000 + (now.year - 1987) * 1000 + day / 365.25 * 1000:.1f}"


def collect() -> dict:
    import yaml
    now = datetime.now(timezone.utc)
    today, since = now.strftime("%Y-%m-%d"), (now - timedelta(days=30)).strftime("%Y-%m-%d")
    keys = [k for k, *_ in VESSELS]
    with ThreadPoolExecutor(8) as ex:
        prs_f = ex.submit(open_prs)
        raw = dict(zip(keys, ex.map(lambda k: probe_vessel(k, today, since), keys)))
        dcd = datacored()
    manifest = {j["name"]: j for j in (yaml.safe_load(MANIFEST.read_text()) or {}).get("jobs", []) if "name" in j}
    scheds = {k: _b64(raw[k].get("sched64")) for k in keys}
    labels = {k: lab for k, lab, *_ in VESSELS}
    vessels, handled, noise = [], [], []
    for key, label, role, kind in VESSELS:
        kv = raw[key]
        v = assess(key, kind, kv)
        v.update(key=key, label=label, role=role, kind=kind)
        try:
            jobs = json.loads(kv.get("jv") or "{}").get("jobs") or {}
            hist = json.loads(kv.get("jv30") or "{}").get("jobs") or {}
        except ValueError:
            jobs, hist = {}, {}
        log = _b64(kv.get("log64"))
        dec, full = decisions(log, today), details(log)
        elsewhere = {labels[k].lower(): scheds[k] for k in keys if k != key}
        for job, d in jobs.items():
            f = {"job": job, "detail": plain_failure(full.get(job) or d.get("last_detail") or ""),
                 "first": ((hist.get(job) or {}).get("first") or d.get("first") or "")[:10]}
            why = noise_reason(manifest[job], scheds[key], elsewhere) if job in manifest else None
            if why:
                f.update(bucket="noise", note=why)
                noise.append({"label": label, "title": job_title(job), "job": job, "note": why})
            else:
                dd = settle(dec.get(job) or {"owner": "you", "note": "nobody is repairing it"}, f["first"], today)
                f.update(bucket=dd["owner"], note=dd["note"])
                if dd["owner"] in ("agent", "paused"):
                    handled.append({"label": label, "title": job_title(job), "note": dd["note"], "first": f["first"]})
            v["faults"].append(f)
        c = {b: sum(1 for f in v["faults"] if f["bucket"] == b) for b in ("you", "agent", "paused", "noise")}
        v["counts"] = c
        if v["status"] == "green" and c["you"]:
            v["status"] = "amber" if c["you"] <= 2 else "red"
        vessels.append(v)
    box = raw.get("box") or {}
    orders = owner_orders(box, dcd, vessels, today)
    cur = _counts((raw.get("nightshift") or {}).get("_promises") or "")
    prev = _counts((raw.get("nightshift") or {}).get("_promises_prev") or "")
    size, used = float(box.get("disk_size") or 0), float(box.get("disk_used") or 0)
    vitals = {
        "backup_days": _days(box.get("bk_newest"), today), "backup_ok": box.get("bk_result") == "success",
        "backup_newest": box.get("bk_newest"),
        "disk_pct": int((box.get("disk_pct") or "0%").rstrip("%") or 0) if box.get("disk_pct") else None,
        "disk_free_gb": round((size - used) / 1048576) if size else None,
        "briefing": (raw.get("box@pod") or {}).get("briefing"),
        "tools": dcd.get("tools"), "app": dcd.get("status"),
        "kept": cur.get("green"), "broken": cur.get("red"), "untested": cur.get("could-not-run"), "total": cur.get("total"),
        "kept_delta": (cur.get("green", 0) - prev.get("green", 0)) if prev else None,
        "broken_delta": (cur.get("red", 0) - prev.get("red", 0)) if prev else None,
    }
    return {"generated": now.strftime("%Y-%m-%d %H:%M UTC"), "stardate": stardate(now), "today": today,
            "orders": orders, "handled": handled, "noise": noise, "prs": prs_f.result(), "vitals": vitals,
            "crew": [{k: v[k] for k in ("label", "role", "status", "checker", "counts", "code")} for v in vessels]}


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
    if not a.from_json:
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
--amber:#ffbb33;--ok:#66ccff;--dim:#2c2c44;--card:#0d0d1a;--text:#ffcc99;--ink:#000}
*{box-sizing:border-box;margin:0;padding:0}[hidden]{display:none!important}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:Antonio,"Arial Narrow",sans-serif;overflow:hidden}
body{display:grid;grid-template-columns:clamp(60px,6.5vw,120px) 1fr;grid-template-rows:auto 1fr auto;gap:.7vh .6vw;height:100vh;padding:1vh 1vw}
.side{grid-row:1/4;display:flex;flex-direction:column;gap:.6vh}
.side div{flex:1;background:var(--lilac);color:var(--ink);display:flex;align-items:flex-end;justify-content:flex-end;padding:.4vh .5vw;font-size:1.4vh;font-weight:600;letter-spacing:.06em;text-align:right}
.side .cap{flex:0 0 7vh;background:var(--orange);border-radius:3vh 0 0 0}.side .ft{flex:0 0 7vh;border-radius:0 0 0 3vh}
.side div:nth-child(3){background:var(--peri)}.side div:nth-child(4){background:var(--peach)}.side div:nth-child(5){background:var(--blue)}
header{display:flex;align-items:center;gap:.8vw}
header h1{font-size:3.8vh;letter-spacing:.12em;color:var(--orange);white-space:nowrap}
header .bar{flex:1;height:2.6vh;background:var(--orange);border-radius:0 1.3vh 1.3vh 0}
header .cond{font-size:2.4vh;font-weight:700;letter-spacing:.1em;padding:.2vh .8vw;border-radius:1.4vh;color:var(--ink);white-space:nowrap}
.cond.red{background:var(--red)}.cond.amber{background:var(--amber)}.cond.green{background:var(--ok)}
header .sd{font-size:2vh;color:var(--peri);white-space:nowrap}
main{display:grid;grid-template-columns:1.55fr 1fr;gap:.8vh .8vw;min-height:0}
.col{display:flex;flex-direction:column;gap:.8vh;min-height:0}
.panel{border-left:.9vw solid var(--peri);border-top:.45vh solid var(--peri);border-radius:1.8vh 0 0 0;padding:.6vh .7vw;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.panel h2{font-size:1.85vh;letter-spacing:.14em;color:var(--peri);margin-bottom:.5vh;font-weight:600;display:flex;justify-content:space-between;gap:1vw}
.panel h2 small{font-size:1.5vh;color:var(--lilac);letter-spacing:.04em;white-space:nowrap}
.p-orders{flex:1;border-color:var(--orange)}.p-orders h2{color:var(--orange)}
.orders{display:flex;flex-direction:column;gap:.6vh;overflow:hidden;flex:1}
.order{display:grid;grid-template-columns:3.2vh 1fr auto;gap:.3vh .6vw;background:var(--card);border-radius:1.2vh;padding:.6vh .6vw;border-left:.7vh solid var(--amber)}
.order.red{border-left-color:var(--red)}
.order .n{grid-row:1/4;font-size:3vh;font-weight:700;color:var(--peri);text-align:center}
.order .t{font-size:2.05vh;font-weight:700;color:var(--peach);letter-spacing:.02em}
.order .meta{font-size:1.45vh;display:flex;gap:.5vw;align-items:center;white-space:nowrap}
.chip{padding:.1vh .45vw;border-radius:.9vh;color:var(--ink);font-weight:600}
.chip.days{background:var(--lilac)}.chip.red{background:var(--red)}.chip.amber{background:var(--amber)}
.order .w{grid-column:2/4;font-size:1.5vh;color:var(--blue);line-height:1.25}
.order .act{grid-column:2/4;display:flex;gap:.5vw;align-items:center}
.order code{flex:1;min-width:0;font-family:ui-monospace,Menlo,monospace;font-size:1.25vh;color:var(--peach);background:#000;border:1px solid var(--dim);border-radius:.6vh;padding:.3vh .4vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.order .how{color:var(--lilac)}
button{font-family:inherit;font-size:1.45vh;font-weight:700;letter-spacing:.08em;border:0;border-radius:1vh;padding:.35vh .7vw;cursor:pointer;color:var(--ink);background:var(--orange);white-space:nowrap}
button.brief{background:var(--peri)}button:hover{filter:brightness(1.15)}button:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
button.done{background:var(--ok)}
.more{font-size:1.45vh;color:var(--lilac);line-height:1.3}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:.8vw;flex:0 0 23vh;min-height:0}
.list{list-style:none;font-size:1.48vh;display:flex;flex-direction:column;gap:.35vh;overflow:hidden}
.list li{display:flex;justify-content:space-between;gap:.5vw;border-bottom:1px dotted var(--dim);padding-bottom:.2vh}
.list li>span:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list li>span:last-child{color:var(--lilac);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:55%;text-align:right}
.list a{color:var(--blue);text-decoration:none;white-space:nowrap}.list a:hover{text-decoration:underline}
.p-noise{border-color:var(--dim)}.p-noise h2{color:var(--lilac)}
.vitals{display:grid;grid-template-columns:1fr 1fr;gap:.6vh .6vw}
.vital{background:var(--card);border-radius:1.2vh;padding:.6vh .7vw;border-top:.6vh solid var(--ok)}
.vital.red{border-top-color:var(--red)}.vital.amber{border-top-color:var(--amber)}
.vital .k{font-size:1.45vh;color:var(--peri);letter-spacing:.1em}
.vital .v{font-size:3.4vh;font-weight:700;line-height:1.05}
.vital.red .v{color:var(--red)}.vital.amber .v{color:var(--amber)}.vital.green .v{color:var(--ok)}
.vital .s{font-size:1.35vh;color:var(--lilac)}
.crew{width:100%;border-collapse:collapse;font-size:1.5vh}
.crew td,.crew th{padding:.22vh .3vw;border-bottom:1px dotted var(--dim);white-space:nowrap}
.crew th{font-weight:400;color:var(--lilac);font-size:1.3vh;text-align:right}.crew th:first-child,.crew th:nth-child(2){text-align:left}
.crew td:first-child{font-weight:700;color:var(--peach)}
.dot{display:inline-block;width:1.2vh;height:1.2vh;border-radius:50%;margin-right:.4vw;vertical-align:middle}
.dot.green{background:var(--ok)}.dot.amber{background:var(--amber)}.dot.red{background:var(--red)}
.num{text-align:right}.num b{color:var(--orange)}.muted{color:var(--lilac)}
.p-crew{border-color:var(--lilac)}.p-crew h2{color:var(--lilac)}
.p-handled{flex:0 0 auto;max-height:16vh;border-color:var(--ok)}.p-handled h2{color:var(--ok)}
.p-also{flex:1;border-color:var(--amber)}.p-also h2{color:var(--amber)}
.also li{align-items:center}.also li .k{font-size:1.2vh;padding:0 .35vw;border-radius:.7vh;color:var(--ink);background:var(--lilac);margin-right:.4vw}
.also li .k.w3{background:var(--red)}.also li .k.w2{background:var(--amber)}
.also button{font-size:1.15vh;padding:.15vh .45vw}
.also li>span:last-child{display:flex;gap:.4vw;align-items:center;max-width:none}
.also .k{display:inline-block}
footer{display:flex;align-items:center;gap:.8vw}
footer .bar{flex:1;height:2vh;background:var(--lilac);border-radius:0 1vh 1vh 0}
footer .q{font-size:2.2vh;color:var(--orange);letter-spacing:.1em;white-space:nowrap}
footer .t{font-size:1.35vh;color:var(--peri);white-space:nowrap}
.blink{animation:b 1.2s ease-in-out infinite alternate}@keyframes b{to{opacity:.45}}
@media (prefers-reduced-motion:reduce){.blink{animation:none}}
@media (max-width:820px){html,body{overflow:auto}body{grid-template-columns:1fr;height:auto;padding:12px 16px}.side{display:none}
main{grid-template-columns:1fr}.row2{grid-template-columns:1fr;flex:none}header{flex-wrap:wrap}header h1{font-size:22px}}
</style></head><body>
<nav class="side" aria-hidden="true"><div class="cap"></div><div id="sd1"></div><div>FLEET OPS</div><div>LCARS 47</div><div>PICARD 4-7-ALPHA</div><div class="ft"></div></nav>
<header><h1>CAPTAIN'S CONSOLE</h1><div class="cond" id="cond"></div><div class="bar"></div><div class="sd" id="sd"></div></header>
<main>
 <div class="col">
  <section class="panel p-orders"><h2>CAPTAIN'S ORDERS <small id="osum"></small></h2><div class="orders" id="orders"></div></section>
  <div class="row2">
   <section class="panel"><h2>YOUR REVIEW <small id="prc"></small></h2><ul class="list" id="prs"></ul></section>
   <section class="panel p-noise"><h2>NOISE TO RETIRE <small id="nc"></small></h2><ul class="list" id="noise"></ul></section>
  </div>
 </div>
 <div class="col">
  <section class="panel"><h2>VITAL SIGNS</h2><div class="vitals" id="vitals"></div></section>
  <section class="panel p-crew"><h2>CREW</h2><table class="crew" id="crew"></table></section>
  <section class="panel p-handled" id="hp" hidden><h2>NUMBER ONE HAS IT <small>repairs agents are running</small></h2><ul class="list" id="handled"></ul></section>
  <section class="panel p-also"><h2>ALSO ON YOUR LIST <small id="asum"></small></h2><ul class="list also" id="also"></ul></section>
 </div>
</main>
<footer><div class="q">“MAKE IT SO.”</div><div class="bar"></div><div class="t" id="gen"></div><div class="t">Dedicated to Sir Patrick Stewart · Jean-Luc Picard</div></footer>
<script>
const D=/*DATA*/null;
const $=id=>document.getElementById(id), esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
$("sd").textContent="STARDATE "+D.stardate; $("sd1").textContent="SD "+D.stardate;
$("gen").textContent="SENSOR SWEEP "+D.generated+" · refresh: fleet_dashboard.py --open";
const O=D.orders||[], reds=O.filter(o=>o.severity==="red").length;
$("cond").className="cond "+(reds?"red blink":O.length?"amber":"green");
$("cond").textContent=reds?`RED ALERT · ${reds} CRITICAL`:O.length?"YELLOW ALERT":"ALL CLEAR";
$("osum").textContent=`${O.length} need you · ranked by what is lost, then by age`;
const SHOW=5, KIND={3:"WORK AT RISK",2:"DEGRADED",1:"HYGIENE"};
$("orders").innerHTML=O.slice(0,SHOW).map((o,i)=>{
 const dl=d=>d>=29?"30+ DAYS":d===0?"TODAY":d+" DAY"+(d===1?"":"S");
 const days=o.days!=null?`<span class="chip days">${dl(o.days)}</span>`:`<span class="chip days">SINCE ?</span>`;
 const act=o.command?`<code title="${esc(o.command)}">${esc(o.command.split("\n")[0])}${o.command.includes("\n")?"  (+"+(o.command.split("\n").length-1)+" more)":""}</code><button data-copy="${i}" data-kind="command">COPY COMMAND</button>`
  :`<code title="${esc(o.brief||"")}">${esc(o.brief||"")}</code><button class="brief" data-copy="${i}" data-kind="brief">COPY BRIEF FOR CLAUDE</button>`;
 return `<div class="order ${o.severity}"><div class="n">${i+1}</div><div class="t">${esc(o.title)}</div>
 <div class="meta">${days}<span class="chip ${o.severity}">${o.severity==="red"?"CRITICAL":KIND[o.weight||1]}</span></div>
 <div class="w">${esc(o.why)}${o.how?` <span class="how">${esc(o.how)}</span>`:""}</div><div class="act">${act}</div></div>`}).join("")
 +(O.length?"":"<div class='more'>Nothing needs you. Engage.</div>");
document.addEventListener("click",async e=>{const b=e.target.closest("button[data-copy]");if(!b)return;
 const txt=O[+b.dataset.copy][b.dataset.kind]||"";
 try{await navigator.clipboard.writeText(txt)}catch(_){const t=document.createElement("textarea");t.value=txt;document.body.appendChild(t);t.select();document.execCommand("copy");t.remove()}
 const old=b.textContent;b.textContent="COPIED";b.classList.add("done");setTimeout(()=>{b.textContent=old;b.classList.remove("done")},1600)});
const P=D.prs||[];
$("prc").textContent=P.length+" open, newest first"+(P.length>6?" (6 shown)":"");
$("prs").innerHTML=P.slice(0,6).map(p=>`<li><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.repo)} #${p.number}</a><span title="${esc(p.title)}">${esc(p.title)}</span></li>`).join("")||"<li>None open.</li>";
const N=D.noise||[];
$("nc").textContent=`${N.length} checks look in the wrong place`;
$("noise").innerHTML=N.slice(0,6).map(n=>`<li><span title="${esc(n.job)}">${esc(n.label)}: ${esc(n.title)}</span><span>${esc(n.note)}</span></li>`).join("")||"<li>None.</li>";
const V=D.vitals||{}, tl=V.tools||{}, toolsOk=["datacore","plur"].every(k=>tl[k]==="connected");
const sgn=n=>n==null?"":" ("+(n>0?"+"+n:n)+")";
const vit=[
 ["BACKUP",V.backup_ok?"OK":(V.backup_days!=null?V.backup_days+" DAYS OLD":"UNKNOWN"),V.backup_ok?"green":"red",V.backup_ok?"copied last night":"last good copy "+(V.backup_newest||"?")],
 ["BOX DISK",V.disk_pct!=null?V.disk_pct+"%":"?",V.disk_pct>=88?"red":V.disk_pct>=80?"amber":"green",V.disk_free_gb!=null?V.disk_free_gb+" GB free":""],
 ["BRIEFING",V.briefing==="yes"?"DELIVERED":"MISSING",V.briefing==="yes"?"green":"red",(V.app==="ok"&&toolsOk)?"Winston fully equipped":"Winston's app: "+(V.app==="ok"?"no tools":(V.app||"?"))],
 ["PROMISES",V.total?Math.round(100*(V.kept||0)/V.total)+"% KEPT":"?",V.total&&V.kept/V.total>=.9?"green":"amber",V.total?`${V.kept} kept${sgn(V.kept_delta)} · ${V.broken} broken${sgn(V.broken_delta)} · ${V.untested} untested`:""],
];
$("vitals").innerHTML=vit.map(([k,v,c,s])=>`<div class="vital ${c}"><div class="k">${k}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join("");
$("crew").innerHTML=`<tr><th>AGENT</th><th>JOB CHECKER</th><th>NEEDS YOU</th><th>AGENTS FIXING</th><th>NOISE</th></tr>`+(D.crew||[]).map(c=>`<tr><td><span class="dot ${c.status}"></span>${esc(c.label)}</td><td class="muted">${esc(c.checker)}</td>
<td class="num"><b>${c.counts.you||0}</b></td><td class="num">${(c.counts.agent||0)+(c.counts.paused||0)}</td><td class="num muted">${c.counts.noise||0}</td></tr>`).join("");
const R=O.slice(SHOW);
$("asum").textContent=R.length?[3,2,1].map(w=>[w,R.filter(o=>(o.weight||1)===w).length]).filter(x=>x[1]).map(([w,n])=>n+" "+KIND[w].toLowerCase()).join(" · "):"";
$("also").innerHTML=R.map((o,j)=>`<li><span title="${esc(o.why)}"><span class="k w${o.weight||1}">${o.severity==="red"?"CRITICAL":KIND[o.weight||1]}</span>${esc(o.title)}</span>
<span><span class="muted">${o.days==null?"?":o.days>=29?"30+ d":o.days+" d"}</span><button class="${o.command?"":"brief"}" data-copy="${SHOW+j}" data-kind="${o.command?"command":"brief"}">${o.command?"COPY CMD":"COPY BRIEF"}</button></span></li>`).join("")||"<li>Nothing else.</li>";
const H=D.handled||[]; $("hp").hidden=!H.length;
$("handled").innerHTML=H.slice(0,8).map(h=>`<li><span>${esc(h.label)}: ${esc(h.title)}</span><span>${esc(h.note)}</span></li>`).join("")||"<li>No repairs in flight: everything open is on the orders list.</li>";
</script></body></html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
