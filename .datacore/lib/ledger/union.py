"""One work view across every ledger space, by a read-time union.

Plan 2026-10-31, phase G option (a), the owner's choice on 2026-11-03.

Each space is folded exactly as today, through `authoritative_state` (its own hub
admission, policy and seals). The results are then unioned keyed by
(space, item id): 696 item ids are created in more than one space, so folding
the spaces as one stream would merge different tasks into one.

Nothing is written: no event, log, seal, config or org file. A space whose fold
fails is named in `errors`, and the installation root is withheld (None) rather
than computed over fewer spaces than there are.

    python3 .datacore/lib/ledger/union.py [--root ~/Data] [--json]
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


def ledger_spaces(root) -> list[Path]:
    """`[0-9]-*` folders under `root` that keep a ledger (an events log)."""
    return [p for p in sorted(Path(root).glob("[0-9]-*"))
            if p.is_dir() and any((p / ".datacore" / "events").glob("*.jsonl"))]


@dataclass
class Union:
    #: (space, item id) -> ItemState, from that space's own fold.
    items: dict = field(default_factory=dict)
    #: space -> that space's `state_root()`.
    roots: dict = field(default_factory=dict)
    #: space -> why its fold failed. Never dropped silently.
    errors: dict = field(default_factory=dict)

    def installation_root(self) -> str | None:
        """One sha256 over each space's name and root, in name order; None when any space failed."""
        if self.errors:
            return None
        h = hashlib.sha256()
        for space in sorted(self.roots):
            h.update(f"{space}\0{self.roots[space]}\n".encode())
        return h.hexdigest()


def union_state(root, spaces=None, *, fold=None) -> Union:
    if fold is None:
        from .hub import authoritative_state as fold
    out = Union()
    for space in (spaces if spaces is not None else ledger_spaces(root)):
        space = Path(space)
        try:
            state = fold(space)
            out.roots[space.name] = state.state_root()
        except Exception as exc:  # noqa: BLE001 -- named in errors, the root is withheld
            out.errors[space.name] = f"{type(exc).__name__}: {exc}"
            continue
        for iid, item in state.items.items():
            out.items[(space.name, iid)] = item
    return out


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path.home() / "Data")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    u = union_state(a.root)
    counts = {s: sum(1 for (sp, _) in u.items if sp == s) for s in u.roots}
    report = {"spaces": {s: {"items": counts[s], "state_root": u.roots[s]} for s in sorted(u.roots)},
              "errors": u.errors, "items": len(u.items), "installation_root": u.installation_root()}
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        for s, r in report["spaces"].items():
            print(f"{s:16} {r['items']:6} items  root {r['state_root'][:12]}")
        for s, e in u.errors.items():
            print(f"{s:16} FAILED  {e}")
        root = report["installation_root"]
        print(f"{len(u.items)} items in {len(u.roots)} spaces; installation root "
              + (root[:12] if root else "withheld (a space failed)"))
    return 1 if u.errors else 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ledger.union import main as _main
    raise SystemExit(_main())
