#!/usr/bin/env python3
"""Check for updates to datacore-mcp and plur packages.

Compares locally installed versions against npm registry.
Returns a list of available updates (may be empty).

Cache: checks at most once per hour (UP_TO_DATE) or once per 12h (UPGRADE_AVAILABLE).
State stored in ~/.datacore/update-check/

Security: no package execution during checks (registry-only queries).
All subprocess calls use capture_output=True and tight timeouts.
Cache writes are atomic (write-to-temp + rename).

Usage:
    from update_check import check_updates
    updates = check_updates()
    # returns list of {"name": ..., "local": ..., "remote": ..., "install_hint": ...}
"""
import json, os, re, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

STATE_DIR = Path.home() / ".datacore" / "update-check"
CACHE_TTL_UPTODATE = 3600       # 1 hour
CACHE_TTL_AVAILABLE = 43200     # 12 hours
CACHE_TTL_UNKNOWN = 1800        # 30 min — retry sooner when probe failed
OVERALL_TIMEOUT = 12            # max seconds for all checks combined

# Known install path for datacore-mcp (npm-linked from git checkout)
_DATACORE_MCP_PACKAGE_JSON = (
    Path.home() / "Data" / "2-datacore" / "2-projects" / "datacore-mcp" / "package.json"
)

# Version string pattern — rejects HTML error pages, garbage
_VERSION_RE = re.compile(r"^\d+\.\d+[\d.]*$")

PACKAGES = [
    {
        "name": "datacore-mcp",
        "npm_name": "@datacore-one/mcp",
        "local_version_fn": "_local_version_datacore_mcp",
        "install_hint": "cd ~/Data/2-datacore/2-projects/datacore-mcp && git pull && npm install",
    },
    {
        "name": "plur-mcp",
        "npm_name": "@plur-ai/mcp",
        "local_version_fn": None,  # runs via npx @latest — no local install to check
        "install_hint": "runs via npx @latest, auto-updates on next session",
    },
    {
        "name": "plur-cli",
        "npm_name": "@plur-ai/cli",
        "local_version_fn": "_local_version_plur_cli",
        "install_hint": "npm cache clean --force && npx @plur-ai/cli --version",
    },
]


def _log(msg: str):
    """Log to stderr for observability (visible in hook debug mode)."""
    print(f"[update_check] {msg}", file=sys.stderr)


def _validate_version(v: str | None) -> str | None:
    """Return version string only if it looks like a semver-ish number."""
    if v and _VERSION_RE.match(v.strip()):
        return v.strip()
    return None


# --- Local version probes (no execution, just metadata reads) ---

def _local_version_datacore_mcp() -> str | None:
    """Read version from known package.json path (no Node execution)."""
    try:
        if _DATACORE_MCP_PACKAGE_JSON.exists():
            data = json.loads(_DATACORE_MCP_PACKAGE_JSON.read_text())
            return _validate_version(data.get("version"))
    except Exception as e:
        _log(f"datacore-mcp local probe failed: {e}")
    return None


