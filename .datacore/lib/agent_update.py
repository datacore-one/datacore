#!/usr/bin/env python3
"""Self-updating for Firm agent hosts, with a health gate and automatic rollback.

Upgrading an agent host unattended is only safe if a bad release can be undone
without a human. hermes-agent 0.18 proved why: it added a hard floor of 64k on
the model context window and refuses to boot below it, so a blind
`install --upgrade` would have left the gateway dead until someone noticed.

So every run is: snapshot -> upgrade -> health-check -> (rollback on failure).
The snapshot pins exact versions, so rollback is a reinstall of known-good pins
rather than a guess. A run that cannot restore health exits non-zero with the
service left stopped rather than crash-looping.

Usage:
    agent_update.py --host hermes --check     # report only, change nothing
    agent_update.py --host hermes --apply     # upgrade, verify, roll back on failure

Profiles are declared below rather than discovered, because "which packages
constitute this agent" is a deployment decision, not something to infer.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

STATE_DIR = Path.home() / ".datacore" / "state"
LOG_PATH = Path.home() / ".datacore" / "logs" / "agent-update.log"

# Health probes get their own generous ceiling: a cold BGE embedder load on a
# large engram store takes ~20s, and a probe that times out early would look
# like a failed upgrade and trigger a needless rollback.
HEALTH_TIMEOUT = 90
HEALTH_RETRIES = 3
HEALTH_BACKOFF = 10


PROFILES = {
    "hermes": {
        "description": "Tris — Hermes Agent gateway + PLUR bridge",
        # Paths are home-relative and expanded at load (see _expand). The
        # updater runs as the agent's own user on the agent's own host.
        "home": "~",
        "venv": "~/.hermes/hermes-agent/venv",
        "uv": "~/.local/bin/uv",
        "npm_prefix": "~/.hermes/node",
        "pip_packages": ["hermes-agent", "plur-hermes"],
        "npm_packages": ["@plur-ai/cli", "@plur-ai/mcp"],
        "service": "hermes-gateway.service",
        "service_scope": "user",
        # Proves the PLUR bridge resolves a direct binary (not the npx
        # fallback, which spawns an orphan-prone node grandchild) AND that a
        # real recall round-trips through the CLI.
        "probe": (
            "from plur_hermes.bridge import PlurBridge\n"
            "b = PlurBridge()\n"
            "binary = b._find_binary()\n"
            "assert not binary.startswith('npx:'), f'bridge fell back to npx: {binary}'\n"
            "r = b.call('recall', ['health probe'], timeout=60, retries=0)\n"
            "assert isinstance(r.get('count'), int), 'recall returned no count'\n"
            "print('probe ok:', binary, r['count'], 'results')\n"
        ),
    },
}


_PATH_KEYS = ("home", "venv", "uv", "npm_prefix")


def _expand(prof: dict) -> dict:
    """Resolve the profile's home-relative paths for the current user."""
    out = dict(prof)
    for k in _PATH_KEYS:
        if k in out:
            out[k] = os.path.expanduser(out[k])
    return out


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass  # logging must never be the thing that fails an upgrade


