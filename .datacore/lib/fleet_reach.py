#!/usr/bin/env python3
"""Run a read-only command where a machine's work lives: its host, or its pod.

Since 2026-10-25 Winston, Tris and Data run as pods on the box (k3s, namespace
firm). Their old hosts are stopped or retired. Every fleet check kept its own
host table and kept ssh-ing to machines that no longer run anything, so it
could only ever answer "could not tell".

The roster (.datacore/registry/infrastructure.yaml) says where each machine's
work runs. A machine whose `access.k8s` names a deployment runs in that pod:

    access:
      k8s: {kubeconfig: ~/.kube/box.yaml, namespace: firm, deployment: tris, container: cron}

Otherwise it is a host reached by `ssh_alias` (a `-` alias means none). A host
that also holds pods (the box: backups, the fleet probe) keeps its own host
duties: pass host=True to reach the host instead of the pod. FLEET_KUBECONFIG
overrides the roster's kubeconfig (the box itself uses k3s's own).

    fleet_reach.py place  <machine>
    fleet_reach.py run    <machine> <shell command> [--host]
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parent
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


class Unreachable(Exception):
    """The roster names no way to reach this machine's work."""


@dataclass
class Place:
    machine: str
    kind: str                       # local | host | pod
    target: str = ""                # ssh alias, or deploy/<name>
    k8s: dict = field(default_factory=dict)


def _roster_path(path: Path | None) -> Path:
    if path:
        return Path(path)
    from actor_identity import INFRA
    return INFRA


def servers(path: Path | None = None) -> dict:
    data = yaml.safe_load(_roster_path(path).read_text()) or {}
    return data.get("servers", data) or {}


def _local_name(entries: dict) -> str | None:
    host = socket.gethostname().split(".")[0]
    for name, e in entries.items():
        if host in (name, ((e or {}).get("access") or {}).get("hostname")):
            return name
    return None


def this_machine(path: Path | None = None) -> str | None:
    """The roster name of the machine this runs on, or None."""
    return _local_name(servers(path))


def agent_machines(path: Path | None = None) -> list[str]:
    """Machines that do fleet work today: not retired."""
    return [n for n, e in servers(path).items() if isinstance(e, dict) and not e.get("retired")]


def place(machine: str, path: Path | None = None, host: bool = False, local: str | None = None) -> Place:
    entries = servers(path)
    e = entries.get(machine)
    if not isinstance(e, dict):
        raise Unreachable(f"{machine}: not in the roster")
    if e.get("retired"):
        raise Unreachable(f"{machine}: retired in the roster")
    k8s = (e.get("access") or {}).get("k8s") or {}
    if k8s.get("deployment") and not host:
        return Place(machine, "pod", f"deploy/{k8s['deployment']}", dict(k8s))
    if machine == (local if local is not None else _local_name(entries)):
        return Place(machine, "local")
    alias = str(e.get("ssh_alias") or machine)
    if alias in ("-", ""):
        raise Unreachable(f"{machine}: no ssh alias and no pod in the roster")
    return Place(machine, "host", alias)


def places(machine: str, path: Path | None = None, local: str | None = None) -> list[Place]:
    """Every place this machine does work: its pod, then its host when it has one too."""
    out = [place(machine, path, local=local)]
    if out[0].kind == "pod":
        try:
            out.append(place(machine, path, host=True, local=local))
        except Unreachable:
            pass
    return out


def containers(pl: Place) -> list[Place]:
    """A pod as one place per container (a process scan sees only its own
    container); any other place as itself. An unreadable pod raises Unreachable."""
    if pl.kind != "pod":
        return [pl]
    cfg = os.environ.get("FLEET_KUBECONFIG") or os.path.expanduser(str(pl.k8s.get("kubeconfig") or ""))
    r = subprocess.run(["kubectl"] + (["--kubeconfig", cfg] if cfg else [])
                       + ["-n", str(pl.k8s.get("namespace") or "default"), "get", pl.target,
                          "-o", "jsonpath={.spec.template.spec.containers[*].name}"],
                       capture_output=True, text=True, timeout=30)
    names = r.stdout.split() if r.returncode == 0 else []
    if not names:
        raise Unreachable(f"{pl.machine}: could not list the containers of {pl.target} ({r.stderr.strip()[-120:]})")
    return [Place(pl.machine, "pod", pl.target, {**pl.k8s, "container": n}) for n in names]


def container_ready(pl: Place, name: str) -> bool | None:
    """Is this pod's container `name` ready? None when the pod has no such
    container or the cluster could not be asked (could not tell)."""
    cfg = os.environ.get("FLEET_KUBECONFIG") or os.path.expanduser(str(pl.k8s.get("kubeconfig") or ""))
    dep = str(pl.k8s.get("deployment") or "")
    r = subprocess.run(["kubectl"] + (["--kubeconfig", cfg] if cfg else [])
                       + ["-n", str(pl.k8s.get("namespace") or "default"), "get", "pods",
                          "-l", f"app.kubernetes.io/name={dep}", "-o",
                          "jsonpath={range .items[*].status.containerStatuses[*]}{.name}={.ready} {end}"],
                       capture_output=True, text=True, timeout=30)
    states = dict(kv.split("=", 1) for kv in r.stdout.split() if "=" in kv) if r.returncode == 0 else {}
    return None if name not in states else states[name] == "true"


def run_at(pl: Place, cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv(pl, cmd), capture_output=True, text=True, timeout=timeout)


def argv(pl: Place, cmd: str) -> list[str]:
    if pl.kind == "local":
        return ["sh", "-c", cmd]
    if pl.kind == "host":
        return [*SSH, pl.target, cmd]
    cfg = os.environ.get("FLEET_KUBECONFIG") or os.path.expanduser(str(pl.k8s.get("kubeconfig") or ""))
    out = ["kubectl"] + (["--kubeconfig", cfg] if cfg else [])
    out += ["-n", str(pl.k8s.get("namespace") or "default"), "exec", pl.target]
    if pl.k8s.get("container"):
        out += ["-c", str(pl.k8s["container"])]
    return out + ["--", "sh", "-c", cmd]


def run(machine: str, cmd: str, path: Path | None = None, host: bool = False, local: str | None = None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv(place(machine, path, host=host, local=local), cmd),
                          capture_output=True, text=True, timeout=timeout)


def schedule_listing(pl: Place) -> str:
    """A shell snippet printing every scheduled command line at this place."""
    if pl.kind == "pod":
        return "cat /etc/datacore/crontab 2>/dev/null"
    return ("(crontab -l 2>/dev/null; sudo -n crontab -l 2>/dev/null; "
            "for u in $(systemctl list-timers --all --no-legend --plain 2>/dev/null | awk '{print $NF}'); do "
            "systemctl cat \"$u\" 2>/dev/null | grep '^ExecStart'; "
            "s=${u%.timer}.service; systemctl cat \"$s\" 2>/dev/null | grep '^ExecStart'; done)")


def main(argv_: list[str]) -> int:
    if len(argv_) >= 2 and argv_[0] == "place":
        print(place(argv_[1]))
        return 0
    if len(argv_) >= 3 and argv_[0] == "run":
        r = run(argv_[1], argv_[2], host="--host" in argv_)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        return r.returncode
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
