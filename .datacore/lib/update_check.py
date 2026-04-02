#!/usr/bin/env python3
"""Check for updates to datacore-mcp and plur packages.

Compares locally installed versions against npm registry.
Returns a list of available updates (may be empty).

Cache: checks at most once per hour (UP_TO_DATE) or once per 12h (UPGRADE_AVAILABLE).
State stored in ~/.datacore/update-check/

Usage:
    from update_check import check_updates
    updates = check_updates()
    # returns list of {"name": ..., "local": ..., "remote": ..., "install_hint": ...}
"""
import json, os, subprocess, time
from pathlib import Path

STATE_DIR = Path.home() / ".datacore" / "update-check"
CACHE_TTL_UPTODATE = 3600       # 1 hour
CACHE_TTL_AVAILABLE = 43200     # 12 hours

PACKAGES = [
    {
        "name": "datacore-mcp",
        "npm_name": "@datacore-one/mcp",
        "local_version_cmd": ["node", "-e",
            "try{console.log(require('@datacore-one/mcp/package.json').version)}catch{console.log('unknown')}"],
        "install_hint": "cd ~/Data/2-datacore/2-projects/datacore-mcp && git pull && npm install",
    },
    {
        "name": "plur-mcp",
        "npm_name": "@plur-ai/mcp",
        "local_version_cmd": None,  # runs via npx @latest, check cli instead
        "install_hint": "npx cache uses @latest, auto-updates on next session",
    },
    {
        "name": "plur-cli",
        "npm_name": "@plur-ai/cli",
        "local_version_cmd": ["npx", "--yes", "@plur-ai/cli", "--version"],
        "install_hint": "npm cache clean --force && npx @plur-ai/cli --version",
    },
]


def _npm_registry_version(pkg_name: str) -> str | None:
    """Fetch latest version from npm registry (fast, single HTTP call)."""
    try:
        result = subprocess.run(
            ["npm", "view", pkg_name, "version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _local_version(cmd: list[str] | None) -> str | None:
    """Get locally installed version."""
    if cmd is None:
        return None
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            ver = result.stdout.strip()
            # Handle multi-line output (take last non-empty line)
            lines = [l.strip() for l in ver.split("\n") if l.strip()]
            if lines:
                return lines[-1]
    except Exception:
        pass
    return None


def _read_cache(pkg_name: str) -> dict | None:
    cache_file = STATE_DIR / f"{pkg_name}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        age = time.time() - data.get("ts", 0)
        ttl = CACHE_TTL_AVAILABLE if data.get("status") == "upgrade" else CACHE_TTL_UPTODATE
        if age < ttl:
            return data
    except Exception:
        pass
    return None


def _write_cache(pkg_name: str, status: str, local_ver: str, remote_ver: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = STATE_DIR / f"{pkg_name}.json"
    cache_file.write_text(json.dumps({
        "status": status,
        "local": local_ver,
        "remote": remote_ver,
        "ts": time.time(),
    }))


def check_updates() -> list[dict]:
    """Check all packages for updates. Returns list of available updates."""
    updates = []

    for pkg in PACKAGES:
        name = pkg["name"]

        # Check cache first
        cached = _read_cache(name)
        if cached:
            if cached["status"] == "upgrade":
                updates.append({
                    "name": name,
                    "local": cached["local"],
                    "remote": cached["remote"],
                    "install_hint": pkg["install_hint"],
                })
            continue

        # Slow path: check registry
        remote = _npm_registry_version(pkg["npm_name"])
        if not remote:
            continue

        local = _local_version(pkg["local_version_cmd"])

        # plur-mcp: since it runs via npx @latest, compare local npx cache
        # If we can't get local version, skip (it auto-updates)
        if local is None and pkg["local_version_cmd"] is None:
            _write_cache(name, "uptodate", remote, remote)
            continue

        if local is None:
            local = "unknown"

        if local != remote and local != "unknown":
            _write_cache(name, "upgrade", local, remote)
            updates.append({
                "name": name,
                "local": local,
                "remote": remote,
                "install_hint": pkg["install_hint"],
            })
        else:
            _write_cache(name, "uptodate", local, remote)

    return updates


if __name__ == "__main__":
    updates = check_updates()
    if updates:
        for u in updates:
            print(f"UPGRADE_AVAILABLE {u['name']} {u['local']} -> {u['remote']}  ({u['install_hint']})")
    else:
        print("All packages up to date.")
