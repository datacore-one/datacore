#!/usr/bin/env python3
"""Step tracker for multi-step commands (/today, /wrap-up, any numbered command).

The checklist lives in the day's journal, so it works the same from a shell, from
the Datacore MCP, in any agent harness and in unattended runs. A harness's own
checklist UI may mirror it; it is never the record.

    command_steps.py steps  <command> [--file PATH]
    command_steps.py start  <command> [--file PATH] [--space NAME] [--date D] [--run-id ID]
    command_steps.py tick   <run_id> <step> [<step> ...] [--note TEXT] [--space NAME]
    command_steps.py status <run_id> [--space NAME]
    command_steps.py resume <command> [--space NAME] [--date D]

Every subcommand prints JSON on stdout. ``resume`` prints ``null`` and exits 1
when there is no unfinished run.

A step is a numbered heading of the command file: ``## Step 8b: Title`` or
``### 3. Title``. Only the heading level of the first numbered heading counts,
fenced code is ignored, and ``0``-prefixed headings (preambles such as 0a-0f)
are not steps.

In the journal a run looks like::

    ## Command steps: /today (run today-20260928-081500-ab12)

    <!-- command-steps run=today-20260928-081500-ab12 command=today started=08:15:00 -->
    - [x] 1. Create Tracked Checklist — done 08:15:02
    - [ ] 2. Check for Existing Briefing

Every write goes through ``journal_store.update_journal`` (the shared journal
transaction), so concurrent writers never clobber each other.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from datetime import date as _date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import date_utils  # noqa: E402
import journal_store  # noqa: E402
from spaces import space_for  # noqa: E402

DEFAULT_SPACE = "personal"
HEADING = re.compile(r"^(#{2,4})\s+(?:Step\s+)?(\d+[a-z]?)[.:]\s+(.+?)\s*#*\s*$")
MARKER = re.compile(r"^<!-- command-steps run=(\S+) command=(\S+) started=(\S+) -->$")
LINE = re.compile(r"^- \[([ xX])\] (\S+?)\. (.*)$")
DONE = re.compile(r"^(.*?) — done (\d\d:\d\d:\d\d)(?: · (.*))?$")
RUN_DATE = re.compile(r"-(\d{4})(\d{2})(\d{2})-\d{6}-[0-9a-f]{4}$")


class StepError(ValueError):
    """A request the tracker cannot honour (unknown run, step or command)."""


# --- locations ----------------------------------------------------------------------

def root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT") or Path.home() / "Data")


def journal_for(day: str, space: str | None = None) -> Path:
    """The day's journal in ``space`` (a bare space name, never a numbered folder)."""
    base = root()
    folder = space_for(space or DEFAULT_SPACE, base, space or DEFAULT_SPACE)
    sdir = base / folder
    if not sdir.is_dir():
        raise StepError(f"no space named {space or DEFAULT_SPACE!r} in {base}")
    jdir = sdir / "notes" / "journals"
    return (jdir if jdir.is_dir() else sdir / "journal") / f"{day}.md"


def command_file(command: str) -> Path:
    """``.datacore/commands/<name>.md``, else a module's ``commands/<name>.md``
    (``module:name`` picks the module)."""
    base = root() / ".datacore"
    name = command.lstrip("/")
    core = base / "commands" / f"{name}.md"
    if core.is_file():
        return core
    module, _, short = name.rpartition(":")
    pattern = f"{module or '*'}/commands/{short}.md"
    found = sorted((base / "modules").glob(pattern)) if (base / "modules").is_dir() else []
    if found:
        return found[0]
    raise StepError(f"no command file for {command!r}")


# --- parsing ------------------------------------------------------------------------

def parse_steps(text: str) -> list[dict]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        text = text[end + 4:] if end != -1 else text
    steps, level, fenced = [], None, False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = HEADING.match(line)
        if not m or m.group(2).startswith("0"):
            continue
        if level is None:
            level = m.group(1)
        if m.group(1) == level and not any(s["id"] == m.group(2) for s in steps):
            steps.append({"id": m.group(2), "title": m.group(3).strip()})
    return steps


