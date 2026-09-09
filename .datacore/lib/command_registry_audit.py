#!/usr/bin/env python3
"""Every module command must be findable through the registry. Most are not.

WHY THIS EXISTS. On 2026-09-31 an agent captured the weekly-planning method as
`.datacore/modules/chief-of-staff/commands/weekly-plan.md` and declared it in
that module's `module.yaml`. It did not add it to
`.datacore/registry/commands.yaml` (commit 050d441 touched two files; the
registry was not one of them). The same author registered a different module's
command four commits later, so the step was known and skipped.

The cost landed on 2026-09-09: an agent asked to plan the week looked in the
registry — the place CLAUDE.md names as the full list — found nothing, and
improvised a weekly plan instead of running the ten-step command that existed
for exactly that purpose. A whole planning session was rebuilt afterwards.

Measured the same day: 86 command files under `.datacore/modules/*/commands/`,
18 command entries in the registry, 68 unregistered.

THE SCHEMA PROBLEM, which is probably why nobody fixed it: those 86 files carry
only 69 distinct basenames. Seventeen modules each ship a `today-hook.md`. A
registry keyed by bare command name cannot hold them — they collide. Module
commands need a namespaced key (`<module>:<command>`), which is already how the
harness lists them. This tool reports; it does not silently pick a winner.

    command_registry_audit.py            # report, exit 1 if anything unregistered
    command_registry_audit.py --emit     # print registry entries for the missing
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / ".datacore" / "registry" / "commands.yaml"
MODULES = REPO / ".datacore" / "modules"


# The registry holds commands in TWO maps: `commands:` (18) for core commands and
# `module_commands:` (60) for module-provided ones. Reading only the first
# reported 85 unregistered when the real figure is far smaller — the same
# one-source-of-truth mistake this file exists to catch, made while writing it.
REGISTRY_MAPS = ("commands", "module_commands")


def registry_keys() -> set[str]:
    if not REGISTRY.exists():
        return set()
    doc = yaml.safe_load(REGISTRY.read_text()) or {}
    keys: set[str] = set()
    for section in REGISTRY_MAPS:
        m = doc.get(section)
        if isinstance(m, dict):
            keys |= set(m.keys())
    return keys


def frontmatter(path: Path) -> dict:
    """name/description from the file's own frontmatter. Never invented: a file
    without them is reported as needing one, not given a guessed description."""
    text = path.read_text(errors="replace")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def module_commands() -> list[tuple[str, str, Path, dict]]:
    """(module, command, path, frontmatter) for every module command file."""
    out = []
    for path in sorted(MODULES.glob("*/commands/*.md")):
        out.append((path.parents[1].name, path.stem, path, frontmatter(path)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true",
                    help="print registry entries for unregistered commands")
    ap.add_argument("--fix", action="store_true",
                    help="append the missing entries to module_commands: in the registry")
    args = ap.parse_args()

    keys = registry_keys()
    cmds = module_commands()

    collisions: dict[str, list[str]] = defaultdict(list)
    for module, name, _p, _fm in cmds:
        collisions[name].append(module)
    colliding = {n: m for n, m in collisions.items() if len(m) > 1}

    # The registry keys module commands three ways, all in use today: the bare
    # name when it is unique (`coach`, `outbox`), `<module>-<command>` when it
    # collides (`crm-today-hook`, `health-today-hook`), and occasionally a
    # `<module>:<command>` form. Checking only one shape overstates the gap —
    # it reported 52 missing when many were registered under the hyphen.
    def registered(mod: str, name: str) -> bool:
        return any(k in keys for k in (name, f"{mod}-{name}", f"{mod}:{name}"))

    missing = [(mod, name, p, fm) for mod, name, p, fm in cmds
               if not registered(mod, name)]
    no_fm = [p for _m, _n, p, fm in cmds
             if not (fm.get("name") and fm.get("description"))]

    print(f"registry entries (commands + module_commands): {len(keys)}")
    print(f"module command files     : {len(cmds)}")
    print(f"distinct command names   : {len(collisions)}")
    print(f"UNREGISTERED             : {len(missing)}")
    print(f"missing frontmatter      : {len(no_fm)}")
    print(f"name collisions across modules: {len(colliding)}")
    for name, mods in sorted(colliding.items()):
        print(f"  {name}: {', '.join(sorted(mods))}")

    if args.emit:
        print("\n# --- entries for unregistered commands (namespaced) ---")
        for mod, name, path, fm in missing:
            desc = str(fm.get("description") or "").strip().splitlines()
            desc = desc[0] if desc else "TODO: no description in frontmatter"
            print(f"  {mod}:{name}:")
            print(f"    name: {fm.get('name') or name}")
            print(f"    description: {desc}")
            print(f"    version: {fm.get('version') or '0.1.0'}")
            print(f"    source: {path.relative_to(REPO)}")

    if missing and args.fix:
        # `module_commands:` is the last top-level key, so correctly-indented
        # entries appended at EOF land inside it. Written this way rather than
        # via yaml.dump because a round-trip would strip the file's comments
        # and reorder 78 existing entries for no reason.
        taken = set(keys)
        lines = []
        for mod, name, path, fm in missing:
            key = name if name not in taken else f"{mod}-{name}"
            while key in taken:
                key = f"{mod}-{key}"
            taken.add(key)
            desc = str(fm.get("description") or "").strip().splitlines()
            desc = desc[0] if desc else f"UNDOCUMENTED — {path.name} has no description frontmatter"
            lines += [
                f"  {key}:",
                f"    name: {fm.get('name') or name}",
                f"    description: {desc}",
                f"    version: {fm.get('version') or '0.1.0'}",
                f"    status: active",
                f"    source: {path.relative_to(REPO)}",
                f"    module: {mod}",
            ]
        text = REGISTRY.read_text()
        if not text.endswith("\n"):
            text += "\n"
        header = ("\n  # Appended 2026-09-09 by command_registry_audit.py --fix.\n"
                  "  # These files existed and were unreachable through the registry;\n"
                  "  # an agent following the documented lookup path could not find them.\n")
        REGISTRY.write_text(text + header + "\n".join(lines) + "\n")
        print(f"\nWROTE {len(missing)} entries into module_commands:")
        return 0

    if missing:
        print(f"\nFAIL: {len(missing)} module command(s) are unreachable through "
              f"the registry. An agent that follows the documented lookup path "
              f"cannot find them.")
        return 1
    print("\nOK: every module command is registered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
