#!/usr/bin/env python3
"""firm_journal_line.py -- one line per agent per day in the shared journal.

Run as a script cadence (`firm-journal-line`, templates/cadences/): the cadence
runner starts it in the shared space with DATACORE_POLICY_PRINCIPAL set to the
agent, so no model is asked. The line is built only from the cadence-run end
events (`metric.attest`, metric `cadence.run`, phase `end`) the agent signed in
every space's ledger that day, and says plainly when there were none.

It is written under one section of `journal/<date>.md` in the space it runs in.
A rerun replaces that agent's line; nothing else on the page is touched.

    firm_journal_line.py [--date YYYY-MM-DD] [--actor NAME] [--space DIR] [--root DIR]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SECTION = "## Agent day lines"
MAX_NAMED = 3


def _day_of(hlc: str) -> str | None:
    try:
        ms = int(str(hlc).split(".", 1)[0])
    except ValueError:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _ended_runs(root: Path, actor: str, day: str) -> list[dict]:
    from ledger.log import read_events
    runs = []
    for space in sorted(Path(root).glob("[0-9]*-*")):
        if not (space / ".datacore").is_dir():
            continue
        for ev in read_events(space):
            p = ev.payload if isinstance(ev.payload, dict) else {}
            if (ev.type == "metric.attest" and ev.actor == actor and p.get("metric") == "cadence.run"
                    and p.get("phase") == "end" and _day_of(ev.hlc) == day):
                runs.append(p)
    return runs


def day_line(root: Path, actor: str, day: str) -> str:
    runs = _ended_runs(root, actor, day)
    if not runs:
        return f"- **{actor}** — no cadence run ended today (none recorded in the ledger)"
    counts = Counter(str(r.get("result") or "unknown") for r in runs)
    parts = [f"{counts.pop('ok', 0)} ok"] + [f"{n} {k}" for k, n in sorted(counts.items())]
    line = f"- **{actor}** — {len(runs)} cadence run(s) ended: {', '.join(parts)}"
    bad = [r for r in runs if r.get("result") != "ok"]
    if bad:
        named = "; ".join(f"{r.get('slug', '?')} ({str(r.get('reason') or r.get('result'))[:80]})"
                          for r in bad[:MAX_NAMED])
        more = f"; +{len(bad) - MAX_NAMED} more" if len(bad) > MAX_NAMED else ""
        line += f". Not ok: {named}{more}"
    return line


def write_line(space: Path, day: str, line: str, actor: str) -> Path:
    from file_utils import atomic_write_text
    page = Path(space) / "journal" / f"{day}.md"
    text = page.read_text(encoding="utf-8") if page.exists() else f"# {day}\n"
    lines = text.rstrip("\n").split("\n")
    prefix = f"- **{actor}** "
    if SECTION not in lines:
        lines += ["", SECTION, ""]
    start = lines.index(SECTION) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    mine = [i for i in range(start, end) if lines[i].startswith(prefix)]
    if mine:
        lines[mine[0]] = line
        for i in reversed(mine[1:]):
            del lines[i]
    else:
        insert = end
        while insert > start and not lines[insert - 1].strip():
            insert -= 1
        if insert == start:  # empty section: keep one blank line under the heading
            lines.insert(start, "")
            insert = start + 1
        lines.insert(insert, line)
    atomic_write_text(page, "\n".join(lines) + "\n")
    return page


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--actor", default=None)
    ap.add_argument("--space", type=Path, default=Path.cwd())
    ap.add_argument("--root", type=Path, default=None)
    a = ap.parse_args(argv)
    root = a.root or Path(os.environ.get("DATACORE_ROOT") or Path.home() / "Data")
    actor = a.actor or os.environ.get("DATACORE_POLICY_PRINCIPAL")
    if not actor:
        from actor_identity import this_actor
        actor = this_actor(strict=True)
    line = day_line(root, actor, a.date)
    page = write_line(a.space, a.date, line, actor)
    print(f"firm_journal_line: {page}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