def run(cmd: list[str], timeout: int = 300, check: bool = False,
        cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run in its own process group and kill the whole group on timeout.

    A plain subprocess.run(timeout=) SIGKILLs only the direct child. npm/npx
    spawn a node grandchild, which then reparents to PID 1 and spins at high
    CPU forever — the datacore-one/datacore#33 orphan leak. Kill the group.
    """
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True, cwd=cwd,
    ) as p:
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), 9)
            except (ProcessLookupError, OSError):
                pass
            p.communicate(timeout=5)
            out, p.returncode = "", 124
    cp = subprocess.CompletedProcess(cmd, p.returncode, out or "", "")
    if check and cp.returncode != 0:
        raise RuntimeError(f"command failed ({cp.returncode}): {' '.join(cmd)}\n{cp.stdout}")
    return cp


# ── version inspection ──────────────────────────────────────────────────────

def pip_versions(prof: dict) -> dict[str, str]:
    py = f"{prof['venv']}/bin/python"
    out = run([py, "-c",
               "import importlib.metadata as m, json, sys;"
               "print(json.dumps({n: m.version(n) for n in sys.argv[1:]}))",
               *prof["pip_packages"]])
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {}


def npm_versions(prof: dict) -> dict[str, str]:
    out = run(["npm", "ls", "-g", "--prefix", prof["npm_prefix"], "--depth", "0", "--json"])
    try:
        deps = json.loads(out.stdout).get("dependencies", {})
    except json.JSONDecodeError:
        return {}
    return {p: deps[p]["version"] for p in prof["npm_packages"] if p in deps}


def snapshot(prof: dict) -> dict:
    return {"pip": pip_versions(prof), "npm": npm_versions(prof)}


def latest_available(prof: dict) -> dict:
    """What we would move TO. Reported by --check so a human can eyeball the
    jump before it happens."""
    latest = {"pip": {}, "npm": {}}
    for pkg in prof["pip_packages"]:
        # PyPI's JSON API rather than `uv pip index versions` — uv's output
        # format is not a stable contract, and info.version is unambiguous.
        try:
            with urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30) as r:
                latest["pip"][pkg] = json.load(r)["info"]["version"]
        except (URLError, HTTPError, KeyError, json.JSONDecodeError, OSError):
            latest["pip"][pkg] = "?"
    for pkg in prof["npm_packages"]:
        out = run(["npm", "view", pkg, "version"], timeout=60)
        latest["npm"][pkg] = out.stdout.strip() or "?"
    return latest


# ── upgrade / rollback ──────────────────────────────────────────────────────

def install(prof: dict, pip_specs: list[str], npm_specs: list[str]) -> None:
    # cwd matters: uv walks up from the working directory looking for uv.toml.
    # Left at whatever cwd the caller had (/root under a plain SSH), it dies on
    # a permission error before installing anything.
    home = prof["home"]
    if pip_specs:
        os.environ["VIRTUAL_ENV"] = prof["venv"]
        run([prof["uv"], "pip", "install", "--upgrade", *pip_specs],
            timeout=600, check=True, cwd=home)
    if npm_specs:
        run(["npm", "install", "-g", "--prefix", prof["npm_prefix"], *npm_specs],
            timeout=600, check=True, cwd=home)


def restart(prof: dict) -> None:
    scope = ["--user"] if prof["service_scope"] == "user" else []
    run(["systemctl", *scope, "restart", prof["service"]], timeout=120, check=True)


def healthy(prof: dict) -> tuple[bool, str]:
    """Service must be active AND the agent must actually work. 'active' alone
    is not health — a gateway can be up with its memory bridge broken."""
    scope = ["--user"] if prof["service_scope"] == "user" else []
    for attempt in range(1, HEALTH_RETRIES + 1):
        time.sleep(HEALTH_BACKOFF)
        active = run(["systemctl", *scope, "is-active", prof["service"]], timeout=30)
        if active.stdout.strip() != "active":
            reason = f"service not active ({active.stdout.strip()})"
        else:
            probe = run([f"{prof['venv']}/bin/python", "-c", prof["probe"]],
                        timeout=HEALTH_TIMEOUT)
            if probe.returncode == 0:
                return True, probe.stdout.strip().splitlines()[-1]
            reason = f"probe failed: {probe.stdout.strip()[-300:]}"
        log(f"  health attempt {attempt}/{HEALTH_RETRIES}: {reason}")
    return False, reason


def rollback(prof: dict, snap: dict) -> bool:
    log("!! HEALTH CHECK FAILED — rolling back to pinned versions")
    pip_specs = [f"{p}=={v}" for p, v in snap["pip"].items()]
    npm_specs = [f"{p}@{v}" for p, v in snap["npm"].items()]
    try:
        install(prof, pip_specs, npm_specs)
        restart(prof)
    except RuntimeError as e:
        log(f"!! ROLLBACK INSTALL FAILED: {e}")
        return False
    ok, detail = healthy(prof)
    log(f"   rollback health: {'RESTORED' if ok else 'STILL BROKEN'} — {detail}")
    return ok


# ── main ────────────────────────────────────────────────────────────────────

def diff(before: dict, after: dict) -> list[str]:
    changes = []
    for eco in ("pip", "npm"):
        for pkg, old in before.get(eco, {}).items():
            new = after.get(eco, {}).get(pkg, old)
            if new != old:
                changes.append(f"{pkg}: {old} -> {new}")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, choices=sorted(PROFILES))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="report versions, change nothing")
    g.add_argument("--apply", action="store_true", help="upgrade with health gate + rollback")
    args = ap.parse_args()

    prof = _expand(PROFILES[args.host])
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # `systemctl --user` needs a bus address. A systemd user timer exports it;
    # cron and a plain SSH shell do not, so derive it rather than fail late
    # (after the upgrade, when we can no longer restart to verify health).
    if prof["service_scope"] == "user":
        os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # npm/uv live outside the default PATH under a non-login shell.
    os.environ["PATH"] = f"{prof['npm_prefix']}/bin:{os.path.dirname(prof['uv'])}:" + os.environ.get("PATH", "")

    before = snapshot(prof)

    if args.check:
        latest = latest_available(prof)
        print(f"\n{args.host} — {prof['description']}\n")
        print(f"  {'package':<24} {'installed':<12} {'latest':<12} {'':<3}")
        stale = 0
        for eco in ("pip", "npm"):
            for pkg, cur in before[eco].items():
                new = latest[eco].get(pkg, "?")
                mark = "" if new in (cur, "?") else "UPDATE"
                stale += 1 if mark else 0
                print(f"  {pkg:<24} {cur:<12} {new:<12} {mark}")
        print(f"\n  {stale} package(s) out of date\n")
        return 0

    # --apply
    log(f"=== update run: {args.host} ===")
    log(f"  before: {json.dumps(before)}")

    state = STATE_DIR / f"agent-update-{args.host}.json"
    state.write_text(json.dumps(
        {"at": datetime.now(timezone.utc).isoformat(), "snapshot": before}, indent=2) + "\n")

    try:
        install(prof, prof["pip_packages"], prof["npm_packages"])
    except RuntimeError as e:
        log(f"!! UPGRADE FAILED (nothing restarted, service untouched): {e}")
        return 1

    after = snapshot(prof)
    changes = diff(before, after)

    # Restart ONLY when something actually changed. A no-op run must not bounce
    # the gateway — systemd's stop timeout SIGKILLs the agent mid-turn, so an
    # unconditional weekly restart would silently truncate whatever Tris was
    # doing, for no benefit.
    if not changes:
        log("  already current — no changes, service left running")
        log("=== done ===")
        return 0

    log("  upgraded: " + "; ".join(changes))
    try:
        restart(prof)
    except RuntimeError as e:
        log(f"!! RESTART FAILED: {e}")
        rollback(prof, before)
        return 1

    ok, detail = healthy(prof)
    if not ok:
        restored = rollback(prof, before)
        log(f"=== FAILED — {'rolled back' if restored else 'ROLLBACK ALSO FAILED, NEEDS A HUMAN'} ===")
        return 1

    log(f"  health: OK — {detail}")
    log("=== done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
