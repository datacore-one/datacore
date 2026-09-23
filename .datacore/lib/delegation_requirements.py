"""Concrete execution requirements shared by reviewers and executors.

These fields describe executable work; they never grant execution authority.
Legacy ACCEPTANCE_CRITERIA remains a supported spelling of DONE_WHEN.

ROADMAP (which outcome the work serves) is required only in a space that HAS a
`roadmap.yaml`. That clause lives HERE, once, for both callers that decide
executability: `ai_task_gate` (where the :AI: tag is written) and
`nightshift_parser._is_executable` (where work is chosen to run). Owner
decision N8, 2026-09-23: the executor requires it exactly as the gate does
(DatacoreSpec/NightshiftGates.lean, `AiGate.gate_eq_executor`). Before, only
the gate checked it, so the gate was stricter than the executor it claimed to
mirror.
"""
from pathlib import Path


def roadmap_spaces(root):
    """Names of the spaces under `root` that carry a roadmap.yaml."""
    return frozenset(p.parent.name for p in Path(root).glob("[0-9]-*/roadmap.yaml"))


def space_of(path, root):
    """The space a file lives in: the first part of its path under `root`."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).parts[0]
    except (ValueError, IndexError, OSError, RuntimeError):
        return "?"


def execution_gaps(properties, *, roadmap_required=False):
    def stated(key):
        value = properties.get(key)
        return value.strip() if isinstance(value, str) else ''
    missing = []
    if stated('SURFACE').lower() in ('', 'unassigned'):
        missing.append('SURFACE')
    if not any(stated(key) for key in ('DONE_WHEN', 'ACCEPTANCE_CRITERIA')):
        missing.append('DONE_WHEN')
    if roadmap_required and not stated('ROADMAP'):
        missing.append('ROADMAP')
    return missing
