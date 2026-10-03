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
    curl             the outside world: api.telegram.org answers {"ok":true}
                     (so the last mile of a delivery can be observed), every
                     other host is unreachable, as with --network none
    ffmpeg/ffprobe   write/describe a placeholder audio file
    --git-gate SUB   called by the sandbox's `git` wrapper before a fetch,
                     pull, push, ls-remote or clone

THE SIMULATED NETWORK (harsh faults, F13+). `$SIM_STATE/net.json`, written by
the harness from the faults active at the time, holds: added latency and a
share of connections that fail (throttling, packet loss), pairs of machines
that cannot reach each other (partitions), whether GitHub (the remote) is
reachable, which machines' remote credential was rotated, and each machine's
clock skew. Every stand-in above that crosses the network reads it. Real
`tc netem` on the container's loopback (the harness sets it when the
container has NET_ADMIN) slows the :22 probes the same way.

Host names come from `$SIM_ROOT/fleet.json`, which the harness writes from
the install's roster. Nothing here knows a fleet name.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
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


def _net() -> dict:
    try:
        return json.loads((STATE / "net.json").read_text())
    except (OSError, ValueError):
        return {}


def _draw() -> float:
    """A deterministic sequence of draws in [0, 1): the same fault schedule
    loses the same connections on a rerun, so two runs can be compared."""
    import fcntl
    import hashlib
    p = STATE / "net-draws"
    try:
        with open(p, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            n = len(fh.read())
            fh.write(".")
    except OSError:
        n = 0
    return int(hashlib.sha256(f"fleet-sim-net-{n}".encode()).hexdigest()[:8], 16) / 2 ** 32


def _link_fault(src: str | None, dst: str) -> str | None:
    """None when a connection from machine `src` to `dst` ("github" for the
    remote) gets through the simulated network, else the reason it did not.
    Latency is spent here either way."""
    net = _net()
    if not net:
        return None
    for a, b in net.get("partitions") or []:
        if {a, b} == {src, dst}:
            time.sleep(float(net.get("connect_timeout_s") or 3))
            return "timeout"
    if dst == "github" and net.get("remote_down"):
        return "dns"
    delay = float(net.get("delay_s") or 0) + float(net.get("jitter_s") or 0) * _draw()
    if delay:
        time.sleep(delay)
    if float(net.get("fail_pct") or 0) and _draw() * 100 < float(net["fail_pct"]):
        time.sleep(float(net.get("connect_timeout_s") or 3))
        return "timeout"
    return None


def _skewed_env(env: dict, target: str) -> dict:
    """The target machine's clock: the caller's fake offset, less the caller's
    skew, plus the target's (clock skew, F19)."""
    skew = _net().get("skew") or {}
    if not skew or not env.get("FAKETIME"):
        return env
    try:
        base = int(env["FAKETIME"]) - int(skew.get(os.environ.get("SIM_MACHINE", ""), 0))
        env["FAKETIME"] = f"{base + int(skew.get(target, 0)):+d}"
    except ValueError:
        pass
    return env


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
    if _link_fault(os.environ.get("SIM_MACHINE"), machine):
        return _unreachable(host, machine)
    if not cmd:
        return 0
    env = _skewed_env(_machine_env(machine), machine)
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
            if _link_fault(os.environ.get("SIM_MACHINE"), machine):
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


def git_gate(sub: str) -> int:
    """Before a git network operation: is the remote (GitHub) reachable from
    this machine, and does it still accept this machine's credential?"""
    me = os.environ.get("SIM_MACHINE", "")
    net = _net()
    url = "https://github.com/datacore-one/"
    if me in (net.get("auth_fail") or []):
        print("remote: Invalid username or password.", file=sys.stderr)
        print(f"fatal: Authentication failed for '{url}'", file=sys.stderr)
        return 128
    why = _link_fault(me, "github")
    if why == "dns":
        print(f"fatal: unable to access '{url}': Could not resolve host: github.com", file=sys.stderr)
        return 128
    if why:
        print(f"fatal: unable to access '{url}': Failed to connect to github.com port 443 after "
              f"134000 ms: Connection timed out", file=sys.stderr)
        return 128
    return 0


def curl(argv: list[str]) -> int:
    """api.telegram.org answers like Telegram when the network lets it; every
    other host is unreachable, as it is from a --network none container."""
    url = next((a for a in argv if re.match(r"https?://", a)), "")
    out = None
    for i, a in enumerate(argv):
        if a in ("-o", "--output") and i + 1 < len(argv):
            out = argv[i + 1]
    host = re.sub(r"^https?://([^/:]+).*$", r"\1", url) if url else ""
    if host in ("localhost", "::1") or host.startswith("127."):
        return subprocess.run(["/usr/bin/curl", *argv]).returncode
    if host == "openrouter.ai":
        return openrouter(argv, out)
    if host != "api.telegram.org":
        print(f"curl: (6) Could not resolve host: {host or 'nothing'}", file=sys.stderr)
        return 6
    if _link_fault(os.environ.get("SIM_MACHINE"), "telegram"):
        print("curl: (28) Failed to connect to api.telegram.org port 443 after 60001 ms: "
              "Connection timed out", file=sys.stderr)
        return 28
    try:
        with open(STATE / "telegram.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "machine": os.environ.get("SIM_MACHINE"),
                                 "job": os.environ.get("SIM_JOB"), "method": url.rsplit("/", 1)[-1],
                                 "args": [a for a in argv if not a.startswith("http")][:12]}) + "\n")
    except OSError:
        pass
    body = json.dumps({"ok": True, "result": {"message_id": 1}}, separators=(",", ":"))   # as Telegram writes it
    if out:
        Path(out).write_text(body)
    else:
        print(body)
    return 0


