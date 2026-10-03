"""Sandbox harness for the Phase 4 org evals (ledger upgrade O1-O3).

Every eval here runs on a SANDBOX COPY of a real Phase-1 space: its ledger
(`.datacore/events`, phase and edit-protocol markers, projection base) and its
two org files are copied into a temporary root, and the cycle runs there. The
live space is only ever read. `source_digest` lets each eval prove that: it is
taken before and after, and the conftest fails the eval if the two differ.

The cycle is the per-space body of `ledger_ingest_org.main()` followed by
`ledger_project_org.project_space()`, in the order `ledger_phase1_cycle.sh` runs
them (ingest, then project; a space whose ingest failed is not projected). It
calls the functions in-process rather than the scripts, so that nothing posts
to the host's daemon (`_notify_daemon`) and no real state directory is used.

"The space stopped" means exactly what it means on a host: its ingest raised
(the cycle log's `FAILED:` line, projection skipped) or its projection was
REFUSED. Either way nothing else in that space moves until someone intervenes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[4]
LIB = INSTALL / ".datacore" / "lib"

#: The space the drill copies, by ROLE (shipped files name no space folder).
#: The audit (B, F3) replayed its gestures on this same space.
DRILL_ROLE = os.environ.get("LEDGER_DRILL_ROLE", "practice")

HUMAN = "drill-human"     # the person editing in Emacs or Obsidian
AGENT = "drill-agent"     # an agent writing to the ledger on another host

ORG_FILES = ("next_actions.org", "inbox.org")
_COPY_IGNORE = shutil.ignore_patterns("knowledge.db", "checkpoints", "learning", "*.lock")


# ── the live source, read only ────────────────────────────────────────────────

def live_space() -> Path:
    out = subprocess.run(["python3", str(LIB / "spaces.py"), "role", DRILL_ROLE, "--root", str(INSTALL)],
                         capture_output=True, text=True, timeout=60)
    name = out.stdout.strip()
    if out.returncode or not name:
        raise RuntimeError(f"SETUP: no space has the role {DRILL_ROLE!r} in {INSTALL}: {out.stderr.strip()}")
    space = INSTALL / name
    if not (space / ".datacore" / "events").is_dir():
        raise RuntimeError(f"SETUP: the {DRILL_ROLE} space has no ledger")
    if (space / ".datacore" / "ledger-phase").read_text().strip() != "1":
        raise RuntimeError(f"SETUP: the {DRILL_ROLE} space is not in Phase 1")
    return space


def source_files(space: Path) -> list[Path]:
    files = [space / "org" / n for n in ORG_FILES if (space / "org" / n).exists()]
    files += sorted(p for p in (space / ".datacore").rglob("*")
                    if p.is_file() and not any(part in ("knowledge.db", "checkpoints", "learning")
                                               for part in p.relative_to(space / ".datacore").parts))
    return files


def source_digest(space: Path) -> dict[str, str]:
    """sha256 of every file the drill copies from the live space."""
    return {str(p.relative_to(space)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_files(space)}


def copy_space(src: Path, root: Path) -> Path:
    """A sandbox copy of `src` under `root`. The source is only read."""
    root = Path(os.path.realpath(root))
    if INSTALL in root.parents or root == INSTALL:
        raise RuntimeError("SETUP: the sandbox must not live inside the installation")
    dst = root / src.name
    (dst / "org").mkdir(parents=True)
    for n in ORG_FILES:
        if (src / "org" / n).exists():
            shutil.copy2(src / "org" / n, dst / "org" / n)
    shutil.copytree(src / ".datacore", dst / ".datacore", ignore=_COPY_IGNORE)
    return dst


# ── one cycle ─────────────────────────────────────────────────────────────────

@dataclass
class Cycle:
    ingest_error: str | None = None
    project: str | None = None
    sync: dict = field(default_factory=dict)

    @property
    def stopped(self) -> bool:
        return self.ingest_error is not None or (self.project or "").startswith("REFUSED")

    @property
    def reason(self) -> str:
        if self.ingest_error:
            return f"ingest FAILED: {self.ingest_error}"
        return f"project: {self.project}"


def cycle(space: Path) -> Cycle:
    import ledger_ingest_org as ing
    import ledger_project_org as prj
    from ledger.genesis import import_space, scan
    from ledger_dismiss_orphans import confirm_and_dismiss
    out = Cycle()
    try:
        ing.ensure_ids(space)
        if scan(space).importable:
            import_space(space, actor=HUMAN)
        out.sync = ing.sync_state(space, actor=HUMAN)
        confirm_and_dismiss(space, time.time(), execute=True)
    except Exception as exc:  # noqa: BLE001 - this is the cycle's own per-space catch
        out.ingest_error = f"{type(exc).__name__}: {exc}"
        return out
    out.project = prj.project_space(space)
    return out


def state(space: Path):
    from ledger.fold import fold
    from ledger.log import read_events
    return fold(read_events(space))


def events(space: Path) -> list:
    from ledger.log import read_events
    return list(read_events(space))


def agent_edit(space: Path, item_id: str, fields: dict) -> None:
    """An agent's conditional edit arriving in the ledger, as the adapter writes one."""
    from ledger.edits import conditional_payload
    from ledger.log import EventLog
    item = state(space).items[item_id]
    EventLog(space, AGENT, sign=False).append("item.update", conditional_payload(item, fields))


def base_text(space: Path) -> str | None:
    p = space / ".datacore" / "state" / "projection" / "last-rendered.json"
    try:
        return json.loads(p.read_text())["text"]
    except FileNotFoundError:
        return None


