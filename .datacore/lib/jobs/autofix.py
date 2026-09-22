#!/usr/bin/env python3
"""Winston takes the failure, Miles takes the repair, and you hear about it only
when that does not work.

WHAT CHANGED AND WHY. A recurring job failure used to do two things: file an
unassigned org TODO, and send the operator a Telegram message once a day for as
long as it kept failing. The TODO had no owner, so it aged; the message was
addressed to the one person on the fleet who is not always awake. Nothing in
that loop repaired anything, and a daily message nobody can act on at the moment
they receive it trains the reader to stop reading.

Now the third consecutive failure is DELEGATED: a ledger item addressed to
miles, carrying the failure text, the producer, and a check that decides for
itself whether the repair worked. The operator is told when -- and only when --
that path is exhausted:

    the gate refuses the delegation      → nobody can take it, so say so now
    miles dead-letters it                → three failed attempts, genuinely stuck
    it is still open after ESCALATE_H    → not progressing; a person should look

THE THRESHOLD IS NOT A NEW NUMBER. Three consecutive runs is DIP-0031's
definition of a recurring failure, which job_verify already uses to decide the
failure is real rather than a blip. A second threshold invented here would be a
second definition of the same thing.

WHAT MILES MAY NOT DO is the part that matters. See jobs/fix_check.py: the
repair is judged against the contract as it stood when the work was handed over,
so making the check pass by weakening the check does not count as passing.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# `jobs.fix_check`, never bare `fix_check`. The bare form resolved only when
# lib/jobs itself was on sys.path -- which the test file arranged and nothing in
# production does. job_verify imports `jobs.autofix` with lib on the path, so
# on every host the first real delegation died with ModuleNotFoundError and
# fell through to "could NOT delegate ... escalating to the operator": the
# first-failure alerts the owner kept receiving on 2026-09-20/21 WERE the
# delegation path, failing at import. Found 2026-09-22; not one delegation
# from job_verify had ever succeeded anywhere.
from jobs.fix_check import contract_sha  # noqa: E402

#: Hours an open repair may sit before the operator is told it is not moving.
ESCALATE_H = 24

MARK = "autofix"


def _space(root: Path) -> Path:
    """Where repair items live: the system space, which is where the jobs are."""
    return root / "2-datacore"


ROSTER = LIB.parent / "registry" / "infrastructure.yaml"


def host_of(actor: str, roster: Path | None = None) -> str | None:
    """The machine whose ledger_actors include `actor`, per the roster; None if unknown."""
    import yaml

    try:
        doc = yaml.safe_load((roster or ROSTER).read_text()) or {}
    except Exception:  # noqa: BLE001 -- an unreadable roster is "unknown", not a crash
        return None
    for host, entry in (doc.get("servers") or {}).items():
        if isinstance(entry, dict) and actor in (entry.get("ledger_actors") or []):
            return host
    return None


def delegate(job, failures: list[str], rec: dict, *, root: Path,
             assignee: str = "miles", dry: bool = False,
             roster: Path | None = None) -> tuple[str, str]:
    """Hand one failing job to `assignee`. Returns (state, detail).

    state: delegated | refused | exists

    REFUSED, BEFORE ANYTHING IS WRITTEN, when the repair cannot be checked
    where it would run. The done-condition is fix_check on `job.machine`'s
    artifacts, and it runs where the assignee's dispatcher runs. Miles runs on
    nightshift, so a box or mac job handed to him can only ever dead-letter
    after three attempts -- which is exactly what every delegation of a box
    or mac job did on 2026-09-21/22, and why the escalation report grew five
    entries, one of them the escalation job itself. "Fixes are not possible"
    is decidable up front here; saying so is what lets the operator be told
    now rather than after three failed attempts and a 48-hour window.
    """
    from actor_identity import this_actor
    from ledger.log import EventLog
    from ledger.policy import guarded_append, PolicyError

    if not getattr(job, "delegate", True):
        return "refused", f"{job.name} opts out of delegation (delegate: false); a person owns it"
    host = host_of(assignee, roster)
    if host and host != job.machine:
        return "refused", (f"{assignee} runs on {host} and cannot verify a {job.machine} job "
                           f"from there; a person owns it")

    manifest = LIB / "jobs" / "manifest.yaml"
    sha = contract_sha(job.name, manifest)
    if not sha:
        return "refused", f"{job.name} is not in the manifest"

    space = _space(root)
    actor = this_actor()
    iid = f"{MARK}-{job.name}-{time.strftime('%Y%m%d', time.gmtime())}"

    title = f"Repair {job.name}: failing on {job.machine} since {rec.get('first_failed')}"
    check = (f"python3 .datacore/lib/jobs/fix_check.py --job {job.name} "
             f"--machine {job.machine} --contract-sha {sha}")

    body = repair_body(job, failures, rec)

    if dry:
        return "delegated", f"would delegate {iid} to {assignee}"

    try:
        guarded_append(EventLog(space, actor), "item.create",
                       {"id": iid, "title": title, "assignee": assignee,
                        "check": check, "body": body, "requested_by": actor,
                        "autofix": True, "job": job.name, "machine": job.machine,
                        "contract_sha": sha,
                        # Repairing a producer is engineering, not research. The
                        # title heuristics cannot know that and inferred the
                        # default route, which framed the executing agent as a
                        # literature reviewer and handed it a broken cron job.
                        "route": "dev"})
    except PolicyError as exc:
        return "refused", f"the gate refused the repair item: {exc}"
    except Exception as exc:  # noqa: BLE001 -- delegation must not break verification
        return "refused", f"{type(exc).__name__}: {exc}"
    return "delegated", f"{iid} -> {assignee}"


def repair_body(job, failures: list[str], rec: dict) -> str:
    """What the repairing agent is actually told.

    Separate and pure so the boundary wording is testable. An agent that learns
    the rule by being REFUSED has already spent one of its three attempts
    discovering what it was not allowed to do; the instruction has to say it up
    front, and something has to keep it saying it.
    """
    return "\n".join([
        f"{job.name} has failed {rec.get('consecutive')} consecutive verification runs.",
        "",
        "Failures this run:",
        *[f"  - {f}" for f in failures[:6]],
        "",
        f"Producer: {getattr(job, 'cmd', '') or 'see manifest'}",
        f"Schedule: {getattr(job, 'schedule', '') or 'see manifest'}",
        "",
        "FIX THE PRODUCER, NOT THE CHECK.",
        "The check below re-reads this job's contract and refuses if it changed:",
        "loosening the regex, widening exit_ok, raising max_age_hours or removing",
        "the job all make verification pass and leave the fleet worse. If the",
        "contract is genuinely wrong, say so and stop -- that is a human's call,",
        "because the contract is what decides whether this job is healthy.",
    ])


def repairs(root: Path) -> list[dict]:
    """Every repair item and what became of it.

    Read from the ledger rather than a side file: the delegation happened in the
    ledger, and a second record of it would be a second thing to keep true.

    `closed_kind` is the field that matters. item.dismiss is overloaded -- org
    ingestion emits it when a human marks a task done, and the dead-letter emits
    it when an agent gave up after three attempts. Same event, opposite
    meanings; "dropped" is the one that means the operator now has to look.
    """
    from ledger.fold import fold
    from ledger.log import read_events

    space = _space(root)
    try:
        state = fold(read_events(space))
    except Exception:  # noqa: BLE001 -- an unreadable ledger is not an escalation
        return []
    out = []
    for iid, item in sorted(state.items.items()):
        payload = item.payload or {}
        if not (payload.get("autofix") or iid.startswith(MARK)):
            continue
        out.append({
            "id": iid,
            "status": item.status,
            "closed_kind": item.closed_kind,
            "closed_reason": item.closed_reason,
            "job": payload.get("job", ""),
            "assignee": payload.get("assignee", ""),
            "owner": item.owner,
            "closed_at": item.closed_at,
            # Each history line begins with the HLC of its event, so the first
            # one dates the item's creation.
            "opened_at": (item.history[0].split(" ", 1)[0] if item.history else None),
        })
    return out


def escalations(root: Path, *, window_h: float = ESCALATE_H * 2,
                now_ms: float | None = None) -> list[str]:
    """Repairs the operator genuinely has to hear about, and nothing else.

    The conditions the delegation path cannot resolve by itself: given up on, or
    closed without being verified. A repair in flight is not one of them.

    WINDOWED, and that is not a detail. A dismissal cannot be undone -- the
    ledger is append-only and a second item.dismiss on a closed item changes
    nothing -- so an unwindowed reading of "was it dropped" reports the same
    dead-letter every hour for the rest of the installation's life. The first
    version of this function did exactly that, and it would have replaced one
    permanent daily alert with a permanent hourly one: the precise failure being
    designed away.

    A dead-letter stops being news once it is older than the window. That is
    safe because it is not the record of the problem -- the JOB is. If the job
    is still failing, job_verify files a fresh repair with today's date and this
    escalates again on its own; if it recovered, there is nothing to say.
    """
    now = now_ms if now_ms is not None else time.time() * 1000.0
    acked = _acked()
    out = []
    for r in repairs(root):
        if r["id"] in acked:
            continue
        if r["status"] in ("created", "claimed"):
            # THE THIRD CONDITION, which this module's docstring promised from
            # the start and the first version never implemented. A repair
            # nobody claims is not dismissed, so it matched nothing above and
            # would have sat in `created` forever: the failure withheld from
            # the operator, handed to nobody, and reported by nothing. That is
            # delegation as a way of losing failures, the exact outcome the
            # escalation job exists to prevent.
            age = _age_h(r.get("opened_at"), now)
            if age is not None and age > ESCALATE_H:
                out.append(f"{r['job']}: repair {r['status']} for {age:.0f}h and not "
                           f"finished — nobody is completing it")
            continue
        if r["status"] != "dismissed":
            continue
        if r["closed_kind"] in ("done", "housekeeping"):
            continue
        age_h = _age_h(r["closed_at"], now)
        if age_h is not None and age_h > window_h:
            continue
        if r["closed_kind"] == "dropped":
            out.append(f"{r['job']}: {r['assignee']} gave up — "
                       f"{r['closed_reason'] or 'dead-lettered'}")
        else:
            out.append(f"{r['job']}: repair closed as {r['closed_kind'] or 'unknown'} "
                       f"without verifying")
    return out


#: Escalations the operator has seen. Local state, not a ledger event: "I read
#: this" is one person's decision about their own alerting, and the fleet's
#: shared history should not fill up with it.
ACK_FILE = Path.home() / ".datacore" / "state" / "autofix-acked.json"


def _acked() -> set[str]:
    try:
        return set(json.loads(ACK_FILE.read_text()).get("acked", []))
    except Exception:  # noqa: BLE001 -- a missing or broken ack file acks nothing
        return set()


def ack(item_id: str) -> None:
    """Stop escalating one repair the operator has dealt with.

    An escalation with no way to say "seen" pages until it ages out, which is
    how a person learns to ignore the channel. Acknowledging does NOT fix the
    job: if the underlying failure persists, job_verify files a fresh repair
    with a new id and it escalates again. This silences the notification, not
    the problem, and the distinction is why it is safe.
    """
    current = _acked()
    current.add(item_id)
    ACK_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ACK_FILE.write_text(json.dumps(
        {"acked": sorted(current), "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        indent=2))


def _age_h(hlc: str | None, now_ms: float) -> float | None:
    """Hours since an HLC timestamp, or None if it cannot be read.

    None means "cannot tell", and a thing that cannot be dated is reported
    rather than silently aged out -- an unreadable timestamp must not become a
    way for an escalation to disappear.
    """
    if not hlc:
        return None
    try:
        return (now_ms - float(str(hlc).split(".")[0])) / 3600000.0
    except (ValueError, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--list", action="store_true", help="show repair items and their state")
    ap.add_argument("--escalations", action="store_true",
                    help="print only what the operator has to act on; exit 1 if any")
    ap.add_argument("--ack", metavar="ITEM_ID",
                    help="stop escalating one repair you have dealt with "
                         "(silences the notification, not the problem: a job that "
                         "keeps failing gets a fresh repair and escalates again)")
    a = ap.parse_args()

    import os
    root = Path(a.root or os.environ.get("DATACORE_ROOT") or (Path.home() / "Data"))

    if a.ack:
        ack(a.ack)
        print(f"acknowledged {a.ack}; it will not escalate again")
        return 0

    if a.escalations:
        rows = escalations(root)
        for r in rows:
            print(f"  {r}")
        # Positively, so "the check ran and found nothing" cannot be mistaken
        # for "the check did not run" -- DIP-0046 §8.
        print(f"autofix: {len(rows)} repair(s) need a person")
        return 1 if rows else 0

    if a.list:
        rows = repairs(root)
        for r in rows:
            mark = r["status"] if r["status"] != "dismissed" else f"dismissed/{r['closed_kind']}"
            print(f"  {mark:<18} {r['id']:<44} {r['job']} -> {r['assignee']}")
        print(f"\nautofix: {len(rows)} repair item(s)")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