def _read_blocks(text: str) -> list[dict]:
    """Every tracked run in a journal, with the line index of each step."""
    lines, runs = text.split("\n"), []
    for i, line in enumerate(lines):
        m = MARKER.match(line.strip())
        if not m:
            continue
        run = {"run_id": m.group(1), "command": m.group(2), "started": m.group(3), "steps": []}
        for j in range(i + 1, len(lines)):
            s = LINE.match(lines[j])
            if not s:
                break
            rest = s.group(3)
            d = DONE.match(rest)
            run["steps"].append({
                "id": s.group(2),
                "title": d.group(1) if d else rest,
                "done": s.group(1) != " ",
                "at": d.group(2) if d else None,
                "note": (d.group(3) if d else None) or None,
                "line": j,
            })
        runs.append(run)
    return runs


def _summary(run: dict, journal: Path) -> dict:
    steps = [{k: v for k, v in s.items() if k != "line"} for s in run["steps"]]
    done = [s["id"] for s in steps if s["done"]]
    pending = [s["id"] for s in steps if not s["done"]]
    return {
        "run_id": run["run_id"], "command": run["command"], "started": run["started"],
        "journal": str(journal), "steps": steps, "done": done, "pending": pending,
        "next": pending[0] if pending else None, "complete": not pending,
        # The wrap-up report's `checklist` rows (wrap_up_report.py), one per step.
        "checklist": [{"step": s["id"], "title": s["title"],
                       "status": (s["note"] or "run ✓") if s["done"] else "not run"}
                      for s in steps],
    }


# --- operations ---------------------------------------------------------------------

def _slug(command: str) -> str:
    return re.sub(r"[^\w-]+", "-", command.lstrip("/")).strip("-") or "command"


def _day_of(run_id: str) -> str | None:
    m = RUN_DATE.search(run_id)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def start(command: str, *, file: str | None = None, space: str | None = None,
          date: str | None = None, run_id: str | None = None) -> dict:
    day = date or date_utils.today().isoformat()
    _date.fromisoformat(day)
    path = Path(file) if file else command_file(command)
    steps = parse_steps(path.read_text(encoding="utf-8"))
    if not steps:
        raise StepError(f"{path} has no numbered steps")
    now = datetime.now()
    rid = run_id or f"{_slug(command)}-{day.replace('-', '')}-{now:%H%M%S}-{secrets.token_hex(2)}"
    if any(c.isspace() for c in rid):
        raise StepError("run id may not contain whitespace")
    journal = journal_for(day, space)
    cmd = _slug(command)
    block = "\n".join(
        [f"## Command steps: /{cmd} (run {rid})", "",
         f"<!-- command-steps run={rid} command={cmd} started={now:%H:%M:%S} -->"]
        + [f"- [ ] {s['id']}. {s['title']}" for s in steps]) + "\n"

    def add(text: str | None) -> str:
        if text is None:
            text = f"---\ndate: {day}\ntype: daily\n---\n"
        if any(r["run_id"] == rid for r in _read_blocks(text)):
            return text
        return text.rstrip("\n") + "\n\n" + block

    journal.parent.mkdir(parents=True, exist_ok=True)
    journal_store.update_journal(journal, add)
    out = status(rid, space=space, day=day)
    out["previous_unfinished"] = [r["run_id"] for r in _unfinished(command, day, space)
                                  if r["run_id"] != rid]
    return out


def _locate(run_id: str, space: str | None, day: str | None) -> tuple[Path, dict]:
    days = [day] if day else ([_day_of(run_id)] if _day_of(run_id) else [])
    if not days:
        today = date_utils.today()
        days = [today.isoformat(), date_utils.add_days(today, -1).isoformat()]
    for d in days:
        journal = journal_for(d, space)
        text = journal_store.read_journal(journal)
        for run in _read_blocks(text or ""):
            if run["run_id"] == run_id:
                return journal, run
    raise StepError(f"no run {run_id!r} in the journal for {', '.join(days)}")


