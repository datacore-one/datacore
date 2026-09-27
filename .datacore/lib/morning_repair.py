#!/usr/bin/env python3
"""Fix the fleet's errors before the owner wakes (spec: .datacore/specs/morning-repair.md).

    morning_repair.py sweep     # 02:00 UTC: find, remediate safely, delegate the rest
    morning_repair.py recheck   # 03:30 UTC: re-check, write the fragment the briefing reads
    morning_repair.py findings  # print what is failing now; changes nothing

A FINDING is one thing that is failing now, with the evidence and a way to check it
again that is fixed when the finding is made. The sweep tries one safe remediation
per kind (re-run a failed one-shot unit; re-run a failed briefing input) and hands
whatever is still failing to its agent (a red duty to the duty's owner, the rest to
Miles) as a repair item: fix it, and if that needs a code
change, open a pull request -- never merge (owner, 2026-09-25). The re-check decides
what was repaired; nobody's word does, and a check that could not run is "could not
tell". The owner is paged only for what needs a person, or a repair that failed three
times or has taken 24 hours (DIP-0050).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

ROOT = Path(os.environ.get("DATACORE_ROOT") or Path.home() / "Data")
STATE = Path.home() / ".datacore" / "state" / "morning-repair"
FRAGMENTS = Path.home() / ".datacore" / "cos" / "fragments"
TRIAGE_LOG = Path.home() / ".datacore" / "cos" / "email-triage.log"
V2_LOG = Path.home() / ".datacore" / "state" / "v2-verify.log"
MAX_ITEMS = 10
PR_RULE = ("If the fix needs a code change, open a pull request whose title carries this item's id and "
           "STOP -- do not merge; the owner merges (owner, 2026-09-25). Fix the producer, never the "
           "check: a repair that edits the check that found it does not count. Never rotate credentials, "
           "spend money, touch trading, or delete data.")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(cmd: list[str] | str, timeout: int = 900, shell: bool = False) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


# ---- collectors: each returns findings {id, kind, title, evidence} -------------------

def v2_checklist(run: bool = True) -> list[dict]:
    """FAIL lines of the v2 checklist. Re-run it: an old log is not today's state."""
    if run:
        _run([str(LIB / "v2_verify_run.sh")], timeout=900)
    try:
        text = _ANSI.sub("", V2_LOG.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return [{"id": "v2-verify-unreadable", "kind": "v2", "title": "v2-verify log unreadable",
                 "evidence": str(exc)}]
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*FAIL\s+(.+?)\s{2,}(.*)$", line)
        if m:
            out.append({"id": f"v2-{_slug(m.group(1))}", "kind": "v2", "title": f"v2-verify: {m.group(1)}",
                        "evidence": m.group(2).strip()[:300]})
    return out


def failed_units() -> list[dict]:
    rc, out = _run(["systemctl", "list-units", "--failed", "--no-legend", "--plain"], timeout=30)
    if rc != 0:
        # A check that could not run is "could not tell", never an empty list (DAY-6):
        # [] here read as "no unit failed" on a host whose systemctl was not there.
        return [{"id": "units-unreadable", "kind": "check", "title": "failed-unit check could not run",
                 "evidence": f"could not tell: systemctl rc {rc}: {out.strip()[-240:]}"}]
    found = []
    for line in out.splitlines():
        unit = line.split()[0] if line.split() else ""
        if unit.endswith(".service") and not unit.startswith("alert@"):
            _, why = _run(["journalctl", "-u", unit, "-n", "5", "--no-pager"], timeout=30)
            found.append({"id": f"unit-{_slug(unit)}", "kind": "unit", "unit": unit,
                          "title": f"unit {unit} failed", "evidence": why.strip()[-300:]})
    return found


def red_cadences() -> list[dict]:
    import cadence_liveness as L
    try:
        red, _ = L.collect_states(ROOT, 1)
    except Exception as exc:  # noqa: BLE001 -- an unreadable judge is itself a finding
        return [{"id": "cadence-liveness-unreadable", "kind": "cadence", "title": "cadence liveness failed",
                 "evidence": f"{type(exc).__name__}: {exc}"[:300]}]
    # The id names the duty, not its state: "late: tris (never ran)" in the id made a
    # new finding every time the reason's wording changed.
    return [{"id": f"cadence-{_slug(v + '-' + r + '-' + c.split(' [', 1)[0])}", "kind": "cadence",
             "title": f"cadence {v} {r} {c} is red", "evidence": f"{days} day(s) late ({f})",
             "owner": L.duty_owner(ROOT, v, r, f, c) or ""}
            for days, v, r, f, c in red]


def mail_triage(today: date | None = None) -> list[dict]:
    """The briefing's inbox section is only as true as the last triage."""
    today = (today or datetime.now(timezone.utc).date()).isoformat()
    try:
        tail = TRIAGE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        tail = []
    done = [l for l in tail if "Completed at" in l]
    last = done[-1] if done else ""
    ok = last and (today in last or (datetime.now(timezone.utc).hour < 4)) and "errors=0" in last \
        and not re.search(r"scanned=0\b", last)
    if ok:
        return []
    why = next((l for l in reversed(tail) if "ERROR" in l or "not configured" in l), last or "no completed run")
    return [{"id": "input-mail-triage", "kind": "input", "title": "mail triage did not complete cleanly",
             "evidence": why.strip()[:300],
             "rerun": "cd ~/Data && DATACORE_ROOT=$HOME/Data bash ~/Data/.datacore/modules/mail/server/"
                      "triage-email.sh >> ~/.datacore/cos/email-triage.log 2>&1"}]


ESCALATIONS_LOG = Path.home() / ".datacore" / "state" / "autofix-escalations.log"


def escalations() -> list[dict]:
    """Repairs Miles gave up on: the autofix pipeline's own "needs a person" list.

    The spec named this source and the first version left it out, so 13 stuck
    repairs (six failing jobs) stayed in a log whose alerts were suppressed as
    repeats (found 2026-09-25). They are findings too, each with no safe remediation.
    """
    try:
        lines = ESCALATIONS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    last = max((i for i, l in enumerate(lines) if l.startswith("autofix: ")), default=None)
    if last is None:
        return []
    jobs: dict[str, str] = {}
    for l in lines[last + 1:]:
        m = re.match(r"\s+([a-z0-9-]+):\s*(.+)$", l)
        if m:
            jobs.setdefault(m.group(1), m.group(2).strip())
    return [{"id": f"escalation-{job}", "kind": "escalation", "title": f"job {job}: repair gave up",
             "evidence": why[:300]} for job, why in sorted(jobs.items())]


# A reason that names a code error is a repair for Miles; a refused token, a missing
# group id or an unreachable Telegram is not -- those need a person (MSG-10).
_TRANSPORT = re.compile(r"^exception: (URLError|HTTPError|TimeoutError|timeout|Connection\w*|OSError|SSL\w*|"
                        r"RemoteDisconnected|IncompleteRead|gaierror)\b")


def _undelivered_log() -> Path:
    return Path(os.environ.get("DATACORE_UNDELIVERED_LOG")
                or Path.home() / ".datacore" / "state" / "undelivered-alerts.jsonl")


def _last_sweep() -> float | None:
    """When the previous day's sweep ran. Today's sweep and 03:30 re-check read the
    same window, so a delivery failure is never "repaired" by being re-read."""
    today = datetime.now(timezone.utc).date().isoformat()
    at = []
    for p in STATE.glob("*.json"):
        if p.stem < today:
            try:
                at.append(float(json.loads(p.read_text())["swept_at"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return max(at) if at else None


def undelivered() -> list[dict]:
    """Alerts and briefings that reached nobody since the last sweep, one finding per sender.

    Every sender that cannot deliver appends a line to undelivered-alerts.jsonl
    (tg_format.record_undelivered). Before this, a failed delivery ended as a local
    log line or stderr (AM-20), so the owner never learned an alert was lost.
    Without a previous sweep, the last day is read.
    """
    since = _last_sweep() or time.time() - 86400
    try:
        lines = _undelivered_log().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    by: dict[str, list[dict]] = {}
    for line in lines:
        try:
            r = json.loads(line)
            at = datetime.fromisoformat(str(r["at"]).replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if at > since:
            by.setdefault(str(r.get("sender") or "unknown"), []).append(r)
    out = []
    for sender, rs in sorted(by.items()):
        last = rs[-1]
        out.append({"id": f"delivery-{_slug(sender)}", "kind": "delivery",
                    "code": any(str(r.get("reason", "")).startswith("exception:")
                                and not _TRANSPORT.match(str(r.get("reason", ""))) for r in rs),
                    "title": f"{sender}: {len(rs)} alert(s) not delivered",
                    "evidence": (f"last on {last.get('host', '?')} at {last.get('at', '?')}: "
                                 f"{last.get('reason', '')} -- {last.get('text_head', '')}")[:300]})
    return out


def findings(run_v2: bool = True) -> list[dict]:
    return v2_checklist(run_v2) + failed_units() + red_cadences() + mail_triage() + escalations() + undelivered()


# ---- remediation, delegation, reporting ------------------------------------------------

def remediate(f: dict) -> str:
    """One safe attempt. Returns what was tried, or '' when nothing is safe to try."""
    if f["kind"] == "unit":
        typ = _run(["systemctl", "show", "-p", "Type", "--value", f["unit"]], timeout=30)[1].strip()
        if typ != "oneshot":
            return ""  # a long-running service is restarted by a person, not a sweep
        rc, _ = _run(["sudo", "-n", "systemctl", "start", f["unit"]], timeout=1800)
        return f"re-ran {f['unit']} (rc {rc})"
    if f["kind"] == "input" and f.get("rerun"):
        rc, _ = _run(f["rerun"], timeout=1800, shell=True)
        return f"re-ran its producer (rc {rc})"
    return ""


def _repairer() -> str | None:
    """The install's chief of operations (principals.yaml), who takes every repair
    that is not some agent's own duty; None when the install names none."""
    import roster
    return roster.by_role("chief of operations")


def delegate(f: dict, day: str) -> str:
    """A repair item for the finding's own agent -- a red duty goes to the agent whose
    duty it is (CAD-6), everything else to the chief of operations. Returns the item
    id, or ''."""
    from actor_identity import this_actor
    from ledger.log import EventLog
    from ledger.policy import guarded_append
    from jobs.autofix import _space
    iid = f"repair-{f['id']}-{day.replace('-', '')}"
    f["assignee"] = f.get("owner") or _repairer()
    if not f["assignee"]:
        f["delegation_error"] = "no principal has the role 'chief of operations' in principals.yaml"
        return ""
    body = "\n".join(["The morning repair sweep found this failing at 02:00 UTC, and one safe "
                      "remediation did not clear it.", "", f"What: {f['title']}",
                      f"Evidence: {f.get('evidence', '')}", *(["Tried: " + f["tried"]] if f.get("tried") else []),
                      "", "Done means the sweep's 03:30 re-check no longer finds it.", "", PR_RULE])
    try:
        guarded_append(EventLog(_space(ROOT), this_actor()), "item.create",
                       {"id": iid, "title": f"Repair: {f['title']}", "assignee": f["assignee"], "route": "dev",
                        "body": body, "requested_by": this_actor(), "morning_repair": True})
    except Exception as exc:  # noqa: BLE001 -- report, never crash the sweep
        f["delegation_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return ""
    return iid


def _alert_group(text: str) -> None:
    subprocess.run([sys.executable, str(LIB / "winston_send.py"), "--alert"], input=text, text=True,
                   capture_output=True, timeout=120)


def pull_latest() -> str:
    """Step 0: bring this host up to date. Many morning failures are already fixed
    upstream and only wait for a pull (2026-09-25: the egress declarations landed at
    07:00, the box still ran yesterday's module). It is the host's own fleet sync."""
    rc, out = _run([sys.executable, str(LIB / "git_fleet_sync.py"), str(ROOT), "--execute", "--pull"],
                   timeout=1200)
    return f"fleet sync rc {rc}"


def sweep() -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    STATE.mkdir(parents=True, exist_ok=True)
    pulled = pull_latest()
    print(f"morning_repair: {pulled}")
    # The budget is PER NIGHT, not per invocation: a manual re-run or a
    # restarted unit must not hand Miles another ten (MEM-66). What earlier
    # sweeps tonight already delegated is read back from tonight's state file.
    try:
        earlier = json.loads((STATE / f"{day}.json").read_text()).get("findings") or []
    except (OSError, ValueError):
        earlier = []
    handed = {e["id"]: e["item"] for e in earlier if isinstance(e, dict) and e.get("item")}
    found, delegated = findings(), 0
    for f in found:
        f["tried"] = remediate(f)
    # One re-check after the remediations; only what is still failing goes to Miles.
    still = {f["id"] for f in findings(run_v2=any(f["kind"] == "v2" for f in found))}
    for f in found:
        f["cleared_by_sweep"] = f["id"] not in still
        if f["id"] in still and f["kind"] == "escalation":
            f["item"] = ""          # miles already gave up; handing it back is a loop, not a repair
        elif f["id"] in still and f["kind"] == "delivery" and not f.get("code"):
            f["item"] = ""          # a refused token or missing group id needs a person, not a code repair
        elif f["id"] in still and f["id"] in handed:
            f["item"] = handed[f["id"]]    # already Miles's tonight: not a second hand-off
        elif f["id"] in still:
            if len(handed) + delegated < MAX_ITEMS:
                f["item"] = delegate(f, day)
                delegated += bool(f["item"])
            else:
                f["item"] = ""
                f["over_budget"] = True
    # Keep tonight's earlier hand-offs on record even if their finding cleared
    # since, so a later sweep still counts them against the budget.
    seen = {f["id"] for f in found}
    found += [dict(e, cleared_by_sweep=True) for e in earlier
              if isinstance(e, dict) and e.get("item") and e.get("id") not in seen]
    (STATE / f"{day}.json").write_text(json.dumps({"swept_at": time.time(), "findings": found}, indent=1))
    print(f"morning_repair sweep: {len(found)} finding(s), {sum(f['cleared_by_sweep'] for f in found)} "
          f"cleared by one safe remediation, {delegated} delegated for repair")
    return 0


#: The collector finding that means a kind's own check could not run at all. A finding
#: of that kind absent while it is present was not repaired: nobody could tell (DAY-6).
CHECK_OF = {"v2": "v2-verify-unreadable", "unit": "units-unreadable", "cadence": "cadence-liveness-unreadable"}


def _pull_request(iid: str) -> tuple[bool, str]:
    """(could ask, url) of an open pull request naming the item; url '' when none."""
    rc, out = _run(["gh", "search", "prs", iid, "--state", "open", "--json", "url,title", "--limit", "5"],
                   timeout=30)
    try:
        prs = json.loads(out) if rc == 0 else None
    except ValueError:
        prs = None
    if not isinstance(prs, list):
        return False, ""
    return True, next((p.get("url", "") for p in prs if iid in str(p.get("title", ""))), "")


def _needs(f: dict) -> str:
    """What the owner has to do, from evidence -- never a pull request that does not exist (MSG-8)."""
    if f.get("could_not_tell"):
        return "could not tell: its check could not run at 03:30"
    if f.get("item"):
        who = f.get("assignee") or _repairer() or "the chief of operations"
        asked, url = _pull_request(f["item"])
        if url:
            # AUD-5: an agent's pull request reaches the owner only after a second
            # model family has commented on it (cross_model_audit.review).
            from cross_model_audit import AGENTS, pr_ready
            ready = pr_ready(url, AGENTS.get(who, "claude"))
            if ready is False:
                return f"in review: a second model reviews the pull request {url} before you do"
            return f"review the pull request {url}" + ("" if ready else " (could not tell whether a second model reviewed it)")
        return (f"in repair: {who} is on it" if asked
                else f"in repair: {who} is on it (could not tell whether a fix is waiting for review)")
    if f.get("kind") == "escalation":
        import roster
        who = _repairer()
        return f"a person: {roster.display(who) if who else 'the repairer'} gave up after three attempts"
    if f.get("kind") == "delivery":
        return "a person: an alert or briefing reached nobody"
    return "a person: nothing could be delegated"


def _dead_lettered(iid: str) -> bool:
    """The repair item was given up on (three failed attempts), per the ledger."""
    try:
        from ledger.fold import fold
        from ledger.log import read_events
        from jobs.autofix import _space
        item = fold(read_events(_space(ROOT))).items.get(iid)
    except Exception:  # noqa: BLE001 -- unreadable: the 24-hour rule still catches it tomorrow
        return False
    return bool(item) and item.status == "dismissed" and item.closed_kind == "dropped"


def _in_repair_since_yesterday(fid: str, day: str) -> bool:
    """Yesterday's sweep already handed this finding over: the repair has taken 24 hours."""
    from datetime import timedelta
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    try:
        old = json.loads((STATE / f"{prev}.json").read_text())["findings"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return any(o.get("id") == fid and o.get("item") for o in old)


def _page_owner(f: dict, day: str) -> bool:
    """DIP-0050, CAD-6/AGT-8: the owner hears only what needs a person -- no repair item,
    or a repair that failed three times or has taken 24 hours. A repair handed over at
    02:00 is not news at 03:30."""
    if not f.get("item"):
        return True
    return _dead_lettered(f["item"]) or _in_repair_since_yesterday(f["id"], day)


def recheck() -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        swept = json.loads((STATE / f"{day}.json").read_text())["findings"]
    except (OSError, ValueError, KeyError):
        swept = None
    now = {f["id"]: f for f in findings()}
    if swept is None:
        # The sweep did not run: that is itself the first thing to report.
        swept = [{"id": "morning-repair-sweep", "kind": "self", "title": "the 02:00 sweep did not run",
                  "evidence": f"no {STATE / (day + '.json')}"}] + list(now.values())
    # Repaired means its own check ran at 03:30 and no longer finds it. A finding about
    # the sweep itself has no re-check, and one whose check could not run is "could not
    # tell" -- neither is ever "repaired" because it is absent from the checks.
    unknown = [{**f, "could_not_tell": True,
                "evidence_now": now[CHECK_OF[f["kind"]]].get("evidence", "")}
               for f in swept if f["id"] not in now and CHECK_OF.get(f.get("kind")) in now]
    repaired = [f for f in swept if f["id"] not in now and f["kind"] != "self"
                and f["id"] not in {u["id"] for u in unknown}]
    failing = [{**f, "evidence_now": now[f["id"]].get("evidence", "")} for f in swept if f["id"] in now]
    failing += unknown
    failing += [f for f in swept if f["kind"] == "self"]
    failing += [f for fid, f in now.items() if fid not in {s["id"] for s in swept}]  # new since 02:00
    out = FRAGMENTS / day / "repairs.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "schema_version": "1", "date": day, "composed_by": "morning-repair",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repaired": [{"title": f["title"], "how": f.get("tried") or f.get("item") or "cleared"} for f in repaired],
        "still_failing": [{"title": f["title"], "evidence": f.get("evidence_now") or f.get("evidence", ""),
                           "item": f.get("item", ""), "needs": _needs(f)} for f in failing],
    }, indent=1))
    page = [f for f in failing if _page_owner(f, day)]
    if page:
        # MEM-66 (owner 2026-09-27): the group gets a summary with the count; the full
        # list of every still-failing item lives in repairs.json, which the briefing reads.
        in_repair = len(failing) - len(page)
        _alert_group(f"Morning repair, 03:30 UTC: {len(failing)} still failing, {len(page)} need a person"
                     + (f", {in_repair} in repair" if in_repair else "")
                     + ": " + "; ".join(f["title"] for f in page[:3])
                     + (f"; and {len(page) - 3} more" if len(page) > 3 else "")
                     + ". The full list is in the morning briefing.")
    print(f"morning_repair recheck: {len(repaired)} repaired, {len(failing)} still failing "
          f"({len(page)} need a person) -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["sweep", "recheck", "findings"])
    a = ap.parse_args()
    if a.cmd == "findings":
        for f in findings():
            print(f"{f['id']:44} {f['title']} :: {f.get('evidence', '')[:120]}")
        return 0
    return sweep() if a.cmd == "sweep" else recheck()


if __name__ == "__main__":
    sys.exit(main())
