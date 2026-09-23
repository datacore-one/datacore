#!/usr/bin/env python3
"""job_verify.py — unified job-manifest verifier.

Loads a job manifest (`jobs.manifest.load_manifest`), filters to the jobs
declared for one `--machine`, and runs every artifact's contract check
(`jobs.checks.run_check`) for each of those jobs. Per job, the verdict
(ok/failures) is recorded as a `metric.attest` ledger event (unless
`--no-emit`), failures are reported on stderr, and exactly one alert is
dispatched per failing job.

Alert routing: each job's own manifest-declared `on_fail` (`log` or
`telegram`) is the source of truth for which channel that job's alert
goes to. `--alert`, when passed, OVERRIDES `on_fail` for every job in
this run (e.g. `--alert log` silences telegram for a run even if some
jobs are declared `on_fail: telegram`). Left unset (the default), each
failing job uses its own `on_fail`.

Usage:
    python3 job_verify.py --machine {mac,box,nightshift}
        [--manifest PATH] [--alert {log,telegram}] [--no-emit] [--space DIR]
        [--doctor]

`--doctor` mode: instead of running artifact checks, runs the config-plane
audit (`config_plane.doctor`) for `--machine` and prints its markdown
`report.table` to stdout. Purely informational -- it always exits 0 (a
manifest that fails to load is the one exception: see below), and
`--alert`/`--no-emit`/`--space` are irrelevant in this mode (ignored; no
events are written, no alerts are dispatched). `config_plane.doctor`'s own
SECRETS RULE guarantees the table never carries variable values, only
names -- see `config_plane.py` for that property's enforcement.

A manifest that fails to load (`ManifestError`) exits 1 with a clean
stderr message naming the manifest as the problem, same as the
artifact-check path. Any OTHER failure inside `doctor()` -- an `OSError`
or `ConfigError` raised while reading the canonical or a legacy env file
(e.g. a path that turns out to be a directory, not a regular file) --
is reported as `doctor failed: <message>` instead, still a clean stderr
message and exit 1, never mislabeled as a manifest problem and never a
traceback.

`DATACORE_CANONICAL_ENV` (advanced, primarily for tests/diagnostics):
when set, overrides the canonical env path `--doctor` mode audits
against, in place of `config_plane.CANONICAL_PATH`. Reading it here is
fine -- the "never reads `os.environ`" rule binds `config_plane`'s pure
functions, not this CLI layer.

Stdout/stderr discipline (matches ledger_cli.py): stdout carries only the
final `OK <n> jobs <m> artifacts` summary; every diagnostic (per-job
failure blocks, alert notes, clean error messages) goes to stderr.
Expected failures (a bad manifest, an unreadable manifest file) are caught
and reported as a clean message with exit code 1 -- never a traceback.

DATACORE_ROOT resolution mirrors `ledger/keys.py`: `$DATACORE_ROOT` env var,
else `~/Data`. It sets the default `--manifest` path
(`<DATACORE_ROOT>/.datacore/lib/jobs/manifest.yaml`), the default `--space`
(events are written to `<space>/.datacore/events/`), and the location of
the Telegram alert helper (`.datacore/modules/chief-of-staff/server/lib/winston_send.py`).

Actor resolution for emitted events (matches ledger_cli.py): `$DATACORE_ACTOR`,
else `socket.gethostname()`.

Per-job isolation: an unexpected exception while checking one job's
artifacts is captured as a failure for that job rather than propagating --
one broken job must never abort verification of the rest of the manifest.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_plane import ConfigError, doctor  # noqa: E402
from jobs.checks import check_repo_sync, run_check  # noqa: E402
from jobs.manifest import Job, ManifestError, load_manifest  # noqa: E402
from ledger.log import EventLog  # noqa: E402

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
WINSTON_SEND = (
    DATACORE_ROOT / ".datacore" / "modules" / "chief-of-staff" / "server" / "lib" / "winston_send.py"
)


def _default_actor() -> str:
    try:
        from actor_identity import this_actor
    except ImportError:
        import importlib.util as _ilu, pathlib as _pl
        _spec = _ilu.spec_from_file_location("actor_identity", _pl.Path(__file__).resolve().parent / "actor_identity.py")
        _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
        this_actor = _m.this_actor
    return this_actor()


def _default_manifest_path() -> Path:
    """The manifest belongs to the INSTALLATION, not to the data root.

    `$DATACORE_ROOT` stays first, because a test pointing it at a scratch tree
    must not fall through to the real manifest, and on mac, winston and
    nightshift the install and the data root are the same `~/Data` -- so this
    changes nothing there.

    They are NOT the same on the satellites: hermes and plur-claw run from
    `~/.datacore/v2-runner` while their spaces live elsewhere, so the default
    named a file that does not exist and every unattended run died with
    "cannot read manifest". hermes's cron works around it with an explicit
    `--manifest`; plur-claw has no such cron, so nothing there was verified at
    all, and `v2_verify`'s job-contract check failed on both for the same
    reason. The manifest that ships beside this file is the one belonging to
    the code actually running.
    """
    from_root = DATACORE_ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"
    if from_root.exists():
        return from_root
    beside = Path(__file__).resolve().parent / "jobs" / "manifest.yaml"
    return beside if beside.exists() else from_root


def _send_telegram(message: str) -> bool:
    """Best-effort Telegram alert via winston_send.py.

    Returns False (never raises) if the helper script isn't present on
    this machine, or if the subprocess exits nonzero -- callers must treat
    False as "fall back to logging", not as an error to propagate. Tests
    monkeypatch this function directly; it must never be exercised for
    real in the test suite.
    """
    if not WINSTON_SEND.exists():
        return False
    try:
        result = subprocess.run(
            [sys.executable, str(WINSTON_SEND)], input=message, capture_output=True, text=True
        )
    except OSError:
        return False
    return result.returncode == 0


_NO_EMIT = False


def _recurrence():
    """The recurrence module, by package import or by path (same fallback the
    two call sites below used to carry separately)."""
    try:
        from jobs import recurrence as _rec
    except ImportError:  # pragma: no cover
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "recurrence", Path(__file__).parent / "jobs" / "recurrence.py")
        _rec = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_rec)
    return _rec


def _prune_recurrence(jobs) -> None:
    """Forget counters for jobs the manifest no longer names (any machine).

    Runs on every real verification so a renamed or deleted job cannot stay
    "recurring" forever -- a record with no job can never receive the pass
    that would reset it.
    """
    if _NO_EMIT:
        return
    try:
        gone = _recurrence().prune({j.name for j in jobs})
    except Exception as exc:  # noqa: BLE001 -- bookkeeping must not stop verification
        print(f"recurrence prune skipped: {exc}", file=sys.stderr)
        return
    for name in gone:
        print(f"recurrence: forgot {name} (no longer in the manifest)", file=sys.stderr)


def _note_pass(job_name: str) -> None:
    """Reset a job's consecutive-failure count.

    Must be called for every PASSING job, not only failing ones -- otherwise a
    job that recovers keeps its old count and escalates on its next unrelated
    failure, which is a false alarm of exactly the kind this is meant to stop.
    """
    try:
        from jobs import recurrence as _rec
    except ImportError:  # pragma: no cover
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "recurrence", Path(__file__).parent / "jobs" / "recurrence.py")
        _rec = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_rec)
    if _NO_EMIT:
        return
    rec = _rec.record(job_name, failed=False)
    # Decision N7 (2026-09-23): a reset that could not be locked or saved is
    # warned loudly -- in the verifier's own report, not only on stderr.
    if isinstance(rec, dict) and rec.get("warning"):
        print(f"WARNING {job_name}: {rec['warning']}")
    tid = rec.get("task_id") if isinstance(rec, dict) else None
    if tid:
        # The pass is the DONE_WHEN of the task the streak filed.
        if _close_task(tid):
            print(f"recovered: task {tid} closed for {job_name}", file=sys.stderr)
        _rec.note_task(job_name, None)


def _artifact_signature(job, *, now: float | None = None) -> str:
    """path + mtime of every artifact: the same signature is the same failure.

    EXCEPT WHEN THE UNCHANGED ARTIFACT *IS* THE FAILURE. The dedup below exists
    so a verifier that looks every 30 minutes does not count one bad artifact
    48 times. But for a producer that has stopped producing, the artifact not
    changing is not "the same failure seen again" -- it is the failure getting
    worse. Measured 2026-09-21: nightshift's overnight run died on 09-18 and
    its manifest aged 29h -> 35h -> 59h while this signature stayed constant,
    so the verifier logged "alert withheld ... (1x); nothing new to report" for
    two and a half days. One alert, then silence, and a streak pinned at 1 so it
    could never become recurring either. The quieter the producer, the quieter
    the alarm.

    So a stale artifact's signature carries how many whole `max_age_hours`
    periods it has now missed, and a missing one carries the day. Each further
    missed period is a new failure: counted, delegated, and escalated like any
    other. Within one period it is still one failure, which is what the dedup
    was for.
    """
    parts = []
    now = time.time() if now is None else now
    today = _dt.date.today().isoformat()
    for a in getattr(job, "artifacts", []) or []:
        raw = os.path.expanduser(str(a.path).replace("{today}", today))
        try:
            mtime = os.stat(raw).st_mtime
        except OSError:
            parts.append(f"{raw}@missing:{today}")
            continue
        part = f"{raw}@{int(mtime)}"
        max_age = getattr(a, "max_age_hours", None)
        if max_age:
            try:
                from jobs.awake import awake_age
                age = awake_age(mtime, job.machine, now=now) if getattr(job, "machine", None) else now - mtime
            except Exception:  # noqa: BLE001 - a signature must never raise
                age = now - mtime
            missed = int(age // (float(max_age) * 3600))
            if missed >= 1:
                part += f"+missed{missed}"
        parts.append(part)
    return "|".join(parts)


def _artifact_tail(job, n: int = 8) -> str:
    """The last `n` lines of each artifact, for an alert that must carry them."""
    out = []
    today = _dt.date.today().isoformat()
    for a in getattr(job, "artifacts", []) or []:
        raw = os.path.expanduser(str(a.path).replace("{today}", today))
        try:
            lines = Path(raw).read_text(errors="replace").rstrip().splitlines()[-n:]
        except OSError:
            continue
        if lines:
            out.append("\n".join(lines))
    return ("\n\n" + "\n\n".join(out)) if out else ""


#: Where a recurring failure becomes a task. 2-datacore is the system space; the
#: machine goes on the task as SURFACE so the owner knows where to look.
TASK_FILE = os.environ.get("JOB_VERIFY_TASK_FILE") or str(
    DATACORE_ROOT / "2-datacore" / "org" / "next_actions.org")


def _delegate_repair(job, failures: list[str], rec: dict) -> tuple[str, str]:
    """Hand a recurring failure to an agent. Never raises; a delegation that
    cannot happen is reported as one, and the operator is told instead."""
    try:
        from jobs.autofix import delegate
    except ImportError:  # pragma: no cover - path layouts differ per host
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "autofix", Path(__file__).parent / "jobs" / "autofix.py")
        _mod = _ilu.module_from_spec(_spec)
        try:
            _spec.loader.exec_module(_mod)
        except Exception as exc:  # noqa: BLE001
            return "refused", f"autofix unavailable: {type(exc).__name__}: {exc}"
        delegate = _mod.delegate
    try:
        return delegate(job, failures, rec, root=DATACORE_ROOT)
    except Exception as exc:  # noqa: BLE001 -- verification must not break on this
        return "refused", f"{type(exc).__name__}: {exc}"


def _file_task(job, rec: dict, failures: list[str]) -> str | None:
    """A recurring failure is a defect with no owner; give it one.

    Escalation used to be a differently worded alert, once a day, forever:
    mac-id-churn reached 48 recurrences with nobody assigned. The third
    consecutive failure now files ONE task in the system space, with the
    machine, the job and the failure text, and the pass that ends the streak
    closes it. Best-effort: the adapter failing must not stop verification.
    """
    adapter = Path(__file__).resolve().parent / "org_workspace_adapter.py"
    heading = (f"job-verify: {job.name} is failing on {job.machine} "
               f"({rec.get('consecutive')} runs since {rec.get('first_failed')})")
    body = ("Recurring failure (DIP-0031: 3 or more consecutive runs). Failures this run:\n"
            + "\n".join(f"- {f}" for f in failures[:6])
            + f"\nProducer: {getattr(job, 'cmd', '') or 'see manifest'}"
            + f"\nSchedule: {getattr(job, 'schedule', '') or 'see manifest'}")
    cmd = [sys.executable, str(adapter), "add", "--allow-any-file", "--file", TASK_FILE,
           "--state", "TODO", "--heading", heading, "--tags", "datacore,ops,job_verify",
           "--priority", "B", "--property", f"SURFACE={job.machine}", "--property", f"JOB={job.name}",
           "--property", (f"DONE_WHEN=job {job.name} passes verification on {job.machine} "
                          "(job_verify records the pass and closes this)"),
           "--body", body]
    try:
        import json as _json
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                d = _json.loads(line)
                if d.get("added") and d.get("id"):
                    return str(d["id"])
        print(f"task not filed for {job.name}: {(out.stderr or out.stdout or '').strip()[-200:]}",
              file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 -- bookkeeping must not stop verification
        print(f"task not filed for {job.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return None


def _close_task(task_id: str) -> bool:
    adapter = Path(__file__).resolve().parent / "org_workspace_adapter.py"
    try:
        out = subprocess.run([sys.executable, str(adapter), "complete", "--file", TASK_FILE,
                              "--id", task_id], capture_output=True, text=True, timeout=120)
        return out.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _dispatch_alert(mode: str, job_name: str, failures: list[str], job=None) -> None:
    """Dispatch exactly one alert for one failing job.

    `log` mode is stderr-only -- no external call is made. `telegram` mode
    tries `_send_telegram`; if that returns False (helper missing, or the
    send itself failed) it falls back to a stderr note rather than
    silently dropping the alert.
    """
    # DIP-0035 Open Question #2, resolved 2026-09-03: a repeated failure must
    # not render identically to a first one. box-projection-drift had failed 22
    # consecutive times, correctly, while the drift it reported grew.
    # Threshold and semantics come from DIP-0031 (">=3 consecutive runs is a
    # recurring failure"), not from a second number invented here.
    try:
        from jobs import recurrence as _rec
    except ImportError:  # pragma: no cover - path layouts differ per host
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "recurrence", Path(__file__).parent / "jobs" / "recurrence.py")
        _rec = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_rec)

    # A dry run must not inflate the streak. `--no-emit` is the dry/test flag
    # (it suppresses ledger events), and running the verifier three times while
    # testing today pushed four jobs to "3 consecutive runs" that had failed
    # once. A counter polluted by its own observer is worse than no counter.
    # Scheduled runs emit, so production counting is unaffected.
    if _NO_EMIT:
        # ...AND MUST NOT PAGE ANYONE. A dry run used to fall through to the
        # Telegram send below: `--no-emit` on a host with `on_fail: telegram`
        # delivered a real alert about a verification nobody scheduled. Found
        # 2026-09-21 only because the send happened to fail from an ssh shell.
        message = f"job.verify FAILED: {job_name} ({len(failures)} failure(s))"
        mode = "log"
    else:
        sig = _artifact_signature(job) if job is not None else None
        _record = _rec.record(job_name, failed=True, artifact_sig=sig)
        if _record.get("same_artifact"):
            print(f"alert withheld: {job_name} failed on the same artifact already counted "
                  f"({_record.get('consecutive')}x); nothing new to report", file=sys.stderr)
            return
        # DELEGATE ON THE FIRST FAILURE, NOT THE THIRD.
        #
        # This waited for DIP-0031's recurring threshold, so failures one and
        # two still went to Telegram -- which is every alert the operator
        # actually received on 2026-09-20 and 09-21, each reading "(1
        # failure(s))". Delegation that begins after the third failure is not
        # "Winston handles it and tells me if he cannot"; it is the old
        # behaviour with a delay.
        #
        # Churn is bounded by the item id, which is one per job per day:
        # repeated failures of the same job reuse it instead of filing a
        # second. The recurring threshold still governs the ORG TASK and the
        # escalation wording, which is what DIP-0031 is about -- it was never
        # about who gets woken first.
        if job is not None:
            state, detail = _delegate_repair(job, failures, _record)
            if state == "delegated":
                print(f"delegated repair of {job_name} ({detail}); "
                      f"operator not alerted", file=sys.stderr)
                if _record.get("recurring") and not _record.get("task_id"):
                    tid = _file_task(job, _record, failures)
                    if tid:
                        _rec.note_task(job_name, tid)
                return
            print(f"could NOT delegate {job_name} ({detail}); "
                  f"escalating to the operator", file=sys.stderr)
        if _record.get("recurring") and job is not None and not _record.get("task_id"):
            tid = _file_task(job, _record, failures)
            if tid:
                _rec.note_task(job_name, tid)
                print(f"recurring: filed task {tid} for {job_name}", file=sys.stderr)
        message = _rec.describe(job_name, _record, len(failures))
        if job is not None and not getattr(job, "delegate", True):
            # A job that opts out of delegation is one whose failure IS the
            # message to a person -- the escalation report above all. Its
            # contract fails on one summary line; the lines above it are the
            # list the person needs. Carry them.
            message += _artifact_tail(job)
        if not _rec.should_alert(_record):
            print(f"alert suppressed: {job_name} is recurring "
                  f"({_record.get('consecutive')}x) and was already escalated today", file=sys.stderr)
            return
        _rec.note_alerted(job_name)
    if mode == "telegram":
        if not _send_telegram(message):
            print(f"alert: telegram unavailable, logged only ({job_name})", file=sys.stderr)
    else:
        print(f"alert: {message}", file=sys.stderr)


def _check_job(job: Job) -> list[str]:
    """Run every artifact check for `job`, collecting failure strings.

    Staleness gate (require_synced_repos): if the job declares one or more
    repo paths under ``require_synced_repos``, each is checked with
    ``check_repo_sync`` BEFORE the artifact content checks run.  When any
    repo is behind its upstream the artifact checks are skipped entirely --
    reading a file out of an unsynced repo and asserting on its content
    produces confident wrong verdicts (the real defect is the missing pull,
    not the file contents), which is worse than producing no verdict.  The
    repo-behind errors are returned in place of the artifact check results
    so the operator knows exactly which repo to sync and by how many commits.

    Never raises: an unexpected exception anywhere in the check pipeline
    becomes a failure string naming the job and the exception, so one
    job's bug can't take down verification of the rest of the manifest.
    """
    failures: list[str] = []
    try:
        for repo_path in job.require_synced_repos:
            failures.extend(check_repo_sync(repo_path))
        if failures:
            # Repo(s) are behind -- artifact content checks would read stale
            # input and emit wrong verdicts.  Return the sync errors only.
            return failures
        for artifact in job.artifacts:
            failures.extend(run_check(artifact, machine=job.machine))
    except Exception as exc:  # noqa: BLE001 -- deliberate: see docstring
        failures.append(f"unexpected exception checking job '{job.name}': {exc}")
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job_verify.py",
        description="Verify job-manifest artifact contracts for one machine.",
    )
    parser.add_argument(
        "--machine",
        required=True,
        metavar="NAME",   # not a fixed choice list: machines come from the installation roster
        help="Machine to verify jobs for",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Manifest path (default: <DATACORE_ROOT>/.datacore/lib/jobs/manifest.yaml)",
    )
    parser.add_argument(
        "--alert",
        choices=("log", "telegram"),
        default=None,
        help="Override every job's on_fail for this run (default: unset -- each job uses its own on_fail)",
    )
    parser.add_argument(
        "--no-emit",
        action="store_true",
        help="Do not write metric.attest ledger events",
    )
    parser.add_argument(
        "--space",
        default=None,
        help="Space directory attest events are written to (default: DATACORE_ROOT)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help=(
            "Run the config-plane doctor for --machine and print its report "
            "table instead of checking artifacts. Informational only: exits "
            "0 always (a broken manifest still exits 1); --alert/--no-emit/"
            "--space are ignored in this mode. Advanced: set "
            "$DATACORE_CANONICAL_ENV to override the canonical env path "
            "doctor audits against (primarily for tests/diagnostics)."
        ),
    )
    return parser


def _run_doctor(machine: str, manifest_path: Path) -> None:
    """Run `config_plane.doctor` for `machine` and print its table.

    Informational only -- always exits 0, EXCEPT:

    - A manifest that fails to load (`ManifestError`) -- reported the same
      way the artifact-check path reports it: `error: invalid manifest
      ...`, clean stderr, exit 1, never a traceback.
    - Any OTHER failure inside `doctor()` -- an `OSError` or `ConfigError`
      raised while reading the canonical or a legacy env file (e.g. a path
      that turns out to be a directory, not a regular file) -- reported as
      `doctor failed: <message>` instead. This is NOT the same branch as
      the manifest one above: doctor()'s canonical/legacy file reads have
      nothing to do with the manifest, and mislabeling one as the other
      (a real bug found by the Task 3.3 real-machine audit) makes the
      wrong file look like the problem. Still clean stderr, exit 1, never
      a traceback either way.

    `DATACORE_CANONICAL_ENV`, read directly from `os.environ` here (the
    CLI layer, not `config_plane`'s pure functions, so this doesn't
    violate doctor's "never reads os.environ" contract), overrides the
    canonical path passed to `doctor()` when set -- advanced, primarily
    for tests and diagnostics.
    """
    canonical_override = os.environ.get("DATACORE_CANONICAL_ENV")
    canonical_path = Path(canonical_override) if canonical_override else None

    try:
        report = doctor(machine, manifest_path=manifest_path, canonical_path=canonical_path)
    except ManifestError as exc:
        print(f"error: invalid manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ConfigError) as exc:
        print(f"doctor failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(report.table)


def _attest_space() -> Path:
    """First space checkout that converges: 2-datacore where present, else any
    `[0-9]-*` repository with an event log under the root, then under ~/Data
    (hermes keeps a 5-plur clone at ~/Data/2-plur), then ~/spaces."""
    home = Path.home()
    roots = [DATACORE_ROOT, home / "Data", home / "spaces"]
    for r in roots:
        c = r / "2-datacore"
        if (c / ".datacore" / "events").is_dir() and (c / ".git").exists():
            return c
    for r in roots:
        for c in sorted(r.glob("[0-9]-*")):
            if (c / ".datacore" / "events").is_dir() and (c / ".git").exists():
                return c
    return DATACORE_ROOT


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # See _dispatch_alert: a dry run must not inflate the recurrence streak.
    global _NO_EMIT
    _NO_EMIT = bool(getattr(args, "no_emit", False))

    manifest_path = Path(args.manifest) if args.manifest else _default_manifest_path()

    if args.doctor:
        _run_doctor(args.machine, manifest_path)
        return

    # The attestation must land in a repository that CONVERGES. The root
    # checkout on a runner never pushes, so a job.verify written there stayed
    # on that host forever: on 2026-09-06 not one verifier attestation from
    # any agent host had reached the operator's machine, and the per-principal
    # scoreboard rows read "not heard from". First space that carries an event
    # log wins; the root is the last resort.
    space = Path(args.space) if args.space else _attest_space()

    try:
        jobs = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"error: invalid manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"error: cannot read manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    _prune_recurrence(jobs)
    machine_jobs = [job for job in jobs if job.machine == args.machine]

    if not machine_jobs:
        print("OK 0 jobs 0 artifacts")
        return

    log = None if args.no_emit else EventLog(space, _default_actor())

    total_artifacts = 0
    any_failed = False
    for job in machine_jobs:
        failures = _check_job(job)
        total_artifacts += len(job.artifacts)
        ok = not failures

        if log is not None:
            log.append(
                "metric.attest",
                {"metric": "job.verify", "job": job.name, "ok": ok, "failures": failures},
            )

        if not ok:
            any_failed = True
            print(f"job '{job.name}' FAILED:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            effective_alert = args.alert if args.alert is not None else job.on_fail
            _dispatch_alert(effective_alert, job.name, failures, job=job)
        else:
            # Reset on every pass, not only on the transition. A job that
            # recovers must not carry its old count into an unrelated future
            # failure and escalate on the first one.
            _note_pass(job.name)

    if any_failed:
        sys.exit(1)

    print(f"OK {len(machine_jobs)} jobs {total_artifacts} artifacts")


if __name__ == "__main__":
    main()
