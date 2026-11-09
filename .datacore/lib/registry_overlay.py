"""Registry files with their installation-private overlay.

`agents.yaml` and `commands.yaml` ship in the public repo. Entries whose
definitions live in a private space (a fundraising agent, a team's finance
module, a venture's agents) belong to one installation only; they go in
`agents.local.yaml` / `commands.local.yaml` beside the public file, which are
gitignored. Readers call `load_registry_file()` and see the union. A public
entry always wins over a local one of the same name.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def local_path(registry_path: Path) -> Path:
    p = Path(registry_path)
    return p.with_name(f"{p.stem}.local{p.suffix}")


def load_registry_file(registry_path: Path) -> dict:
    p = Path(registry_path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    lp = local_path(p)
    if lp.exists():
        local = yaml.safe_load(lp.read_text(encoding="utf-8")) or {}
        for section, entries in local.items():
            if not isinstance(entries, dict):
                continue
            block = data.get(section)
            if block is None:
                data[section] = dict(entries)
            elif isinstance(block, dict):
                for name, entry in entries.items():
                    block.setdefault(name, entry)
    return data
