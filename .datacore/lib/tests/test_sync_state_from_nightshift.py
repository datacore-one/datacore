"""sync_state_from_nightshift.sh: the mac-state-sync job.

Two failures seen live (2026-09-24 .. 09-27):
  1. An rsync over ssh with no timeout hung for days on one space. launchd does
     not start a StartInterval job while the previous run is alive, so the job
     silently stopped for 3 days.
  2. A space that exists only on the Mac (9-practice) was rsynced from the
     remote every run, leaving "No such file or directory" in the log and a
     "FAILED (rc=0)" line (the rc was read after `if`, so always 0).

The script is run against fake `ssh`/`rsync` on PATH; nothing leaves the machine.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "sync_state_from_nightshift.sh"


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(tmp_path: Path, remote_spaces: str, rsync_rc: int = 0, ssh_rc: int = 0):
    home = tmp_path / "home"
    data = home / "Data"
    for s in ("1-alpha", "2-beta", "9-local-only"):
        (data / s).mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _exe(bin_dir / "ssh", f'echo "ssh $*" >> "{calls}"\nprintf "{remote_spaces}"\nexit {ssh_rc}\n')
    _exe(bin_dir / "rsync", f'echo "rsync $*" >> "{calls}"\nexit {rsync_rc}\n')
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "DATA_DIR": str(data),
           "LOG_FILE": str(tmp_path / "sync.log")}
    subprocess.run(["/bin/bash", str(SCRIPT)], env=env, check=False, timeout=60)
    return (tmp_path / "sync.log").read_text(), calls.read_text() if calls.exists() else ""


def test_every_remote_call_is_bounded_so_a_dead_link_cannot_hang_the_job(tmp_path):
    _, calls = _run(tmp_path, "1-alpha\\n2-beta\\n")
    rsyncs = [c for c in calls.splitlines() if c.startswith("rsync ")]
    assert rsyncs, "no rsync ran"
    for c in rsyncs:
        assert "--timeout=" in c, f"rsync without an I/O timeout: {c}"
        assert "ServerAliveInterval" in c and "ConnectTimeout" in c and "BatchMode=yes" in c, \
            f"rsync's ssh transport has no keepalive/connect bound: {c}"
    for c in (c for c in calls.splitlines() if c.startswith("ssh ")):
        assert "ServerAliveInterval" in c and "ConnectTimeout" in c, f"unbounded ssh: {c}"


def test_a_space_the_remote_does_not_have_is_skipped_not_failed(tmp_path):
    log, calls = _run(tmp_path, "1-alpha\\n2-beta\\n")
    assert "9-local-only/.datacore/state" not in calls, "rsynced a space the remote does not have"
    assert "FAILED" not in log
    assert "venture state: 2 ok, 0 failed" in log
    assert "9-local-only not on nightshift" in log


def test_a_real_rsync_failure_logs_its_real_exit_code(tmp_path):
    log, _ = _run(tmp_path, "1-alpha\\n", rsync_rc=30)
    assert "1-alpha FAILED (rc=30)" in log, log


def test_an_unreachable_remote_is_one_clear_failure(tmp_path):
    log, calls = _run(tmp_path, "", rsync_rc=255, ssh_rc=255)
    assert "cannot list spaces on nightshift" in log, log
    assert "Data/1-alpha/.datacore/state" not in calls


def test_a_space_numbered_differently_on_the_remote_is_matched_by_name(tmp_path):
    """The number prefix is local and differs per host (ENG-2026-08-03-047):
    `2-beta` here and `4-beta` on the remote are the same space, mirrored from
    the remote's own folder into this host's."""
    log, calls = _run(tmp_path, "1-alpha\\n4-beta\\n")
    beta = [c for c in calls.splitlines() if c.startswith("rsync ") and "beta" in c]
    assert beta, f"the beta space was not mirrored: {log}"
    assert "nightshift:Data/4-beta/.datacore/state/" in beta[0], beta[0]
    assert "/Data/2-beta/.datacore/state/" in beta[0], beta[0]
    assert "venture state: 2 ok, 0 failed" in log, log
