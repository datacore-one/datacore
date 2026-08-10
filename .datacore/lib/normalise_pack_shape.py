#!/usr/bin/env python3
"""Normalise a PLUR pack's engrams.yaml to the standard `engrams:` mapping shape.

PLUR's loader refuses a pack whose top-level YAML is a bare sequence
("top-level value is not a mapping"), which makes its engrams invisible to
plur_feedback writes. The 2026-08-09 audit found the-firm was the only one of
ten installed packs in that shape, and the only one whose records carried no
`status` field.

The transform is TEXTUAL on purpose. A yaml.load/yaml.dump round-trip would
rewrite every string's quoting and folding across a live memory store; indenting
the existing lines and inserting one key per record leaves the statements
byte-identical. The semantic check then proves it: the records parsed out of the
result must equal the records parsed out of the original, key for key, apart
from the `status` that was added.

Usage:
    normalise_pack_shape.py <pack-engrams.yaml> [--apply]

Without --apply it prints what would change and verifies, writing nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def transform(text: str, status: str = "active") -> str:
    """Wrap a bare sequence under `engrams:` and add `status:` to each record."""
    out = ["engrams:"]
    for line in text.split("\n"):
        if not line.strip():
            out.append("")
            continue
        out.append("  " + line)
        stripped = line.lstrip()
        if stripped.startswith("- id:"):
            # Record keys sit at the dash's indent + 2; the dash itself gained 2.
            indent = len(line) - len(stripped) + 2 + 2
            out.append(" " * indent + f"status: {status}")
    return "\n".join(out)


def records(doc: object) -> list[dict]:
    if isinstance(doc, dict):
        value = doc.get("engrams")
        return list(value) if isinstance(value, list) else []
    return list(doc) if isinstance(doc, list) else []


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    path = Path(argv[0])
    apply = "--apply" in argv[1:]
    original = path.read_text(encoding="utf-8")

    before = records(yaml.safe_load(original))
    if not before:
        print(f"{path}: no records found — refusing", file=sys.stderr)
        return 1

    parsed = yaml.safe_load(original)
    if isinstance(parsed, dict) and "engrams" in parsed:
        print(f"{path}: already an `engrams:` mapping — nothing to do")
        return 0

    result = transform(original)
    after = records(yaml.safe_load(result))

    # Every record survives, in order, with exactly `status` added.
    if len(before) != len(after):
        print(f"{path}: record count changed {len(before)} -> {len(after)}", file=sys.stderr)
        return 1
    for old, new in zip(before, after):
        if new.get("status") != "active":
            print(f"{path}: {old.get('id')} did not gain status", file=sys.stderr)
            return 1
        if {k: v for k, v in new.items() if k != "status"} != old:
            print(f"{path}: {old.get('id')} changed beyond `status`", file=sys.stderr)
            return 1

    print(f"{path}: {len(before)} records verified identical apart from `status: active`")
    if apply:
        path.write_text(result, encoding="utf-8")
        print(f"{path}: written")
    else:
        print("(dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