# ── org text, edited the way a person edits it ────────────────────────────────

HEADING = re.compile(r"^(\*+) ")


def org(space: Path) -> Path:
    return space / "org" / "next_actions.org"


def read(space: Path) -> str:
    return org(space).read_text(encoding="utf-8")


def write(space: Path, text: str) -> None:
    org(space).write_text(text, encoding="utf-8")


def _lines(text: str) -> list[str]:
    return text.split("\n")


def block(text: str, item_id: str) -> tuple[int, int]:
    """(start, end) line indexes of the heading whose OWN drawer has `:ID: item_id`, subtree included."""
    lines = _lines(text)
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m:
            continue
        level = len(m.group(1))
        j = i + 1
        own = True
        found = False
        while j < len(lines):
            mm = HEADING.match(lines[j])
            if mm and len(mm.group(1)) <= level:
                break
            if mm:
                own = False
            if own and re.match(rf"^\s*:ID:\s*{re.escape(item_id)}\s*$", lines[j]):
                found = True
            j += 1
        if found:
            return i, j
    raise KeyError(f"no heading with :ID: {item_id}")


def heading_order(text: str) -> list[str]:
    """Item ids in file order."""
    return re.findall(r"^\s*:ID:\s*(\S+)\s*$", text, re.M)


def edit_heading(text: str, item_id: str, fn) -> str:
    lines = _lines(text)
    s, _ = block(text, item_id)
    lines[s] = fn(lines[s])
    return "\n".join(lines)


def set_state(text: str, item_id: str, new: str) -> str:
    return edit_heading(text, item_id,
                        lambda h: re.sub(r"^(\*+) (TODO|NEXT|WAITING|REVIEW|DONE|DEFERRED|CANCELLED) ",
                                         rf"\1 {new} ", h))


def retitle(text: str, item_id: str, new_title: str) -> str:
    def fn(h):
        m = re.match(r"^(\*+ (?:TODO|NEXT|WAITING|REVIEW|DONE|DEFERRED|CANCELLED) (?:\[#[A-C]\] )?)(.*?)(\s+:[\w@:]+:)?$", h)
        return f"{m.group(1)}{new_title}{m.group(3) or ''}"
    return edit_heading(text, item_id, fn)


def add_property(text: str, item_id: str, key: str, value: str) -> str:
    lines = _lines(text)
    s, e = block(text, item_id)
    for k in range(s, e):
        if re.match(rf"^\s*:ID:\s*{re.escape(item_id)}\s*$", lines[k]):
            indent = re.match(r"^(\s*)", lines[k]).group(1)
            lines.insert(k, f"{indent}:{key}: {value}")
            return "\n".join(lines)
    raise KeyError(item_id)


def append_body(text: str, item_id: str, body_lines: list[str]) -> str:
    lines = _lines(text)
    s, e = block(text, item_id)
    end = e
    while end > s + 1 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = body_lines
    return "\n".join(lines)


def after_drawer(text: str, item_id: str, new_lines: list[str]) -> str:
    """Insert lines right after the item's PROPERTIES drawer (where Emacs puts LOGBOOK)."""
    lines = _lines(text)
    s, e = block(text, item_id)
    for k in range(s, e):
        if lines[k].strip() == ":END:":
            lines[k + 1:k + 1] = new_lines
            return "\n".join(lines)
    raise KeyError(item_id)


def remove_block(text: str, item_id: str) -> tuple[str, str]:
    lines = _lines(text)
    s, e = block(text, item_id)
    removed = "\n".join(lines[s:e])
    return "\n".join(lines[:s] + lines[e:]), removed


def insert_after(text: str, item_id: str, new_block: str) -> str:
    lines = _lines(text)
    _, e = block(text, item_id)
    lines[e:e] = new_block.rstrip("\n").split("\n") + [""]
    return "\n".join(lines)


def swap(text: str, a: str, b: str) -> str:
    lines = _lines(text)
    sa, ea = block(text, a)
    sb, eb = block(text, b)
    if sa > sb:
        (sa, ea), (sb, eb) = (sb, eb), (sa, ea)
    return "\n".join(lines[:sa] + lines[sb:eb] + lines[ea:sb] + lines[sa:ea] + lines[eb:])


def pick_items(space: Path, n: int = 3) -> list[str]:
    """`n` live, level-1, childless TODO items, in file order -- the ones a gesture targets."""
    st = state(space)
    text = read(space)
    out = []
    for item_id in heading_order(text):
        item = st.items.get(item_id)
        if item is None or item.status == "dismissed":
            continue
        s, e = block(text, item_id)
        first = _lines(text)[s]
        if not re.match(r"^\* (TODO|NEXT|WAITING) ", first):
            continue
        if any(HEADING.match(l) for l in _lines(text)[s + 1:e]):
            continue
        out.append(item_id)
        if len(out) == n:
            return out
    raise RuntimeError(f"SETUP: the copy has fewer than {n} childless level-1 open tasks")


def conflict_named(space: Path, item_id: str) -> bool:
    """A recorded, named conflict for THIS item: retained in the ledger, or rendered in the file."""
    item = state(space).items.get(item_id)
    if item is not None and item.edit_conflicts:
        return True
    text = read(space)
    for m in re.finditer(r"^\*+ .*:conflict:.*$", text, re.M):
        s = m.start()
        nxt = re.search(r"^\* ", text[m.end():], re.M)
        chunk = text[s: m.end() + (nxt.start() if nxt else len(text) - m.end())]
        if item_id in chunk:
            return True
    return False
