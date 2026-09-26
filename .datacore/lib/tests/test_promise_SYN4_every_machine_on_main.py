"""SYN-4: Every machine runs the current main version of Datacore; a machine left
on an old or side branch alerts me within a day.

Kind: production contract (read-only ssh, timeouts).

For every server in `registry/infrastructure.yaml` (mac, box, nightshift,
hermes, plur-claw), every Datacore CODE checkout that exists there -- the data
root when its origin is the datacore repo, and `~/.datacore/v2-runner` -- must
  * be on branch `main` (not a side branch or a detached HEAD), and
  * hold every commit of origin/main older than one day (`git ls-remote` for
    the tip; commit dates read from this machine's clone).

The "alerts me within a day" half (deterministic): no detector exists today
(OI-07). The fleet probe (`fleet_status.probe`) already reads each machine's
runner and data checkouts, so it is the one to name the branch: run against a
fixture machine whose runner is on a side branch, it must report that branch
and flag it. (Scheduling it daily with an alert is the rest of the fix.)
origin/main is read from a throwaway partial clone in tmp (the real clone's
refs are never written).

Seeded failure: 2026-09-26 probe -- hermes ~/.datacore/v2-runner on
fix/geo-research-delivery-format, 29 commits behind origin/main, no alert.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DAY = 24 * 3600

PROBE = r'''for d in __DIRS__; do
  d=$(eval echo "$d"); [ -d "$d/.git" ] || continue
  url=$(git -C "$d" remote get-url origin 2>/dev/null)
  case "$url" in *datacore-one/datacore|*datacore-one/datacore.git|*/datacore.git|*/datacore) ;; *) continue;; esac
  echo "CHECKOUT $d $(git -C "$d" rev-parse --abbrev-ref HEAD) $(git -C "$d" rev-parse HEAD) $(git -C "$d" log --format=%H -300 | tr '\n' ,)"
done'''


def _servers() -> dict:
    reg = yaml.safe_load((ROOT / ".datacore" / "registry" / "infrastructure.yaml").read_text())
    return {k: v for k, v in (reg.get("servers") or {}).items() if isinstance(v, dict)}


def _probe(name: str, cfg: dict) -> tuple[str, str]:
    access = cfg.get("access") or {}
    dirs = [access.get("data_root") or "~/Data", access.get("runner") or "~/.datacore/v2-runner"]
    script = PROBE.replace("__DIRS__", " ".join(f"'{d}'" for d in dict.fromkeys(dirs)))
    alias = cfg.get("ssh_alias") or name
    cmd = (["bash", "-c", script] if alias == "-" or name == "mac" else
           ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, script])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return name, "UNREACHABLE timeout"
    return name, r.stdout if r.returncode == 0 else f"UNREACHABLE rc={r.returncode}"


def _mirror(tmp: Path) -> tuple[Path, str]:
    """A tree-less bare clone of origin in tmp: commit graph and dates only."""
    url = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                         capture_output=True, text=True, timeout=20).stdout.strip()
    dest = tmp / "origin-mirror.git"
    r = subprocess.run(["git", "clone", "-q", "--bare", "--filter=tree:0", url, str(dest)],
                       capture_output=True, text=True, timeout=50,
                       env={**os.environ, "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10"})
    assert r.returncode == 0, f"could not read origin/main (could not check): {r.stderr.strip()[:120]}"
    tip = subprocess.run(["git", "-C", str(dest), "rev-parse", "main"],
                         capture_output=True, text=True, timeout=20).stdout.strip()
    return dest, tip


def _origin_commits(mirror: Path) -> set[str]:
    return set(subprocess.run(["git", "-C", str(mirror), "rev-list", "--all"],
                              capture_output=True, text=True, timeout=45).stdout.split())


def _stale_since(mirror: Path, head: str, tip: str) -> float | None:
    """Commit time of the oldest origin/main commit this checkout lacks; None if none."""
    r = subprocess.run(["git", "-C", str(mirror), "log", "--format=%ct", f"{head}..{tip}"],
                       capture_output=True, text=True, timeout=45,
                       env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
    if r.returncode != 0:
        return -1.0   # objects unknown here: cannot prove it is current
    times = [int(t) for t in r.stdout.split()]
    return min(times) if times else None


@pytest.mark.production
def test_every_machine_runs_current_main(tmp_path):
    mirror, tip = _mirror(tmp_path)
    known = _origin_commits(mirror)
    servers = _servers()
    with ThreadPoolExecutor(len(servers)) as pool:
        outs = dict(pool.map(lambda kv: _probe(*kv), servers.items()))
    problems, seen = [], 0
    now = time.time()
    for name, out in sorted(outs.items()):
        if out.startswith("UNREACHABLE"):
            problems.append(f"{name}: could not check ({out})")
            continue
        for line in out.splitlines():
            m = re.match(r"CHECKOUT (\S+) (\S+) ([0-9a-f]{40}) (\S*)", line)
            if not m:
                continue
            seen += 1
            path, branch, head, ancestry = m.groups()
            # Local commits not yet pushed (the operator's own work) are ahead,
            # not old: judge from the newest ancestor origin knows.
            head = next((c for c in ancestry.split(",") if c in known), head)
            if branch != "main":
                problems.append(f"{name}:{path} on {branch}, not main")
                continue
            since = _stale_since(mirror, head, tip)
            if since == -1.0:
                problems.append(f"{name}:{path} at {head[:9]}, a commit origin does not have")
            elif since is not None and now - since > DAY:
                problems.append(f"{name}:{path} lacks origin/main commits from "
                                f"{(now - since) / 3600:.0f} h ago")
    assert seen, "no Datacore checkout found on any machine (could not check)"
    assert not problems, "machines not on current main: " + "; ".join(problems)



def test_the_fleet_probe_flags_a_runner_on_a_side_branch(tmp_path):
    import fleet_status
    runner = tmp_path / "runner"
    subprocess.run(["git", "init", "-q", "-b", "main", str(runner)], check=True, timeout=30)
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["remote", "add", "origin", "git@github.com:datacore-one/datacore.git"],
                 ["commit", "-q", "--allow-empty", "-m", "base"],
                 ["checkout", "-q", "-b", "fix/geo-research-delivery-format"]):
        subprocess.run(["git", "-C", str(runner), *args], check=True, timeout=30)
    row = fleet_status.probe("fixture", {"ssh_alias": "-", "access": {
        "data_root": str(tmp_path / "data"), "runner": str(runner)}})
    assert row.get("reachable"), row
    assert row.get("runner_branch") == "fix/geo-research-delivery-format", \
        f"the probe does not report which branch a machine's runner is on: {row}"
