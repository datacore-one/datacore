"""The residue of a service-user move, as rows instead of incidents.

The 2026-08-13 root->gregor move on the CoS box left three things behind,
each found separately and weeks apart: a gateway process running 25 days in
`user-0.slice` with no unit (a third Telegram consumer), a `plur` symlink
into root's home that the service user could not execute, and a deploy key.
Three incidents, one cause."""
from __future__ import annotations

import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import v2_verify as vv  # noqa: E402


def _proc(root: Path, pid: str, cmd: str, cgroup: str) -> None:
    d = root / pid
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(cmd.encode().replace(b" ", b"\0") + b"\0")
    (d / "cgroup").write_text(f"0::{cgroup}\n")


def test_the_orphaned_gateway_is_found_and_the_managed_one_is_not(tmp_path):
    procfs = tmp_path / "proc"
    _proc(procfs, "527467", "python -m hermes_cli.main gateway run",
          "/user.slice/user-1000.slice/user@1000.service/app.slice/hermes-gateway.service")
    _proc(procfs, "3960788", "python -m hermes_cli.main gateway run",
          "/user.slice/user-0.slice/user@0.service/app.slice/hermes-gateway.service")
    _proc(procfs, "999", "python -m datacored",
          "/system.slice/datacored.service")
    _proc(procfs, "1234", "bash -l", "/user.slice/user-1000.slice/session.scope")

    found = vv.unmanaged_service_processes(procfs=procfs, uid=1000)
    assert len(found) == 1
    assert found[0].startswith("pid 3960788: python -m hermes_cli.main gateway run")
    assert "user-0.slice" in found[0]


def test_cgroup_classification():
    assert vv.cgroup_is_managed("/user.slice/user-1000.slice/user@1000.service/app.slice/x.service", 1000)
    assert vv.cgroup_is_managed("/system.slice/datacored.service", 1000)
    assert not vv.cgroup_is_managed("/user.slice/user-0.slice/user@0.service/app.slice/x.service", 1000)
    assert not vv.cgroup_is_managed("/user.slice/user-1000.slice/session.scope", 1000)


def test_a_path_link_into_another_users_home_is_reported(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    good_target = tmp_path / "mine" / "tool"
    good_target.parent.mkdir()
    good_target.write_text("#!/bin/sh\n")
    good_target.chmod(0o755)
    (bindir / "mine").symlink_to(good_target)
    (bindir / "plur").symlink_to("/root/.hermes/node/bin/plur")   # the real one
    (bindir / "elsewhere").symlink_to("/usr/lib/whatever")         # not a home

    import pwd
    monkeypatch.setattr(vv.os, "getuid", lambda: 1000)
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: type("P", (), {"pw_name": "gregor"})())
    found = vv.foreign_home_links(bindirs=(str(bindir),))
    assert len(found) == 1
    assert found[0].startswith("plur -> /root/.hermes/node/bin/plur")
    assert "not executable by gregor" in found[0]


def test_both_rows_are_added(tmp_path, monkeypatch):
    monkeypatch.setattr(vv, "foreign_home_links", lambda *a, **k: [])
    monkeypatch.setattr(vv, "unmanaged_service_processes", lambda *a, **k: [])
    rep = vv.Report()
    vv.check_migration_leftovers(rep)
    names = [c.name for c in rep.checks]
    assert names == ["no PATH link into another user's home", "service processes are managed"]


def test_a_shell_that_merely_mentions_the_service_is_not_flagged(tmp_path):
    """The first version matched its own operator: `ps | grep 'hermes_cli.main
    gateway'` carries the search string in its own cmdline, so the row failed
    on the command used to investigate it (2026-09-08)."""
    procfs = tmp_path / "proc"
    _proc(procfs, "111409",
          "bash -c ps -eo pid,cmd | grep hermes_cli.main gateway",
          "/user.slice/user-1000.slice/session-31472.scope")
    _proc(procfs, "222", "sudo -n systemctl --user stop hermes-gateway.service",
          "/user.slice/user-1000.slice/session-9.scope")
    _proc(procfs, "3960788", "python -m hermes_cli.main gateway run",
          "/user.slice/user-0.slice/user@0.service/app.slice/hermes-gateway.service")

    found = vv.unmanaged_service_processes(procfs=procfs, uid=1000)
    assert len(found) == 1 and found[0].startswith("pid 3960788:")

