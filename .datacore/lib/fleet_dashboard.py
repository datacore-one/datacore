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
                       "machine": "BOX HOST", "job": "fleet-backup",
                       "how": "Clears 15 GB of leftovers (temp audit folders, a worker folder unused since 09-14), then runs the backup."})
    tools = dcd.get("tools") or {}
    down = [k for k in ("datacore", "plur") if tools.get(k) and tools.get(k) != "connected"]
    if dcd.get("status") == "unreachable":
        orders.append({"severity": "red", "title": "Winston's app server is unreachable", "why": dcd.get("error") or "",
                       "owner": "you", "machine": "WINSTON", "job": "datacored",
                       "brief": "Winston's datacored on the box does not answer its health check. Find out why."})
    elif down:
        orders.append({"severity": "red", "title": "Winston's app server has no tools",
                       "why": f"It reports ok, but its {' and '.join(down)} links are down: the app's chat cannot read your tasks or memory.",
                       "owner": "you", "command": CMD_TOOLS, "machine": "WINSTON", "job": "datacored-tools",
                       "how": "Fixes two paths in his tool config, then restarts his pod (about a minute)."})
    good = next((POD_DEPLOY[v["key"]] for v in vessels
                 if v.get("key") in POD_DEPLOY and v.get("checker") == "checked today"), None)
    for v in vessels:
        if str(v.get("checker", "")).startswith("checker crashes"):
            o = {"severity": "red", "title": f"{v['label']}'s job checker crashes",
                 "why": f"Every run is rejected ({v['checker'][17:]}), so none of {v['label']}'s jobs are being verified.",
                 "owner": "you", "machine": v["label"], "job": "job-checker"}
            if v.get("key") in POD_DEPLOY and good and good != POD_DEPLOY[v["key"]]:
                o["command"] = cmd_roster(POD_DEPLOY[v["key"]], good)
                o["how"] = f"Copies {good.title()}'s complete machine list over {v['label']}'s, keeping a backup."
            else:
                o["brief"] = f"{v['label']}'s job checker crashes: {v['checker']}. Find and fix the cause."
            orders.append(o)
        elif not v.get("reachable", True):
            orders.append({"severity": "red", "title": f"{v['label']} is unreachable", "why": v.get("error") or "",
                           "owner": "you", "machine": v["label"], "job": "reach", "brief": f"The fleet console cannot reach {v['label']}: {v.get('error')}. Find out why."})
    for v in vessels:
        for f in v.get("faults", []):
            if f.get("bucket") != "you":
                continue
            weight, lost = IMPACT.get(_key(f["job"]), (1, ""))
            orders.append({"severity": "amber", "title": f"{v['label']}: {job_title(f['job'])}", "weight": weight,
                           "why": (f"{lost} " if lost else "") + f"{f['detail'][:1].upper()}{f['detail'][1:]}. "
                                  f"{f['note'][:1].upper()}{f['note'][1:]}.",
                           "since": f.get("first") or None, "days": _days(f.get("first"), today), "owner": "you",
                           "machine": v["label"], "job": f["job"],
                           "brief": (f"On the Datacore fleet, the job '{f['job']}' on {v['label']} has been failing since "
                                     f"{f.get('first') or 'today'}: {f['detail']} ({f['note']}). Find the root cause and fix it. "
                                     f"Owner rules apply: tests first, and pod or box writes go to the owner as commands.")})
    rank = {"red": 0, "amber": 1}
    return sorted(orders, key=lambda o: (rank.get(o["severity"], 2), -o.get("weight", 9), -(o.get("days") or 0)))


# -- v3: crew log and decision board ----------------------------------------------

FIXES = ROOT / ".datacore" / "state" / "fleet-fixes.jsonl"
DECISIONS = ROOT / ".datacore" / "state" / "fleet-decisions.jsonl"
BOARD = "fleet-console"
CREW_OPTIONS = [
    {"value": "crew", "label": "Crew fix", "consequence": "An agent takes the brief; the result shows in the crew log."},
    {"value": "retire", "label": "Retire", "consequence": "The job contract leaves the manifest and is no longer checked."},
    {"value": "snooze", "label": "Snooze 7d", "consequence": "Off the console for a week, then back if still failing."},
    {"value": "me", "label": "Mine", "consequence": "Stays on your list until you act."},
]
RUN_OPTIONS = [
    {"value": "run", "label": "I run it", "consequence": "Copy the command and paste it in a terminal on the Mac."},
    {"value": "grant", "label": "Crew runs it", "consequence": "Allow Claude to run box and pod commands from this Mac; the crew runs it and logs the result."},
    {"value": "later", "label": "Later", "consequence": "Stays red on the console."},
]


