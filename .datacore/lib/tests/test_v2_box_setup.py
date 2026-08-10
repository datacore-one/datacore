"""Tests for v2_box_setup.sh -- the Datacore v2 Phase 6 box installer (Task 6.2).

The script is bash, not Python, so these tests drive it as a subprocess
against a fixture tree built under `tmp_path`, using the script's
`DATACORE_V2_SETUP_PREFIX` override so every `/root/...` (and `/etc/...`)
path it touches is redirected under the fixture instead of the real
filesystem. No real value ever appears here -- all env values in the
fixture legacy files are made-up placeholders, never anything real or
box-derived.

Coverage:
    - `--verify` fails cleanly when the canonical env doesn't exist yet.
    - `--apply` creates the canonical env at 0600, migrates legacy keys,
      and resolves same-key conflicts first-source-wins (with a `SKIP`
      echo for the loser).
    - The cron AND cryptography steps are both gated off entirely under the
      test prefix (real crontab and real `pip3 install` are never faked or
      touched -- a `pip3` spy on `PATH` proves the latter).
    - The TODO(verify-on-box) report renders EXISTS/MISSING lines,
      including the "candidate found via ls of the parent dir" case.
    - A second `--apply` run is a true no-op: the entire fixture tree is
      byte-identical before and after.
    - `--verify` passes (exit 0) once `--apply` has run.
    - Env-key migration validates the FULL `^[A-Za-z_][A-Za-z0-9_]*$` shape
      (invalid keys dropped with a distinct log line), strips leading
      whitespace on the key before validating, and strips a trailing CR
      (CRLF line ending) so it never contaminates a migrated value.
    - Mutating commands (dir/file creation, chmod) are checked: a read-only
      `.datacore` dir makes the env step report FAILED and the overall run
      exit nonzero, instead of silently claiming success.
    - `--verify --apply` together is a usage error, not "last one wins".
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "v2_box_setup.sh"


def _extract_source_line(prefix_marker: str) -> str:
    """Return the single line in the real script starting with `prefix_marker`.

    Used so a test can exercise the REAL, verbatim source text (never a
    hand-duplicated reimplementation that could silently drift from it).
    """
    for line in SCRIPT_PATH.read_text().splitlines():
        if line.strip().startswith(prefix_marker):
            return line
    raise AssertionError(f"no line starting with {prefix_marker!r} found in {SCRIPT_PATH}")


def _run(prefix: Path, mode: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATACORE_V2_SETUP_PREFIX"] = str(prefix)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), mode],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _build_fixture(tmp_path: Path) -> Path:
    """Build a fixture tree mirroring the box layout under `tmp_path`.

    - manifest.yaml with one `TODO(verify-on-box)` marker (same-line style,
      matching the real manifest's convention) whose artifact is missing
      but has sibling files in its parent dir.
    - Three legacy env sources with made-up placeholder values, including
      one conflicting key (`FOO_TOKEN`) present in two sources with
      different values -- first-source-wins should keep the cos.env one.
    - No canonical `datacore.env` yet -- the script must create it.
    """
    root = tmp_path / "root" / "Data" / ".datacore" / "lib" / "jobs"
    root.mkdir(parents=True)
    (root / "manifest.yaml").write_text(
        "version: 1\n"
        "jobs:\n"
        "  - name: box-fixture-job\n"
        "    machine: box\n"
        '    schedule: "0 9 * * *"\n'
        '    cmd: "~/Data/.datacore/lib/fixture.sh"\n'
        "    artifacts:\n"
        '      - path: "~/.datacore/cos/fixture-artifact.log"  # TODO(verify-on-box): confirm this fixture path once the box has real jobs\n'
        "        check: nonempty\n"
    )

    cos_env_dir = tmp_path / "root" / ".config"
    cos_env_dir.mkdir(parents=True)
    (cos_env_dir / "cos.env").write_text(
        "# fake cos secrets -- placeholder values only\n"
        "WINSTON_BOT_TOKEN=placeholder-bot-token\n"
        "FOO_TOKEN=from-cos-fixture\n"
        "\n"
    )

    etc_dir = tmp_path / "etc"
    etc_dir.mkdir(parents=True)
    (etc_dir / "datacored.env").write_text(
        "DATACORED_TOKEN=placeholder-datacored-token\n"
        "FOO_TOKEN=from-datacored-fixture-conflict\n"
    )

    hermes_dir = tmp_path / "root" / ".hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=placeholder-hermes-token\n"
        "BAR_TOKEN=from-hermes-fixture\n"
    )

    cos_state_dir = tmp_path / "root" / ".datacore" / "cos"
    cos_state_dir.mkdir(parents=True)
    (cos_state_dir / "sibling-a.log").write_text("evidence sibling a\n")
    (cos_state_dir / "sibling-b.log").write_text("evidence sibling b\n")

    return tmp_path


def _build_key_validation_fixture(tmp_path: Path) -> Path:
    """A minimal fixture isolating key-validation edge cases (Important 3).

    One legacy source (`cos.env`) with four deliberately awkward lines,
    each obviously-fake in value:
        - `FOO BAR=...`   -- embedded space in the key -> invalid, dropped
        - `FOO.BAR=...`   -- a dot in the key -> invalid, dropped
        - `  LEADWS=...`  -- leading whitespace on the key -> stripped,
                             then a valid identifier -> migrated normally
        - `CRTEST=...\\r\\n` -- CRLF line ending -> the \\r must never survive
                             into the migrated value
    No datacored.env / hermes.env needed -- the script tolerates missing
    legacy sources (`[ -f "$legacy_file" ] || continue`).
    """
    root = tmp_path / "root" / "Data" / ".datacore" / "lib" / "jobs"
    root.mkdir(parents=True)
    (root / "manifest.yaml").write_text("version: 1\njobs: []\n")

    cos_env_dir = tmp_path / "root" / ".config"
    cos_env_dir.mkdir(parents=True)
    content = (
        "FOO BAR=placeholder-space-key\n"
        "FOO.BAR=placeholder-dot-key\n"
        "  LEADWS=placeholder-leadws-value\n"
        "CRTEST=placeholder-crlf-value\r\n"
    )
    (cos_env_dir / "cos.env").write_bytes(content.encode("utf-8"))
    return tmp_path


def _canonical_env_path(prefix: Path) -> Path:
    return prefix / "root" / ".datacore" / "datacore.env"


def _snapshot(prefix: Path) -> dict[str, bytes]:
    root = prefix / "root"
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(prefix))] = path.read_bytes()
    return out


# --- bash -n syntax sanity (belt-and-suspenders; also run standalone) ----


def test_script_is_syntactically_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT_PATH)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


# --- --verify before --apply: canonical env missing -> FAILED, exit 1 ---


def test_verify_fails_before_apply_env_missing(tmp_path):
    prefix = _build_fixture(tmp_path)
    result = _run(prefix, "--verify")
    assert result.returncode == 1
    assert "[v2] env: FAILED" in result.stdout
    assert "missing" in result.stdout
    assert not _canonical_env_path(prefix).exists()


def test_verify_never_mutates_anything(tmp_path):
    """--verify must be assert-only: no canonical env, no crontab, nothing."""
    prefix = _build_fixture(tmp_path)
    before = _snapshot(prefix)
    _run(prefix, "--verify")
    after = _snapshot(prefix)
    assert before == after


# --- cron step: gated off entirely under the test prefix ----------------


def test_cron_step_skipped_under_test_prefix(tmp_path):
    prefix = _build_fixture(tmp_path)
    result = _run(prefix, "--verify")
    assert "[v2] cron: SKIPPED (test prefix)" in result.stdout
    # A skipped cron step must never fail the overall verify run by itself
    # -- only env/cryptography do at this point (env is the one that's
    # missing in this fixture).
    result_apply = _run(prefix, "--apply")
    assert "[v2] cron: SKIPPED (test prefix)" in result_apply.stdout


def test_cron_line_present_exact_match_not_substring(tmp_path):
    """`cron_line_present()` must exact-match the whole `$CRON_LINE`, not
    merely contain "job_verify.py" as a substring.

    `step_cron` is unreachable past its own prefix-gate in every other test
    (by design -- every other step needs the prefix for fixture-tree
    safety, and unsetting it here would let THOSE steps touch the real
    filesystem). So this test drives `cron_line_present()` in isolation: a
    tiny harness sources the `CRON_LINE=` assignment and the
    `cron_line_present()` function definition extracted VERBATIM from the
    shipped script (never hand-duplicated -- if the real lines change,
    this test automatically uses the updated version), then calls the
    function against a fake `crontab` on PATH backed by a plain file
    (never touching the real system crontab).
    """
    cron_line_assignment = _extract_source_line("CRON_LINE=")
    cron_line_present_def = _extract_source_line("cron_line_present()")
    real_line = cron_line_assignment.split("=", 1)[1].strip().strip('"')

    spy_dir = tmp_path / "spybin"
    spy_dir.mkdir()
    crontab_file = tmp_path / "fake_crontab"
    fake_crontab = spy_dir / "crontab"
    fake_crontab.write_text(
        "#!/bin/sh\n"
        f'F="{crontab_file}"\n'
        '[ "$1" = "-l" ] && { [ -f "$F" ] && cat "$F"; exit 0; }\n'
        '[ "$1" = "-" ] && { cat > "$F"; exit 0; }\n'
        "exit 1\n"
    )
    fake_crontab.chmod(0o755)

    harness = tmp_path / "harness.sh"
    harness.write_text(
        f"#!/bin/bash\n{cron_line_assignment}\n{cron_line_present_def}\n"
        'cron_line_present; echo "present=$?"\n'
    )
    harness.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{spy_dir}{os.pathsep}{env.get('PATH', '')}"

    def present_after(crontab_contents: str) -> bool:
        crontab_file.write_text(crontab_contents)
        r = subprocess.run(
            ["bash", str(harness)], env=env, capture_output=True, text=True, timeout=10
        )
        return "present=0" in r.stdout

    # A crontab line that only MENTIONS job_verify.py (stale entry, comment,
    # different schedule/flags) must NOT be treated as the real line present.
    assert present_after("# old attempt: python3 job_verify.py --machine box\n") is False
    # The exact real line -- present.
    assert present_after(real_line + "\n") is True
    # Both the different line above AND the exact line together -- still present.
    assert present_after(f"# old attempt: python3 job_verify.py\n{real_line}\n") is True


# --- todo-report: EXISTS / MISSING-with-candidates rendering -------------


def test_todo_report_lists_missing_artifact_with_sibling_candidates(tmp_path):
    prefix = _build_fixture(tmp_path)
    result = _run(prefix, "--verify")
    assert "[v2] todo-report:" in result.stdout
    assert "TODO(verify-on-box)" in result.stdout
    assert "fixture-artifact.log" in result.stdout
    assert "MISSING" in result.stdout
    assert "sibling-a.log" in result.stdout
    assert "sibling-b.log" in result.stdout


def test_todo_report_says_none_when_no_markers_present(tmp_path):
    prefix = _build_fixture(tmp_path)
    manifest = prefix / "root" / "Data" / ".datacore" / "lib" / "jobs" / "manifest.yaml"
    manifest.write_text(
        "version: 1\n"
        "jobs:\n"
        "  - name: box-clean-job\n"
        "    machine: box\n"
        '    schedule: "0 9 * * *"\n'
        '    cmd: "~/Data/.datacore/lib/clean.sh"\n'
        "    artifacts:\n"
        '      - path: "~/.datacore/cos/clean.log"\n'
        "        check: nonempty\n"
    )
    result = _run(prefix, "--verify")
    assert "[v2] todo-report:" in result.stdout
    assert "(none)" in result.stdout
    assert "TODO(verify-on-box)" not in result.stdout.split("todo-report:")[1]


def test_todo_report_marks_existing_artifact(tmp_path):
    prefix = _build_fixture(tmp_path)
    artifact = prefix / "root" / ".datacore" / "cos" / "fixture-artifact.log"
    artifact.write_text("present\n")
    result = _run(prefix, "--verify")
    assert "EXISTS" in result.stdout
    assert "MISSING" not in result.stdout.split("todo-report:")[1]


# --- --apply: canonical env creation, permissions, migration, conflicts -


def test_apply_creates_canonical_env_0600(tmp_path):
    prefix = _build_fixture(tmp_path)
    result = _run(prefix, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    env_path = _canonical_env_path(prefix)
    assert env_path.exists()
    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == 0o600
    assert "[v2] env: APPLIED created" in result.stdout


def test_apply_migrates_legacy_keys_and_resolves_conflict_first_wins(tmp_path):
    prefix = _build_fixture(tmp_path)
    result = _run(prefix, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr

    content = _canonical_env_path(prefix).read_text()
    lines = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in content.splitlines() if ln}

    # Unique keys from each legacy source made it in.
    assert lines["WINSTON_BOT_TOKEN"] == "placeholder-bot-token"
    assert lines["DATACORED_TOKEN"] == "placeholder-datacored-token"
    assert lines["TELEGRAM_BOT_TOKEN"] == "placeholder-hermes-token"
    assert lines["BAR_TOKEN"] == "from-hermes-fixture"

    # Conflict: cos.env is processed first -> its value wins, datacored.env's
    # conflicting value for the same key is skipped, not appended.
    assert lines["FOO_TOKEN"] == "from-cos-fixture"
    assert content.count("FOO_TOKEN=") == 1

    # The skip is echoed for the losing source.
    assert "[v2] env: SKIP FOO_TOKEN (already set)" in result.stdout


def test_env_migration_rejects_invalid_keys_and_handles_edge_cases(tmp_path):
    prefix = _build_key_validation_fixture(tmp_path)
    result = _run(prefix, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr

    # Invalid keys (a character outside [A-Za-z0-9_] anywhere in the key)
    # are dropped, never migrated, with a distinct "(invalid key)" SKIP log
    # naming the raw key -- not silently ignored, not silently accepted.
    assert "[v2] env: SKIP FOO BAR (invalid key)" in result.stdout
    assert "[v2] env: SKIP FOO.BAR (invalid key)" in result.stdout

    content = _canonical_env_path(prefix).read_text()
    assert "FOO BAR=" not in content
    assert "FOO.BAR=" not in content

    # Leading whitespace on the key is stripped BEFORE validation, so a
    # legitimately-indented legacy line still migrates as a normal key.
    assert "LEADWS=placeholder-leadws-value" in content
    assert "[v2] env: APPLIED migrated LEADWS" in result.stdout

    # A trailing CR (CRLF line ending) is stripped from the whole line
    # before parsing -- it must never survive into the migrated value.
    assert "CRTEST=placeholder-crlf-value" in content
    assert "\r" not in content


def test_apply_does_not_touch_legacy_source_files(tmp_path):
    prefix = _build_fixture(tmp_path)
    cos_env = prefix / "root" / ".config" / "cos.env"
    datacored_env = prefix / "etc" / "datacored.env"
    hermes_env = prefix / "root" / ".hermes" / ".env"
    before = {p: p.read_bytes() for p in (cos_env, datacored_env, hermes_env)}

    _run(prefix, "--apply")

    for p, content in before.items():
        assert p.read_bytes() == content, f"{p} was mutated by the installer"


# --- mutating commands' exit codes are checked (Important 2) ------------


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="permission bits don't restrict root -- the failure can't be forced",
)
def test_apply_reports_failed_when_env_dir_is_read_only(tmp_path):
    """A read-only `.datacore` dir must FAIL the env step, not silently APPLIED.

    Before this fix, `: > "$CANONICAL_ENV"` and `chmod` failures were never
    checked -- the script would echo APPLIED/OK regardless of whether the
    file was actually created. Chmod the fixture's `.datacore` dir to 555
    (no write bit) so the file-creation redirect fails with EACCES, then
    assert the script reports the failure honestly and exits nonzero.
    """
    prefix = _build_fixture(tmp_path)
    home_dc = prefix / "root" / ".datacore"
    os.chmod(home_dc, 0o555)
    try:
        result = _run(prefix, "--apply")
        assert result.returncode != 0
        assert "[v2] env: FAILED" in result.stdout
        assert "[v2] apply: FAILED" in result.stdout
    finally:
        os.chmod(home_dc, 0o755)  # restore so tmp_path teardown can clean up


def test_apply_creates_home_datacore_dir_when_entirely_absent(tmp_path):
    """The mkdir -p path actually creates a missing dir tree, not just a no-op.

    `_build_fixture` always pre-creates `.datacore/cos/`, so the normal
    creation tests only exercise `mkdir -p` as a no-op against an existing
    directory. This builds a fixture where `.datacore` doesn't exist at
    all yet -- the first-ever run on a fresh box -- to prove the mutation
    actually creates it, not merely tolerates it already existing.
    """
    root = tmp_path / "root" / "Data" / ".datacore" / "lib" / "jobs"
    root.mkdir(parents=True)
    (root / "manifest.yaml").write_text("version: 1\njobs: []\n")
    cos_env_dir = tmp_path / "root" / ".config"
    cos_env_dir.mkdir(parents=True)
    (cos_env_dir / "cos.env").write_text("WINSTON_BOT_TOKEN=placeholder-bot-token\n")

    assert not (tmp_path / "root" / ".datacore").exists()

    result = _run(tmp_path, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    env_path = _canonical_env_path(tmp_path)
    assert env_path.exists()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


# --- idempotency: a second --apply changes nothing at all ---------------


def test_second_apply_is_a_true_no_op(tmp_path):
    prefix = _build_fixture(tmp_path)
    first = _run(prefix, "--apply")
    assert first.returncode == 0, first.stdout + first.stderr

    snapshot_after_first = _snapshot(prefix)

    second = _run(prefix, "--apply")
    assert second.returncode == 0, second.stdout + second.stderr

    snapshot_after_second = _snapshot(prefix)
    assert snapshot_after_first == snapshot_after_second

    # Second run should report every migrated key as a SKIP, not an APPLIED
    # migration -- nothing new should be appended.
    assert "SKIP WINSTON_BOT_TOKEN (already set)" in second.stdout
    assert "SKIP FOO_TOKEN (already set)" in second.stdout
    assert "SKIP DATACORED_TOKEN (already set)" in second.stdout
    assert "SKIP TELEGRAM_BOT_TOKEN (already set)" in second.stdout
    assert "SKIP BAR_TOKEN (already set)" in second.stdout
    assert "migrated" not in second.stdout


# --- --verify passes once --apply has run --------------------------------


def test_verify_passes_after_apply(tmp_path):
    prefix = _build_fixture(tmp_path)
    apply_result = _run(prefix, "--apply")
    assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr

    verify_result = _run(prefix, "--verify")
    assert verify_result.returncode == 0, verify_result.stdout + verify_result.stderr
    assert "[v2] env: OK" in verify_result.stdout
    assert "[v2] verify: OK" in verify_result.stdout


# --- cryptography step: fully gated behind the test prefix (Critical fix) -


def test_cryptography_step_skipped_under_test_prefix(tmp_path):
    prefix = _build_fixture(tmp_path)
    result = _run(prefix, "--verify")
    assert "[v2] cryptography: SKIPPED (test prefix)" in result.stdout
    result_apply = _run(prefix, "--apply")
    assert "[v2] cryptography: SKIPPED (test prefix)" in result_apply.stdout


def test_cryptography_pip3_never_invoked_under_test_prefix(tmp_path):
    """A `pip3` spy on PATH proves the real install command is never reached.

    Without the prefix gate, a machine missing `cryptography>=41` would
    trigger a REAL `pip3 install` the moment the test suite ran `--apply`
    (this was the Critical finding). The spy here is a fake executable
    named `pip3` placed earlier on PATH than any real one; if the script
    ever calls `pip3` for real, the spy writes a marker file. Asserting the
    marker is absent proves `pip3` was never invoked here.

    NON-DISCRIMINATING BY ITSELF (re-review finding): on any machine where
    `cryptography>=41` is already installed (true of this dev environment),
    the real version check inside `step_cryptography` succeeds before ever
    reaching `pip3`, so this test would ALSO pass against a reverted/buggy
    script that removed the prefix gate entirely -- pip3 simply never gets
    reached either way in that case. This test alone does not prove the
    gate exists; see
    `test_cryptography_pip3_never_invoked_even_when_check_forced_to_fail`
    below for the version that forces the check to fail and so actually
    discriminates fixed-vs-reverted behavior.
    """
    prefix = _build_fixture(tmp_path)
    spy_dir = tmp_path.parent / f"{tmp_path.name}-spybin"
    spy_dir.mkdir(exist_ok=True)
    marker = tmp_path.parent / f"{tmp_path.name}-pip3-called.marker"
    marker.unlink(missing_ok=True)
    pip3_spy = spy_dir / "pip3"
    pip3_spy.write_text(f'#!/bin/sh\necho "pip3 called: $@" >> "{marker}"\nexit 1\n')
    pip3_spy.chmod(0o755)

    env = dict(os.environ)
    env["DATACORE_V2_SETUP_PREFIX"] = str(prefix)
    env["PATH"] = f"{spy_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--apply"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert not marker.exists(), (
        "pip3 spy was invoked -- the cryptography step escaped the test prefix gate: "
        + result.stdout
    )
    assert "[v2] cryptography: SKIPPED (test prefix)" in result.stdout


def test_cryptography_pip3_never_invoked_even_when_check_forced_to_fail(tmp_path):
    """Force the `import cryptography` version check to FAIL, then prove
    pip3 is STILL never invoked under the test prefix.

    This closes the discriminating-power gap the re-review found in
    `test_cryptography_pip3_never_invoked_under_test_prefix` above: that
    test passes trivially on any machine that already has
    `cryptography>=41` installed, because the real check succeeds before
    ever reaching pip3 -- true whether or not the prefix gate exists at
    all. Here, a `python3` shim on PATH fails any `-c` invocation whose
    arguments mention "cryptography" (matching BOTH of
    `step_cryptography`'s real python3 calls -- the version check and the
    OK-branch's version-print), forcing the check to fail regardless of
    what's actually installed. Non-matching python3 invocations fall
    through to the real interpreter, so nothing else on the system is
    disturbed.

    If the prefix gate is checked first (the actual, fixed behavior),
    `step_cryptography` returns before ever touching python3 OR pip3 --
    both a python3-invocation marker and a pip3-invocation marker are
    asserted absent. Verified (by hand, documented in the fix report) to
    FAIL against a reverted script with the gate removed: there, the
    forced check-failure falls through to a real `pip3 install` attempt,
    which the spy catches.
    """
    prefix = _build_fixture(tmp_path)
    spy_dir = tmp_path.parent / f"{tmp_path.name}-spybin2"
    spy_dir.mkdir(exist_ok=True)
    python3_marker = tmp_path.parent / f"{tmp_path.name}-python3-cryptography.marker"
    pip3_marker = tmp_path.parent / f"{tmp_path.name}-pip3-forced.marker"
    python3_marker.unlink(missing_ok=True)
    pip3_marker.unlink(missing_ok=True)

    real_python3 = shutil.which("python3") or "/usr/bin/python3"
    python3_shim = spy_dir / "python3"
    python3_shim.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *cryptography*) echo "called: $@" >> "{python3_marker}"; exit 1 ;;\n'
        "esac\n"
        f'exec "{real_python3}" "$@"\n'
    )
    python3_shim.chmod(0o755)

    pip3_shim = spy_dir / "pip3"
    pip3_shim.write_text(f'#!/bin/sh\necho "pip3 called: $@" >> "{pip3_marker}"\nexit 1\n')
    pip3_shim.chmod(0o755)

    env = dict(os.environ)
    env["DATACORE_V2_SETUP_PREFIX"] = str(prefix)
    env["PATH"] = f"{spy_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--apply"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "[v2] cryptography: SKIPPED (test prefix)" in result.stdout
    assert not python3_marker.exists(), (
        "python3 cryptography-check shim was invoked -- the version check ran "
        "before the test-prefix gate: " + result.stdout
    )
    assert not pip3_marker.exists(), (
        "pip3 spy was invoked -- the cryptography step escaped the test prefix "
        "gate even with a forced-failing version check: " + result.stdout
    )


# --- usage: no mode flag is a clean usage error, not a crash -------------


def test_no_mode_flag_is_a_usage_error():
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_verify_and_apply_together_is_a_usage_error():
    """Passing both flags must be a usage error, not 'last flag wins'."""
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--verify", "--apply"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()

    result_reversed = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--apply", "--verify"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result_reversed.returncode == 2
    assert "usage" in result_reversed.stderr.lower()
