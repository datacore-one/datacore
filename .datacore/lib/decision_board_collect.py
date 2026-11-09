#!/usr/bin/env python3
"""File saved decision-board answers into the default private directory.

A board page can only download its answers (<slug>.decisions.json) to the
browser's download folder. This moves them next to the boards themselves, in
$DATACORE_STATE/decision-boards/answers/ (default ~/.datacore/state/...), so
every board and every answer lives in one owner-only place that the machine's
backup already covers. The Downloads copy is removed only after the filed copy
has been written and read back identical.

Usage:
    decision_board_collect.py                  # file every *.decisions.json in ~/Downloads
    decision_board_collect.py --dry-run        # show what would be filed
    decision_board_collect.py --only <slug>    # file just that board's answers
    decision_board_collect.py --latest <slug>  # print the path of the newest filed answers
    decision_board_collect.py --backfill       # add every filed answer to the decisions dataset

Every filed answer is also appended to dataset/decisions.jsonl beside it: one
record per decision, joined with the row it answered (question, context,
options, suggestion), so the owner's decisions can later be mined for rules or
used as training data. An answers file alone holds only {row: choice, note};
the board page that explains it can be regenerated or deleted. Private: the
dataset stays in the owner-only state folder, never in a repository.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_utils import atomic_write_text, private_state_directory  # noqa: E402


def answers_dir() -> Path:
    d = private_state_directory("decision-boards") / "answers"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


DATASET_SCHEMA = 1
KINDS = ("review", "mail", "github-triage", "nightshift", "priorities", "weekly", "triage")


def dataset_path() -> Path:
    d = private_state_directory("decision-boards") / "dataset"
    d.mkdir(mode=0o700, exist_ok=True)
    return d / "decisions.jsonl"


def _board_data(path: Path):
    try:
        m = re.search(r'<script type="application/json" id="data">(.*?)</script>', path.read_text(), re.S)
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def find_board(slug: str, build: str | None):
    """The board page these answers came from: same slug, same build when known."""
    hits = sorted(private_state_directory("decision-boards").glob(f"*-{slug}.html"), reverse=True)
    loaded = [(p, _board_data(p)) for p in hits]
    loaded = [(p, d) for p, d in loaded if isinstance(d, dict)]
    for p, d in loaded:
        if build and (d.get("meta") or {}).get("build") == build:
            return d
    # A board of the same slug with a different build explains other rows:
    # joining it would mislabel decisions. Only pages that predate build ids
    # (or answers without one) fall back to the newest page of that slug.
    legacy = [d for p, d in loaded if not (d.get("meta") or {}).get("build")]
    if not build:
        return loaded[0][1] if loaded else None
    return legacy[0] if legacy else None


def _kind(slug: str, meta: dict) -> str:
    text = f"{slug} {meta.get('eyebrow', '')}".lower()
    return next((k for k in KINDS if k in text), "other")


def records(data: dict) -> list[dict]:
    """One dataset record per decision in a saved answers file."""
    board = find_board(data["board"], data.get("build"))
    meta = (board or {}).get("meta") or {}
    rows = {r.get("id"): (s, r) for s in (board or {}).get("sections", []) for r in s.get("rows", [])}
    out = []
    for rid, d in sorted((data.get("decisions") or {}).items()):
        d = d if isinstance(d, dict) else {"choice": d}
        sec, row = rows.get(rid, ({}, {}))
        suggested = row.get("suggested")
        out.append({
            "schema": DATASET_SCHEMA, "board": data["board"], "build": data.get("build"),
            "saved_at": data.get("savedAt"), "row": rid, "kind": _kind(data["board"], meta),
            "board_found": board is not None and bool(row),
            "section": sec.get("label"), "title": row.get("title"), "context": row.get("context"),
            "meta": row.get("meta"), "options": row.get("options"), "suggested": suggested,
            "choice": d.get("choice"), "note": d.get("note") or "",
            "followed_suggestion": (d.get("choice") == suggested) if suggested is not None else None,
        })
    return out


def _key(r: dict) -> tuple:
    return (r.get("board"), r.get("build"), r.get("saved_at"), r.get("row"))


def append_records(new: list[dict]) -> int:
    path = dataset_path()
    have = set()
    if path.exists():
        have = {_key(json.loads(l)) for l in path.read_text().splitlines() if l.strip()}
    fresh = [r for r in new if _key(r) not in have]
    if fresh:
        text = (path.read_text() if path.exists() else "") + "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in fresh)
        atomic_write_text(path, text)
        path.chmod(0o600)
    return len(fresh)


def backfill() -> int:
    added = 0
    for f in sorted(answers_dir().glob("*.decisions.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("board"):
            added += append_records(records(data))
    return added


def target_for(data: dict, src: Path, dest_dir: Path) -> Path:
    date = str(data.get("savedAt", ""))[:10] or "undated"
    stamp = str(data.get("savedAt", "")).replace(":", "").replace("-", "")[:15]
    base = dest_dir / f"{date}-{data['board']}.decisions.json"
    if not base.exists() or base.read_text() == src.read_text():
        return base
    return dest_dir / f"{date}-{data['board']}.{stamp or 'x'}.decisions.json"


def collect(downloads: Path, only: str | None, dry: bool) -> int:
    dest_dir = answers_dir()
    filed = 0
    for src in sorted(downloads.glob("*.decisions.json")):
        try:
            data = json.loads(src.read_text())
        except (OSError, ValueError) as e:
            print(f"skip {src.name}: not readable JSON ({e})")
            continue
        if not isinstance(data, dict) or not data.get("board"):
            print(f"skip {src.name}: no 'board' field")
            continue
        if only and data["board"] != only:
            continue
        dest = target_for(data, src, dest_dir)
        print(f"{'would file' if dry else 'filed'} {src.name} -> {dest}")
        if dry:
            continue
        text = src.read_text()
        atomic_write_text(dest, text)
        dest.chmod(0o600)
        if dest.read_text() != text:
            print(f"  copy differs; kept {src}")
            continue
        src.unlink()
        append_records(records(data))
        filed += 1
    return filed


def latest(slug: str) -> None:
    hits = sorted(answers_dir().glob(f"*-{slug}*.decisions.json"))
    if not hits:
        raise SystemExit(f"no filed answers for {slug}")
    print(hits[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--downloads", default=str(Path.home() / "Downloads"))
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--latest")
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    if a.backfill:
        print(f"added {backfill()} decision(s) to {dataset_path()}")
        return 0
    if a.latest:
        latest(a.latest)
        return 0
    collect(Path(a.downloads), a.only, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