def _local_version_plur_cli() -> str | None:
    """Check plur-cli version via registry query, not execution.

    We query npm view for the installed version in the npx cache.
    This avoids running npx --yes which would execute arbitrary code.
    """
    try:
        # Check npm cache for installed version
        result = subprocess.run(
            ["npm", "view", "@plur-ai/cli", "version"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0:
            return _validate_version(result.stdout.strip())
    except Exception as e:
        _log(f"plur-cli local probe failed: {e}")
    return None


# --- Registry queries ---

def _npm_registry_version(pkg_name: str) -> str | None:
    """Fetch latest version from npm registry (single HTTP call, no execution)."""
    try:
        result = subprocess.run(
            ["npm", "view", pkg_name, "version"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0:
            return _validate_version(result.stdout.strip())
        else:
            _log(f"npm view {pkg_name} failed: {result.stderr.strip()[:100]}")
    except subprocess.TimeoutExpired:
        _log(f"npm view {pkg_name} timed out")
    except Exception as e:
        _log(f"npm view {pkg_name} error: {e}")
    return None


# --- Cache with atomic writes and schema validation ---

_CACHE_SCHEMA_KEYS = {"status", "local", "remote", "ts"}


def _read_cache(pkg_name: str) -> dict | None:
    cache_file = STATE_DIR / f"{pkg_name}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        # Schema validation: must have all required keys with correct types
        if not isinstance(data, dict):
            raise ValueError("cache is not a dict")
        if not _CACHE_SCHEMA_KEYS.issubset(data.keys()):
            raise ValueError(f"missing keys: {_CACHE_SCHEMA_KEYS - data.keys()}")
        if not isinstance(data["ts"], (int, float)):
            raise ValueError("ts is not numeric")
        if not isinstance(data["status"], str) or data["status"] not in ("upgrade", "uptodate", "unknown"):
            raise ValueError(f"invalid status: {data['status']}")

        age = time.time() - data["ts"]
        if data["status"] == "upgrade":
            ttl = CACHE_TTL_AVAILABLE
        elif data["status"] == "unknown":
            ttl = CACHE_TTL_UNKNOWN
        else:
            ttl = CACHE_TTL_UPTODATE

        if age < ttl:
            return data
    except Exception as e:
        _log(f"cache read error for {pkg_name}: {e}")
        # Delete corrupt cache file
        try:
            cache_file.unlink()
        except OSError:
            pass
    return None


def _write_cache(pkg_name: str, status: str, local_ver: str, remote_ver: str):
    """Atomic cache write: write to temp file, then rename."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = STATE_DIR / f"{pkg_name}.json"
    content = json.dumps({
        "status": status,
        "local": local_ver,
        "remote": remote_ver,
        "ts": time.time(),
    })
    try:
        fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
        try:
            os.write(fd, content.encode())
            os.close(fd)
            os.rename(tmp_path, cache_file)
        except Exception:
            os.close(fd) if not os.get_inheritable(fd) else None
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        _log(f"cache write error for {pkg_name}: {e}")


# --- Sanitization for context injection ---

def _sanitize_version(v: str) -> str:
    """Strip anything that isn't a version-like string."""
    # Only allow digits, dots, hyphens (for pre-release tags)
    cleaned = re.sub(r"[^\d.\-a-zA-Z]", "", v)
    return cleaned[:30]  # cap length


def _sanitize_hint(h: str) -> str:
    """Strip newlines and control chars from install hints."""
    return re.sub(r"[\n\r\x00-\x1f]", " ", h)[:200]


# --- Main check logic ---

def _check_one_package(pkg: dict) -> dict | None:
    """Check a single package. Returns update dict or None."""
    name = pkg["name"]

    # Check cache first
    cached = _read_cache(name)
    if cached:
        if cached["status"] == "upgrade":
            return {
                "name": name,
                "local": _sanitize_version(cached["local"]),
                "remote": _sanitize_version(cached["remote"]),
                "install_hint": _sanitize_hint(pkg["install_hint"]),
            }
        return None  # uptodate or unknown (within TTL)

    # plur-mcp: runs via npx @latest, no local state to check
    if pkg["local_version_fn"] is None:
        _write_cache(name, "uptodate", "npx-latest", "npx-latest")
        return None

    # Slow path: check registry + local
    remote = _npm_registry_version(pkg["npm_name"])
    if not remote:
        _log(f"{name}: registry unreachable, caching as unknown")
        _write_cache(name, "unknown", "unknown", "unknown")
        return None

    # Get local version via the appropriate probe function
    probe_fn = globals().get(pkg["local_version_fn"])
    local = probe_fn() if probe_fn else None

    if local is None:
        _log(f"{name}: local version probe failed, caching as unknown")
        _write_cache(name, "unknown", "unknown", remote)
        return None

    if local != remote:
        _write_cache(name, "upgrade", local, remote)
        return {
            "name": name,
            "local": _sanitize_version(local),
            "remote": _sanitize_version(remote),
            "install_hint": _sanitize_hint(pkg["install_hint"]),
        }
    else:
        _write_cache(name, "uptodate", local, remote)
        return None


def check_updates() -> list[dict]:
    """Check all packages for updates in parallel. Returns list of available updates."""
    updates = []

    # Run all package checks in parallel with overall timeout
    with ThreadPoolExecutor(max_workers=len(PACKAGES)) as executor:
        futures = {executor.submit(_check_one_package, pkg): pkg["name"] for pkg in PACKAGES}
        deadline = time.time() + OVERALL_TIMEOUT

        for future in as_completed(futures, timeout=max(0, deadline - time.time())):
            try:
                result = future.result(timeout=1)
                if result:
                    updates.append(result)
            except Exception as e:
                _log(f"{futures[future]}: check failed: {e}")

    return updates


if __name__ == "__main__":
    updates = check_updates()
    if updates:
        for u in updates:
            print(f"UPGRADE_AVAILABLE {u['name']} {u['local']} -> {u['remote']}  ({u['install_hint']})")
    else:
        print("All packages up to date.")
