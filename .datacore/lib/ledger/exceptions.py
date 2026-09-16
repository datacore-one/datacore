"""Reviewed, per-event exceptions to hash verification.

WHY THIS EXISTS. The ledger is append-only, so an event written with a hash
that never matched its body cannot be corrected without re-sealing the tail of
that log -- and origin holds the same bytes, so a partial re-seal forks the log,
the exact failure DIP-0034 exists to prevent. Meanwhile `ledger_checkpoint`
re-verifies every hash and refuses the whole space on the first mismatch. One
malformed historical event therefore denied a space any restore point, forever.

That inverts the intent: the check exists to make recovery trustworthy, and it
was removing recovery entirely. Measured 2026-09-16 -- 64,846 events, exactly
one mismatch (5-plur tris.jsonl seq 5, written 2026-09-11, byte-identical to the
commit that added it), and it alone kept 5-plur from checkpointing while the
other nine spaces verified.

WHY THIS CANNOT HIDE AN EDIT. An entry pins BOTH hashes: the one stored in the
file and the one its body actually produces. An exception applies only when the
event still hashes to exactly the recorded pair. Change so much as a character
of the body and the computed hash moves, no entry matches, and verification
fails as it always did. So this widens nothing: it names specific, reviewed,
already-written events and leaves every other mismatch -- including any later
alteration of these very events -- reported.

The file is tracked and reviewed like principals.yaml; adding an entry is a
deliberate act with evidence, not a way to silence a failing check.
"""

from __future__ import annotations

import os
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
REGISTRY_REL = Path(".datacore") / "registry" / "ledger-exceptions.yaml"


def _path(root: Path | None = None) -> Path:
    return (root or DATACORE_ROOT) / REGISTRY_REL


def _digest(value: object) -> str | None:
    """A sha256 digest as 64 lowercase hex chars, or None if it is not one.

    YAML types an all-digit scalar as an INT, so a digest that happens to carry
    no letters arrives here as a number and an `isinstance(str)` test silently
    drops the entry. Normalising by shape rather than by parsed type also
    rejects anything that is not a digest at all, which the old check did not.
    """
    if isinstance(value, bool) or value is None:
        return None
    text = format(value, "064x") if isinstance(value, int) else str(value).strip().lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        return None
    return text


def load(root: Path | None = None) -> set[tuple[str, str, int, str, str]]:
    """Every recorded exception as (space, log, seq, recorded, computed).

    Fails CLOSED: an absent, unreadable or malformed file yields no exceptions,
    so verification behaves exactly as it did before this module existed. A
    registry that cannot be read must never be able to excuse anything.
    """
    try:
        import yaml
        data = yaml.safe_load(_path(root).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- see docstring: absent/broken means none
        return set()
    if not isinstance(data, dict):
        return set()
    out: set[tuple[str, str, int, str, str]] = set()
    for entry in data.get("malformed_hash") or ():
        if not isinstance(entry, dict):
            continue
        space, log, seq = entry.get("space"), entry.get("log"), entry.get("seq")
        recorded, computed = _digest(entry.get("recorded")), _digest(entry.get("computed"))
        if (isinstance(space, str) and isinstance(log, str) and type(seq) is int
                and seq >= 0 and recorded and computed and recorded != computed):
            out.add((space, log, seq, recorded, computed))
    return out


def is_recorded(space: str, log: str, seq: int, recorded: str, computed: str,
                root: Path | None = None) -> bool:
    """Is THIS mismatch, on exactly these two hashes, a reviewed exception?"""
    if not (space and log) or recorded == computed:
        return False
    return (space, log, int(seq), str(recorded).lower(),
            str(computed).lower()) in load(root)
