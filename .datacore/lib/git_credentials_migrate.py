#!/usr/bin/env python3
"""Move a host's plaintext ~/.git-credentials into the credential broker.

    git_credentials_migrate.py inventory          # tokens (fingerprints only), repos, live access
    git_credentials_migrate.py index-stanzas      # credential-index.yaml entries to add on the mac
    git_credentials_migrate.py apply              # values -> local.env, git -> broker helper, verify
    git_credentials_migrate.py retire             # after apply verified: delete the plaintext file

WHY. `credential.helper store` kept every scoped token in a plaintext file outside
the broker, so nothing could list, verify, audit or rotate them, and a repo nobody
minted a token for simply went stale. After migration each token lives once, in the
host's own instance-local store (~/Data/.datacore/env/local.env, never distributed),
indexed by the broker, and git asks for it per repository through
git_credential_broker.py at the moment it needs it.

Values never leave this host and are never printed: tokens are named by the first
8 hex of their sha256. A token that no longer reaches any of its repositories is
reported and not migrated. `retire` refuses unless every migrated repository still
authenticates through the broker.
"""
from __future__ import annotations

import collections
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
STORE = HOME / ".git-credentials"
LOCAL_ENV = HOME / "Data" / ".datacore" / "env" / "local.env"
HELPER = Path(__file__).resolve().parent / "git_credential_broker.py"
HOST = os.environ.get("CREDS_INSTANCE") or "winston"
LINE = re.compile(r"https://([^:]+):([^@]+)@github\.com/(.+?)(?:\.git)?$")


def entries() -> list[tuple[str, str]]:
    """(owner/repo, token) for every parseable line."""
    out = []
    for line in STORE.read_text().splitlines():
        m = LINE.match(line.strip())
        if m:
            out.append((m.group(3), m.group(2)))
    return out


def fp(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def _git_with(token: str, *args: str) -> bool:
    helper = f"!f() {{ echo username=x-access-token; echo password={token}; }}; f"
    r = subprocess.run(["git", "-c", "credential.helper=", "-c", f"credential.helper={helper}", *args],
                       capture_output=True, text=True, timeout=90, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    return r.returncode == 0


def reads(token: str, repo: str) -> bool:
    return _git_with(token, "ls-remote", f"https://github.com/{repo}.git", "HEAD")


def groups() -> dict[str, dict]:
    g: dict[str, dict] = {}
    for repo, tok in entries():
        d = g.setdefault(fp(tok), {"token": tok, "repos": []})
        if repo not in d["repos"]:
            d["repos"].append(repo)
    return g


def cred_id(f: str) -> str:
    return f"github-pat-{HOST}-git-{f}"


def var(f: str) -> str:
    return f"{HOST.upper()}_GIT_TOKEN_{f.upper()}"


def inventory() -> int:
    for f, d in groups().items():
        kind = "fine-grained" if d["token"].startswith("github_pat_") else "classic" if d["token"].startswith("ghp_") else "other"
        live = {r: reads(d["token"], r) for r in d["repos"]}
        ok = [r for r, v in live.items() if v]
        bad = [r for r, v in live.items() if not v]
        print(f"token {f} ({kind}): reads {len(ok)}/{len(live)}"
              + (f"  ok: {', '.join(ok)}" if ok else "") + (f"  NO ACCESS: {', '.join(bad)}" if bad else ""))
    return 0


def live_groups() -> dict[str, dict]:
    out = {}
    for f, d in groups().items():
        ok = [r for r in d["repos"] if reads(d["token"], r)]
        if ok:
            out[f] = {**d, "repos": ok}
    return out


def index_stanzas() -> int:
    for f, d in live_groups().items():
        print(f"- id: {cred_id(f)}\n  name: GitHub token for {HOST}'s git ({', '.join(d['repos'])})\n"
              f"  type: api_key\n  tier: high\n  scope: instance-local\n  category: development\n"
              f"  provider: github\n  var_name: {var(f)}\n  hosts:\n  - {HOST}\n"
              f"  description: Migrated from {HOST}'s plaintext ~/.git-credentials (git_credentials_migrate.py);\n"
              f"    used only through git_credential_broker.py for {', '.join(d['repos'])}. Value lives in\n"
              f"    ~/Data/.datacore/env/local.env on {HOST}.\n  lifecycle: static")
    return 0


def _config(*args: str) -> None:
    subprocess.run(["git", "config", "--global", *args], check=True)


def apply() -> int:
    present = {l.split("=", 1)[0] for l in LOCAL_ENV.read_text().splitlines() if "=" in l} if LOCAL_ENV.exists() else set()
    lg = live_groups()
    with LOCAL_ENV.open("a") as fh:
        for f, d in lg.items():
            if var(f) not in present:
                fh.write(f"{var(f)}={d['token']}\n")
    LOCAL_ENV.chmod(0o600)
    failed = []
    for f, d in lg.items():
        for repo in d["repos"]:
            for url in (f"https://github.com/{repo}.git", f"https://github.com/{repo}"):
                key = f"credential.{url}.helper"
                subprocess.run(["git", "config", "--global", "--unset-all", key], capture_output=True)
                _config(key, "")
                _config("--add", key, f"!/usr/bin/python3 {HELPER} {cred_id(f)}")
            r = subprocess.run(["git", "ls-remote", f"https://github.com/{repo}.git", "HEAD"], capture_output=True,
                               text=True, timeout=90, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            print(f"{repo:40} via broker ({cred_id(f)}): {'OK' if r.returncode == 0 else 'FAILED'}")
            if r.returncode:
                failed.append(repo)
    print(f"{sum(len(d['repos']) for d in lg.values())} repo(s), {len(lg)} token(s) migrated; {len(failed)} failed")
    return 1 if failed else 0


def retire() -> int:
    configured = []
    for line in subprocess.run(["git", "config", "--global", "--get-regexp", r"^credential\.https://github\.com/.*\.helper$"],
                               capture_output=True, text=True).stdout.splitlines():
        key, _, val = line.partition(" ")
        if "git_credential_broker.py" in val and key.endswith(".git.helper"):
            configured.append(key[len("credential.https://github.com/"):-len(".git.helper")])
    missing = [repo for repo, _ in entries() if repo not in configured]
    if missing:
        print(f"refused: {len(missing)} stored entr(ies) not migrated: {', '.join(sorted(set(missing)))}")
        print("(dead tokens are listed by `inventory`; confirm they are dead, then remove those lines first)")
        return 2
    for repo in configured:
        r = subprocess.run(["git", "-c", "credential.helper=", "ls-remote", f"https://github.com/{repo}.git", "HEAD"],
                           capture_output=True, text=True, timeout=90, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode:
            print(f"refused: {repo} does not authenticate through the broker")
            return 2
    STORE.unlink()
    subprocess.run(["git", "config", "--global", "--unset-all", "credential.helper"], capture_output=True)
    print(f"retired {STORE} and the global `store` helper; {len(configured)} repo(s) authenticate through the broker")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inventory"
    sys.exit({"inventory": inventory, "index-stanzas": index_stanzas, "apply": apply, "retire": retire}[cmd]())
