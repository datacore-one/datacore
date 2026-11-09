#!/usr/bin/env python3
"""Deliver ledger events to the subscribers declared in registry/hooks.yaml.

DIP-0024 amendment 2026-11-09. Before this, each observer (Winston's request
reporter among them) refolded every space on a timer, kept its own "already
told" record, and nothing recorded a delivery that failed.

Contract:
  * at least once, in log order within one writer's log; no order across logs;
  * a cursor per subscriber, per space, per writer, moved past an event only
    when the call returned without raising;
  * a failed call is recorded with its error, holds back the later events of
    that one log, and is retried on the next run;
  * a subscriber first seen starts at the current end of every log -- it gets
    what happens from now on, not the history;
  * a name not declared in the registry is refused;
  * a subscriber gets a copy of the event and cannot veto a write. Anything it
    writes goes through EventLog and its guards like any other write.

    ledger_subscribers.py run    [--root ~/Data] [--only NAME]
    ledger_subscribers.py status [--json]
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

DEFAULT_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
DEFAULT_STATE = Path.home() / ".datacore" / "state" / "subscribers"
REGISTRY = LIB.parent / "registry" / "hooks.yaml"

#: The nearest event name in the Paperclip plugin spec where the meaning is the
#: same, so a bridge can forward an event without its own table. "Task" stays
#: Datacore's word; "issue" is only the mapping.
ALIASES = {
    "item.create": "issue.created",
    "item.claim": "agent.run.started",
    "item.complete": "agent.run.finished",
    "item.verify": "agent.run.finished",
    "item.release": "agent.run.failed",
    "item.dismiss": "issue.closed",
    "approval.grant": "approval.decided",
    "spend.record": "cost_event.created",
}


class UnknownSubscriber(LookupError):
    """A name that registry/hooks.yaml does not declare under ledger_events."""


def load_subscribers(registry: Path = REGISTRY) -> list[dict]:
    import yaml
    data = yaml.safe_load(Path(registry).read_text()) or {}
    return [s for s in data.get("ledger_events") or [] if isinstance(s, dict) and s.get("name")]


def resolve_call(call: str) -> Callable[[dict], object]:
    module, _, func = call.partition(":")
    return getattr(importlib.import_module(module), func)


def _read_task_events(space: Path):
    from ledger.log import read_task_events
    return read_task_events(space)


def _state_path(state_dir: Path, name: str) -> Path:
    return Path(state_dir) / f"{name}.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _envelope(space: Path, e) -> dict:
    return {"type": e.type, "alias": ALIASES.get(e.type), "space": Path(space).name,
            "space_dir": str(space),
            "actor": e.actor, "seq": e.seq, "hash": getattr(e, "hash", None),
            "hlc": getattr(e, "hlc", None), "payload": copy.deepcopy(e.payload or {})}


def _by_log(events) -> dict[str, list]:
    logs: dict[str, list] = {}
    for e in events:
        if isinstance(getattr(e, "seq", None), int) and getattr(e, "actor", None):
            logs.setdefault(e.actor, []).append(e)
    for evs in logs.values():
        evs.sort(key=lambda e: e.seq)
    return logs


def deliver(sub: dict, *, spaces: Iterable[Path], state_dir: Path,
            read_events: Callable, handler: Callable[[dict], object]) -> dict:
    """One subscriber over every space. {delivered, failed}."""
    path = _state_path(state_dir, sub["name"])
    st = _load(path)
    cursors = st.setdefault("cursors", {})
    failures: list[dict] = []
    delivered = 0
    types = set(sub.get("types") or [])
    for space in spaces:
        space = Path(space)
        try:
            events = read_events(space)
        except Exception as exc:  # noqa: BLE001 -- an unreadable space is a recorded failure
            failures.append({"space": space.name, "error": f"could not read: {exc}"[:300],
                             "at": int(time.time())})
            continue
        new_space = space.name not in cursors
        mine = cursors.setdefault(space.name, {})
        for actor, evs in _by_log(events).items():
            if new_space:
                mine[actor] = evs[-1].seq      # subscriber new to this space: start at the end
                continue
            mine.setdefault(actor, -1)         # a writer that appeared later: from its first event
            for e in evs:
                if e.seq <= mine[actor]:
                    continue
                if e.type in types:
                    try:
                        handler(_envelope(space, e))
                    except Exception as exc:  # noqa: BLE001 -- recorded, retried next run
                        failures.append({"space": space.name, "actor": actor, "seq": e.seq,
                                         "type": e.type, "error": f"{type(exc).__name__}: {exc}"[:300],
                                         "at": int(time.time())})
                        break
                    delivered += 1
                mine[actor] = e.seq
    st["failures"] = failures
    st["last_run"] = int(time.time())
    _save(path, st)
    return {"delivered": delivered, "failed": len(failures)}


def run(*, spaces: Iterable[Path] | None = None, subscribers: list[dict] | None = None,
        state_dir: Path = DEFAULT_STATE, read_events: Callable = _read_task_events,
        resolve: Callable[[str], Callable] = resolve_call, only: str | None = None,
        root: Path = DEFAULT_ROOT) -> dict:
    subs = load_subscribers() if subscribers is None else subscribers
    if only is not None:
        subs = [s for s in subs if s["name"] == only]
        if not subs:
            raise UnknownSubscriber(f"{only!r} is not declared under ledger_events in registry/hooks.yaml")
    if spaces is None:
        from ledger.union import ledger_spaces
        spaces = list(ledger_spaces(root))
    spaces = list(spaces)
    return {s["name"]: deliver(s, spaces=spaces, state_dir=state_dir, read_events=read_events,
                               handler=resolve(s["call"]))
            for s in subs}


def status(*, state_dir: Path = DEFAULT_STATE) -> dict:
    out = {}
    for p in sorted(Path(state_dir).glob("*.json")):
        st = _load(p)
        out[p.stem] = {"failures": st.get("failures") or [], "last_run": st.get("last_run")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "status"])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--only")
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.command == "run":
        try:
            out = run(root=a.root, only=a.only, state_dir=a.state_dir)
        except UnknownSubscriber as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(out) if a.json else
              "\n".join(f"{n}: delivered {r['delivered']}, failed {r['failed']}" for n, r in out.items()))
        return 1 if any(r["failed"] for r in out.values()) else 0
    st = status(state_dir=a.state_dir)
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        for n, s in st.items():
            print(f"{n}: {len(s['failures'])} failure(s), last run {s['last_run']}")
            for f in s["failures"]:
                print(f"  - {f.get('space')} {f.get('actor', '')} seq {f.get('seq', '')}: {f['error']}")
    return 1 if any(s["failures"] for s in st.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
