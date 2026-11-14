"""The data the processes page draws: this installation's own processes.

process_views.py draws each process as swimlanes (Previous, Current, Desired). The
drawings describe one installation -- its machines, its agents, its spaces -- so
they are not shipped code (INS-3). They live in the gitignored
`.datacore/config/processes.local.yaml`; a fresh install, which has none, draws
the placeholder processes in `processes.local.yaml.example` until its owner
writes its own.

Keys: flows (Current), upgrades (Desired), plan_progress, plan_path,
overlap_fixed, previous {as_of, commit, flows}.
"""
from __future__ import annotations

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"
LOCAL = CONFIG / "processes.local.yaml"
EXAMPLE = CONFIG / "processes.local.yaml.example"


def load(path: Path | None = None) -> dict:
    """The local file when it exists, else the placeholder example."""
    p = Path(path) if path is not None else LOCAL
    if not p.is_file():
        p = EXAMPLE
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    prev = doc.get("previous") or {}
    return {
        "flows": list(doc.get("flows") or []),
        "upgrades": dict(doc.get("upgrades") or {}),
        "plan_progress": list(doc.get("plan_progress") or []),
        "plan_path": str(doc.get("plan_path") or ""),
        "overlap_fixed": list(doc.get("overlap_fixed") or []),
        "previous": {"as_of": str(prev.get("as_of") or ""), "commit": str(prev.get("commit") or ""),
                     "flows": dict(prev.get("flows") or {})},
    }


DATA = load()
