#!/usr/bin/env python3
"""Remove an installed module: its code and its schedules go, the owner's data stays.

Owner decision 2026-09-27 (MOD-3): "remove it, but don't delete user data".

    module_remove.py <name> [--dry-run] [--force]

Root is $DATACORE_ROOT, else the installation this file lives in.

What goes:
  * the module folder .datacore/modules/<name>/ (code, commands, agents, tools);
  * every job in .datacore/lib/jobs/manifest.yaml the module owns: the ones its
    module.yaml declares under `schedules:` and any whose cmd runs the module's code.
    The manifest is edited in place as text, so every other entry and comment is
    kept byte for byte.

What stays:
  * the module's data/, state/ and settings.local.yaml (the components
    module_data_migrate.py treats as user state) are copied, verified by digest,
    to .datacore/state/removed-modules/<name>-<UTC stamp>/ before anything is
    deleted, and the command prints where;
  * nothing in a space (journals, org files, knowledge) is read or written.

What it never does: touch a crontab or a systemd unit. It prints, per removed job,
the machine it ran on, so the installed entry can be retired there (for cron:
cron_install.py --retire <script> --state <dir>).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_data_migrate import COMPONENTS  # noqa: E402  ('data', 'state', 'settings.local.yaml')

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT") or Path(__file__).resolve().parents[2])


def _digests(base: Path) -> dict[str, str]:
    if base.is_file():
        return {"": hashlib.sha256(base.read_bytes()).hexdigest()}
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*")) if p.is_file() and not p.is_symlink()}


def owned_jobs(module: str, mod_dir: Path, jobs: list) -> list[dict]:
    """Jobs the module declares under schedules:, plus any whose cmd runs its code."""
    try:
        m = yaml.safe_load((mod_dir / "module.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        m = {}
    provides = m.get("provides") or {}
    declared = m.get("schedules") or provides.get("schedules") or provides.get("jobs") or []
    names = {d.get("name") if isinstance(d, dict) else d for d in declared}
    runs = re.compile(r"\.datacore/modules/" + re.escape(module) + r"/")
    return [j for j in jobs if isinstance(j, dict)
            and (j.get("name") in names or runs.search(str(j.get("cmd", ""))))]


def strip_jobs(text: str, names: set[str]) -> str:
    """Drop the list items named `names` from the top-level `jobs:` list, as text."""
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, l in enumerate(lines) if re.match(r"jobs:\s*(#.*)?$", l))
    except StopIteration:
        return text
    item = re.compile(r"^(\s*)- ")
    indent = None
    for l in lines[start + 1:]:
        mm = item.match(l)
        if mm:
            indent = mm.group(1)
            break
        if l.strip() and not l.startswith((" ", "\t", "#", "-")):
            return text                       # jobs: is empty / not a block list
    if indent is None:
        return text
    out, i, n = lines[:start + 1], start + 1, len(lines)
    while i < n:
        l = lines[i]
        if not l.startswith(indent + "- "):
            if l.strip() and len(l) - len(l.lstrip()) < len(indent) + (0 if indent else 1) \
                    and not l.lstrip().startswith(("#", "-")):
                out.extend(lines[i:])         # the list ended: next top-level key
                break
            out.append(l)
            i += 1
            continue
        j = i + 1
        while j < n:
            nxt = lines[j]
            if nxt.startswith(indent + "- ") or (nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(indent)):
                break
            j += 1
        seg = lines[i:j]
        try:
            body = yaml.safe_load("".join(seg))
            name = body[0].get("name") if isinstance(body, list) and isinstance(body[0], dict) else None
        except yaml.YAMLError:
            name = None
        if name not in names:
            out.extend(seg)
        i = j
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    ap.add_argument("--force", action="store_true", help="remove even with uncommitted code changes")
    a = ap.parse_args()
    if not NAME.match(a.name):
        print(f"module_remove: not a module name: {a.name!r}", file=sys.stderr)
        return 2
    root = _root().resolve()
    mods = root / ".datacore" / "modules"
    mod = mods / a.name
    if not mod.is_dir():
        print(f"module_remove: no installed module {a.name} at {mod}", file=sys.stderr)
        return 1

    if (mod / ".git").exists() and not a.force:
        dirty = subprocess.run(["git", "-C", str(mod), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True).stdout.strip()
        if dirty:
            print(f"module_remove: {mod} has uncommitted code changes; commit them or pass --force:\n{dirty}",
                  file=sys.stderr)
            return 1

    manifest = root / ".datacore" / "lib" / "jobs" / "manifest.yaml"
    text = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
    jobs = ((yaml.safe_load(text) or {}).get("jobs") or []) if text else []
    leaving = owned_jobs(a.name, mod, jobs)
    names = {j.get("name") for j in leaving}
    new_text = strip_jobs(text, names) if leaving else text
    if leaving:
        after = [j.get("name") for j in (yaml.safe_load(new_text) or {}).get("jobs") or [] if isinstance(j, dict)]
        expected = [j.get("name") for j in jobs if isinstance(j, dict) and j.get("name") not in names]
        if after != expected:
            print(f"module_remove: could not take {sorted(names)} out of {manifest} cleanly; nothing changed",
                  file=sys.stderr)
            return 1

    keep = [c for c in COMPONENTS if (mod / c).exists() and not (mod / c).is_symlink()]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = root / ".datacore" / "state" / "removed-modules" / f"{a.name}-{stamp}"

    if a.dry_run:
        print(f"would remove {mod}")
        for c in keep:
            print(f"would keep {c} at {dest / c}")
        for j in leaving:
            print(f"would drop job {j.get('name')} (machine {j.get('machine', '?')}) from {manifest}")
        return 0

    # 1. The owner's data first, verified, before anything is deleted.
    if keep:
        dest.mkdir(parents=True, mode=0o700)
        for c in keep:
            src, dst = mod / c, dest / c
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=True)
            else:
                shutil.copy2(src, dst)
            if _digests(src) != _digests(dst):
                print(f"module_remove: copy of {src} to {dst} did not verify; module left in place",
                      file=sys.stderr)
                return 1
    # 2. Its schedules out of the job list.
    if leaving:
        tmp = manifest.with_suffix(".yaml.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, manifest)
    # 3. The code.
    if mod.is_symlink():
        mod.unlink()
    else:
        shutil.rmtree(mod)

    print(f"removed module {a.name} ({mod})")
    for c in keep:
        print(f"kept your {c} at {dest / c}")
    if not keep:
        print("the module held no data/, state/ or settings.local.yaml")
    for j in leaving:
        print(f"dropped job {j.get('name')} from {manifest.relative_to(root)}; if it is installed on "
              f"machine {j.get('machine', '?')}, retire it there (cron: cron_install.py --retire <script> "
              f"--state <dir>; systemd: disable its timer). No crontab or unit was touched.")
    print("space files (journals, org, knowledge) were not touched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
