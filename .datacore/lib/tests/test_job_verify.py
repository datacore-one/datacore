"""Tests for job_verify.py -- the unified job-manifest runner.

Most scenarios call `job_verify.main()` directly (capturing stdout/stderr
via pytest's `capsys` and the exit code via the `SystemExit` main() raises
on failure paths) so failures are fast to diagnose. Exactly one subprocess
test covers the real CLI wiring (argv parsing, real process exit code)
end to end, per the task brief.

Every scenario that touches `--alert telegram` monkeypatches
`job_verify._send_telegram` -- these tests must never perform a real
network call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

import config_plane
import job_verify
from ledger.log import read_events

CLI = Path(__file__).parent.parent / "job_verify.py"


def _write_manifest(tmp_path: Path, jobs: list[dict]) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "jobs": jobs}, sort_keys=False))
    return path


def _space(tmp_path: Path) -> Path:
    space = tmp_path / "space"
    space.mkdir()
    return space


def _run_main(argv: list[str]) -> int:
    """Call job_verify.main(argv), normalizing to a plain exit code.

    main() returns normally (implicit success) on the all-green / zero-jobs
    paths, and raises SystemExit on every failure path -- tests shouldn't
    need to know which, so both are folded into an int here.
    """
    try:
        job_verify.main(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def _job(name: str, machine: str, artifact_path: str, **overrides) -> dict:
    job = {
        "name": name,
        "machine": machine,
        "schedule": "0 3 * * *",
        "cmd": "true",
        "artifacts": [{"path": artifact_path}],
    }
    job.update(overrides)
    return job


# --- all-green --------------------------------------------------------------


def test_all_green_manifest_exits_0_and_attests_ok_true(tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "backup.log"
    artifact.write_text("done")
    manifest = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(artifact))])
    space = _space(tmp_path)
    monkeypatch.setenv("DATACORE_ACTOR", "test-actor")

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == "OK 1 jobs 1 artifacts"
    assert out.err == ""

    events = [e for e in read_events(space) if e.type == "metric.attest"]
    assert len(events) == 1
    assert events[0].payload == {
        "metric": "job.verify",
        "job": "backup-notes",
        "ok": True,
        "failures": [],
    }
    assert events[0].actor == "test-actor"


def test_all_green_multiple_jobs_sums_artifacts_in_summary(tmp_path, monkeypatch, capsys):
    a1 = tmp_path / "a1.log"
    a1.write_text("x")
    a2 = tmp_path / "a2.log"
    a2.write_text("y")
    manifest = _write_manifest(
        tmp_path,
        [
            _job("job-one", "mac", str(a1)),
            {
                "name": "job-two",
                "machine": "mac",
                "schedule": "* * * * *",
                "cmd": "true",
                "artifacts": [{"path": str(a1)}, {"path": str(a2)}],
            },
        ],
    )
    space = _space(tmp_path)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == "OK 2 jobs 3 artifacts"


# --- failing artifact --------------------------------------------------------


def test_failing_artifact_exits_1_stderr_names_job_and_failure(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "does-not-exist.log"
    manifest = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(missing))])
    space = _space(tmp_path)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 1
    assert out.out == ""
    assert "backup-notes" in out.err
    assert str(missing) in out.err
    assert "Traceback" not in out.err

    events = [e for e in read_events(space) if e.type == "metric.attest"]
    assert len(events) == 1
    assert events[0].payload["ok"] is False
    assert events[0].payload["job"] == "backup-notes"
    assert len(events[0].payload["failures"]) == 1
    assert str(missing) in events[0].payload["failures"][0]


def test_failing_job_dispatches_alert_exactly_once(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "does-not-exist.log"
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "name": "multi-artifact-job",
                "machine": "mac",
                "schedule": "* * * * *",
                "cmd": "true",
                "artifacts": [{"path": str(missing)}, {"path": str(missing.with_name("also-missing.log"))}],
            }
        ],
    )
    space = _space(tmp_path)

    calls = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: calls.append(msg) or True)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--alert", "telegram"]
    )

    assert code == 1
    # two failing artifacts in the SAME job -> exactly one alert dispatch,
    # not one per failing artifact.
    assert len(calls) == 1


def test_two_failing_jobs_dispatch_two_alerts(tmp_path, monkeypatch, capsys):
    missing1 = tmp_path / "missing1.log"
    missing2 = tmp_path / "missing2.log"
    manifest = _write_manifest(
        tmp_path,
        [_job("job-a", "mac", str(missing1)), _job("job-b", "mac", str(missing2))],
    )
    space = _space(tmp_path)

    calls = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: calls.append(msg) or True)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--alert", "telegram"]
    )

    assert code == 1
    assert len(calls) == 2


def test_log_alert_mode_never_calls_send_telegram(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "does-not-exist.log"
    manifest = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(missing))])
    space = _space(tmp_path)

    calls = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: calls.append(msg) or True)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--alert", "log"]
    )

    assert code == 1
    assert calls == []


def test_telegram_fallback_when_send_telegram_returns_false(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "does-not-exist.log"
    manifest = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(missing))])
    space = _space(tmp_path)

    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: False)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--alert", "telegram"]
    )

    out = capsys.readouterr()
    assert code == 1
    assert "telegram unavailable" in out.err
    assert "logged only" in out.err


# --- per-job on_fail routing (--alert overrides when passed) ----------------


def _mixed_on_fail_manifest(tmp_path: Path) -> Path:
    """Two failing jobs: one declares on_fail: log, the other on_fail:
    telegram. Used to prove alert routing is per-job (from the manifest)
    when --alert is left unset, and uniformly overridden when it's passed.
    """
    missing_log_job = tmp_path / "missing-log-job.log"
    missing_telegram_job = tmp_path / "missing-telegram-job.log"
    return _write_manifest(
        tmp_path,
        [
            _job("log-job", "mac", str(missing_log_job), on_fail="log"),
            _job("telegram-job", "mac", str(missing_telegram_job), on_fail="telegram"),
        ],
    )


def test_no_alert_flag_routes_by_each_jobs_own_on_fail(tmp_path, monkeypatch, capsys):
    manifest = _mixed_on_fail_manifest(tmp_path)
    space = _space(tmp_path)

    calls = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: calls.append(msg) or True)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 1
    # only the on_fail: telegram job dispatches via _send_telegram
    assert len(calls) == 1
    assert "telegram-job" in calls[0]

    # both jobs still get stderr blocks + attest ok:false, regardless of
    # which channel their alert went out on
    assert "log-job" in out.err
    assert "telegram-job" in out.err

    events = {e.payload["job"]: e.payload for e in read_events(space) if e.type == "metric.attest"}
    assert events["log-job"]["ok"] is False
    assert events["telegram-job"]["ok"] is False


def test_alert_log_override_silences_telegram_jobs_too(tmp_path, monkeypatch, capsys):
    manifest = _mixed_on_fail_manifest(tmp_path)
    space = _space(tmp_path)

    calls = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: calls.append(msg) or True)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--alert", "log"]
    )

    assert code == 1
    # --alert log overrides on_fail: telegram too -> zero telegram calls
    assert calls == []


def test_alert_telegram_override_routes_log_jobs_to_telegram_too(tmp_path, monkeypatch, capsys):
    manifest = _mixed_on_fail_manifest(tmp_path)
    space = _space(tmp_path)

    calls = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: calls.append(msg) or True)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--alert", "telegram"]
    )

    assert code == 1
    # --alert telegram overrides on_fail: log too -> both jobs go via telegram
    assert len(calls) == 2


# --- unexpected exception in a job's checks ---------------------------------


def test_unexpected_exception_in_one_job_does_not_abort_the_run(tmp_path, monkeypatch, capsys):
    ok_artifact = tmp_path / "ok.log"
    ok_artifact.write_text("fine")
    boom_artifact = tmp_path / "boom.log"
    boom_artifact.write_text("fine")
    manifest = _write_manifest(
        tmp_path,
        [_job("boom-job", "mac", str(boom_artifact)), _job("fine-job", "mac", str(ok_artifact))],
    )
    space = _space(tmp_path)

    real_run_check = job_verify.run_check

    def _flaky_run_check(artifact, **kwargs):
        if artifact.path == str(boom_artifact):
            raise RuntimeError("disk on fire")
        return real_run_check(artifact, **kwargs)

    monkeypatch.setattr(job_verify, "run_check", _flaky_run_check)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 1  # boom-job failed
    assert "boom-job" in out.err
    assert "disk on fire" in out.err
    assert "Traceback" not in out.err

    events = {e.payload["job"]: e.payload for e in read_events(space) if e.type == "metric.attest"}
    # both jobs got processed and attested despite boom-job's exception
    assert events["boom-job"]["ok"] is False
    assert "disk on fire" in events["boom-job"]["failures"][0]
    assert events["fine-job"]["ok"] is True


# --- --no-emit ---------------------------------------------------------------


def test_no_emit_creates_no_events_dir(tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "backup.log"
    artifact.write_text("done")
    manifest = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(artifact))])
    space = _space(tmp_path)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--no-emit"]
    )

    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == "OK 1 jobs 1 artifacts"
    assert not (space / ".datacore" / "events").exists()


def test_no_emit_on_failure_still_reports_but_writes_nothing(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "does-not-exist.log"
    manifest = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(missing))])
    space = _space(tmp_path)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space), "--no-emit"]
    )

    out = capsys.readouterr()
    assert code == 1
    assert "backup-notes" in out.err
    assert not (space / ".datacore" / "events").exists()


# --- machine filtering ---------------------------------------------------


def test_machine_filtering_only_attests_matching_machine(tmp_path, monkeypatch, capsys):
    mac_artifact = tmp_path / "mac.log"
    mac_artifact.write_text("x")
    box_artifact = tmp_path / "box-does-not-exist.log"  # would fail if ever checked
    manifest = _write_manifest(
        tmp_path,
        [
            _job("mac-job", "mac", str(mac_artifact)),
            _job("box-job", "box", str(box_artifact)),
            _job("nightshift-job", "nightshift", str(box_artifact)),
        ],
    )
    space = _space(tmp_path)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == "OK 1 jobs 1 artifacts"

    events = [e for e in read_events(space) if e.type == "metric.attest"]
    assert len(events) == 1
    assert events[0].payload["job"] == "mac-job"


def test_zero_jobs_for_machine_exits_0_no_events_dir(tmp_path, monkeypatch, capsys):
    mac_artifact = tmp_path / "mac.log"
    mac_artifact.write_text("x")
    manifest = _write_manifest(tmp_path, [_job("mac-job", "mac", str(mac_artifact))])
    space = _space(tmp_path)

    code = _run_main(
        ["--machine", "box", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == "OK 0 jobs 0 artifacts"
    assert out.err == ""
    assert not (space / ".datacore" / "events").exists()


# --- ManifestError -----------------------------------------------------------


def test_manifest_error_exits_1_clean_no_traceback(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [{"name": "bad"}]}))
    space = _space(tmp_path)

    code = _run_main(
        ["--machine", "mac", "--manifest", str(manifest), "--space", str(space)]
    )

    out = capsys.readouterr()
    assert code == 1
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "error" in out.err.lower()
    assert not (space / ".datacore" / "events").exists()


# --- --doctor ------------------------------------------------------------


def test_doctor_flag_prints_table_with_machine_name_and_section_headers(
    tmp_path, monkeypatch, capsys
):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", "/tmp/x", required_env=["FOO"])])
    canonical = tmp_path / "canonical-env"
    canonical.write_text("")
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", canonical)
    monkeypatch.setattr(config_plane, "LEGACY_SOURCES", {})

    code = _run_main(["--machine", "mac", "--doctor", "--manifest", str(manifest)])

    out = capsys.readouterr()
    assert code == 0
    assert "mac" in out.out
    assert "Missing" in out.out
    assert "Conflicts" in out.out
    assert "Legacy-only" in out.out
    assert "Unparseable" in out.out
    assert "FOO" in out.out


def test_doctor_flag_machine_filtering_excludes_other_machines_required_env(
    tmp_path, monkeypatch, capsys
):
    manifest = _write_manifest(
        tmp_path,
        [
            _job("mac-a", "mac", "/tmp/x", required_env=["MAC_ONLY_VAR"]),
            _job("box-a", "box", "/tmp/y", required_env=["BOX_ONLY_VAR"]),
        ],
    )
    canonical = tmp_path / "canonical-env"
    canonical.write_text("")
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", canonical)
    monkeypatch.setattr(config_plane, "LEGACY_SOURCES", {})

    code = _run_main(["--machine", "mac", "--doctor", "--manifest", str(manifest)])

    out = capsys.readouterr()
    assert code == 0
    assert "MAC_ONLY_VAR" in out.out
    assert "BOX_ONLY_VAR" not in out.out


def test_doctor_flag_broken_manifest_exits_1_clean(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [{"name": "bad"}]}))

    code = _run_main(["--machine", "mac", "--doctor", "--manifest", str(manifest)])

    out = capsys.readouterr()
    assert code == 1
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "error" in out.err.lower()


def test_doctor_flag_secrets_never_appear_in_stdout(tmp_path, monkeypatch, capsys):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", "/tmp/x")])
    canonical_secret = "sk-canonical-VALUE1"
    legacy_secret = "sk-live-LEGACYVALUE2"
    canonical = tmp_path / "canonical-env"
    canonical.write_text(f"API_KEY={canonical_secret}\n")
    legacy = tmp_path / "cos.env"
    legacy.write_text(f"API_KEY={legacy_secret}\n")
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", canonical)
    monkeypatch.setattr(config_plane, "LEGACY_SOURCES", {"cos.env": legacy})

    code = _run_main(["--machine", "mac", "--doctor", "--manifest", str(manifest)])

    out = capsys.readouterr()
    assert code == 0
    assert canonical_secret not in out.out
    assert legacy_secret not in out.out
    # names only -- the conflict is still surfaced, just without values
    assert "API_KEY" in out.out
    assert "cos.env" in out.out


def test_doctor_flag_ignores_no_emit_alert_and_space(tmp_path, monkeypatch, capsys):
    # --doctor mode is informational only -- no events, no alerts. Passing
    # the artifact-check flags alongside --doctor must not error and must
    # not create an events dir.
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", "/tmp/x")])
    canonical = tmp_path / "canonical-env"
    canonical.write_text("")
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", canonical)
    monkeypatch.setattr(config_plane, "LEGACY_SOURCES", {})
    space = _space(tmp_path)

    code = _run_main(
        [
            "--machine", "mac", "--doctor", "--manifest", str(manifest),
            "--space", str(space), "--no-emit", "--alert", "telegram",
        ]
    )

    out = capsys.readouterr()
    assert code == 0
    assert not (space / ".datacore" / "events").exists()


# --- --doctor: error labeling + DATACORE_CANONICAL_ENV (Task 3.3 fix round) --
#
# A real-machine audit found the pre-fix canonical path already occupied by
# a directory. `doctor()` now raises `ConfigError` for that (config_plane
# fix, same round); this section pins that job_verify.py's --doctor mode
# reports such a failure as its own thing ("doctor failed: ..."), never
# mislabeled as the unrelated "invalid manifest" branch. `DATACORE_CANONICAL_ENV`
# is the advanced, test/diagnostic-only override that makes this exercisable
# without touching any real machine's canonical file.


def test_doctor_flag_directory_canonical_env_reports_doctor_failed_not_manifest_error(
    tmp_path, monkeypatch, capsys
):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", "/tmp/x")])
    canonical_dir = tmp_path / "canonical-is-a-dir"
    canonical_dir.mkdir()
    monkeypatch.setenv("DATACORE_CANONICAL_ENV", str(canonical_dir))

    code = _run_main(["--machine", "mac", "--doctor", "--manifest", str(manifest)])

    out = capsys.readouterr()
    assert code == 1
    assert out.out == ""
    assert "doctor failed" in out.err
    assert "not a regular file" in out.err
    assert "invalid manifest" not in out.err
    assert "Traceback" not in out.err


def test_doctor_flag_canonical_env_var_overrides_default_canonical_path(
    tmp_path, monkeypatch, capsys
):
    manifest = _write_manifest(
        tmp_path, [_job("mac-a", "mac", "/tmp/x", required_env=["FOO"])]
    )
    canonical = tmp_path / "canonical-env"
    canonical.write_text("FOO=set\n")
    monkeypatch.setenv("DATACORE_CANONICAL_ENV", str(canonical))
    monkeypatch.setattr(config_plane, "LEGACY_SOURCES", {})

    code = _run_main(["--machine", "mac", "--doctor", "--manifest", str(manifest)])

    out = capsys.readouterr()
    assert code == 0
    # header names the overridden path -- proves the env var actually won
    assert str(canonical) in out.out
    # FOO is satisfied by the overridden canonical file -> not reported missing
    assert "FOO" not in out.out


# --- subprocess smoke test (real CLI wiring, real exit codes) ---------------


def test_subprocess_smoke_exit_codes(tmp_path):
    artifact = tmp_path / "backup.log"
    artifact.write_text("done")
    manifest = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(artifact))])
    space = _space(tmp_path)

    env = dict(os.environ)
    env["DATACORE_ROOT"] = str(tmp_path)
    env["DATACORE_ACTOR"] = "test-actor"

    r_ok = subprocess.run(
        [sys.executable, str(CLI), "--machine", "mac", "--manifest", str(manifest), "--space", str(space)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r_ok.returncode == 0, r_ok.stderr
    assert r_ok.stdout.strip() == "OK 1 jobs 1 artifacts"

    missing = tmp_path / "gone.log"
    manifest_fail = _write_manifest(tmp_path, [_job("backup-notes", "mac", str(missing))])
    r_fail = subprocess.run(
        [
            sys.executable, str(CLI), "--machine", "mac",
            "--manifest", str(manifest_fail), "--space", str(space),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r_fail.returncode == 1
    assert r_fail.stdout == ""
    assert "backup-notes" in r_fail.stderr
    assert "Traceback" not in r_fail.stderr

    bad_manifest = tmp_path / "bad.yaml"
    bad_manifest.write_text(yaml.safe_dump({"version": 2, "jobs": []}))
    r_bad = subprocess.run(
        [
            sys.executable, str(CLI), "--machine", "mac",
            "--manifest", str(bad_manifest), "--space", str(space),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r_bad.returncode == 1
    assert r_bad.stdout == ""
    assert "Traceback" not in r_bad.stderr
