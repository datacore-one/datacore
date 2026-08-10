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
import os
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_plane import ConfigError, doctor  # noqa: E402
from jobs.checks import run_check  # noqa: E402
from jobs.manifest import Job, ManifestError, load_manifest  # noqa: E402
from ledger.log import EventLog  # noqa: E402

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
WINSTON_SEND = (
    DATACORE_ROOT / ".datacore" / "modules" / "chief-of-staff" / "server" / "lib" / "winston_send.py"
)


def _default_actor() -> str:
    return os.environ.get("DATACORE_ACTOR") or socket.gethostname()


def _default_manifest_path() -> Path:
    return DATACORE_ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"


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


def _dispatch_alert(mode: str, job_name: str, failures: list[str]) -> None:
    """Dispatch exactly one alert for one failing job.

    `log` mode is stderr-only -- no external call is made. `telegram` mode
    tries `_send_telegram`; if that returns False (helper missing, or the
    send itself failed) it falls back to a stderr note rather than
    silently dropping the alert.
    """
    message = f"job.verify FAILED: {job_name} ({len(failures)} failure(s))"
    if mode == "telegram":
        if not _send_telegram(message):
            print(f"alert: telegram unavailable, logged only ({job_name})", file=sys.stderr)
    else:
        print(f"alert: {message}", file=sys.stderr)


def _check_job(job: Job) -> list[str]:
    """Run every artifact check for `job`, collecting failure strings.

    Never raises: an unexpected exception anywhere in the check pipeline
    becomes a failure string naming the job and the exception, so one
    job's bug can't take down verification of the rest of the manifest.
    """
    failures: list[str] = []
    try:
        for artifact in job.artifacts:
            failures.extend(run_check(artifact))
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
        choices=("mac", "box", "nightshift"),
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


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    manifest_path = Path(args.manifest) if args.manifest else _default_manifest_path()

    if args.doctor:
        _run_doctor(args.machine, manifest_path)
        return

    space = Path(args.space) if args.space else DATACORE_ROOT

    try:
        jobs = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"error: invalid manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"error: cannot read manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(1)

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
            _dispatch_alert(effective_alert, job.name, failures)

    if any_failed:
        sys.exit(1)

    print(f"OK {len(machine_jobs)} jobs {total_artifacts} artifacts")


if __name__ == "__main__":
    main()
