#!/usr/bin/env python3
"""What version is every machine running? One command, no guessing.

Built after a component sat six weeks behind on one machine with nothing
surfacing it, and after three wrong diagnoses in a single session that all had
the same cause: ASSUMING how to reach a machine instead of reading it.

  - Tris's repos were reported as "not git repos". They were git repos; ssh
    lands on hermes as root, the repos are gregor-owned, and git refused them.
  - The nightshift watchdog reported 15 phantom `origin/HEAD unset` failures
    every 30 minutes for weeks. Same cause, under systemd with no HOME.
  - A filesystem search for datacore-mcp found it on NO machine while Winston
    was demonstrably running v1.6.0.

So this reads `registry/infrastructure.yaml -> servers.<name>.access`, which
records ssh_user and service_user SEPARATELY because on hermes they differ.
Nothing here hardcodes a user or a path.

A machine that cannot be reached is reported as unreachable, never skipped:
"all green" must not be able to mean "all the ones that answered" (DIP-0046).

    fleet_status.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))

# Each probe prints "<label>=<value>" or nothing. Kept as shell so one ssh
# round-trip answers everything — response time is the point of this tool.
PROBE = r'''
# eval, because ~ and ~user do NOT expand inside quotes — the first version
# cd'd into a literal "~/Data" and reported every head as empty.
D=$(eval echo __DATA__); R=$(eval echo __RUNNER__)
cd "$D" 2>/dev/null && echo "data_head=$(git rev-parse --short HEAD 2>/dev/null)"
# Core version can live in the data root OR the runner. On plur-claw and
# hermes the "Data" tree is the AGENT'S OWN SPACE repo (data-space /
# tris-space), not the datacore core — so reading VERSION only from there
# reported them as having no core at all, when the core is in their runner.
CORE=""
[ -f "$D/.datacore/VERSION" ] && CORE=$(head -1 "$D/.datacore/VERSION" | tr -d " ")
[ -z "$CORE" ] && [ -n "$R" ] && [ -f "$R/.datacore/VERSION" ] && \
  CORE=$(head -1 "$R/.datacore/VERSION" | tr -d " ")
echo "core=${CORE:-?}"
[ -n "$R" ] && cd "$R" 2>/dev/null && echo "runner_head=$(git rev-parse --short HEAD 2>/dev/null)"
# datacore-mcp is an IN-REPO BUILD, not a package. The deployed tree has dist/
# and node_modules/ and NO package.json, so a package.json probe reports it
# absent on every server while Winston serves v1.6.0. The version is compiled
# into dist/index.js, and the launch path lives in ~/.hermes/config.yaml — not
# .mcp.json, which is why a config fallback missed it as well.
# ASK THE SYSTEM WHAT IT ACTUALLY RUNS, FIRST.
# Every other branch below is inference — a config file that may name a path
# nothing uses, or a source checkout that may be a leftover. `command -v` is
# the only one that answers "what executes when something types this". It was
# last, so on winston the probe kept reporting the stale in-repo build (1.6.0)
# while /usr/bin/datacore-mcp was 2.1.0: a machine we had just upgraded looked
# un-upgraded, and the disagreement pointed at the wrong thing entirely.
m=""
if command -v datacore-mcp >/dev/null 2>&1; then
  real=$(readlink -f "$(command -v datacore-mcp)")
  # .../<pkg>/dist/index.js -> <pkg>
  m=$(printf '%s' "$real" | sed "s#/dist/.*##")
  [ -f "$m/package.json" ] || m=""
fi
[ -z "$m" ] && m=$(grep -ho "[^ \"']*datacore-mcp[^ \"']*" ~/.hermes/config.yaml ~/.claude.json ~/.mcp.json "$D/.mcp.json" 2>/dev/null | head -1 | sed "s#/dist/.*##")
# A config may name a path that DOES NOT EXIST — nightshift's .mcp.json points
# at a tree that was never deployed there. Accepting it non-empty made every
# later fallback unreachable, so the machine reported no MCP while Miles was
# running one. Validate before accepting.
[ -n "$m" ] && [ ! -d "$m" ] && m=""
[ -z "$m" ] && m=$(ls -d "$D"/*/2-projects/datacore-mcp 2>/dev/null | head -1)
# GLOBAL NPM INSTALL. Miles runs /usr/bin/datacore-mcp ->
# /usr/lib/node_modules/@datacore-one/mcp, which no in-repo search could ever
# find — the third place this probe had to learn to look, after a guessed path
# and the hermes config.
[ -z "$m" ] && for g in /usr/lib/node_modules/@datacore-one/mcp \
                        /usr/local/lib/node_modules/@datacore-one/mcp; do
  [ -f "$g/package.json" ] && { m="$g"; break; }
done
# Hermes ships its OWN node install, so the package is not on the default PATH
# and `command -v` finds nothing — Tris has been running v1.6.0 for 14 days
# while every probe reported it absent.
for hp in "$HOME/.hermes/node/lib/node_modules/@datacore-one/mcp"; do
  [ -z "$m" ] && [ -f "$hp/package.json" ] && m="$hp"
done
[ -z "$m" ] && command -v datacore-mcp >/dev/null 2>&1 && \
  m=$(dirname "$(dirname "$(readlink -f "$(command -v datacore-mcp)")")")
if [ -n "$m" ] && [ -f "$m/package.json" ]; then
  echo "mcp=$(python3 -c "import json;print(json.load(open('$m/package.json'))['version'])" 2>/dev/null)"
elif [ -n "$m" ] && [ -f "$m/dist/index.js" ]; then
  v=$(grep -oE "version[\": ]+[0-9]+\.[0-9]+\.[0-9]+" "$m/dist/index.js" 2>/dev/null | head -1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+")
  [ -n "$v" ] && echo "mcp=$v"
fi

# PYTHON: report the interpreter this machine actually resolves, by path AND
# version. `python3` under a non-interactive shell picked /usr/bin/python3 on
# the Mac (3.9) while the real one is pyenv — a version that is true of no job
# that runs here.
PY3=$(command -v python3 2>/dev/null)
[ -x "$HOME/.pyenv/shims/python3" ] && PY3="$HOME/.pyenv/shims/python3"
echo "python=$("$PY3" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null)"
echo "python_path=$PY3"
echo "org_workspace=$("$PY3" -c 'import org_workspace as o;print(getattr(o,"__version__","?"))' 2>/dev/null)"

# Hermes ships its OWN node, so its global bin is not on a non-interactive
# PATH and `command -v datacore` finds nothing there — the same blind spot that
# reported Tris as having no MCP for 14 days. List it explicitly rather than
# hoping the login shell exports it.
for c in datacore "$HOME/.hermes/node/bin/datacore" "$HOME/.local/bin/datacore" \
         /usr/local/bin/datacore /usr/bin/datacore; do
  command -v "$c" >/dev/null 2>&1 && { echo "cli=$("$c" --version 2>&1 | head -1)"; break; }
done
'''


def _expand(path: str, service_user: str | None) -> str:
    """~ and ~user forms are resolved ON THE TARGET by the shell, not here."""
    return path or ""


def probe(name: str, cfg: dict) -> dict:
    access = (cfg or {}).get("access") or {}
    alias = (cfg or {}).get("ssh_alias") or name
    data = _expand(access.get("data_root", "~/Data"), access.get("service_user"))
    runner = _expand(access.get("runner", ""), access.get("service_user"))
    svc = access.get("service_user")
    ssh_user = access.get("ssh_user")

    script = PROBE.replace("__DATA__", data).replace("__RUNNER__", runner or "")

    # BASE64 the script. Nesting quotes through ssh AND sudo -u is how the
    # first version died on hermes — the only machine where the two users
    # differ, i.e. the exact case this tool exists for. Encoding removes the
    # entire quoting class rather than escaping it more carefully.
    import base64
    b64 = base64.b64encode(script.encode()).decode()
    inner = f"echo {b64} | base64 -d | bash"
    # Run AS THE SERVICE USER when it differs from the ssh user. Reading a
    # gregor-owned repo as root produced every wrong answer this replaces.
    if svc and ssh_user and svc != ssh_user:
        inner = f"sudo -u {svc} bash -lc 'echo {b64} | base64 -d | bash'"

    if alias == "-" or name == "mac":
        cmd = ["bash", "-lc", inner]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, inner]

    out = {"machine": name, "reachable": False}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["error"] = f"{type(exc).__name__}"
        return out
    if r.returncode != 0 and not (r.stdout or "").strip():
        out["error"] = (r.stderr or "").strip().splitlines()[-1][:70] if r.stderr else "no output"
        return out
    out["reachable"] = True
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if v.strip():
                out[k.strip()] = v.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    import yaml
    reg = yaml.safe_load((ROOT / ".datacore/registry/infrastructure.yaml").read_text())
    servers = {k: v for k, v in (reg.get("servers") or {}).items() if isinstance(v, dict)}
    if not servers:
        print("ERROR: no servers in registry — refusing to report a healthy fleet")
        return 2

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(lambda kv: probe(*kv), servers.items()))
    rows.sort(key=lambda r: r["machine"])

    if a.json:
        print(json.dumps({"machines": rows}, indent=2))
        return 1 if any(not r["reachable"] for r in rows) else 0

    cols = ["data_head", "core", "mcp", "org_workspace", "cli", "python"]
    print(f"  {'machine':<12} {'data':<9} {'core':<7} {'mcp':<7} {'org-ws':<7} {'cli':<7} py")
    for r in rows:
        if not r["reachable"]:
            print(f"  {r['machine']:<12} UNREACHABLE — {r.get('error','?')}")
            continue
        # "?" means THE PROBE COULD NOT TELL; "—" would say "not installed".
        # Conflating those is the error that made me report Tris as having no
        # MCP while it had 14 days of uptime: a failed lookup rendered as a
        # finding. An unknown must look different from a known absence.
        vals = [r.get(c, "?") for c in cols]
        print(f"  {r['machine']:<12} {vals[0]:<9} {vals[1]:<9} {vals[2]:<7} "
              f"{vals[3]:<7} {vals[4]:<7} {vals[5]}")

    # Disagreement is the finding. A fleet where one machine is six weeks behind
    # looks identical to a healthy one unless the versions are compared.
    def spread(key: str) -> set:
        # "?" is excluded from drift: an unknown is not a disagreement, and
        # counting it as one would manufacture findings out of probe failures.
        return {r.get(key) for r in rows
                if r["reachable"] and r.get(key) and r.get(key) != "?"}

    drift = [k for k in ("data_head", "core", "mcp", "org_workspace", "cli")
             if len(spread(k)) > 1]
    unknown = sum(1 for r in rows if r["reachable"]
                  for k in ("core", "mcp", "org_workspace", "cli")
                  if r.get(k, "?") == "?")
    unreachable = [r["machine"] for r in rows if not r["reachable"]]
    print(f"\nfleet: {len(rows)} machine(s), {len(unreachable)} unreachable"
          + (f", DRIFT in {', '.join(drift)}" if drift else ", all versions agree")
          + (f", {unknown} value(s) the probe COULD NOT DETERMINE — ask the "
             f"machine directly" if unknown else ""))
    return 1 if (drift or unreachable) else 0


if __name__ == "__main__":
    raise SystemExit(main())
