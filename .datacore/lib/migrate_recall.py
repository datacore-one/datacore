#!/usr/bin/env python3
"""
migrate_recall.py — DIP-0029 Phase 3 helper.

Adds default `recall:` frontmatter blocks to commands and module commands that
don't have one yet. Idempotent: skips files that already declare `recall:`.

Default block (per DIP-0029 §1, command-level minimum):

    recall:
      # DIP-0029 default — engrams scoped to this command + tag-matched.
      scopes:
        - command:<NAME>
      tags:
        - <NAME>

Usage:
  python3 .datacore/lib/migrate_recall.py [--dry-run] [--path .datacore/commands] [...]
  python3 .datacore/lib/migrate_recall.py --module-yaml          # adds module-level recall: to module.yaml
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FRONTMATTER_RE = re.compile(r"\A(---\s*\n)(.*?\n)(---\s*\n)", re.DOTALL)
HAS_RECALL_RE = re.compile(r"^recall:\s*$", re.MULTILINE)


def default_block(name: str) -> str:
    return (
        "recall:\n"
        f"  # DIP-0029 default — engrams scoped to this command + tag-matched.\n"
        f"  scopes:\n"
        f"    - command:{name}\n"
        f"  tags:\n"
        f"    - {name}\n"
    )


def derive_name(path: Path) -> str:
    return path.stem


def migrate_command(path: Path, dry_run: bool = False) -> str:
    """Return one of: 'updated', 'skipped-existing', 'skipped-no-frontmatter', 'no-change'."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "skipped-unreadable"

    m = FRONTMATTER_RE.match(text)
    if not m:
        # No frontmatter — add one with name + recall
        name = derive_name(path)
        new_fm = (
            "---\n"
            f"name: {name}\n"
            f"description: {name} command\n"
            f"{default_block(name)}"
            "---\n\n"
        )
        new_text = new_fm + text
    else:
        head, body, foot = m.group(1), m.group(2), m.group(3)
        if HAS_RECALL_RE.search(body):
            return "skipped-existing"
        name = derive_name(path)
        # Append recall: at end of frontmatter, preserving everything else
        new_body = body.rstrip("\n") + "\n" + default_block(name)
        new_text = head + new_body + foot + text[m.end():]

    if dry_run:
        return "would-update"
    path.write_text(new_text, encoding="utf-8")
    return "updated"


def migrate_module_yaml(path: Path, dry_run: bool = False) -> str:
    """Add a module-level recall: block to a module.yaml if missing."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "skipped-unreadable"

    if re.search(r"^recall:\s*$", text, flags=re.MULTILINE):
        return "skipped-existing"

    name = path.parent.name
    block = (
        "\n"
        "recall:\n"
        "  # DIP-0029 — module-level engram scope; composes with command-level recall:.\n"
        "  scopes:\n"
        f"    - module:{name}\n"
        "  tags:\n"
        f"    - {name}\n"
    )
    new_text = text.rstrip("\n") + "\n" + block
    if dry_run:
        return "would-update"
    path.write_text(new_text, encoding="utf-8")
    return "updated"


def iter_command_files(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            out.append(root)
            continue
        for p in sorted(root.rglob("*.md")):
            sp = str(p)
            if "_deprecated" in sp or "/node_modules/" in sp or "/demoted/" in sp or "/_patterns/" in sp:
                continue
            if p.is_symlink():
                continue
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--paths", nargs="*", default=None,
                    help="Paths to scan (default: .datacore/commands + .datacore/modules/*/commands)")
    ap.add_argument("--module-yaml", action="store_true",
                    help="Migrate module.yaml files instead of command markdown files")
    args = ap.parse_args()

    if args.module_yaml:
        modules_dir = ROOT / ".datacore" / "modules"
        files = sorted(modules_dir.glob("*/module.yaml"))
    elif args.paths:
        files = iter_command_files([Path(p) for p in args.paths])
    else:
        roots = [ROOT / ".datacore" / "commands"]
        modules_dir = ROOT / ".datacore" / "modules"
        if modules_dir.exists():
            for d in sorted(modules_dir.iterdir()):
                if d.is_dir():
                    cmd_dir = d / "commands"
                    if cmd_dir.exists():
                        roots.append(cmd_dir)
        files = iter_command_files(roots)

    counts: dict[str, int] = {}
    for f in files:
        if args.module_yaml:
            result = migrate_module_yaml(f, dry_run=args.dry_run)
        else:
            result = migrate_command(f, dry_run=args.dry_run)
        counts[result] = counts.get(result, 0) + 1
        if result.startswith("update") or result == "would-update":
            try:
                rel = f.resolve().relative_to(ROOT)
            except ValueError:
                rel = f
            print(f"  [{result}] {rel}")

    print()
    print("Summary:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    print(f"Total: {sum(counts.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
