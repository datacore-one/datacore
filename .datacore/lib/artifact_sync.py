#!/usr/bin/env python3
"""artifact_sync.py -- pull CoS briefing artifacts from box to mac (Task 6.1).

The box is the source of truth for the CoS briefing artifacts (it
generates them); the mac is a read-only puller. This closes the
mac-single-point-of-failure gap (ENG-2026-0612-017) in the box -> mac
direction: if the mac's own generation ever breaks or falls behind, it
still has a fresh copy synced FROM the box.

Two functions, cleanly split:

- `sync_plan(role, today=None)` -- pure, no I/O: WHAT to sync for a given
  role, as `(remote_path, local_path)` pairs. `today` is injectable so
  callers (and tests) can pin the calendar day instead of reading the
  wall clock.
- `run_sync(role, dry_run=False, env=None, today=None)` -- HOW: turns a
  plan into rsync-over-ssh commands and (unless `dry_run`) actually runs
  them. `env` is injectable (defaults to `config_plane.load()`, the
  canonical `~/.datacore/datacore.env` reader) so tests never need a real
  `COS_SERVER_SSH` value or a real box. `today` is forwarded verbatim to
  `sync_plan` -- `run_sync` itself never reads the clock; `main()` reads
  it exactly once and passes the same value to both its preview
  `sync_plan` call and to `run_sync`, so a run straddling midnight can
  never see two different dates for the same invocation.

Role gating:
  - "client" (mac): pull the three CoS briefing artifacts FROM the box.
  - "server" (box) or anything else: no-op -- the box IS the source, it
    never pulls from itself. An empty plan short-circuits `run_sync`
    before it even looks at `COS_SERVER_SSH`.

Failure handling (scoped, binding): `run_sync` never raises on a
sync/network failure -- a missing `COS_SERVER_SSH`, a failed rsync, a
timeout, or a missing `rsync` binary all become a human-readable error
string in the returned list instead of an exception, so a caller (this
module's own CLI, or a cron/launchd job) can handle a partial or total
failure without wrapping every call in try/except. This scoping is
deliberate: if `env` is omitted, `config_plane.load()` can still raise
`ConfigError` on a malformed (not merely missing) canonical env file --
that is a system-configuration problem, not a sync/network failure, and
is allowed to propagate by design rather than being masked here.

No real host or IP is ever hardcoded here -- `COS_SERVER_SSH` always
comes from the config plane (or an injected `env` dict in tests, using
fake values like `fake@host`).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import config_plane

# Remote base is deliberately a `~`-relative string, not expanded here --
# it is expanded by the remote login shell when rsync invokes it over ssh
# (`ssh_target:~/...`). The local base IS expanded (via `Path.home()`)
# because it names a path on THIS machine, resolved right now.
REMOTE_BASE = "~/.datacore/cos"
LOCAL_BASE = Path.home() / ".datacore" / "cos"

RSYNC_TIMEOUT_SECONDS = 20  # passed to rsync itself via --timeout
SUBPROCESS_TIMEOUT_SECONDS = 30  # outer guard around the whole rsync call


def _today() -> str:
    """Local calendar date (ISO `YYYY-MM-DD`), derived from `time.time()`.

    Reads `time.time()` rather than `datetime.now()`/`date.today()` so a
    single, well-known call can be monkeypatched in tests to pin "today"
    without freezing the whole `datetime` module.
    """
    return date.fromtimestamp(time.time()).isoformat()


def sync_plan(role: str, today: str | None = None) -> list[tuple[str, str]]:
    """(remote_path, local_path) pairs to pull for `role`. Pure -- no I/O.

    role "client" (mac): the three CoS briefing artifacts --
    `app-briefing.json` (dated, under `briefings/{today}/`), `answers.yaml`,
    and `facts.json` (both un-dated, directly under the cos base) -- paired
    remote (box, `~/.datacore/cos/...`) to local (this machine's home,
    same shape).

    role "server" (box) or anything else: `[]` -- the box never pulls from
    itself.

    `today` defaults to `_today()` (the injectable wall-clock read above)
    when omitted; passing it explicitly bypasses the clock entirely.
    """
    if role != "client":
        return []

    day = today if today is not None else _today()

    return [
        (
            f"{REMOTE_BASE}/briefings/{day}/app-briefing.json",
            str(LOCAL_BASE / "briefings" / day / "app-briefing.json"),
        ),
        (f"{REMOTE_BASE}/answers.yaml", str(LOCAL_BASE / "answers.yaml")),
        (f"{REMOTE_BASE}/facts.json", str(LOCAL_BASE / "facts.json")),
    ]


def _rsync_argv(ssh_target: str, remote_path: str, local_path: str) -> list[str]:
    return ["rsync", "-az", f"--timeout={RSYNC_TIMEOUT_SECONDS}", f"{ssh_target}:{remote_path}", local_path]


def run_sync(
    role: str,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
    today: str | None = None,
) -> list[str]:
    """Execute (or, if `dry_run`, merely preview) the sync plan for `role`.

    Never raises on a sync/network failure -- every such failure mode
    (missing `COS_SERVER_SSH`, failed rsync, timeout, missing `rsync`
    binary) becomes an "error: ..." string in the returned list instead of
    an exception. This does NOT cover `config_plane.load()` itself raising
    `ConfigError` on a malformed canonical env file when `env` is omitted
    -- that's a system-config problem, not a sync/network one, and
    propagates by design.

    `today` is forwarded verbatim to `sync_plan` -- this function never
    reads the clock itself. Passing it explicitly (as `main()` does)
    guarantees the plan built here matches whatever plan a caller already
    logged/previewed, even if the call straddles local midnight.

    `env` defaults to `config_plane.load()` (the canonical env-file
    reader); tests should pass an explicit dict instead of relying on this
    machine's real `~/.datacore/datacore.env`.

    - Empty plan (role != "client"): returns `[]` immediately -- no env
      lookup, no `COS_SERVER_SSH` check, nothing to sync.
    - Non-empty plan, `COS_SERVER_SSH` missing from `env`: returns a
      single "error: ..." string naming the missing var -- checked before
      building any command, so this happens whether or not `dry_run` is
      set (there is no valid command to build without an ssh target).
    - `dry_run=True`: for each pair, appends the single-line rsync command
      string that WOULD run, without creating any directory or invoking
      subprocess at all.
    - `dry_run=False`: for each pair, creates the local path's parent
      directory (`mkdir -p` equivalent), then runs the rsync command via
      `subprocess.run` with a `SUBPROCESS_TIMEOUT_SECONDS` guard. Success
      appends an "ok: ..." string; a non-zero exit, a timeout, or an
      `OSError` (e.g. `rsync` not on `PATH`) all append an "error: ..."
      string instead -- and the loop continues to the next pair either
      way, so one bad pair never stops the rest.
    """
    plan = sync_plan(role, today=today)
    if not plan:
        return []

    if env is None:
        env = config_plane.load()

    ssh_target = env.get("COS_SERVER_SSH")
    if not ssh_target:
        return ["error: COS_SERVER_SSH not set (config_plane) -- cannot sync artifacts"]

    results: list[str] = []
    for remote_path, local_path in plan:
        argv = _rsync_argv(ssh_target, remote_path, local_path)

        if dry_run:
            results.append(" ".join(argv))
            continue

        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            results.append(
                f"error: {remote_path} -> {local_path}: "
                f"rsync timed out after {SUBPROCESS_TIMEOUT_SECONDS}s"
            )
            continue
        except OSError as exc:
            results.append(f"error: {remote_path} -> {local_path}: {exc}")
            continue

        if proc.returncode == 0:
            results.append(f"ok: {remote_path} -> {local_path}")
        else:
            detail = (proc.stderr or proc.stdout or f"rsync exit {proc.returncode}").strip()
            results.append(f"error: {remote_path} -> {local_path}: {detail}")

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=["client", "server"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # Read the clock exactly once and thread the same value through both
    # the preview and the actual run -- otherwise a call straddling local
    # midnight could preview one date's plan and execute another's.
    today = _today()

    plan = sync_plan(args.role, today=today)
    print(f"artifact_sync: role={args.role} plan={len(plan)} pair(s)")
    for remote_path, local_path in plan:
        print(f"  {remote_path} -> {local_path}")

    results = run_sync(args.role, dry_run=args.dry_run, today=today)
    for line in results:
        print(line)

    return 1 if any(line.startswith("error:") for line in results) else 0


if __name__ == "__main__":
    sys.exit(main())
