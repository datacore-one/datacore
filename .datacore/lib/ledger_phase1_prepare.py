#!/usr/bin/env python3
"""Make a space's ledger and its org file agree, in the ledger, before Phase 1.

Two things keep a space's shadow diff dirty for months and neither is fixed by
editing org:

  TWINS    After the 2026-08-11 id regeneration, every task in org carried a new
           id and the next ingest created a NEW ledger item for each. The old
           items stayed live with no org task. 2-datacore: 271 of 272 orphans
           have a same-title task alive under another id. Retire the old one
           as housekeeping (never as abandoned work), naming the twin.
  DRIFT    Ingest fills holes; it never removes a tag or rewrites a title the
           ledger already holds. A task whose tags or title moved in org stays
           "changed" forever. Emit the org values as an update.

Ledger only: this never touches an org file. Dry-run by default.

    ledger_phase1_prepare.py --space 2-datacore [--root DIR] [--actor mac] [--apply]
"""
from __future__ import annotations

import argparse
import collections
import inspect
import os
import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402

LIVE = ("created", "claimed", "granted")
STATE_RE = re.compile(r"^\*+\s+(?:(?:TODO|NEXT|DONE|WAITING|REVIEW|CANCELLED|DEFERRED|QUEUED)\s+)?(?:\[#[A-C]\]\s+)?(.*?)(?:\s+:[^\s]+:)?\s*$")
ID_RE = re.compile(r"^\s*:ID:\s*(\S+)\s*$")
TAGS_RE = re.compile(r"\s+:([^\s:]+(?::[^\s:]+)*):\s*$")


def norm(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip().lower()


def org_index(space: Path) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """id -> {title, tags (effective, minus filetags)}; normalised title -> [ids].

    Effective tags, not heading tags: the ledger's fingerprint is inherited
    tags included (ledger_checkpoint._fingerprint), so comparing against the
    heading alone would "fix" every child task by stripping what it inherits.
    """
    from org_workspace import OrgWorkspace
    by_id: dict[str, dict] = {}
    by_title: dict[str, list[str]] = collections.defaultdict(list)
    for f in sorted((space / "org").glob("*.org")):
        ws = OrgWorkspace()
        try:
            ws.load(f)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {f.name}: {exc}", file=sys.stderr); continue
        filetags = set()
        for line in f.read_text(errors="replace").splitlines()[:40]:
            m = re.match(r"#\+FILETAGS:\s*:?(.*?):?\s*$", line, re.I)
            if m:
                filetags = {t for t in m.group(1).split(":") if t}
        for node in ws.all_nodes():
            props = node.properties or {}
            tid = props.get("ID")
            if not tid:
                continue
            eff = sorted(set(node.tags or []) - filetags)
            own = sorted(set(node.shallow_tags or []) - filetags)
            by_id[tid] = {"title": (node.heading or "").strip(), "tags": eff, "own": own}
            by_title[norm(node.heading or "")].append(tid)
    return by_id, by_title


def _open_log(space: Path, actor: str) -> EventLog:
    params = list(inspect.signature(EventLog.__init__).parameters)
    kwargs = {}
    for name in params[1:]:
        if name in ("space", "space_dir", "root"):
            kwargs[name] = space
        elif name in ("events_dir", "log_dir", "path"):
            kwargs[name] = space / ".datacore" / "events"
        elif name == "actor":
            kwargs[name] = actor
    return EventLog(**kwargs)


def plan(space: Path) -> dict:
    state = fold(read_events(space))
    by_id, by_title = org_index(space)
    live = [i for i in state.items.values() if i.status in LIVE and not (i.payload or {}).get("section")]
    dismiss, update = [], []
    for item in live:
        if item.id in by_id:
            org = by_id[item.id]
            p = item.payload or {}
            eff = sorted(set(p.get("effective_tags") or p.get("tags") or []) - set(p.get("filetags") or []))
            if norm(item.title) != norm(org["title"]) or eff != org["tags"]:
                update.append((item.id, org["title"], org["tags"], org["own"]))
            continue
        twins = [t for t in by_title.get(norm(item.title), []) if t != item.id]
        if twins:
            dismiss.append((item.id, f"duplicate: superseded by {twins[0]} after the 2026-08-11 id regeneration"))
        else:
            dismiss.append((item.id, "no org task in this space at Phase 1 preparation"))
    return {"live": len(live), "dismiss": dismiss, "update": update}


def apply(space: Path, actor: str, p: dict) -> int:
    log = _open_log(space, actor)
    n = 0
    for iid, reason in p["dismiss"]:
        log.append("item.dismiss", {"id": iid, "reason": reason, "kind": "housekeeping"}); n += 1
    for iid, title, tags, own in p["update"]:
        # both fields: the fingerprint falls back to `tags` when `effective_tags` is empty
        log.append("item.update", {"id": iid, "title": title, "tags": own, "effective_tags": tags}); n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data")))
    ap.add_argument("--space", required=True)
    ap.add_argument("--actor", default=os.environ.get("DATACORE_ACTOR") or os.uname().nodename.split(".")[0])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    space = a.root / a.space
    p = plan(space)
    twins = sum(1 for _, r in p["dismiss"] if r.startswith("duplicate"))
    print(f"  {a.space}: live={p['live']} dismiss={len(p['dismiss'])} (twins {twins}, no-twin {len(p['dismiss']) - twins}) update={len(p['update'])}")
    for iid, title, tags, own in p["update"]:
        print(f"    update {iid}: title={title[:50]!r} tags={tags}")
    if not a.apply:
        print("  dry run — re-run with --apply"); return 0
    n = apply(space, a.actor, p)
    print(f"  appended {n} event(s) as {a.actor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
