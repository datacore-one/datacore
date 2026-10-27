"""fleet-backup must never push the node into disk pressure.

2026-10-26: Winston's 07:05 backup took the box to 97%; the kubelet marked the
node under disk pressure, evicted Tris and Data and would not start Winston --
every agent down for hours. Before stopping an agent the backup now makes room
by dropping that agent's oldest backups (never the newest), and skips the agent,
left running, when even that leaves under the floor.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "bin" / "fleet-backup"


def stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def setup(tmp_path, backups_mb: list[int], total_mb: int, other_used_mb: int):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    dest = tmp_path / "dest"
    (dest / "winston").mkdir(parents=True)
    for i, mb in enumerate(backups_mb):  # oldest first
        f = dest / "winston" / f"2026102{i}T010000Z.tar.gz"
        f.write_bytes(b"\0" * mb * 1024 * 1024)
        os.utime(f, (1_000_000 + i * 100, 1_000_000 + i * 100))
    home = tmp_path / "vol"
    home.mkdir()
    calls = tmp_path / "kubectl.calls"
    # df: the disk is TOTAL, OTHER is used outside the backups, the backups use what they use.
    stub(bin_ / "df", f'''used=$(( {other_used_mb} * 1024 + $(du -sk "{dest}" | cut -f1) ))
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
echo "/dev/x {total_mb * 1024} $used $(( {total_mb} * 1024 - used )) 0% /"
''')
    stub(bin_ / "kubectl", f'''echo "$*" >> "{calls}"
case "$*" in
  *"get pvc"*) echo pv1 ;;
  *"get pv "*) echo "{home}" ;;
  *"get configmap winston-agent"*) echo "" ;;
  *"get deploy"*) echo 1 ;;
  *"get secret"*) echo "kind: Secret" ;;
esac
exit 0
''')
    tool = tmp_path / "agent-state"
    tool.write_text("import sys; o=sys.argv[sys.argv.index('--out')+1]; open(o,'wb').write(b'\\0'*1024)\n")
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", DEST=str(dest), STATE_TOOL=str(tool),
               KUBECONFIG="/dev/null")
    return dest, calls, env


def test_room_is_made_from_the_agents_oldest_backups(tmp_path):
    # 100 MB disk, 58 MB used elsewhere, three 10 MB backups: 12% free. The next
    # copy (~10 MB) needs the oldest two gone to keep 20% free.
    dest, calls, env = setup(tmp_path, [10, 10, 10], total_mb=100, other_used_mb=58)
    r = subprocess.run(["sh", str(TOOL), "winston"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    left = sorted(p.name for p in (dest / "winston").glob("*.tar.gz"))
    assert "20261020T010000Z.tar.gz" not in left and "20261022T010000Z.tar.gz" in left
    assert "scale" in calls.read_text()


def test_an_agent_is_skipped_and_left_running_when_no_room_can_be_made(tmp_path):
    dest, calls, env = setup(tmp_path, [10], total_mb=100, other_used_mb=78)
    r = subprocess.run(["sh", str(TOOL), "winston"], capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert "not enough disk" in r.stdout
    assert (dest / "winston/20261020T010000Z.tar.gz").exists(), "the newest backup is never dropped"
    assert "scale" not in (calls.read_text() if calls.exists() else "")
