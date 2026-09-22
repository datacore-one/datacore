#!/usr/bin/env python3
"""Does every module deliver what its manifest promises?

WHY. `comms/module.yaml` declared a `brand-positioning` command for months and
`commands/brand-positioning.md` did not exist. Nothing noticed, because nothing
checks. Anything reading the manifest — a user, a wizard, an agent following the
venture-creation path — believed the command was there.

It was not the only one: the same module declares four more commands with no
file. A single missing file is cheap; a manifest nobody verifies is not, because
every consumer downstream trusts it.

    python3 module_manifest_check.py            # every module
    python3 module_manifest_check.py --module comms
    python3 module_manifest_check.py --strict   # exit 1 on any gap, for CI
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

MODULES = Path.home() / "Data/.datacore/modules"


def _names(entries) -> list[str]:
    out = []
    for e in entries or []:
        out.append(e.get("name") if isinstance(e, dict) else str(e))
    return [n for n in out if n]


def check(mod: Path) -> list[str]:
    """Return one finding per broken promise. Empty means the manifest is honest."""
    manifest = mod / "module.yaml"
    if not manifest.exists():
        return [f"{mod.name}: no module.yaml"]
    try:
        d = yaml.safe_load(manifest.read_text()) or {}
    except Exception as e:                                   # noqa: BLE001
        return [f"{mod.name}: module.yaml does not parse — {e}"]

    provides = d.get("provides") or {}
    findings: list[str] = []

    # commands/<name>.md and agents/<name>.md are the two the manifest names
    # directly, so they are the two that can be wrong without anyone noticing.
    # A module may legitimately declare something implemented at CORE level —
    # gtd claims /today and /wrap-up, which live in .datacore/commands/. Look
    # there before calling it missing, or the check cries wolf and gets ignored.
    core = mod.parent.parent
    for kind, subdir in (("commands", "commands"), ("agents", "agents")):
        for name in _names(provides.get(kind)):
            if "/" in name or " " in name:
                findings.append(f"{mod.name}: declares {kind[:-1]} '{name}' — not a valid file stem")
                continue
            if (mod / subdir / f"{name}.md").exists():
                continue
            if (core / subdir / f"{name}.md").exists():
                continue                       # implemented at core level
            findings.append(f"{mod.name}: declares {kind[:-1]} '{name}' but {subdir}/{name}.md is missing")

    # A hooks: entry points at a file by path; a dangling one silently drops the
    # module's section from /today.
    for hook, target in (d.get("hooks") or {}).items():
        targets = target if isinstance(target, list) else [target]
        for tgt in targets:
            if isinstance(tgt, dict):
                continue                       # structured hook, not a path
            s = str(tgt or '')
            # A hook value may be inline markdown rather than a path — the
            # trading module embeds a whole briefing section. Only something
            # path-shaped is worth resolving.
            if not s or '\n' in s or len(s) > 200 or s.lstrip().startswith(('#', '`')):
                continue
            if not (mod / s).exists():
                findings.append(f"{mod.name}: hook '{hook}' points at missing {tgt}")

    # requirements.txt is declared by its presence, not in the manifest — but a
    # module with Python libs and no requirements file is the undeclared-dep
    # failure that has bitten this system repeatedly.
    if (mod / "lib").is_dir() and not (mod / "requirements.txt").exists():
        py = list((mod / "lib").rglob("*.py"))
        if py:
            findings.append(f"{mod.name}: has {len(py)} Python file(s) under lib/ and no requirements.txt")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default="")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--root", default=str(MODULES))
    a = ap.parse_args()

    root = Path(a.root)
    mods = [root / a.module] if a.module else sorted(
        p for p in root.iterdir() if p.is_dir() and (p / "module.yaml").exists())

    total = 0
    for m in mods:
        findings = check(m)
        total += len(findings)
        if findings:
            for f in findings:
                print(" ", f)
    print(f"\n  {len(mods)} module(s) checked, {total} broken promise(s)")
    return 1 if (a.strict and total) else 0


if __name__ == "__main__":
    raise SystemExit(main())
