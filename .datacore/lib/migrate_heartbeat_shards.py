#!/usr/bin/env python3
"""Migrate heartbeat.json from a contested file to per-writer shards.

One-shot, idempotent, and reversible by deleting the shard directory. See
state_writer.heartbeat_shard_path for why: three hosts write one path, and on
2026-09-02 that conflict aborted 59 nightshift runs and stranded 85 commits.

The existing heartbeat.json is attributed to the actor named in the file if it
carries one, otherwise to `legacy`. Attributing it to THIS host would be a
guess, and a wrong guess would make one host's shard carry another's fire time
— which is the same class of silent wrongness being removed.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".datacore" / "modules" / "ventures" / "lib"))

import state_writer as sw  # noqa: E402


def main() -> int:
    apply = "--apply" in sys.argv
    spaces = sorted(d for d in ROOT.glob("[0-9]-*") if d.is_dir())
    moved = skipped = 0

    for space in spaces:
        legacy = sw.heartbeat_state_path(space)
        shard_dir = sw.heartbeat_shard_dir(space)
        if not legacy.exists():
            continue
        if any(shard_dir.glob("*.json")):
            print(f"  skip  {space.name}: already sharded")
            skipped += 1
            continue
        try:
            payload = json.loads(legacy.read_text())
        except (OSError, ValueError) as e:
            print(f"  FAIL  {space.name}: unreadable ({e})")
            continue

        actor = payload.get("actor") or "legacy"
        target = shard_dir / f"{actor}.json"
        print(f"  move  {space.name}: heartbeat.json -> heartbeat/{actor}.json "
              f"(last_fire {payload.get('last_fire')})")
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload["actor"] = actor
            # Atomic, like every other writer of this artifact family. A crash
            # mid-write must leave either no shard (legacy fallback still
            # works) or a complete one -- never a half-written file that
            # reduce_heartbeat silently skips while the migration looks done.
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            tmp.replace(target)
            sw.materialize_heartbeat(space)
        moved += 1

    print("-" * 78)
    print(f"{moved} space(s) to migrate, {skipped} already sharded"
          + ("" if apply else "   [dry run — pass --apply]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
