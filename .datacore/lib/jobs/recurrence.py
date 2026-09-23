#!/usr/bin/env python3
"""recurrence.py — make a repeated job.verify failure read differently.

RESOLVES DIP-0035 OPEN QUESTION #2, which reads:

    "Remediation policy — job_verify.py currently only detects and alerts;
     whether a Phase 6+ follow-on should add auto-retry or auto-escalation on
     repeated failure is unresolved."

It does NOT invent a threshold. DIP-0031 already settled that for nightshift
task failures and this adopts it verbatim rather than picking a second number:

    "A task failing with the same category >=3 consecutive runs is a
     *recurring failure* — CoS triage escalates it as a decision item, and the
     executor MUST stop retrying it."

WHY IT WAS NEEDED. Measured on winston 2026-09-03: `box-projection-drift` had
22 consecutive failure records. The detector was correct every single time and
the drift it reported grew while it fired — 0-personal's projection went from
49 extra tasks to 339. Three other checks had been failing for days.

A 22nd consecutive failure rendered identically to a 1st, so the loudest
signal in the system was the one that had been normalised into background.
Every one of the five bug classes fixed this week was being reported before it
was found: box-briefing was red for six days, ledger-ingest aged 26 -> 50 ->
74 hours, and the nightshift watchdog said "This has happened 59 times in 48h"
in its own message.

So this is not another detector. It is the missing consumer of detections that
already work. The claim it adds is narrow and true: after three identical
runs, the interesting fact is no longer the failure — it is that nobody is
reading the alert.

NOT AUTO-REMEDIATION. DIP-0035 lists auto-retry alongside escalation; this
implements only escalation. Restarting a job whose producer is unscheduled
would loop forever, and a checker that repairs what it measures cannot be
trusted to measure it.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import pathlib
import sys

# DIP-0031: ">=3 consecutive runs is a recurring failure". Same number, on
# purpose -- two thresholds for one idea is bug class 1.
RECURRING_AFTER = 3

STATE = pathlib.Path(
    os.environ.get("DATACORE_STATE", pathlib.Path.home() / ".datacore" / "state")
) / "job-verify-recurrence.json"


def _path() -> pathlib.Path:
    """Resolve the state file at CALL time, not import time.

    Import-time resolution meant a test that set DATACORE_STATE after this
    module was first imported still wrote to ~/.datacore/state -- and did:
    eleven test job names (boom-job, log-job x18, telegram-job x18 ...) were
    found in the production recurrence file on 2026-09-03, inflating the
    "recurring" summary the alerts read. A module attribute override (tests
    that set STATE directly) still wins, so both isolation styles work.
    """
    env = os.environ.get("DATACORE_STATE")
    if env and STATE == _DEFAULT_STATE:
        return pathlib.Path(env) / "job-verify-recurrence.json"
    return STATE


_DEFAULT_STATE = STATE


def _load() -> dict:
    """Never raises. A corrupt or absent state file must not stop verification.

    Losing the count degrades escalation to a first-occurrence alert, which is
    exactly today's behaviour -- acceptable. Raising here would let a bookkeeping
    file take down the check that reports everything else.
    """
    try:
        d = json.loads(_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> bool:
    """Write the state; True when it reached disk. Never raises.

    A failed save must not stop verification, but it must not be silent
    either: `record` turns False into a WARNING (decision N7).
    """
    try:
        state = _path()
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = state.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
        tmp.replace(state)
        return True
    except OSError:
        return False


def _warn(rec: dict, message: str) -> None:
    """Loud, never fatal: stderr AND the job's record (decision N7, 2026-09-23).

    `describe` puts the record's warning into the alert line, so a failure
    report carries it too. Verification of the remaining jobs continues.
    """
    line = f"WARNING: recurrence: {message}"
    print(line, file=sys.stderr)
    rec["warning"] = f"{rec['warning']} | {line}" if rec.get("warning") else line


def record(job_name: str, failed: bool, *, today: str | None = None,
           artifact_sig: str | None = None) -> dict:
    """Update and return this job's recurrence record.

    Returns {consecutive, first_failed, recurring}. A pass resets the count to
    zero and clears first_failed -- an intermittent job must not accumulate
    toward escalation across unrelated incidents.
    """
    today = today or datetime.date.today().isoformat()
    # Serialise load-modify-save. Two verifiers at once (cron and a hand run)
    # otherwise race: both read N, both write N+1, and a pass's reset can be
    # overwritten by a concurrent fail's increment. A counter that can be off
    # by one is fine; a reset that can be lost is not -- it is how a recovered
    # job stays "recurring".
    #
    # When the lock or the save fails, verification CONTINUES but says so
    # loudly (owner decision N7, 2026-09-23): a WARNING on stderr and in the
    # returned record, naming the reset that may be lost. It used to degrade
    # silently, and a lost reset is exactly how a recovered job reads
    # "recurring" on its next single failure
    # (DatacoreSpec/NightshiftGates.lean, Recurrence).
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(_locked())
        except OSError as exc:
            return _record_without_lock(job_name, failed, today, artifact_sig, exc)
        return _record_unlocked(job_name, failed, today, artifact_sig)


def _record_without_lock(job_name: str, failed: bool, today: str,
                         artifact_sig: str | None, exc: OSError) -> dict:
    """A home where mkdir or flock fails (read-only, NFS without locks) must
    not take down verification of every remaining job. Degrade to the
    unserialised update -- and warn, because a concurrent verifier can now
    overwrite this record."""
    rec = _record_unlocked(job_name, failed, today, artifact_sig)
    what = ("this pass's reset to 0" if not failed
            else f"this failure's count ({rec['consecutive']})")
    _warn(rec, f"lock unavailable for {job_name} ({exc}); recorded without "
               f"it, so a concurrent verifier can overwrite {what}")
    return rec


@contextlib.contextmanager
def _locked():
    state = _path()
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = state.with_suffix(".lock")
    with open(lock, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _record_unlocked(job_name: str, failed: bool, today: str,
                     artifact_sig: str | None = None) -> dict:
    state = _load()
    rec = state.get(job_name) or {"consecutive": 0, "first_failed": None}
    if failed:
        # ONE FAILURE PER ARTIFACT, not per look. The verifier runs every 30
        # minutes; a producer runs three times a day. Re-reading the same
        # failed artifact counted mac-config-drift 8x and mac-id-churn 48x on
        # 2026-09-05 for one and two real failures -- the streak was a clock,
        # not a count, and the operator learned to ignore it. A signature of
        # the artifacts (path + mtime) that has not changed since the last
        # recorded failure is the same failure: reported once, counted once.
        if artifact_sig and rec.get("artifact_sig") == artifact_sig:
            rec["same_artifact"] = True
        else:
            rec["consecutive"] = int(rec.get("consecutive") or 0) + 1
            rec["first_failed"] = rec.get("first_failed") or today
            rec["last_failed"] = today
            rec["same_artifact"] = False
            if artifact_sig:
                rec["artifact_sig"] = artifact_sig
    else:
        before = int(rec.get("consecutive") or 0)
        rec = {"consecutive": 0, "first_failed": None, "last_passed": today,
               "task_id": rec.get("task_id")}
    rec["recurring"] = rec["consecutive"] >= RECURRING_AFTER
    state[job_name] = rec
    if not _save(dict(state)):
        out = dict(rec)
        if failed:
            _warn(out, f"could not save the failure count for {job_name} "
                       f"(now {rec['consecutive']}); the streak on disk is stale")
        else:
            _warn(out, f"could not save the reset for {job_name}: the streak of "
                       f"{before} consecutive failure(s) is still on disk, so its "
                       f"next single failure will read {before + 1}"
                       + (" and escalate as recurring" if before + 1 >= RECURRING_AFTER else ""))
        return out
    return rec


def prune(known: set[str]) -> list[str]:
    """Drop records for jobs the manifest no longer names. Returns what went.

    A record for a job that no longer exists can never receive a pass, so it
    stays "recurring" forever and the summary keeps naming it: on 2026-09-03
    winston reported box-registry-gc 3x recurring for a job that had been
    renamed to mac-registry-gc that morning. Class 4, applied to the counter.
    """
    if not known:
        # A manifest that loaded with zero jobs is not a reason to forget
        # every counter -- that is the same wipe a failed load would cause.
        return []
    with _locked():
        state = _load()
        gone = sorted(k for k in state if k not in known)
        if gone:
            for k in gone:
                del state[k]
            _save(state)
        return gone


def should_alert(rec: dict, *, today: str | None = None) -> bool:
    """Alert on every failure below the threshold, ONCE at escalation, then
    once a day while it stays recurring.

    The escalation text said "needs a decision, not another alert" while
    being another alert every 30 minutes: six mac jobs produced twelve
    Telegram messages an hour the moment the relay started working
    (2026-09-03). A reader cannot decide anything inside that.
    """
    today = today or datetime.date.today().isoformat()
    if not rec.get("recurring"):
        return True
    if int(rec.get("consecutive") or 0) == RECURRING_AFTER:
        return True
    return rec.get("last_alerted") != today


def note_task(job_name: str, task_id: str | None) -> None:
    """Remember (or forget) the task filed for this job's recurrence."""
    try:
        with _locked():
            state = _load()
            rec = state.get(job_name)
            if isinstance(rec, dict):
                if task_id:
                    rec["task_id"] = task_id
                else:
                    rec.pop("task_id", None)
                state[job_name] = rec
                _save(state)
    except OSError:
        pass