def read_jsonl(path: Path) -> list:
    rows = []
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def merge_fixes(orders: list, fixes: list) -> tuple[list, list]:
    """Take what the crew fixed off the owner's list; keep it in the log, flagged if the checker still sees it."""
    open_ = {(o.get("machine"), o.get("job")) for o in orders}
    done = {(f.get("machine"), f.get("job")) for f in fixes}
    log = [dict(f, still_failing=(f.get("machine"), f.get("job")) in open_)
           for f in sorted(fixes, key=lambda f: f.get("at") or "", reverse=True)]
    return [o for o in orders if (o.get("machine"), o.get("job")) not in done], log


def decision_rows(orders: list, extra: list) -> list:
    rows = []
    asked = {(d.get("machine"), d.get("job")) for d in extra if d.get("job")}
    age = {(o.get("machine"), o.get("job")): o.get("days") for o in orders}
    for o in orders:
        if (o.get("machine"), o.get("job")) in asked:
            continue  # the crew asked a sharper question about this job
        slug = re.sub(r"[^a-z0-9]+", "-", f"{o.get('machine', '')}-{o.get('job') or o['title']}".lower()).strip("-")
        cmd = bool(o.get("command"))
        rows.append({"id": slug, "title": o["title"], "severity": o["severity"], "weight": o.get("weight"),
                     "machine": o.get("machine"), "days": o.get("days"), "why": o.get("why", ""), "how": o.get("how", ""),
                     "command": o.get("command"), "brief": o.get("brief"),
                     "options": RUN_OPTIONS if cmd else CREW_OPTIONS, "suggested": "run" if cmd else "crew"})
    for d in extra:
        opts = [o for o in d.get("options") or [] if o.get("value")][:4]
        if len(opts) < 2:
            continue
        sug = d.get("suggested") if d.get("suggested") in [o["value"] for o in opts] else opts[0]["value"]
        rows.append({"id": str(d.get("id") or d["title"])[:60], "title": d["title"], "severity": d.get("severity", "amber"),
                     "machine": d.get("machine"), "days": age.get((d.get("machine"), d.get("job"))), "why": d.get("context", ""), "how": "",
                     "command": d.get("command"), "brief": None, "options": opts, "suggested": sug, "asked_by": "crew"})
    return rows


