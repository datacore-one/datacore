"""RETIRED 2026-09-26: the out-of-band exception list excuses nothing.

Owner decision 6 (ledger-upgrade PLAN, 2026-09-26): no exceptions. A bad event
is cancelled only by an authorised, append-only `ledger.void` record inside the
ledger (see `ledger.voids`); verify accepts nothing else. The reviewed entries
that `registry/ledger-exceptions.yaml` used to carry (`malformed_hash`,
`invalid_signature`) are no longer read by anything.

`is_recorded` survives only as a compatibility shim for `ledger_checkpoint`,
which asks it whether a saved event's hash mismatch is excused. It now answers
from the space's in-ledger voids: yes only when an authorised void names that
exact event (log, seq, stored hash) and pins the hash its body produces today.
Edit the body and the pin no longer matches, exactly as before.
"""

from __future__ import annotations

import os
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))


def is_recorded(space: str, log: str, seq: int, recorded: str, computed: str,
                root: Path | None = None) -> bool:
    """Is THIS hash mismatch cancelled by an authorised in-ledger void?"""
    if not (space and log) or recorded == computed:
        return False
    from .voids import for_events_dir, log_stem
    voids = for_events_dir((root or DATACORE_ROOT) / space / ".datacore" / "events")
    key = (log_stem(log), int(seq), str(recorded).lower())
    return key in voids.effective and voids.effective[key] == str(computed).lower()