def status(run_id: str, *, space: str | None = None, day: str | None = None) -> dict:
    journal, run = _locate(run_id, space, day)
    return _summary(run, journal)


def tick(run_id: str, step_ids: list[str], *, note: str | None = None,
         space: str | None = None) -> dict:
    journal, run = _locate(run_id, space, None)
    known = {s["id"] for s in run["steps"]}
    unknown = [s for s in step_ids if s not in known]
    if unknown:
        raise StepError(f"run {run_id} has no step {', '.join(unknown)}")
    if note is not None and ("\n" in note or not note.strip()):
        raise StepError("a note is one non-empty line")
    stamp = datetime.now().strftime("%H:%M:%S")

    def mark(text: str | None) -> str:
        if text is None:
            raise StepError(f"{journal} disappeared")
        lines = text.split("\n")
        current = next((r for r in _read_blocks(text) if r["run_id"] == run_id), None)
        if current is None:
            raise StepError(f"run {run_id} is no longer in {journal}")
        for s in current["steps"]:
            if s["id"] in step_ids and not s["done"]:
                suffix = f" — done {stamp}" + (f" · {note.strip()}" if note else "")
                lines[s["line"]] = f"- [x] {s['id']}. {s['title']}{suffix}"
        return "\n".join(lines)

    journal_store.update_journal(journal, mark)
    return status(run_id, space=space)


def _unfinished(command: str, day: str, space: str | None) -> list[dict]:
    journal = journal_for(day, space)
    text = journal_store.read_journal(journal) or ""
    cmd = _slug(command)
    return [r for r in _read_blocks(text)
            if r["command"] == cmd and any(not s["done"] for s in r["steps"])]


def resume(command: str, *, space: str | None = None, date: str | None = None) -> dict | None:
    """The latest unfinished run of ``command`` on ``date`` (today by default)."""
    day = date or date_utils.today().isoformat()
    runs = _unfinished(command, day, space)
    return _summary(runs[-1], journal_for(day, space)) if runs else None


# --- CLI ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="command_steps.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="op", required=True)
    a = sub.add_parser("steps", help="list a command's numbered steps")
    a.add_argument("command")
    a.add_argument("--file")
    a = sub.add_parser("start", help="write the run's checklist into the day's journal")
    a.add_argument("command")
    a.add_argument("--file")
    a.add_argument("--space")
    a.add_argument("--date")
    a.add_argument("--run-id")
    a = sub.add_parser("tick", help="mark steps done, with a timestamp")
    a.add_argument("run_id")
    a.add_argument("step", nargs="+")
    a.add_argument("--note", help="one-line status, e.g. skipped-by-user")
    a.add_argument("--space")
    a = sub.add_parser("status", help="done and pending steps of a run")
    a.add_argument("run_id")
    a.add_argument("--space")
    a = sub.add_parser("resume", help="the latest unfinished run of a command")
    a.add_argument("command")
    a.add_argument("--space")
    a.add_argument("--date")
    args = p.parse_args(argv)
    try:
        if args.op == "steps":
            path = Path(args.file) if args.file else command_file(args.command)
            out = parse_steps(path.read_text(encoding="utf-8"))
        elif args.op == "start":
            out = start(args.command, file=args.file, space=args.space, date=args.date,
                        run_id=args.run_id)
        elif args.op == "tick":
            out = tick(args.run_id, args.step, note=args.note, space=args.space)
        elif args.op == "status":
            out = status(args.run_id, space=args.space)
        else:
            out = resume(args.command, space=args.space, date=args.date)
    except (StepError, OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}))
        print(f"command_steps: {error}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if args.op == "resume" and out is None else 0


if __name__ == "__main__":
    raise SystemExit(main())
