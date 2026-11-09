"""The standing context a Datacore agent really gets, for agent-behaviour evals.

An interactive Datacore session starts with two things every time:
  * ~/Data/CLAUDE.md (project instructions), and
  * every PINNED engram, injected by the PLUR session hook.
Unpinned engrams reach a session only if recall happens to rank them, so an
eval that hands them to the agent would test a delivery the system does not
guarantee. ``system_context()`` rebuilds exactly the first two, for a
scaffold's CLAUDE.md, so a conduct eval measures the rules as delivered.

Read-only: parses ~/.plur/engrams.yaml line by line (the file is large; a full
YAML load takes too long for an eval).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENGRAMS = Path(os.environ.get("PLUR_ENGRAMS", Path.home() / ".plur" / "engrams.yaml"))


def pinned_statements(path: Path = ENGRAMS) -> list[str]:
    """Statements of active, pinned engrams (the ones injected every session)."""
    if not path.is_file():
        return []
    out, cur, in_stmt, stmt = [], None, False, []

    def flush():
        if cur and cur.get("pinned") and cur.get("status", "active") == "active" and cur.get("statement"):
            out.append(cur["statement"])

    for line in path.open(encoding="utf-8", errors="replace"):
        if line.startswith("  - id: "):
            if in_stmt and cur is not None:
                cur["statement"] = " ".join(stmt).strip()
            flush()
            cur, in_stmt, stmt = {"id": line.split(":", 1)[1].strip()}, False, []
            continue
        if cur is None:
            continue
        m = re.match(r"^    ([a-z_]+):\s*(.*)$", line)
        if m:
            if in_stmt:
                cur["statement"] = " ".join(stmt).strip()
                in_stmt = False
            key, val = m.groups()
            if key == "statement":
                if val in (">-", ">", "|", "|-"):
                    in_stmt, stmt = True, []
                else:
                    cur["statement"] = val.strip("'\"")
            elif key == "pinned":
                cur["pinned"] = val.strip() == "true"
            elif key == "status":
                cur["status"] = val.strip()
        elif in_stmt and line.startswith("      "):
            stmt.append(line.strip())
    if in_stmt and cur is not None:
        cur["statement"] = " ".join(stmt).strip()
    flush()
    return out


def system_context() -> str:
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    pinned = pinned_statements()
    return (claude_md + "\n\n## Pinned memory (injected by PLUR every session)\n\n"
            + "\n".join(f"- {s}" for s in pinned) + "\n")


def write_context(scaffold: Path, extra: str = "") -> None:
    (scaffold / "CLAUDE.md").write_text(system_context() + (("\n" + extra) if extra else ""),
                                        encoding="utf-8")