def openrouter(argv: list[str], out: str | None) -> int:
    """The OpenRouter chat API, answered by the model stand-in (stand_in.py
    as runtime "openrouter"), so its faults -- slow, 429 -- reach this route."""
    if _link_fault(os.environ.get("SIM_MACHINE"), "openrouter"):
        print("curl: (28) Failed to connect to openrouter.ai port 443 after 30000 ms: Connection timed out",
              file=sys.stderr)
        return 28
    body = ""
    for i, a in enumerate(argv):
        if a in ("-d", "--data", "--data-binary", "--data-raw") and i + 1 < len(argv):
            body = sys.stdin.read() if argv[i + 1] == "@-" else argv[i + 1]
    try:
        prompt = "\n".join(m.get("content") or "" for m in json.loads(body).get("messages") or [])
    except (ValueError, AttributeError):
        prompt = body
    here = Path(__file__).resolve().parent
    r = subprocess.run([sys.executable, str(here / "stand_in.py"), "-p", "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, env={**os.environ, "SIM_RUNTIME": "openrouter"})
    try:
        env = json.loads(r.stdout or "{}")
    except ValueError:
        env = {}
    if r.returncode != 0 or env.get("is_error"):
        text = (env.get("result") or r.stderr or "").strip()
        code = 429 if "429" in text else 401 if "API key" in text else 500
        doc = {"error": {"message": text[:200] or "upstream error", "code": code}}
    else:
        doc = {"id": "fleet-sim", "choices": [{"index": 0, "finish_reason": "stop",
                                               "message": {"role": "assistant", "content": env.get("result", "")}}]}
    if out:
        Path(out).write_text(json.dumps(doc))
    else:
        print(json.dumps(doc))
    return 0


def ffmpeg(argv: list[str]) -> int:
    """Transcode: the output (the last argument) is a copy of the input."""
    src = next((argv[i + 1] for i, a in enumerate(argv) if a == "-i" and i + 1 < len(argv)), None)
    dst = argv[-1] if argv else None
    if not src or not dst or not Path(src).is_file():
        print(f"{src}: No such file or directory", file=sys.stderr)
        return 1
    try:
        Path(dst).write_bytes(Path(src).read_bytes() or b"\0")
    except OSError as exc:
        print(f"{dst}: {exc}", file=sys.stderr)
        return 1
    return 0


def ffprobe(argv: list[str]) -> int:
    print("42.0")
    return 0


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
    if sys.argv[1:2] == ["--git-gate"]:
        return git_gate(sys.argv[2] if len(sys.argv) > 2 else "")
    table = {"ssh": ssh, "rsync": rsync, "scp": scp, "gh": gh, "sudo": sudo,
             "systemctl": systemctl, "crontab": crontab, "curl": curl, "ffmpeg": ffmpeg,
             "ffprobe": ffprobe}
    fn = table.get(NAME)
    if fn is None:  # launchctl and anything else linked here: succeed quietly
        return 0
    return fn(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
