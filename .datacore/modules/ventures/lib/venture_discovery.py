"""
venture_discovery.py — Find all venture spaces in a Datacore installation.

Scans the data directory for numbered space directories ([0-9]-*/) that
contain a valid venture.yaml. Used by the cadence runner and status scripts.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import re

from venture_loader import load_venture_file, VentureConfig


@dataclass
class VentureSpace:
    name: str
    space_dir: Path
    config: VentureConfig


def discover_ventures(data_dir: Path, nightshift_only: bool = False) -> list[VentureSpace]:
    """Find all numbered spaces ([0-9]-*/) with valid venture.yaml.

    Returns list sorted by space number. Skips invalid/broken venture.yaml
    silently. If nightshift_only=True, only returns ventures with
    nightshift.enabled=True.
    """
    pattern = re.compile(r"^(\d+)-")
    results: list[VentureSpace] = []

    for entry in data_dir.iterdir():
        if not entry.is_dir():
            continue
        match = pattern.match(entry.name)
        if not match:
            continue

        venture_file = entry / "venture.yaml"
        if not venture_file.exists():
            continue

        try:
            config = load_venture_file(venture_file)
        except Exception:
            continue

        if nightshift_only:
            if config.nightshift is None or not config.nightshift.enabled:
                continue

        results.append(VentureSpace(name=entry.name, space_dir=entry, config=config))

    results.sort(key=lambda vs: int(pattern.match(vs.name).group(1)))
    return results


def default_templates_dir(data_dir: Optional[Path] = None) -> Path:
    """Return default role templates dir: data_dir/.datacore/templates/roles/"""
    if data_dir is None:
        data_dir = Path.home() / "Data"
    return data_dir / ".datacore" / "templates" / "roles"
