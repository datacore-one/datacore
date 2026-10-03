#!/usr/bin/env python3
"""What this machine has recorded in a space's ledger that the remote does not have yet.

RECORD, ISOLATE, CONTINUE (owner, fleet sim 2026-10-03, break 3). With GitHub
unreachable, the owner's evening delegation was appended and committed on the
workstation and the push failed. The fact was safe -- it was in this machine's
ledger -- but nothing said so: the publish log read "fetch failed; inspect local
remote configuration", and nobody was told that a delegated task had not reached
the overnight host.

So, per space:
  * `unpublished_creates(space)` -- the `item.create` events in this checkout's
    event logs that the remote-tracking branch does not hold (the last fetch's
    view; nothing here touches the network);
  * `note_held(space, reason)` -- the lines to print, and ONE message to the owner
    per newly queued task: "queued, not lost", with the cause;
  * `note_published(space)` -- once those tasks are on the remote, one message
    saying so. State is a small JSON file, so neither message repeats.

    ledger_queue.py [--root DIR]     # print what is queued, per space
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

STATE = Path.home() / ".datacore" / "state" / "ledger-queue.json"
EVENTS = ".datacore/events"


def _git(space: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(space), *args], capture_output=True, text=True, timeout=60)


def _upstream(space: Path) -> str | None:
    for ref in ("@{u}", "origin/HEAD", "origin/main", "origin/master"):
        if _git(space, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0:
            return ref
    return None


def unpublished_creates(space: Path) -> list[dict]:
    """item.create events here that the remote-tracking branch does not hold, oldest first."""
    space = Path(space)
    folder = space / EVENTS
    if not folder.is_dir():
        return []
    up = _upstream(space)
    out: list[dict] = []
    for path in sorted(folder.glob("*.jsonl")):
        rel = f"{EVENTS}/{path.name}"
        remote = set()
        if up:
            shown = _git(space, "show", f"{up}:{rel}")
            if shown.returncode == 0:
                remote = {line.strip() for line in shown.stdout.splitlines() if line.strip()}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line in remote:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") != "item.create":
                continue
            payload = ev.get("payload") or {}
            out.append({"id": str(payload.get("id") or "?"), "title": str(payload.get("title") or "(untitled)"),
                        "log": path.stem, "hlc": ev.get("hlc", "")})
    return out


def _load() -> dict:
    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, STATE)
    except OSError as exc:
        print(f"  ledger queue state not saved ({type(exc).__name__}); the owner may hear this twice",
              file=sys.stderr)


def _send(text: str) -> bool:
    """This host's own alert route (promise_nightly.send_to_firm)."""
    try:
        from promise_nightly import send_to_firm
        return bool(send_to_firm(text)[0])
    except Exception:  # noqa: BLE001 -- telling is best effort; the log line stands
        return False


def _titles(items: list[dict], n: int = 5) -> str:
    shown = "; ".join(f"'{i['title'][:80]}'" for i in items[:n])
    return shown + (f" and {len(items) - n} more" if len(items) > n else "")


def note_held(space: Path, reason: str) -> list[str]:
    """Lines for the publish log; tells the owner once per newly queued task."""
    space = Path(space)
    items = unpublished_creates(space)
    if not items:
        return []
    line = (f"QUEUED, NOT LOST: {len(items)} task(s) recorded on this machine in {space.name} are not on "
            f"the remote yet: {_titles(items)} ({reason}). They are in this machine's ledger and are "
            f"published by the next publish run that reaches the remote.")
    state = _load()
    told = set(state.get(space.name, []))
    new = [i for i in items if i["id"] not in told]
    if new:
        text = (f"Queued, not lost -- {space.name}: {_titles(new)} is recorded on this machine but has "
                f"not reached the remote ({reason}). It goes out by itself when the remote answers; "
                f"until then the other hosts cannot see it. You hear once more when it is published.")
        if _send(text):
            state[space.name] = sorted(told | {i["id"] for i in new})
            _save(state)
    return [line]


def note_published(space: Path) -> list[str]:
    """Once tasks the owner was told about are on the remote: say so, once."""
    space = Path(space)
    state = _load()
    told = state.get(space.name) or []
    if not told:
        return []
    waiting = {i["id"] for i in unpublished_creates(space)}
    out = [t for t in told if t not in waiting]
    if not out:
        return []
    titles = {}
    try:
        from ledger.fold import fold
        from ledger.log import read_events
        items = fold(read_events(space)).items
        titles = {t: str((getattr(items.get(t), "payload", None) or {}).get("title") or t) for t in out}
    except Exception:  # noqa: BLE001 -- ids are enough
        titles = {t: t for t in out}
    text = (f"Published -- {space.name}: {_titles([{'title': titles[t]} for t in out])} reached the remote; "
            f"the other hosts can see it now.")
    _send(text)
    rest = [t for t in told if t in waiting]
    if rest:
        state[space.name] = rest
    else:
        state.pop(space.name, None)
    _save(state)
    return [f"published {space.name}: {len(out)} queued task(s) are on the remote now"]


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="what this machine has recorded that the remote does not have")
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data")))
    a = ap.parse_args(argv)
    found = 0
    for space in sorted(p for p in a.root.glob("[0-9]-*") if (p / ".git").exists()):
        items = unpublished_creates(space)
        if items:
            found += len(items)
            print(f"{space.name}: {len(items)} task(s) recorded here, not on the remote yet: {_titles(items)}")
    if not found:
        print("nothing queued: every recorded task here is on the remote (as of the last fetch)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
