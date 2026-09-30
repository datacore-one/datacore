#!/usr/bin/env python3
"""The sandbox fleet's stand-ins for everything that would leave the machine.

Linked onto the sandbox PATH by fleet_week_sim.py under the names of the tools
it replaces, and dispatched on that name:

    ssh HOST CMD     runs CMD as sandbox machine HOST (its home, its identity,
                     the same simulated clock); an offline or unknown host
                     fails exactly as ssh does, exit 255
    rsync / scp      HOST:PATH rewritten to that machine's sandbox path
    gh               GitHub is not reachable: lists answer "[]", the rest fail
    sudo             runs the command (the sandbox has one user)
    systemctl        every unit is "active"; start/restart do nothing
    crontab -l       this machine's schedule, as the manifest declares it
    launchctl        succeeds, does nothing

Host names come from `$SIM_ROOT/fleet.json`, which the harness writes from
the install's roster. Nothing here knows a fleet name.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

NAME = Path(sys.argv[0]).name
ROOT = Path(os.environ.get("SIM_ROOT", "/sim"))
STATE = Path(os.environ.get("SIM_STATE", str(ROOT / "state")))


def _fleet() -> dict:
    try:
        return json.loads((ROOT / "fleet.json").read_text())
    except (OSError, ValueError):
        return {"aliases": {}, "machines": {}}


def _offline() -> set[str]:
    try:
        return set(json.loads((STATE / "offline.json").read_text()))
    except (OSError, ValueError):
        return set()


def _resolve(host: str) -> str | None:
    host = host.split("@", 1)[-1]
    return _fleet()["aliases"].get(host)


def _machine_env(machine: str) -> dict:
    spec = _fleet()["machines"][machine]
    env = dict(os.environ)
    env.update(spec["env"])
    env["SIM_MACHINE"] = machine
    return env


def _unreachable(host: str, machine: str | None) -> int:
    if machine is None:
        print(f"ssh: Could not resolve hostname {host}: Name or service not known", file=sys.stderr)
    else:
        print(f"ssh: connect to host {host} port 22: Connection timed out", file=sys.stderr)
    return 255


_SSH_ARG = set("BbcDEeFIiJLlmOoPpQRSWw")


def ssh(argv: list[str]) -> int:
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        flag = argv[i]
        i += 2 if (len(flag) == 2 and flag[1] in _SSH_ARG) else 1
    if i >= len(argv):
        print("usage: ssh host [command]", file=sys.stderr)
        return 255
    host, cmd = argv[i], argv[i + 1:]
    machine = _resolve(host)
    if machine is None or machine in _offline():
        return _unreachable(host, machine)
    if not cmd:
        return 0
    env = _machine_env(machine)
    return subprocess.run(["bash", "-c", " ".join(cmd)], env=env, cwd=env["HOME"]).returncode


_REMOTE = re.compile(r"^(?:[^@/:\s]+@)?([A-Za-z0-9._-]+):(.*)$")


def _rewrite(args: list[str]) -> tuple[list[str], int | None]:
    out = []
    for a in args:
        m = _REMOTE.match(a) if not a.startswith("-") else None
        if m and not a.startswith("/"):
            host, path = m.group(1), m.group(2)
            machine = _resolve(host)
            if machine is None or machine in _offline():
                return [], _unreachable(host, machine)
            home = _fleet()["machines"][machine]["env"]["HOME"]
            path = path.strip("'\"")
            if path.startswith("~"):
                path = home + path[1:]
            elif path.startswith("$HOME"):
                path = home + path[5:]
            elif not path.startswith("/"):
                path = home + "/" + path
            out.append(path)
        else:
            out.append(a)
    return out, None


def rsync(argv: list[str]) -> int:
    args, err = _rewrite(argv)
    if err is not None:
        print("rsync: connection unexpectedly closed (0 bytes received so far) [Receiver]", file=sys.stderr)
        return 255
    clean, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("-e", "--rsh"):
            skip = True
            continue
        if a.startswith("--rsh=") or a.startswith("-e"):
            continue
        clean.append(a)
    return subprocess.run(["/usr/bin/rsync", *clean]).returncode


def scp(argv: list[str]) -> int:
    args, err = _rewrite([a for a in argv if not a.startswith("-")])
    if err is not None:
        return err
    return subprocess.run(["cp", "-r", *args]).returncode


def gh(argv: list[str]) -> int:
    if argv[:2] == ["auth", "status"]:
        print("github.com: not reachable from the sandbox", file=sys.stderr)
        return 1
    if "--json" in argv or (argv[:1] == ["api"] and "--paginate" in argv):
        print("[]")
        return 0
    print("gh: GitHub is not reachable from the fleet sandbox", file=sys.stderr)
    return 1


def sudo(argv: list[str]) -> int:
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("-u", "-g", "-C") else 1
    if i >= len(argv):
        return 0
    cmd = argv[i:]
    return subprocess.run(["bash", "-c", " ".join(cmd)] if len(cmd) == 1 else cmd).returncode


def systemctl(argv: list[str]) -> int:
    verbs = [a for a in argv if not a.startswith("-")]
    if verbs[:1] == ["is-active"]:
        print("active")
    elif verbs[:1] == ["is-enabled"]:
        print("enabled")
    elif verbs[:1] in (["show"],):
        print("ActiveState=active\nResult=success")
    return 0


def crontab(argv: list[str]) -> int:
    machine = os.environ.get("SIM_MACHINE", "")
    try:
        print((STATE / f"crontab-{machine}.txt").read_text(), end="")
    except OSError:
        pass
    return 0


def main() -> int:
    table = {"ssh": ssh, "rsync": rsync, "scp": scp, "gh": gh, "sudo": sudo,
             "systemctl": systemctl, "crontab": crontab}
    fn = table.get(NAME)
    if fn is None:  # launchctl and anything else linked here: succeed quietly
        return 0
    return fn(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
