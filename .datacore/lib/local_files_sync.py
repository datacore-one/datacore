#!/usr/bin/env python3
"""Bring a host's install-local files up to what this install's shipped code expects.

The fleet-names work (INS-3, 2026-09-27) moved this install's own values out of
tracked files into gitignored local ones. A host that pulls the new code without
them does not fail loudly: jobs go unchecked, seals stop counting, roles fall back.
So the local files reach each host BEFORE it pulls.

Two kinds of file:
  * WHOLE  -- new local-only files; copied as they are when the host has none, never
              overwritten when it has one (its own copy wins; the difference is shown).
  * MERGE  -- files every host already has with its own values (machine roster,
              principals, install.yaml). Only top-level keys the host LACKS are added,
              and for the `roles:` mappings only sub-keys it lacks. Nothing it has is
              changed.

Dry run by default. --apply backs up every file it touches to
~/.datacore/state/local-files-backup-<stamp>/ on the host first.

    local_files_sync.py HOST [--apply] [--runner]

--runner also places manifest.local.yaml in the host's ~/.datacore/v2-runner copy,
which the verifier crons on runner hosts read.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REMOTE_ROOT = "~/Data"

WHOLE = [
    ".datacore/config/approvals_policy.local.yaml",
    ".datacore/config/task-themes.local.yaml",
    ".datacore/lib/jobs/manifest.local.yaml",
    ".datacore/config/authorship-reviewed.local.yaml",
    ".datacore/config/ledger-invariants-baseline.local.yaml",
    ".datacore/config/gh-reconcile.local.yaml",
    ".datacore/config/standup.local.yaml",
    ".datacore/config/publish-scrub.local.yaml",
]
MERGE = [
    ".datacore/registry/infrastructure.yaml",
    ".datacore/registry/principals.yaml",
    "install.yaml",
]
NESTED = {"roles"}   # mappings merged key by key rather than as a whole


def ssh(host: str, cmd: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, cmd],
                          input=stdin, capture_output=True, text=True, timeout=120)


def remote_read(host: str, rel: str) -> str | None:
    p = ssh(host, f"cat {REMOTE_ROOT}/{rel} 2>/dev/null || echo __MISSING__")
    return None if p.stdout.strip() == "__MISSING__" else p.stdout


def merged(local: dict, remote: dict) -> tuple[dict, list[str]]:
    out, added = dict(remote), []
    for k, v in local.items():
        if k not in out:
            out[k] = v
            added.append(k)
        elif k in NESTED and isinstance(v, dict) and isinstance(out[k], dict):
            for sk, sv in v.items():
                if sk not in out[k]:
                    out[k] = {**out[k], sk: sv}
                    added.append(f"{k}.{sk}")
    return out, added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--runner", action="store_true")
    a = ap.parse_args()

    plan: list[tuple[str, str, str]] = []   # (remote path, content, note)
    for rel in WHOLE:
        src = ROOT / rel
        if not src.is_file():
            print(f"  skip  {rel}: not on this machine")
            continue
        have = remote_read(a.host, rel)
        if have is None:
            plan.append((f"{REMOTE_ROOT}/{rel}", src.read_text(), "copy (host has none)"))
        elif have == src.read_text():
            print(f"  same  {rel}")
        else:
            print(f"  keep  {rel}: host has its own copy, differs from this machine's (not overwritten)")
    if a.runner:
        rel = ".datacore/lib/jobs/manifest.local.yaml"
        rp = f"~/.datacore/v2-runner/{rel}"
        have = ssh(a.host, f"cat {rp} 2>/dev/null || echo __MISSING__").stdout
        if have.strip() == "__MISSING__":
            plan.append((rp, (ROOT / rel).read_text(), "copy to runner (host has none)"))
        else:
            print(f"  keep  runner {rel}: host has its own copy")
    for rel in MERGE:
        src = ROOT / rel
        if not src.is_file():
            continue
        local = yaml.safe_load(src.read_text()) or {}
        have = remote_read(a.host, rel)
        if have is None:
            print(f"  WARN  {rel}: host has none -- create it by hand from this machine's copy; not copied blindly")
            continue
        remote = yaml.safe_load(have) or {}
        out, added = merged(local, remote)
        if added and all("." not in k for k in added):
            # only whole top-level keys are new: append them, keeping the host's
            # own text and comments exactly as they are
            extra = yaml.safe_dump({k: out[k] for k in added}, sort_keys=False, allow_unicode=True)
            text = have if have.endswith("\n") else have + "\n"
            plan.append((f"{REMOTE_ROOT}/{rel}", text + "\n# added by local_files_sync.py (INS-3)\n" + extra,
                         "append " + ", ".join(added)))
        elif added:
            plan.append((f"{REMOTE_ROOT}/{rel}", yaml.safe_dump(out, sort_keys=False, allow_unicode=True),
                         "rewrite (comments not kept) to add " + ", ".join(added)))
        else:
            print(f"  same  {rel}: nothing missing")

    for path, _, note in plan:
        print(f"  {'WRITE' if a.apply else 'would'} {path}: {note}")
    if not a.apply or not plan:
        if not a.apply:
            print("dry run: nothing written (--apply to write)")
        return 0

    payload = json.dumps([[p, c] for p, c, _ in plan])
    script = (
        "import json,os,shutil,sys,time\n"
        "items=json.load(sys.stdin)\n"
        "bk=os.path.expanduser('~/.datacore/state/local-files-backup-'+time.strftime('%Y%m%d-%H%M%S'))\n"
        "for p,c in items:\n"
        "  p=os.path.expanduser(p)\n"
        "  if os.path.exists(p):\n"
        "    d=os.path.join(bk,p.lstrip('/'));os.makedirs(os.path.dirname(d),exist_ok=True);shutil.copy2(p,d)\n"
        "  os.makedirs(os.path.dirname(p),exist_ok=True)\n"
        "  t=p+'.tmp';open(t,'w').write(c);os.chmod(t,0o600);os.replace(t,p)\n"
        "print('backup:',bk)\n"
    )
    p = ssh(a.host, f"python3 -c {json.dumps(script)}", stdin=payload)
    print(p.stdout.strip() or p.stderr.strip())
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
