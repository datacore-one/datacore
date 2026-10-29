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


def _run(tmp_path: Path, remote_spaces: str, rsync_rc: int = 0, ssh_rc: int = 0, remote: str | None = "nightshift",
         remote_user: str | None = None):
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
    if remote is not None:
        env["REMOTE"] = remote
    if remote_user is not None:
        env["REMOTE_USER"] = remote_user
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


def test_with_no_remote_configured_nothing_is_pulled(tmp_path):
    """2026-10-29: the nightshift host was retired, but the job kept pulling from it
    every 5 minutes (two ssh timeouts per run). The remote is now opt-in: with no
    REMOTE set the mirror is skipped -- one log line, no ssh, no rsync -- and the
    rest of the job (the v2-runner refresh) still runs."""
    log, calls = _run(tmp_path, "1-alpha\\n", remote=None)
    assert calls == "", f"pulled from a remote nobody configured: {calls}"
    assert "FAILED" not in log, log
    assert "no remote configured" in log, log


def test_state_kept_by_another_user_on_the_remote_is_read_as_that_user(tmp_path):
    """2026-10-29: Miles writes this state on dayshift as the Unix user miles, whose
    home the ssh login (gregor) cannot read. With REMOTE_USER set, every listing and
    every rsync runs as that user there (sudo -n -u), from that user's Data folder."""
    log, calls = _run(tmp_path, "1-alpha\\n2-beta\\n", remote="dayshift", remote_user="miles")
    rsyncs = [c for c in calls.splitlines() if c.startswith("rsync ")]
    assert rsyncs and all("--rsync-path=sudo -n -u miles rsync" in c for c in rsyncs), rsyncs
    assert all("dayshift:~miles/Data/" in c for c in rsyncs), rsyncs
    lists = [c for c in calls.splitlines() if c.startswith("ssh ")]
    assert lists and all("sudo -n -u miles" in c and "~miles/Data" in c for c in lists), lists
    assert "venture state: 2 ok, 0 failed" in log, log
