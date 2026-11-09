#!/usr/bin/env python3
"""Validate and render the nightly GitHub triage decision board.

The triage run (Miles, nightshift host) writes board DATA as JSON into the
owner's private personal space; it travels through git. This script checks
that data and renders it locally, on the owner's machine, with the shared
decision-board page. The page is never published anywhere.

  triage_board.py validate <board.json>
  triage_board.py render   <board.json>      # prints the path of the page
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "board-template.html"
ROW_FIELDS = ("id", "area", "title", "context", "options", "suggested", "meta")
META_FIELDS = ("slug", "title", "h1", "eyebrow", "lede", "asOf", "path")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")


def validate(board: dict) -> list:
    errs = []
    meta = board.get("meta") or {}
    for f in META_FIELDS:
        if not meta.get(f):
            errs.append(f"meta.{f} missing")
    if meta.get("slug") and not SLUG.match(meta["slug"]):
        errs.append("meta.slug must be lowercase letters, digits and dashes")
    ids = set()
    sections = board.get("sections")
    if not isinstance(sections, list) or not sections:
        return errs + ["sections missing or empty"]
    for s in sections:
        if not s.get("key") or not s.get("label"):
            errs.append("section without key/label")
        for r in s.get("rows") or []:
            rid = r.get("id", "?")
            for f in ROW_FIELDS:
                if f not in r:
                    errs.append(f"{rid}: {f} missing")
            if rid in ids:
                errs.append(f"{rid}: duplicate id")
            ids.add(rid)
            opts = r.get("options") or []
            if not 2 <= len(opts) <= 4:
                errs.append(f"{rid}: needs 2-4 options, has {len(opts)}")
            values = [o.get("value") for o in opts]
            if len(set(values)) != len(values):
                errs.append(f"{rid}: option values must be unique")
            for o in opts:
                if not (o.get("value") and o.get("label") and o.get("consequence") is not None):
                    errs.append(f"{rid}: option needs value, label and consequence")
            if r.get("suggested") not in values:
                errs.append(f"{rid}: suggested '{r.get('suggested')}' is not one of its options")
            for m in r.get("meta") or []:
                if isinstance(m, str) and "://" in m and not m.startswith(("https://", "http://")):
                    errs.append(f"{rid}: link must be http(s): {m[:40]}")
                if isinstance(m, str) and re.match(r"^\s*(javascript|data|file|vbscript):", m, re.I):
                    errs.append(f"{rid}: unsafe link scheme: {m[:40]}")
    return errs


def _blob(board: dict) -> str:
    s = json.dumps(board, ensure_ascii=False)
    return s.replace("<", "\\u003c").replace(chr(8232), "\\u2028").replace(chr(8233), "\\u2029")


def render(board: dict) -> Path:
    board = dict(board)
    board["meta"] = dict(board["meta"])
    board["meta"]["build"] = hashlib.sha256(
        json.dumps(board["sections"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    board.setdefault("prefill", {})
    page = TEMPLATE.read_text()
    title = board["meta"]["title"].replace("<", "&lt;").replace(">", "&gt;")
    page = page.replace("__BOARD_TITLE__", title, 1).replace("__BOARD_DATA__", _blob(board), 1)
    state = Path(os.environ.get("DATACORE_STATE") or Path.home() / ".datacore" / "state")
    out_dir = state / "decision-boards"
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    date = re.search(r"\d{4}-\d{2}-\d{2}", board["meta"]["slug"])
    name = board["meta"]["slug"] if date else f"{board['meta'].get('asOf', '')[:10]}-{board['meta']['slug']}"
    out = out_dir / f"{name}.html"
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(page)
    os.chmod(tmp, 0o600)
    os.replace(tmp, out)
    return out


def main(argv):
    if len(argv) != 3 or argv[1] not in ("validate", "render"):
        print(__doc__, file=sys.stderr)
        return 2
    board = json.loads(Path(argv[2]).read_text())
    errs = validate(board)
    if errs:
        print("INVALID board data:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 1
    if argv[1] == "validate":
        print("valid:", sum(len(s.get("rows") or []) for s in board["sections"]), "rows")
        return 0
    print(render(board))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
