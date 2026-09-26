"""Which promise evals a test run collects.

Evals first means evals are committed red, before the code that makes them
green. Collected by CI or a pre-push hook, a red-by-design eval would break
every unrelated change. So an ordinary run collects a promise's evals only when
that promise is green in the committed baseline (.datacore/registry/
promise-baseline.json, written by `promise_evals.py --write-baseline`): from
then on a regression fails the gate. The scoreboard sets PROMISE_EVALS_ALL=1
and collects everything. Ordinary tests are never affected.

Used from each suite's conftest.py:
    def pytest_ignore_collect(collection_path, config):
        return promise_gate.should_ignore(collection_path) or None
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".datacore" / "registry" / "promise-baseline.json"
_FILE = re.compile(r"^test_promise_([A-Za-z]+)_?(\d+)(?:_([0-9]+))?_")


def promise_id(path: Path) -> str | None:
    m = _FILE.match(Path(path).name)
    return (m.group(1) + m.group(2)).upper() if m else None


def green() -> set[str]:
    try:
        return {str(x).upper() for x in json.loads(BASELINE.read_text()).get("green", [])}
    except (OSError, ValueError, AttributeError):
        return set()


def should_ignore(path: Path) -> bool:
    pid = promise_id(path)
    if pid is None or os.environ.get("PROMISE_EVALS_ALL") == "1":
        return False
    return pid not in green()
