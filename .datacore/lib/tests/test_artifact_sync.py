"""Tests for artifact_sync.py -- box -> mac CoS briefing artifact pull (Task 6.1).

Closes the Mac-single-point-of-failure gap for the CoS briefing artifacts
in the box -> mac direction (ENG-2026-0612-017): the box is home for these
artifacts, the mac pulls a read-only copy via rsync-over-ssh.

`sync_plan` computes WHAT to sync per role -- pure, no I/O, no clock reads
unless `today` is omitted. `run_sync` computes/executes HOW -- it is pure
w.r.t. side effects when `dry_run=True` or the plan is empty (role !=
"client"): no filesystem writes, no subprocess calls. The real
rsync-over-ssh execution path is exercised entirely via a monkeypatched
`subprocess.run`, so these tests never touch a real network, a real box,
or this developer machine's real `~/.datacore/datacore.env`.

Fake values only: `fake@host` stands in for `COS_SERVER_SSH` everywhere
below -- never a real address, per the public-repo rule.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import artifact_sync
from artifact_sync import run_sync, sync_plan
from jobs.manifest import load_manifest

CLI = Path(__file__).resolve().parent.parent / "artifact_sync.py"
REAL_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "jobs" / "manifest.yaml"


# --- sync_plan -------------------------------------------------------------


def test_client_plan_has_three_pairs_with_today_substituted():
    pairs = sync_plan("client", today="2026-07-30")

    assert len(pairs) == 3
    remote_paths = [remote for remote, _local in pairs]
    local_paths = [local for _remote, local in pairs]

    assert any("briefings/2026-07-30/app-briefing.json" in r for r in remote_paths)
    assert any(r.endswith("answers.yaml") for r in remote_paths)
    assert any(r.endswith("facts.json") for r in remote_paths)

    # local paths mirror the same shape, under the local home
    assert any("briefings/2026-07-30/app-briefing.json" in l for l in local_paths)
    assert any(l.endswith("answers.yaml") for l in local_paths)
    assert any(l.endswith("facts.json") for l in local_paths)
    for local in local_paths:
        assert Path(local).is_absolute()


def test_server_role_plan_is_empty():
    assert sync_plan("server") == []


def test_unknown_role_plan_is_empty():
    assert sync_plan("something-else") == []
    assert sync_plan("") == []


def test_today_defaults_from_injectable_clock(monkeypatch):
    # A fixed epoch stands in for "now" -- the exact local date it maps to
    # is timezone-dependent, so the assertion re-derives the expected date
    # via the same conversion rather than hardcoding a calendar day.
    fixed_epoch = 1785370000.0
    monkeypatch.setattr(artifact_sync.time, "time", lambda: fixed_epoch)
    expected_day = date.fromtimestamp(fixed_epoch).isoformat()

    pairs = sync_plan("client")

    assert any(f"briefings/{expected_day}/app-briefing.json" in r for r, _ in pairs)


def test_explicit_today_bypasses_the_clock_entirely(monkeypatch):
    def _boom():
        raise AssertionError("time.time() must not be called when today= is given")

    monkeypatch.setattr(artifact_sync.time, "time", _boom)

    pairs = sync_plan("client", today="2020-01-01")

    assert any("briefings/2020-01-01/app-briefing.json" in r for r, _ in pairs)


# --- run_sync: dry_run ------------------------------------------------------


def test_dry_run_returns_command_strings_without_executing(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called in dry_run mode")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _boom)

    results = run_sync("client", dry_run=True, env={"COS_SERVER_SSH": "fake@host"})

    assert len(results) == 3
    for cmd in results:
        assert cmd.startswith("rsync -az --timeout=20 fake@host:")
        assert "\n" not in cmd  # single-line


def test_dry_run_server_role_is_empty_and_never_touches_subprocess(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called for role=server")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _boom)

    assert run_sync("server", dry_run=True, env={"COS_SERVER_SSH": "fake@host"}) == []


# --- run_sync: today threading (single clock read, no monkeypatching needed) ---


def test_dry_run_threads_pinned_today_into_command_strings(monkeypatch):
    # No time.time() monkeypatching anywhere in this test -- run_sync must
    # forward `today` to sync_plan() itself rather than reading the clock,
    # so a pinned date reaches the built commands regardless of the real
    # wall-clock date.
    def _boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called in dry_run mode")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _boom)

    results = run_sync(
        "client", dry_run=True, env={"COS_SERVER_SSH": "fake@host"}, today="2019-05-17"
    )

    assert len(results) == 3
    assert any("briefings/2019-05-17/app-briefing.json" in cmd for cmd in results)


def test_execution_threads_pinned_today_into_the_command_that_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(artifact_sync, "LOCAL_BASE", tmp_path / "cos")
    calls = []

    def _fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _fake_run)

    results = run_sync(
        "client", dry_run=False, env={"COS_SERVER_SSH": "fake@host"}, today="2019-05-17"
    )

    assert len(results) == 3
    assert all(r.startswith("ok:") for r in results)
    briefing_calls = [c for c in calls if "app-briefing.json" in c[-2]]
    assert len(briefing_calls) == 1
    assert "briefings/2019-05-17/app-briefing.json" in briefing_calls[0][-2]
    assert (tmp_path / "cos" / "briefings" / "2019-05-17").is_dir()


# --- run_sync: missing COS_SERVER_SSH --------------------------------------


def test_missing_ssh_var_returns_single_error_and_never_raises(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called when the ssh var is missing")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _boom)

    results = run_sync("client", dry_run=False, env={})

    assert len(results) == 1
    assert results[0].startswith("error:")
    assert "COS_SERVER_SSH" in results[0]


def test_server_role_never_errors_even_without_ssh_var():
    # role "server" has an empty plan -- there's nothing to sync, so a
    # missing var is irrelevant and must not be reported as an error.
    assert run_sync("server", env={}) == []


def test_run_sync_defaults_env_to_config_plane_load(monkeypatch):
    monkeypatch.setattr(
        artifact_sync.config_plane, "load", lambda *_a, **_k: {"COS_SERVER_SSH": "fake@host"}
    )

    def _boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called in dry_run mode")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _boom)

    results = run_sync("client", dry_run=True)  # env omitted entirely

    assert len(results) == 3
    assert all("fake@host" in r for r in results)


# --- run_sync: real execution path (subprocess monkeypatched) --------------


def test_execution_collects_success_and_failure_without_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(artifact_sync, "LOCAL_BASE", tmp_path / "cos")
    calls = []

    def _fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        if cmd[-2].endswith("facts.json"):
            return subprocess.CompletedProcess(
                cmd, returncode=23, stdout="", stderr="rsync: connection unexpectedly closed"
            )
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _fake_run)

    results = run_sync("client", dry_run=False, env={"COS_SERVER_SSH": "fake@host"})

    assert len(calls) == 3
    oks = [r for r in results if r.startswith("ok:")]
    errors = [r for r in results if r.startswith("error:")]
    assert len(oks) == 2
    assert len(errors) == 1
    assert "connection unexpectedly closed" in errors[0]


def test_execution_never_raises_on_subprocess_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(artifact_sync, "LOCAL_BASE", tmp_path / "cos")

    def _timeout(cmd, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(artifact_sync.subprocess, "run", _timeout)

    results = run_sync("client", dry_run=False, env={"COS_SERVER_SSH": "fake@host"})

    assert len(results) == 3
    assert all(r.startswith("error:") for r in results)


def test_execution_never_raises_on_oserror(monkeypatch, tmp_path):
    monkeypatch.setattr(artifact_sync, "LOCAL_BASE", tmp_path / "cos")

    def _oserror(cmd, **_kwargs):
        raise OSError("rsync binary not found")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _oserror)

    results = run_sync("client", dry_run=False, env={"COS_SERVER_SSH": "fake@host"})

    assert len(results) == 3
    assert all(r.startswith("error:") for r in results)


def test_execution_creates_local_parent_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(artifact_sync, "LOCAL_BASE", tmp_path / "cos")

    def _ok(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(artifact_sync.subprocess, "run", _ok)

    run_sync("client", dry_run=False, env={"COS_SERVER_SSH": "fake@host"})

    assert (tmp_path / "cos" / "answers.yaml").parent.is_dir()
    assert (tmp_path / "cos" / "facts.json").parent.is_dir()


# --- main(): single clock read, threaded to both calls ---------------------


def test_main_derives_today_once_and_passes_same_value_to_both_calls(monkeypatch):
    # In-process spy on sync_plan/run_sync (cheaper and more precise than a
    # subprocess-level clock freeze): proves main() reads the clock exactly
    # once and forwards the identical `today` to both its preview
    # sync_plan() call and its run_sync() call -- the exact bug this fix
    # addresses (a second independent clock read could mismatch across a
    # midnight straddle).
    seen_sync_plan_today = []
    seen_run_sync_today = []
    real_sync_plan = artifact_sync.sync_plan

    def _spy_sync_plan(role, today=None):
        seen_sync_plan_today.append(today)
        return real_sync_plan(role, today=today)

    def _spy_run_sync(role, dry_run=False, env=None, today=None):
        seen_run_sync_today.append(today)
        return []

    monkeypatch.setattr(artifact_sync, "sync_plan", _spy_sync_plan)
    monkeypatch.setattr(artifact_sync, "run_sync", _spy_run_sync)

    exit_code = artifact_sync.main(["--role", "client", "--dry-run"])

    assert exit_code == 0
    assert len(seen_sync_plan_today) == 1
    assert len(seen_run_sync_today) == 1
    assert seen_sync_plan_today[0] is not None
    assert seen_sync_plan_today[0] == seen_run_sync_today[0]


# --- CLI smoke (real subprocess wiring, real exit codes) --------------------


def test_cli_server_role_dry_run_exits_zero_regardless_of_env(tmp_path):
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)  # no datacore.env present here

    result = subprocess.run(
        [sys.executable, str(CLI), "--role", "server", "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "plan=0 pair(s)" in result.stdout


def test_cli_client_role_dry_run_exits_zero_with_configured_ssh(tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".datacore").mkdir(parents=True)
    (fake_home / ".datacore" / "datacore.env").write_text("COS_SERVER_SSH=fake@host\n")

    env = dict(os.environ)
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        [sys.executable, str(CLI), "--role", "client", "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "rsync -az --timeout=20 fake@host:" in result.stdout


def test_cli_client_role_missing_ssh_var_exits_one(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()  # no .datacore/datacore.env -> config_plane.load() == {}

    env = dict(os.environ)
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        [sys.executable, str(CLI), "--role", "client", "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "error" in result.stdout.lower()
    assert "Traceback" not in result.stderr


# --- manifest integration (mac-artifact-pull job) ---------------------------


def test_manifest_loads_and_mac_artifact_pull_job_present():
    jobs = load_manifest(REAL_MANIFEST_PATH)
    by_name = {job.name: job for job in jobs}

    assert "mac-artifact-pull" in by_name
    job = by_name["mac-artifact-pull"]

    assert job.machine == "mac"
    assert job.cmd == "python3 ~/Data/.datacore/lib/artifact_sync.py --role client"
    assert job.on_fail == "log"
    assert len(job.artifacts) == 1

    artifact = job.artifacts[0]
    assert "app-briefing.json" in artifact.path
    # NOT `{today}`: a daily artifact cannot exist between midnight and the hour
    # its job runs, so a date-templated path failed this contract every night
    # for hours. It globs to the newest briefing instead, and `max_age_hours` —
    # not the filename — catches "the brief stopped running", which is the
    # failure actually worth alerting on.
    assert "*" in artifact.path
    assert artifact.max_age_hours and artifact.max_age_hours <= 26
    assert artifact.check == "json_has_keys"
    assert artifact.arg == ["headline"]
    assert artifact.max_age_hours == 26
