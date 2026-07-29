#!/usr/bin/env python3
"""datacore-smoke — DIP-0033 §4 smoke scenarios, Mac edition.

Asserts INVARIANTS of the most likely end-to-end flows — existence,
freshness, bounds, structure — never exact content, so non-deterministic
AI output passes while a broken pipeline fails.

Checks (see .datacore/registry/deliverables.yaml):
  journal          today's journal exists, has the briefing, is >5k chars
  journal_opened   the consumption marker — it was actually shown
  repos_health     space repos: not behind, ZERO stashes, tree not exploding
  org_readonly     loading org files does not modify them (write-on-load
                   regression tripwire — the 2026-07-29 root cause)
  intents_api      app daemon up and the /intents router mounted
  failed_units     `systemctl --failed` empty on box + nightshift (via ssh)
  mail_triage      box triage audit < 26h old
  audio_stamp      box audio-last-ok stamped today (meaningful after 08:30)

Policy: definite failures ALERT (macOS notification, exit 1). An
unreachable host is a WARN, not an alert — offline is a state the user
can see; a false page every travel day trains alert-blindness.

Run: python3 .datacore/lib/smoke.py [--verbose]
Launchd: io.datacore.smoke (08:40 local, after morning_journal at 08:30).
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

DATA = Path.home() / "Data"


def _load_hosts() -> tuple[str, str]:
    """SSH targets come from the gitignored env file, never from this file —
    the root repo is public and machine topology stays out of it
    (public-repo boundary, enforced by the pre-push scanner)."""
    env: dict[str, str] = {}
    env_file = DATA / ".datacore" / "env" / "verify.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env.get("SMOKE_BOX", ""), env.get("SMOKE_NIGHTSHIFT", "nightshift")


BOX, NIGHTSHIFT = _load_hosts()

FAIL, WARN, OK = "FAIL", "warn", "ok"


def ssh(host: str, cmd: str, timeout: int = 15) -> tuple[bool, str]:
    if not host:
        return False, "(unreachable)"
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", host, cmd],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return False, "(unreachable)"


def check_journal() -> tuple[str, str]:
    j = DATA / "0-personal" / "notes" / "journals" / f"{date.today().isoformat()}.md"
    if not j.exists():
        return FAIL, "today's journal missing"
    text = j.read_text()
    if "## Daily Briefing" not in text:
        return FAIL, "journal has no briefing section"
    if len(text) < 5000:
        return FAIL, f"briefing suspiciously short ({len(text)} chars)"
    return OK, f"{len(text)} chars"


def check_journal_opened() -> tuple[str, str]:
    marker = (Path.home() / ".datacore" / "state" / "morning-journal"
              / f"opened-{date.today().isoformat()}")
    if marker.exists():
        return OK, "opened"
    return WARN, "not opened yet (marker absent)"


def check_repos_health() -> tuple[str, str]:
    problems = []
    for repo in sorted(DATA.glob("[0-9]-*")):
        if not (repo / ".git").exists():
            continue
        r = subprocess.run(["git", "-C", str(repo), "stash", "list"],
                           capture_output=True, text=True, timeout=30)
        stashes = len([l for l in r.stdout.splitlines() if l.strip()])
        if stashes:
            problems.append(f"{repo.name}: {stashes} stash(es)")
        r = subprocess.run(["git", "-C", str(repo), "status", "-sb"],
                           capture_output=True, text=True, timeout=30)
        head = r.stdout.splitlines()[0] if r.stdout else ""
        if "behind" in head:
            behind = head.split("behind ")[-1].rstrip("]")
            if behind.isdigit() and int(behind) > 30:
                problems.append(f"{repo.name}: behind {behind}")
    if problems:
        return FAIL, "; ".join(problems)
    return OK, "no stashes, nothing far behind"


def check_org_readonly() -> tuple[str, str]:
    """Loading an org file must not modify it (write-on-load tripwire)."""
    target = DATA / "0-personal" / "org" / "inbox.org"
    if not target.exists():
        return WARN, "inbox.org not found"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'" + str(DATA / '.datacore' / 'lib') + "'); "
         "from org_workspace import OrgWorkspace; "
         "w = OrgWorkspace(); w.load(r'" + str(target) + "')"],
        capture_output=True, text=True, timeout=60)
    after = hashlib.sha256(target.read_bytes()).hexdigest()
    if before != after:
        return FAIL, "org_workspace.load() MODIFIED the file — write-on-load is back"
    if r.returncode != 0:
        return WARN, f"load errored: {r.stderr.strip()[:80]}"
    return OK, "load left the file untouched"


def check_intents_api() -> tuple[str, str]:
    r = subprocess.run(["pgrep", "-f", "bin/datacored"],
                       capture_output=True, text=True)
    pid = r.stdout.split()[0] if r.stdout.strip() else None
    if not pid:
        return WARN, "daemon not running (app closed?)"
    r = subprocess.run(["lsof", "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN"],
                       capture_output=True, text=True, timeout=15)
    port = None
    for line in r.stdout.splitlines():
        if "127.0.0.1:" in line:
            port = line.split("127.0.0.1:")[1].split(" ")[0].strip()
            break
    if not port:
        return WARN, "daemon running but no listen port found"
    r = subprocess.run(["curl", "-s", "-m", "5", "-o", "/dev/null",
                        "-w", "%{http_code}", f"http://127.0.0.1:{port}/intents"],
                       capture_output=True, text=True, timeout=10)
    code = r.stdout.strip()
    if code in ("200", "401", "403"):
        return OK, f"router mounted (HTTP {code} on :{port})"
    return FAIL, f"/intents answered HTTP {code} — router missing or daemon stale"


def check_failed_units() -> tuple[str, str]:
    problems, unreachable = [], []
    for label, host in (("box", BOX), ("nightshift", NIGHTSHIFT)):
        ok, out = ssh(host, "systemctl --failed --no-legend | head -5")
        if not ok and out == "(unreachable)":
            unreachable.append(label)
        elif out:
            units = [tok for l in out.splitlines() for tok in l.split()
                     if tok.endswith((".service", ".timer"))]
            problems.append(f"{label}: {', '.join(units) or out[:60]}")
    if problems:
        return FAIL, "; ".join(problems)
    if unreachable:
        return WARN, f"unreachable: {', '.join(unreachable)}"
    return OK, "no failed units"


def check_mail_triage() -> tuple[str, str]:
    ok, out = ssh(BOX, "tail -1 /root/Data/.datacore/state/mail/audit.jsonl")
    if not ok:
        return WARN, "box unreachable"
    try:
        ts = json.loads(out).get("completed_at", "")
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(ts.replace("Z", "+00:00"))
                 ).total_seconds() / 3600
    except (ValueError, json.JSONDecodeError):
        return FAIL, "audit log unreadable"
    if age_h > 26:
        return FAIL, f"last triage {age_h:.0f}h ago"
    return OK, f"triage {age_h:.1f}h ago"


def check_audio_stamp() -> tuple[str, str]:
    ok, out = ssh(BOX, "cat /root/.datacore/cos/audio-last-ok")
    if not ok:
        return WARN, "box unreachable"
    if out.startswith(date.today().isoformat()):
        return OK, out
    return FAIL, f"audio stamp is {out or 'missing'} — no audio today"


CHECKS = [
    ("journal", check_journal),
    ("journal_opened", check_journal_opened),
    ("repos_health", check_repos_health),
    ("org_readonly", check_org_readonly),
    ("intents_api", check_intents_api),
    ("failed_units", check_failed_units),
    ("mail_triage", check_mail_triage),
    ("audio_stamp", check_audio_stamp),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    failures = []
    for name, fn in CHECKS:
        try:
            status, detail = fn()
        except Exception as e:  # a broken check is itself a failure, loudly
            status, detail = FAIL, f"check crashed: {e}"
        print(f"  {status:4s} {name}: {detail}")
        if status == FAIL:
            failures.append(f"{name}: {detail}")

    today = date.today().isoformat()
    if failures:
        msg = f"smoke {today}: {len(failures)} FAILED — " + "; ".join(failures)[:180]
        print(msg)
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "Datacore smoke"'],
            capture_output=True, timeout=10)
        return 1
    print(f"smoke {today}: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
