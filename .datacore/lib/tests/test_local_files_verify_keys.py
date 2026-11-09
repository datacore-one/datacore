"""A writer's proven verify key reaches every host (ledger hub activation, 2026-10-24).

principals.yaml is a per-host file. `ledger_keys_collect --apply` writes a
writer's proven public key into the collecting host's copy only, so a key the
Mac proves (its own; the Mac runs no ssh server, so no other host can collect
it) never reached the box, which must verify the owner's signed hub records.
The private-files sync carries `verify_keys` by its MERGE rule: a key the host
lacks is added; a key it has is never changed.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import local_files_sync as lfs  # noqa: E402


def test_verify_keys_travel_and_only_missing_ones_are_added():
    assert "verify_keys" in lfs.ALLOWED[".datacore/registry/principals.yaml"]
    local = {"verify_keys": {"mac": "aa" * 32, "winston": "bb" * 32}}
    remote = {"principals": {"x": {}}, "verify_keys": {"winston": "cc" * 32}}
    out, added = lfs.merged(local, remote)
    assert out["verify_keys"] == {"winston": "cc" * 32, "mac": "aa" * 32}   # the host's own key kept
    assert added == ["verify_keys.mac"]
