"""The fleet probe from inside a pod (Winston in k3s, 2026-10-25).

A pod has one way out, the egress proxy, and no direct TCP to the tailnet; the
agents themselves are pods (roster `ssh_alias: '-'`). When HTTPS_PROXY is set the
probe must:

- reach a host through the proxy (`nc -X connect`) at its `access.tailnet_ip`,
  never by a direct /dev/tcp connect that the network policy drops (every host
  would read DOWN and page the owner);
- ask the cluster for a pod agent (`fleetctl status`), and read a stopped one as DOWN.

Outside a pod nothing changes (the OPS-14 promise eval covers that path).
Uses that eval's sandbox (imported, not modified) with its own nc and fleetctl fakes.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(LIB / "tests"))

from test_promise_OPS14_quiet_machine_reported_from_roster import (  # noqa: E402
    START, _monitor_jobs, _sandbox, _script)

JOB = next(j for j in _monitor_jobs() if j["name"] == "box-fleet-probe")

NC = r'''#!{py}
import json, os, sys
from pathlib import Path
S = Path(os.environ["SIM_NET"])
args = sys.argv[1:]
with open(S / "nc.jsonl", "a") as f:
    f.write(json.dumps(args) + "\n")
up = json.loads((S / "pod-up.json").read_text())
via_proxy = "-X" in args and "connect" in args and "-x" in args
target = [a for a in args if a.count(".") == 3 and ":" not in a]
sys.exit(0 if via_proxy and target and up.get(target[-1]) else 1)
'''

FLEETCTL = r'''#!{py}
import json, os, sys
from pathlib import Path
S = Path(os.environ["SIM_NET"])
for name, running in json.loads((S / "pods.json").read_text()).items():
    print(f"firm/{name}\t{'running' if running else 'stopped'}\t{1 if running else 0}/{1 if running else 0}")
'''


def _pod_sandbox(tmp_path, host_up: bool, pod_running: bool):
    sb = _sandbox(tmp_path)
    reg = sb["home"] / "Data" / ".datacore" / "registry" / "infrastructure.yaml"
    reg.write_text(yaml.safe_dump({
        "roles": {"always_on": "alpha"},
        "servers": {
            "alpha": {"kind": "server", "ssh_alias": "sim-alpha", "access": {"hostname": "alpha"}},
            "beta": {"kind": "server", "ssh_alias": "sim-beta",
                     "access": {"hostname": "beta", "tailnet_ip": "100.64.0.2"}},
            "gamma": {"kind": "server", "ssh_alias": "-", "access": {"hostname": "gamma"}},
        }}, sort_keys=False))
    bin_dir = sb["home"] / "bin"
    for name, body in (("nc", NC), ("fleetctl", FLEETCTL)):
        (bin_dir / name).unlink(missing_ok=True)
        (bin_dir / name).write_text(body.replace("{py}", sys.executable))
        (bin_dir / name).chmod(0o755)
    (sb["net"] / "pod-up.json").write_text(json.dumps({"100.64.0.2": host_up}))
    (sb["net"] / "pods.json").write_text(json.dumps({"gamma": pod_running}))
    (sb["net"] / "now").write_text(START.isoformat())
    (sb["net"] / "up.json").write_text(json.dumps({}))
    sb["env"] = {**sb["env"], "HTTPS_PROXY": "http://egress.shared.svc.cluster.local:3128"}
    return sb


def _probe(sb) -> str:
    script = sb["lib"] / _script(JOB).name
    r = subprocess.run(["bash", str(script)], env=sb["env"], cwd=sb["home"],
                       capture_output=True, text=True, timeout=120)
    return r.stdout + r.stderr


def test_in_a_pod_hosts_go_through_the_proxy_and_agents_are_asked_of_the_cluster(tmp_path):
    sb = _pod_sandbox(tmp_path, host_up=True, pod_running=True)
    out = _probe(sb)
    assert " beta UP " in out and " gamma UP " in out, out
    calls = [json.loads(line) for line in (sb["net"] / "nc.jsonl").read_text().splitlines()]
    assert calls and all("-X" in c and "egress.shared.svc.cluster.local:3128" in c for c in calls), calls
    assert all("100.64.0.2" in c for c in calls), "a host is reached at its tailnet address"
    targets = sb["net"] / "targets.jsonl"
    assert not targets.exists() or "/dev/tcp" not in targets.read_text(), "no direct connect from a pod"
    assert "fleet presence: ok" in out


def test_in_a_pod_a_stopped_agent_and_an_unreachable_host_read_down(tmp_path):
    sb = _pod_sandbox(tmp_path, host_up=False, pod_running=False)
    out = _probe(sb)
    assert " beta DOWN " in out and " gamma DOWN " in out, out
