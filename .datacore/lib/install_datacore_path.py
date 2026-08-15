#!/usr/bin/env python3
"""Make `import datacore` work for the interpreter that runs this (DIP-0047).

Writes a `.pth` into the running interpreter's site-packages pointing at the
`.datacore/lib` that holds the core. That is the whole mechanism: a `.pth` is
read by every Python start-up with no environment, no wrapper and no shell
profile — which matters because most Datacore code runs from cron and systemd,
where PYTHONPATH set in a login shell does not exist.

RUN IT ONCE PER INTERPRETER, not once per machine. A box with a system python,
a homebrew python and a venv has three answers to "can you import datacore", and
the one that matters is whichever one the job actually uses. `--check` reports
without writing so a machine can be audited before it is changed.

    install_datacore_path.py [--check] [--lib DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PTH_NAME = "datacore-core.pth"


def _default_lib() -> Path:
    """The lib directory containing this file — never a guess.

    This script lives in the core it installs, so its own location IS the
    answer. Deriving it any other way would reintroduce the guessing that
    DIP-0047 exists to remove.
    """
    return Path(__file__).resolve().parent


def _importable_cleanly() -> bool:
    """Can a FRESH interpreter import datacore, with no help from us?

    Testing with a plain `import datacore` in this process is rigged: Python
    puts a script's own directory on sys.path[0], and this script lives inside
    the very lib it installs. So the import always succeeded, the installer
    reported "already importable" on every machine, and wrote nothing — while a
    job running from any other directory still could not import it. Verified by
    running the check from /tmp, where it failed immediately.

    A subprocess, started elsewhere with an empty PYTHONPATH, is the only
    honest form of the question, because it is the situation the cron jobs are
    actually in.
    """
    import os
    import subprocess
    import tempfile
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([sys.executable, "-c", "import datacore.ledger"],
                           cwd=td, env=env, capture_output=True, timeout=60)
    return r.returncode == 0


def _site_dir() -> Path | None:
    """The USER site-packages, created if absent — never a system directory.

    The first version accepted any existing site dir, preferring the user one
    only if it already existed. On a fresh box the user site does not exist, so
    it fell straight through to `/usr/local/lib/python3.12/dist-packages` and
    failed with PermissionError on winston and plur-claw.

    Falling back to a system path is wrong even where it would succeed: it makes
    an unprivileged, per-user change into a machine-wide one, and installs the
    core for interpreters no Datacore job uses. Absent is a reason to CREATE the
    user site, not to escalate out of it.
    """
    import site
    try:
        user = Path(site.getusersitepackages())
        user.mkdir(parents=True, exist_ok=True)
        return user
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report only; do not write")
    ap.add_argument("--lib", type=Path, default=_default_lib())
    a = ap.parse_args()

    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if _importable_cleanly():
        print(f"  ok   python{ver} ({sys.executable}) — datacore.ledger importable")
        return 0

    if not (a.lib / "datacore" / "ledger.py").is_file():
        print(f"  FAIL python{ver} — no datacore package under {a.lib}")
        return 2

    site_dir = _site_dir()
    if site_dir is None:
        print(f"  FAIL python{ver} — no writable site-packages found")
        return 2

    target = site_dir / PTH_NAME
    if a.check:
        print(f"  n-a  python{ver} — would write {target} -> {a.lib}")
        return 1

    site_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{a.lib}\n", encoding="utf-8")
    print(f"  ok   python{ver} — wrote {target} -> {a.lib}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
