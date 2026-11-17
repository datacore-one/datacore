#!/usr/bin/env python3
"""Scan org files for tasks matching a keyword regex.

Usage: python3 .datacore/lib/find_health_tasks.py <regex> <org file> [<org file> ...]
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ADAPTER = Path(__file__).with_name("org_workspace_adapter.py")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.strip())
        return 2
    pattern = re.compile(sys.argv[1], re.I)
    for path in sys.argv[2:]:
        out = subprocess.run(
            ["python3", str(ADAPTER), "list", "--file", path],
            capture_output=True,
            text=True,
        ).stdout
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            print(f"== {path}: could not parse adapter output")
            continue
        hits = [t for t in data["tasks"] if pattern.search(t["heading"])]
        print(f"== {path}: {data['count']} tasks, {len(hits)} matching")
        for t in hits:
            print(f"   [{t['state']}] {t['heading']}  tags={t['tags']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
