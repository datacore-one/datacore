#!/usr/bin/env python3
"""Validate sprint.yaml files against .datacore/schemas/sprint.schema.json.

Usage:
    python scripts/validate_sprint.py sprints/*/sprint.yaml
    python scripts/validate_sprint.py sprints/2026-W36-sprint9/sprint.yaml

Exits 0 if all files pass, 1 if any fail.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
from ruamel.yaml import YAML

_yaml = YAML(typ="safe")

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "sprint.schema.json"


def load_schema() -> dict:
    with SCHEMA_PATH.open() as f:
        return json.load(f)


def _coerce(obj: Any) -> Any:
    """Recursively convert YAML-native types to JSON-compatible types.

    YAML's safe loader parses bare dates (2026-08-30) as datetime.date and
    datetimes as datetime.datetime. JSON Schema "type: string" rejects these,
    so coerce them to ISO strings before validation — the schema is testing
    structure and presence, not calendar arithmetic.
    """
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _coerce(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_coerce(v) for v in obj]
    return obj


def validate_file(path: Path, schema: dict) -> list[str]:
    """Return a list of human-readable error strings for the given sprint file."""
    try:
        with path.open() as f:
            raw = _yaml.load(f)
    except Exception as exc:
        return [f"YAML parse error: {exc}"]

    if raw is None:
        return ["Empty or null document"]

    data = _coerce(raw)

    validator = jsonschema.Draft7Validator(schema)
    return [
        f"{'.'.join(str(p) for p in err.path) or '(root)'}: {err.message}"
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    ]


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: validate_sprint.py <sprint.yaml> [...]", file=sys.stderr)
        return 1

    schema = load_schema()
    paths = [Path(p) for p in argv]
    failed: list[Path] = []

    for path in paths:
        errors = validate_file(path, schema)
        if errors:
            print(f"FAIL  {path}")
            for err in errors:
                print(f"      {err}")
            failed.append(path)
        else:
            print(f"ok    {path}")

    total = len(paths)
    if failed:
        print(f"\n{len(failed)}/{total} sprint file(s) failed", file=sys.stderr)
        return 1

    print(f"\n{total}/{total} sprint file(s) valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