def note_alerted(job_name: str, *, today: str | None = None) -> None:
    today = today or datetime.date.today().isoformat()
    try:
        with _locked():
            state = _load()
            rec = state.get(job_name)
            if isinstance(rec, dict):
                rec["last_alerted"] = today
                state[job_name] = rec
                _save(state)
    except OSError:
        pass


def describe(job_name: str, rec: dict, n_failures: int) -> str:
    """The alert line. Below the threshold it is unchanged from before.

    Above it, the sentence changes subject: the failure is no longer the news.
    Naming the count and the date is what makes a repeat visibly a repeat --
    the whole defect was that it did not.
    """
    n = int(rec.get("consecutive") or 0)
    warn = f" {rec['warning']}" if rec.get("warning") else ""
    if not rec.get("recurring"):
        return f"job.verify FAILED: {job_name} ({n_failures} failure(s)){warn}"
    since = rec.get("first_failed") or "unknown"
    return (
        f"job.verify RECURRING: {job_name} has failed {n} consecutive runs "
        f"since {since} ({n_failures} failure(s) this run). "
        f"Per DIP-0031 this is a recurring failure and needs a decision, not "
        f"another alert: fix the producer, or delete the check.{warn}"
    )


def summary() -> list[dict]:
    """Every job currently in a recurring state, worst first."""
    out = [dict(job=k, **v) for k, v in _load().items() if v.get("recurring")]
    return sorted(out, key=lambda r: -int(r.get("consecutive") or 0))


def main() -> int:
    rows = summary()
    if not rows:
        print("no recurring job failures")
        return 0
    print(f"{len(rows)} job(s) in a recurring failure state "
          f"(>={RECURRING_AFTER} consecutive, DIP-0031):")
    for r in rows:
        print(f"  {r['consecutive']:>3}x since {r.get('first_failed')}  {r['job']}")
    print("\nEach of these needs a decision, not another alert.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
