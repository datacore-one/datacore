#!/usr/bin/env python3
"""A git credential helper that asks the credential broker, and stores nothing.

    git config --global credential.https://github.com/datacore-one.helper ""   # drop `store` here
    git config --global --add credential.https://github.com/datacore-one.helper \
        "!/usr/bin/python3 ~/Data/.datacore/lib/git_credential_broker.py github-pat-winston-git-read"

WHY. The box pulled with per-repo tokens in plaintext ~/.git-credentials, so a
repo nobody had minted a token for could never be pulled (11 modules went stale),
and every token sat in a file outside the broker. Now git asks the broker at the
moment it needs one: the value comes from the host's own store, is handed to git
on stdout, and is written nowhere.

It answers only `get`, and only for https://github.com/datacore-one/...; for
anything else it says nothing, so git falls through to whatever else is
configured. `store` and `erase` are ignored: the broker, not git, owns the value.
The token it names is READ-ONLY by design -- a push with it is refused by GitHub.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
OWNER = "datacore-one/"


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] != "get":
        return 0
    req = dict(line.split("=", 1) for line in sys.stdin.read().splitlines() if "=" in line)
    if req.get("protocol") != "https" or req.get("host") != "github.com":
        return 0
    if req.get("path") and not req["path"].startswith(OWNER):
        return 0
    r = subprocess.run([sys.executable, str(LIB / "creds.py"), "get", argv[1], "--consumer", "git"],
                       capture_output=True, text=True, timeout=60)
    token = r.stdout.strip()
    if r.returncode or not token:
        print(f"git_credential_broker: broker gave no value for {argv[1]}: {r.stderr.strip()[-200:]}",
              file=sys.stderr)
        return 0
    sys.stdout.write(f"username=x-access-token\npassword={token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
