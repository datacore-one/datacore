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
import ast
import re
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
            # Two layouts are in use. health/agents/dashboard-curator/ is a
            # DIRECTORY holding agent.yaml + prompt.md; every other health agent
            # is a flat .md. Both are real agents, so the check accepts either
            # rather than reporting a working agent as missing.
            as_dir = mod / subdir / name
            if as_dir.is_dir() and any((as_dir / f).exists()
                                       for f in ('agent.yaml', 'prompt.md', 'index.md')):
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
        third_party = _third_party_imports(mod / "lib")
        if third_party:
            findings.append(
                f"{mod.name}: imports {', '.join(sorted(third_party)[:6])} "
                f"and has no requirements.txt")
    return findings


# Counting .py files flagged 20 modules, most of them stdlib-only — `meetings`
# imports nothing outside the standard library and needs no requirements file.
# A check that reports mostly noise gets ignored, which is the failure it exists
# to prevent, so it asks the question that matters: does this module import
# something that will not be there?
# Counting .py files flagged 20 modules, most stdlib-only — `meetings` imports
# nothing outside the standard library and needs no requirements file. A regex
# over import lines was no better: it matched prose inside docstrings and
# credited `trading` with importing "THIS", "anywhere" and "a".
#
# So parse it. A check that reports mostly noise gets ignored, which is the
# failure it exists to prevent.
_LOCAL_OK = {"lib", "tests", "adapters"}


def _shared_lib_modules() -> set[str]:
    """Modules importable from .datacore/lib, which every module puts on sys.path.

    `env_utils`, `triage_utils`, `agent_emit` and friends are siblings, not
    packages. Reporting them as undeclared dependencies would send someone to
    pip for something that is already in the repo — and would put a nonexistent
    package name into a requirements.txt, which is worse than having none.
    """
    shared = Path(__file__).resolve().parent
    return ({p.stem for p in shared.glob("*.py")}
            | {p.name for p in shared.iterdir() if p.is_dir() and (p / "__init__.py").exists()})


_VENDOR = {".venv", "venv", ".git", "node_modules", "site-packages",
           "__pycache__", ".pytest_cache", "dist", "build", ".tox"}


def _own_names(root: Path) -> set[str]:
    """Module names the module itself defines, skipping vendored trees."""
    names: set[str] = set()
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name in _VENDOR:
                    continue
                names.add(e.name)
                stack.append(e)
            elif e.suffix == ".py":
                names.add(e.stem)
    return names


def _third_party_imports(lib: Path) -> set[str]:
    stdlib = getattr(sys, "stdlib_module_names", set())
    local = ({p.stem for p in lib.rglob("*.py")}
             | {p.name for p in lib.iterdir() if p.is_dir()}
             | _shared_lib_modules()
             # Sibling modules reach each other by package name.
             | {m.name.replace("-", "_") for m in lib.parent.parent.iterdir() if m.is_dir()}
             # A module's own top-level packages: mail/processors/ is imported
             # as `processors`, and it is not on PyPI.
             # Anything the module itself contains, at any depth. trading keeps
             # bzz_whale and data several levels down; they are its own files,
             # not packages anyone can install.
             #
             # NOT into vendor trees. voice-terminal has a .venv beside its lib,
             # so an unfiltered walk reached site-packages and declared numpy,
             # sounddevice and openwakeword "local" — silently hiding the exact
             # dependencies this check exists to find.
             | _own_names(lib.parent))
    found: set[str] = set()
    for f in lib.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(errors="ignore"))
        except (OSError, SyntaxError):
            continue                      # unparseable is not an import claim
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:            # relative import: always local
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if (not name or name in stdlib or name in local
                        or name in _LOCAL_OK or name.startswith("_")):
                    continue
                found.add(name)
    return found


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
