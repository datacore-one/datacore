#!/usr/bin/env python3
"""Keys defined in BOTH the fleet `.env` and this host's `local.env` -- read-only.

Decisions C4/C5 (2026-09-23). `load_env_files()` and `creds get` both let the
host's `local.env` beat the fleet `.env`; before that fix the fleet value won
for most callers. On a host where a key is in both files with DIFFERENT
values, what a process saw depends on which code it ran. This report says
where that is the case, so the rollout can be checked host by host.

It prints key names and a truncated sha256 of each value (12 hex chars, the
same fingerprint as `credential_access.fingerprint`), never a value. The
fingerprints are comparable across hosts: run it on each host and diff.

Standalone and stdlib-only on purpose, so it runs on a host that does not yet
have the new `creds.py` by piping it over ssh:

    ssh <host> 'python3 - --root ~/Data' < .datacore/lib/env_overlap_report.py

It opens two files for reading and writes nothing. A duplicate key inside one
file is parsed last-wins (decision C2) and counted in the footer.
"""

import argparse
import hashlib
import os
import socket
import sys
from pathlib import Path


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else "(empty)"


def parse(path: Path) -> tuple[dict, list]:
    """(last-wins values, keys assigned more than once). Never raises on a
    missing or unreadable file: it reports it instead."""
    vals: dict[str, str] = {}
    seen: dict[str, int] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        vals[k] = v
        seen[k] = seen.get(k, 0) + 1
    return vals, sorted(k for k, n in seen.items() if n > 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=os.environ.get(
        "DATACORE_ROOT", str(Path.home() / "Data")), help="Datacore root")
    args = ap.parse_args(argv)
    env = Path(args.root).expanduser() / ".datacore" / "env"
    fleet_p, host_p = env / ".env", env / "local.env"

    print(f"host {socket.gethostname()}  root {Path(args.root).expanduser()}")
    files = {}
    for label, p in ((".env", fleet_p), ("local.env", host_p)):
        if not p.is_file():
            print(f"  {label}: absent ({p})")
            files[label] = ({}, [])
            continue
        try:
            files[label] = parse(p)
        except OSError as exc:
            print(f"  {label}: unreadable ({exc.__class__.__name__})")
            files[label] = ({}, [])
    fleet, fleet_dups = files[".env"]
    host, host_dups = files["local.env"]

    both = sorted(set(fleet) & set(host))
    if not both:
        print("  no key is defined in both .env and local.env")
    else:
        w = max(len(k) for k in both)
        print(f"  {'KEY':{w}}  {'.env':12}  {'local.env':12}  verdict")
        for k in both:
            a, b = fingerprint(fleet[k]), fingerprint(host[k])
            verdict = "same" if a == b else "DIFFER (local.env wins now; old load_env_files() defaults exported .env)"
            print(f"  {k:{w}}  {a:12}  {b:12}  {verdict}")
    differ = sum(1 for k in both if fleet[k] != host[k])
    print(f"\n  in both: {len(both)}   differ: {differ}")
    for label, dups in ((".env", fleet_dups), ("local.env", host_dups)):
        if dups:
            print(f"  duplicated inside {label} (last wins): {', '.join(dups)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
