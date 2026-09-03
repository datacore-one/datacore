#!/usr/bin/env python3
"""Keep NotebookLM auth alive on the servers that generate podcasts.

WHY THIS EXISTS. `nlm` authenticates by extracting cookies from a locally
signed-in browser. Winston and nightshift have no browser, so they can never
refresh their own credential — they can only be handed one. Nothing handed
them one between 2026-07-14 and 2026-08-16, so both quietly aged out and the
research podcast stopped being produced. Nobody noticed for six days because
the pipeline treated "no podcast" as a best-effort skip.

So: the Mac is the only place a refresh can happen, and the servers are
downstream copies. That is not a workaround, it is the actual shape of the
dependency, and this script makes it explicit and scheduled instead of
remembered.

    nlm_auth_sync.py check          # is auth alive here and on each host?
    nlm_auth_sync.py sync           # refresh locally, verify, push, verify again
    nlm_auth_sync.py sync --hosts winston

THREE-STATE REPORTING. Every host reports ok / FAIL / n-a. "Could not tell"
is never rendered as "fine" — an unreachable host is `n-a`, not a pass. The
whole failure being repaired here is a broken thing that looked healthy.

VERIFY, DON'T ASSUME. A copied file proves nothing: the credential may be
expired at the moment it is copied. Both the local refresh and every remote
push are confirmed with a live `notebook list` call, because the only
evidence that auth works is auth working.

NEVER PRINTS THE CREDENTIAL. ~/.nlm/env holds cookies and an auth token.
Identity is shown as a truncated sha256, per the credential-audit convention.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ENV = Path.home() / ".nlm" / "env"
DEFAULT_HOSTS = ("winston", "nightshift")
# `nlm auth` drives a headless browser and `notebook list` is a network round
# trip; both are slow enough that a short timeout reads as a failure.
AUTH_TIMEOUT = 240
LIST_TIMEOUT = 120


def fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "(absent)"


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 127, str(exc)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ssh runs a non-login shell. The servers install nlm to /usr/local/bin (on the
# default PATH); the Mac installs to ~/go/bin (not on a minimal PATH). Resolve
# at call time on the remote rather than hardcoding either location.
REMOTE_NLM = "$(command -v nlm || echo $HOME/go/bin/nlm)"


def _nlm_bin() -> str | None:
    """Absolute path to the `nlm` binary, or None if it is genuinely absent.

    `shutil.which("nlm")` alone is wrong here. nlm is a Go binary installed to
    ~/go/bin, which is on an interactive shell's PATH but NOT on the minimal
    PATH launchd/cron hands a scheduled job. So this script ran by hand said
    "installed" and the same script run on schedule said "nlm is not installed
    on this machine" -- and, believing that, never refreshed the credential.
    The podcast pipeline then produced nothing from 2026-08-30 onward while the
    binary sat in ~/go/bin the whole time (verified 2026-09-02).

    Order: $NLM override, then PATH, then the known install location -- the
    same precedence nlm_audio_retry.sh already uses.
    """
    override = os.environ.get("NLM")
    if override and os.access(override, os.X_OK):
        return override
    found = shutil.which("nlm")
    if found:
        return found
    fallback = os.path.expanduser("~/go/bin/nlm")
    return fallback if os.access(fallback, os.X_OK) else None


def _list_ok(output: str) -> bool:
    """A real listing has the header row. An auth failure prints prose.

    Checking the exit code alone is not enough: nlm exits 0 while printing
    'launching browser to login...' when the credential is dead, which is
    precisely how a broken host passed for a month.
    """
    return "ID\tTITLE" in output or "no notebooks" in output.lower()


def check_local() -> bool:
    code, out = _run([_nlm_bin() or "nlm", "notebook", "list"], LIST_TIMEOUT)
    ok = code == 0 and _list_ok(out)
    print(f"  mac          {'ok' if ok else 'FAIL'}   auth {fingerprint(ENV)}")
    if not ok:
        print(f"               {out.strip().splitlines()[-1][:100] if out.strip() else 'no output'}")
    return ok


def check_host(host: str) -> str:
    """Returns 'ok' | 'FAIL' | 'n-a' (unreachable — NOT a pass)."""
    code, _ = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                    host, "true"], 20)
    if code != 0:
        print(f"  {host:12} n-a    unreachable — status unknown, NOT assumed healthy")
        return "n-a"
    code, out = _run(["ssh", host, REMOTE_NLM + " notebook list"], LIST_TIMEOUT)
    ok = code == 0 and _list_ok(out)
    print(f"  {host:12} {'ok' if ok else 'FAIL'}")
    return "ok" if ok else "FAIL"


def refresh_local() -> bool:
    """Re-extract cookies from the local browser, then prove it worked."""
    print("  refreshing from browser cookies…")
    before = fingerprint(ENV)
    code, out = _run([_nlm_bin() or "nlm", "auth"], AUTH_TIMEOUT)
    if code != 0:
        print(f"  FAILED: nlm auth exited {code}")
        # The usual cause is no browser profile holding notebook.google.com
        # cookies — i.e. sign in to NotebookLM in Chrome or Brave first.
        for line in out.strip().splitlines()[-4:]:
            print(f"    {line[:110]}")
        return False
    if not check_local():
        print("  FAILED: nlm auth reported success but the credential does not work")
        return False
    print(f"  refreshed    {before} -> {fingerprint(ENV)}")
    return True


def push(host: str) -> str:
    code, out = _run(["scp", "-q", str(ENV), f"{host}:/tmp/.nlm-env-new"], 60)
    if code != 0:
        print(f"  {host:12} FAIL   copy failed: {out.strip()[:90]}")
        return "FAIL"
    # Back up, install, then verify with a live call before calling it done.
    cmd = ("mkdir -p ~/.nlm && cp ~/.nlm/env ~/.nlm/env.bak 2>/dev/null; "
           "mv /tmp/.nlm-env-new ~/.nlm/env && chmod 600 ~/.nlm/env")
    code, out = _run(["ssh", host, cmd], 60)
    if code != 0:
        print(f"  {host:12} FAIL   install failed: {out.strip()[:90]}")
        return "FAIL"
    code, out = _run(["ssh", host, REMOTE_NLM + " notebook list"], LIST_TIMEOUT)
    ok = code == 0 and _list_ok(out)
    print(f"  {host:12} {'ok' if ok else 'FAIL'}   {'verified live' if ok else out.strip()[:80]}")
    return "ok" if ok else "FAIL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("op", choices=["check", "sync"])
    ap.add_argument("--hosts", default=",".join(DEFAULT_HOSTS))
    a = ap.parse_args()
    hosts = [h.strip() for h in a.hosts.split(",") if h.strip()]

    if not _nlm_bin():
        print("nlm is not installed on this machine — cannot refresh or verify")
        return 2

    if a.op == "check":
        print("nlm auth status\n")
        results = [check_local()] + [check_host(h) == "ok" for h in hosts]
        print()
        return 0 if all(results) else 1

    print("nlm auth sync\n")
    if not ENV.exists():
        print(f"  no credential at {ENV} — run `nlm auth` once by hand first")
        return 2
    if not refresh_local():
        # Without a good local credential there is nothing worth pushing;
        # copying a dead one would overwrite whatever still works remotely.
        print("\n  ABORTED: not pushing an unverified credential to any host")
        return 1
    print()
    states = [push(h) for h in hosts]
    print()
    bad = [h for h, s in zip(hosts, states) if s != "ok"]
    if bad:
        print(f"  {len(bad)} host(s) NOT healthy: {', '.join(bad)}")
        return 1
    print(f"  all {len(hosts)} host(s) verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