def board_build(rows: list) -> str:
    import hashlib
    sig = [[r["id"], [o["value"] for o in r["options"]]] for r in rows]
    return hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:12]


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
    since2 = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    orders, crewlog = merge_fixes(owner_orders(box, dcd, vessels, today),
                                  [f for f in read_jsonl(FIXES) if (f.get("at") or "") >= since2])
    rows = decision_rows(orders, [d for d in read_jsonl(DECISIONS) if (d.get("at") or "") >= since2])
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
            "orders": orders, "rows": rows, "board": BOARD, "build": board_build(rows), "crewlog": crewlog,
            "handled": handled, "noise": noise, "prs": prs_f.result(), "vitals": vitals,
            "crew": [{**{k: v.get(k) for k in ("label", "role", "kind", "status", "checker", "counts", "code")},
                      "faults": [{"title": job_title(f["job"]), "bucket": f["bucket"], "note": f.get("note", ""),
                                  "detail": f.get("detail", "")} for f in v["faults"]]} for v in vessels]}


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
--amber:#ffbb33;--ok:#66ddaa;--dim:#2c2c44;--card:#0f0f1e;--text:#f3e3cf;--sub:#c4c4e6;--ink:#000}
*{box-sizing:border-box;margin:0;padding:0}[hidden]{display:none!important}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:Antonio,"Arial Narrow",sans-serif;overflow:hidden}
body{display:grid;grid-template-columns:clamp(54px,5.5vw,104px) 1fr;grid-template-rows:auto 1fr auto;gap:.8vh .7vw;height:100vh;padding:1vh 1vw}
svg.i{width:1em;height:1em;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;vertical-align:-.12em;flex:none}
.side{grid-row:1/4;display:flex;flex-direction:column;gap:.6vh}
.side div{flex:1;background:var(--lilac);color:var(--ink);display:flex;align-items:flex-end;justify-content:flex-end;padding:.4vh .5vw;font-size:1.4vh;font-weight:600;letter-spacing:.06em;text-align:right}
.side .cap{flex:0 0 7vh;background:var(--orange);border-radius:3vh 0 0 0}.side .ft{flex:0 0 7vh;border-radius:0 0 0 3vh}
.side div:nth-child(3){background:var(--peri)}.side div:nth-child(4){background:var(--peach)}.side div:nth-child(5){background:var(--blue)}
header{display:flex;align-items:center;gap:.9vw}
header h1{font-size:3.9vh;letter-spacing:.12em;color:var(--orange);white-space:nowrap}
header .bar{flex:1;height:2.6vh;background:var(--orange);border-radius:0 1.3vh 1.3vh 0}
.cond{font-size:2.4vh;font-weight:700;letter-spacing:.1em;padding:.2vh .9vw;border-radius:1.4vh;color:var(--ink);white-space:nowrap}
.cond.red{background:var(--red)}.cond.amber{background:var(--amber)}.cond.green{background:var(--ok)}
header .sd{font-size:2.1vh;color:var(--peri);white-space:nowrap}
main{display:grid;grid-template-columns:1.5fr 1fr;grid-template-rows:auto 1fr;gap:.9vh .8vw;min-height:0}
.fleet{grid-column:1/3;display:grid;grid-template-columns:repeat(7,1fr);gap:.6vw}
.ves{background:var(--card);border-radius:1.6vh 0 0 1.6vh;border-left:1vh solid var(--ok);padding:.7vh .7vw;display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto auto;column-gap:.6vw;cursor:default}
.ves.amber{border-left-color:var(--amber)}.ves.red{border-left-color:var(--red)}
.ves .k{grid-row:1/3;font-size:3.6vh;color:var(--peri);align-self:center}
.ves .nm{font-size:2.4vh;font-weight:700;letter-spacing:.06em;color:var(--peach);line-height:1.05}
.ves .rl{font-size:1.5vh;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ves .ct{grid-column:1/3;display:flex;gap:.8vw;font-size:2vh;margin-top:.4vh;font-weight:600}
.ct span{display:flex;align-items:center;gap:.25vw}.ct .y{color:var(--red)}.ct .a{color:var(--ok)}.ct .n{color:#77779a}.ct .z{opacity:.28}
.panel{border-left:.9vw solid var(--peri);border-top:.45vh solid var(--peri);border-radius:1.8vh 0 0 0;padding:.6vh .7vw;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.panel h2{font-size:2vh;letter-spacing:.14em;color:var(--peri);margin-bottom:.6vh;font-weight:600;display:flex;align-items:center;gap:.8vw}
.panel h2 small{font-size:1.6vh;color:var(--sub);letter-spacing:.04em;white-space:nowrap;font-weight:400}
.panel h2 .sp{flex:1}
.p-dec{border-color:var(--orange)}.p-dec h2{color:var(--orange)}
.rows{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:.5vh;padding-right:.3vw;scrollbar-width:thin;scrollbar-color:var(--dim) transparent}
.row{display:grid;grid-template-columns:3vh minmax(0,1fr) auto auto;align-items:center;gap:.3vh .6vw;background:var(--card);border-radius:1.1vh;padding:.55vh .6vw;border-left:.6vh solid var(--amber)}
.row.red{border-left-color:var(--red)}.row.done{opacity:.62}.row.done:hover{opacity:1}
.row .sv{font-size:2.5vh;color:var(--amber)}.row.red .sv{color:var(--red)}.row.crew .sv{color:var(--peri)}
.row .tt{font-size:2.15vh;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:.5vw;min-width:0}
.row .tt .x{overflow:hidden;text-overflow:ellipsis}
.tag{font-size:1.45vh;font-weight:600;letter-spacing:.06em;padding:.05vh .45vw;border-radius:.8vh;color:var(--ink);background:var(--lilac);flex:none}
.tag.age{background:transparent;color:var(--sub);border:1px solid var(--dim);display:flex;align-items:center;gap:.25vw}
.tag.age.old{color:var(--amber);border-color:#5a4410}
.opts{display:flex;gap:.3vw}
.opt{font-family:inherit;font-size:1.55vh;font-weight:600;letter-spacing:.05em;border:1px solid #4a4a70;background:transparent;color:var(--sub);border-radius:1vh;padding:.3vh .55vw;cursor:pointer;white-space:nowrap;position:relative}
.opt.sug::after{content:"";position:absolute;top:-.35vh;right:-.35vh;width:.9vh;height:.9vh;border-radius:50%;background:var(--orange)}
.opt.on{background:var(--orange);border-color:var(--orange);color:var(--ink)}
.opt:hover{border-color:var(--peach);color:var(--peach)}.opt.on:hover{color:var(--ink)}
.ib{font-size:2.1vh;background:transparent;border:0;color:var(--peri);cursor:pointer;padding:.2vh .25vw;border-radius:.6vh;display:flex}
.ib:hover{color:var(--peach)}.ib.has{color:var(--orange)}.ib.ok{color:var(--ok)}
.tools{display:flex;gap:.1vw}
.nt{grid-column:2/5;font-family:-apple-system,"Segoe UI",sans-serif;font-size:1.6vh;background:#000;color:var(--text);border:1px solid var(--dim);border-radius:.6vh;padding:.4vh .5vw}
.nt:focus{outline:1px solid var(--peri)}
.save{font-family:inherit;font-size:1.8vh;font-weight:700;letter-spacing:.1em;border:0;border-radius:1.1vh;padding:.35vh .9vw;cursor:pointer;color:var(--ink);background:var(--orange);display:flex;align-items:center;gap:.4vw}
.save:hover{filter:brightness(1.15)}
.lnk{font-family:inherit;font-size:1.6vh;background:none;border:0;color:var(--blue);cursor:pointer;letter-spacing:.04em;text-decoration:underline dotted}
.prog{font-size:1.75vh;color:var(--peach);white-space:nowrap}.st{font-size:1.5vh;color:var(--ok);white-space:nowrap}
.col{display:flex;flex-direction:column;gap:.9vh;min-height:0}
.vitals{display:grid;grid-template-columns:repeat(5,1fr);gap:.5vw}
.vital{background:var(--card);border-radius:1.2vh;padding:.7vh .5vw;border-top:.6vh solid var(--ok);text-align:center;cursor:default}
.vital.red{border-top-color:var(--red)}.vital.amber{border-top-color:var(--amber)}
.vital .ic{font-size:2.6vh;color:var(--peri)}
.vital .v{font-size:3.3vh;font-weight:700;line-height:1.05}
.vital.red .v{color:var(--red)}.vital.amber .v{color:var(--amber)}.vital.green .v{color:var(--ok)}
.vital .k{font-size:1.4vh;color:var(--sub);letter-spacing:.1em}
.meter{height:.6vh;background:var(--dim);border-radius:.3vh;margin:.4vh .3vw 0;display:flex;overflow:hidden}
.meter i{display:block;height:100%}
.p-vit{flex:none;border-color:var(--blue)}.p-vit h2{color:var(--blue)}
.p-log{flex:1;border-color:var(--ok)}.p-log h2{color:var(--ok)}
.log{list-style:none;overflow-y:auto;display:flex;flex-direction:column;gap:.45vh;scrollbar-width:thin;scrollbar-color:var(--dim) transparent}
.log li{display:grid;grid-template-columns:2.6vh auto minmax(0,1fr) auto;gap:.5vw;align-items:center;font-size:1.95vh;padding:.3vh .2vw;border-bottom:1px dotted var(--dim);cursor:default}
.log .s{font-size:2.2vh}.log .s.ok{color:var(--ok)}.log .s.wait{color:var(--amber)}.log .s.no{color:var(--red)}
.log .t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)}
.log .tm{color:var(--sub);font-size:1.55vh}
.empty{font-size:1.8vh;color:var(--sub);padding:.5vh 0}
.strip{flex:none;display:grid;grid-template-columns:repeat(3,1fr);gap:.5vw}
.chipx{background:var(--card);border-radius:1.2vh;padding:.6vh .6vw;display:flex;align-items:center;gap:.6vw;cursor:default;color:var(--sub);text-decoration:none;border:1px solid transparent}
.chipx:hover{border-color:var(--dim)}
.chipx .ic{font-size:2.6vh;color:var(--peri)}.chipx b{font-size:3vh;color:var(--peach);line-height:1}.chipx span{font-size:1.45vh;letter-spacing:.08em;line-height:1.1}
footer{display:flex;align-items:center;gap:.8vw}
footer .bar{flex:1;height:2vh;background:var(--lilac);border-radius:0 1vh 1vh 0}
footer .q{font-size:2.2vh;color:var(--orange);letter-spacing:.1em;white-space:nowrap}
footer .t{font-size:1.4vh;color:var(--peri);white-space:nowrap}
#tip{position:fixed;z-index:9;max-width:min(460px,80vw);background:#0b0b18;border:1px solid var(--peri);border-left:6px solid var(--orange);border-radius:10px;padding:10px 12px;
font:13px/1.45 -apple-system,"Segoe UI",sans-serif;color:var(--text);white-space:pre-line;pointer-events:none;box-shadow:0 8px 30px #000c}
.blink{animation:b 1.2s ease-in-out infinite alternate}@keyframes b{to{opacity:.45}}
@media (prefers-reduced-motion:reduce){.blink{animation:none}}
@media (max-width:820px){html,body{overflow:auto}body{grid-template-columns:1fr;height:auto;padding:12px 16px}.side{display:none}
main{grid-template-columns:1fr}.fleet{grid-column:1;grid-template-columns:repeat(2,1fr)}.vitals{grid-template-columns:repeat(3,1fr)}
.row{grid-template-columns:3vh minmax(0,1fr)}.opts,.tools{grid-column:2/3;flex-wrap:wrap}header{flex-wrap:wrap}header h1{font-size:22px}}
</style></head><body>
<nav class="side" aria-hidden="true"><div class="cap"></div><div id="sd1"></div><div>FLEET OPS</div><div>LCARS 47</div><div>PICARD 4-7-ALPHA</div><div class="ft"></div></nav>
<header><h1>CAPTAIN'S CONSOLE</h1><div class="cond" id="cond"></div><div class="bar"></div><div class="sd" id="sd"></div></header>
<main>
 <section class="fleet" id="fleet" aria-label="Fleet"></section>
 <section class="panel p-dec"><h2>BRIDGE DECISIONS <small id="dsum"></small><span class="sp"></span>
  <span class="prog" id="prog"></span><button class="lnk" id="sugg">use suggestions for the rest</button><span class="st" id="st"></span>
  <button class="save" id="save"></button></h2>
  <div class="rows" id="rows"></div></section>
 <div class="col">
  <section class="panel p-vit"><h2>VITAL SIGNS</h2><div class="vitals" id="vitals"></div></section>
  <section class="panel p-log"><h2>CREW LOG <small id="lsum"></small></h2><ul class="log" id="log"></ul></section>
  <section class="strip" id="strip"></section>
 </div>
</main>
<footer><div class="q">“MAKE IT SO.”</div><div class="bar"></div><div class="t" id="gen"></div><div class="t">Dedicated to Sir Patrick Stewart · Jean-Luc Picard</div></footer>
<div id="tip" role="tooltip" hidden></div>
<script type="application/json" id="data">/*DATA*/null</script>
<script>
const D=JSON.parse(document.getElementById("data").textContent);
const $=id=>document.getElementById(id), esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const P={alert:'<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
wrench:'<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
mute:'<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>',
clock:'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
disk:'<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
db:'<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
mail:'<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>',
zap:'<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
shield:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
okc:'<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
xc:'<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
pr:'<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" y1="9" x2="6" y2="21"/>',
term:'<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
brief:'<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
note:'<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
save:'<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
visitor:'<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
host:'<rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
pod:'<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
user:'<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
ask:'<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>'};
const I=n=>`<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${P[n]||""}</svg>`;
const tipAttr=s=>` data-tip="${esc(s)}"`;
$("sd").textContent="STARDATE "+D.stardate; $("sd1").textContent="SD "+D.stardate;
$("gen").textContent="SENSOR SWEEP "+D.generated+" · refresh: fleet_dashboard.py --open";

// fleet strip
const BK={you:"needs you",agent:"crew repairing",paused:"repair paused",noise:"noise"};
$("fleet").innerHTML=(D.crew||[]).map(c=>{const n=c.counts||{},fx=(c.faults||[]);
 const tip=[`${c.label} · ${c.role}`,`Job checker: ${c.checker||"?"}`,c.code?`Code: ${c.code}`:"",
  fx.length?"\n"+fx.map(f=>`${f.bucket==="you"?"▲":f.bucket==="noise"?"·":"⚙"} ${f.title} (${BK[f.bucket]||f.bucket})${f.detail?"\n   "+f.detail:""}`).join("\n"):"\nNo job faults."].filter(Boolean).join("\n");
 const ct=(cls,ic,v,lab)=>`<span class="${cls}${v?"":" z"}" title="${lab}">${I(ic)}${v||0}</span>`;
 return `<div class="ves ${c.status}"${tipAttr(tip)}><span class="k">${I(c.kind)}</span><div class="nm">${esc(c.label)}</div><div class="rl">${esc(c.role)}</div>
 <div class="ct">${ct("y","alert",n.you,"needs you")}${ct("a","wrench",(n.agent||0)+(n.paused||0),"crew repairing")}${ct("n","mute",n.noise,"noise")}</div></div>`}).join("");

// vital signs
const V=D.vitals||{}, tl=V.tools||{}, tup=["datacore","plur"].filter(k=>tl[k]==="connected").length;
const sgn=n=>n==null?"":" ("+(n>0?"+"+n:n)+")";
const pk=V.total?Math.round(100*(V.kept||0)/V.total):null;
const vit=[
 ["db","BACKUP",V.backup_ok?"OK":(V.backup_days!=null?V.backup_days+"d":"?"),V.backup_ok?"green":"red",
  V.backup_ok?"Agent volumes copied last night.":`No agent backup since ${V.backup_newest||"?"}.\nThe nightly run skips while the box disk is too full.`,""],
 ["disk","DISK",V.disk_pct!=null?V.disk_pct+"%":"?",V.disk_pct>=88?"red":V.disk_pct>=80?"amber":"green",
  `Box disk ${V.disk_pct??"?"}% used, ${V.disk_free_gb??"?"} GB free.\nBackups need about 20% free.`,
  V.disk_pct!=null?`<div class="meter"><i style="width:${V.disk_pct}%;background:${V.disk_pct>=88?"var(--red)":"var(--amber)"}"></i></div>`:""],
 ["mail","BRIEFING",V.briefing==="yes"?"✓":"✗",V.briefing==="yes"?"green":"red",V.briefing==="yes"?"Today's morning briefing was delivered.":"No briefing folder for today on Winston.",""],
 ["zap","TOOLS",`${tup}/2`,V.app!=="ok"?"red":tup===2?"green":"red",
  `Winston's app server: ${V.app||"?"}\nDatacore link: ${tl.datacore||"?"}\nPLUR link: ${tl.plur||"?"}`,""],
 ["shield","PROMISES",pk!=null?pk+"%":"?",pk>=90?"green":"amber",
  V.total?`${V.kept} kept${sgn(V.kept_delta)}\n${V.broken} broken${sgn(V.broken_delta)}\n${V.untested} could not be tested\nof ${V.total} promises (last night's scoreboard)`:"No scoreboard.",
  V.total?`<div class="meter"><i style="width:${100*V.kept/V.total}%;background:var(--ok)"></i><i style="width:${100*V.broken/V.total}%;background:var(--red)"></i></div>`:""],
];
$("vitals").innerHTML=vit.map(([ic,k,v,c,t,m])=>`<div class="vital ${c}"${tipAttr(t)}><div class="ic">${I(ic)}</div><div class="v">${esc(v)}</div>${m}<div class="k">${k}</div></div>`).join("");

// decision board (decision-board skill behaviour, LCARS look)
const R=D.rows||[], KEY=`dboard:${D.board}:${D.build}`;
let S={}; try{S=JSON.parse(localStorage.getItem(KEY)||"{}")||{}}catch(_){S={}}
const persist=()=>{try{localStorage.setItem(KEY,JSON.stringify(S))}catch(_){}};
const KIND={3:"WORK AT RISK",2:"DEGRADED",1:"HYGIENE"};
const reds=R.filter(r=>r.severity==="red").length;
$("cond").className="cond "+(reds?"red blink":R.length?"amber":"green");
$("cond").textContent=reds?`RED ALERT · ${reds} CRITICAL`:R.length?"YELLOW ALERT":"ALL CLEAR";
$("dsum").textContent=R.length?`${R.length} need your word`:"";
$("save").innerHTML=I("save")+"SAVE";
function rowHTML(r,i){const s=S[r.id]||{};
 const age=r.days==null?"":`<span class="tag age${r.days>=7?" old":""}">${I("clock")}${r.days>=29?"30+":r.days}d</span>`;
 const tip=[r.title,r.severity==="red"?"CRITICAL":KIND[r.weight||1]||"",r.why,r.how,r.asked_by?"Asked by the crew.":""].filter(Boolean).join("\n\n");
 const opts=r.options.map(o=>`<button class="opt${o.value===r.suggested?" sug":""}${s.choice===o.value?" on":""}" data-row="${i}" data-v="${esc(o.value)}"${tipAttr((o.value===r.suggested?"Suggested. ":"")+o.label+(o.consequence?" — "+o.consequence:""))}>${esc(o.label.length>20?o.label.slice(0,19)+"…":o.label)}</button>`).join("");
 const cp=r.command?`<button class="ib" data-copy="${i}" data-kind="command"${tipAttr("Copy command:\n"+r.command)}>${I("term")}</button>`
   :r.brief?`<button class="ib" data-copy="${i}" data-kind="brief"${tipAttr("Copy brief for Claude:\n"+r.brief)}>${I("brief")}</button>`:"";
 return `<div class="row ${r.severity}${r.asked_by?" crew":""}${s.choice?" done":""}" id="r${i}"><span class="sv">${I(r.asked_by?"ask":"alert")}</span>
 <div class="tt"${tipAttr(tip)}><span class="x">${esc(r.machine&&r.title.startsWith(r.machine+": ")?r.title.slice(r.machine.length+2):r.title)}</span>${r.machine?`<span class="tag">${esc(r.machine)}</span>`:""}${age}</div>
 <div class="opts">${opts}</div><div class="tools">${cp}<button class="ib${s.note?" has":""}" data-note="${i}"${tipAttr("Note for Claude")}>${I("note")}</button></div>
 <input class="nt" data-nt="${i}" placeholder="Note for Claude" value="${esc(s.note||"")}"${s.note?"":" hidden"}></div>`}
function draw(){
 $("rows").innerHTML=R.map(rowHTML).join("")||`<div class="empty">Nothing needs your word. The crew has the conn.</div>`;
 const n=R.filter(r=>(S[r.id]||{}).choice).length;
 $("prog").textContent=R.length?`${n} OF ${R.length} DECIDED`:""; $("sugg").hidden=!R.length||n===R.length; $("save").hidden=!R.length;
}
draw();
document.addEventListener("click",async e=>{
 const o=e.target.closest(".opt[data-row]");
 if(o){const r=R[+o.dataset.row];S[r.id]=Object.assign(S[r.id]||{},{choice:o.dataset.v});persist();draw();$("st").textContent="";return}
 const nb=e.target.closest("[data-note]");
 if(nb){const f=document.querySelector(`[data-nt="${nb.dataset.note}"]`);f.hidden=false;f.focus();return}
 const b=e.target.closest("[data-copy]");
 if(b){const txt=R[+b.dataset.copy][b.dataset.kind]||"";
  try{await navigator.clipboard.writeText(txt)}catch(_){const t=document.createElement("textarea");t.value=txt;document.body.appendChild(t);t.select();document.execCommand("copy");t.remove()}
  b.classList.add("ok");setTimeout(()=>b.classList.remove("ok"),1500);$("st").textContent="Copied";return}
});
document.addEventListener("input",e=>{const f=e.target.closest("[data-nt]");if(!f)return;const r=R[+f.dataset.nt];
 S[r.id]=Object.assign(S[r.id]||{},{note:f.value});persist();f.previousElementSibling.querySelector("[data-note]").classList.toggle("has",!!f.value)});
$("sugg").onclick=()=>{R.forEach(r=>{if(!(S[r.id]||{}).choice)S[r.id]=Object.assign(S[r.id]||{},{choice:r.suggested})});persist();draw()};
$("save").onclick=()=>{const dec={};R.forEach(r=>{const s=S[r.id]||{};if(s.choice||s.note)dec[r.id]={choice:s.choice||"",note:s.note||""}});
 const blob=new Blob([JSON.stringify({board:D.board,build:D.build,savedAt:new Date().toISOString(),decisions:dec},null,1)],{type:"application/json"});
 const a=document.createElement("a"),f=`${D.board}.decisions.json`;a.href=URL.createObjectURL(blob);a.download=f;document.body.appendChild(a);a.click();
 setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},500);$("st").textContent=`Saved to Downloads as ${f}`};

// crew log
const L=D.crewlog||[];
$("lsum").textContent=L.length?`${L.filter(f=>f.verified).length} verified · ${L.filter(f=>f.verified&&f.still_failing).length} awaiting the checker's rerun · ${L.filter(f=>!f.verified).length} not verified`:"fixed by the crew, last 48 h";
$("log").innerHTML=L.map(f=>{const st=!f.verified?["no","xc","not verified"]:f.still_failing?["wait","clock","fixed; the job checker has not rerun yet"]:["ok","okc","fixed and verified"];
 const tip=[`${f.machine} · ${f.title}`,st[2],f.what,f.evidence?"Evidence: "+f.evidence:"",f.at?"Logged "+f.at:""].filter(Boolean).join("\n\n");
 return `<li${tipAttr(tip)}><span class="s ${st[0]}">${I(st[1])}</span><span class="tag">${esc(f.machine)}</span><span class="t">${esc(f.title)}</span><span class="tm">${esc((f.at||"").slice(11,16))}</span></li>`}).join("")
 ||`<li class="empty">No fixes logged yet.</li>`;

// strip: review, in flight, noise
const PR=D.prs||[], H=D.handled||[], N=D.noise||[];
$("strip").innerHTML=
 `<a class="chipx" href="https://github.com/pulls" target="_blank" rel="noopener"${tipAttr(PR.length?"Your open pull requests, newest first:\n"+PR.slice(0,12).map(p=>`${p.repo} #${p.number} · ${p.title}`).join("\n"):"No open pull requests.")}><span class="ic">${I("pr")}</span><b>${PR.length}</b><span>YOUR<br>REVIEW</span></a>`+
 `<div class="chipx"${tipAttr(H.length?"Repairs agents are running:\n"+H.map(h=>`${h.label}: ${h.title} — ${h.note}`).join("\n"):"No repairs in flight.")}><span class="ic">${I("wrench")}</span><b>${H.length}</b><span>REPAIRS<br>IN FLIGHT</span></div>`+
 `<div class="chipx"${tipAttr(N.length?"Checks that look in the wrong place:\n"+N.map(n=>`${n.label}: ${n.title} — ${n.note}`).join("\n"):"No noise.")}><span class="ic">${I("mute")}</span><b>${N.length}</b><span>NOISE<br>TO RETIRE</span></div>`;

// one tooltip for every [data-tip]
const tip=$("tip");
function showTip(t,x,y){tip.textContent=t.dataset.tip;tip.hidden=false;place(x,y)}
function place(x,y){const w=tip.offsetWidth,h=tip.offsetHeight;tip.style.left=Math.min(x+14,innerWidth-w-8)+"px";tip.style.top=(y+18+h>innerHeight?y-h-12:y+18)+"px"}
document.addEventListener("mouseover",e=>{const t=e.target.closest("[data-tip]");if(t)showTip(t,e.clientX,e.clientY);else tip.hidden=true});
document.addEventListener("mousemove",e=>{if(!tip.hidden)place(e.clientX,e.clientY)});
document.addEventListener("focusin",e=>{const t=e.target.closest("[data-tip]");if(t){const r=t.getBoundingClientRect();showTip(t,r.left,r.bottom)}});
document.addEventListener("focusout",()=>{tip.hidden=true});
</script></body></html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
