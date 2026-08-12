#!/usr/bin/env python3
"""What may an unattended run commit? (DIP-0046 E3)

`git_commit_push` ran `git add -A`. An overnight task that edits one report
therefore commits whatever else happens to be in the tree — a half-finished
edit left open on the Mac, another agent's scratch file, a credential someone
dropped in to test with. The task's own diff is correct; everything travelling
with it is unreviewed, and it is committed under the task's message, which is
how it stops looking like anything worth checking.

The rule is narrow on purpose: **an unattended run commits what it produced,
and nothing else.** Anything else in the tree is not discarded, not committed,
and not silently ignored — it becomes a PENDING DECISION, a file naming exactly
what was found and which task found it, for a human to resolve.

Why a file rather than a prompt: nightshift runs ~20 tasks with nobody awake.
A gate that blocks on an answer nobody is there to give converts one unreviewed
commit into a stalled queue, which is worse. So the run continues, having
committed only its own output, and the backlog is counted and alerted
(`detectors/pending_decisions.py`) so the operator learns about it in the
morning rather than discovering it in a diff three weeks later.

Deliberately NOT here: any attempt to judge whether the extra changes are
*good*. This gate answers "did this run produce it?", which is a question with
an answer. "Is this change safe?" is not, and a gate that pretends otherwise
would be trusted for a guarantee it cannot make (DIP-0046 E1: the defence is
check strength, not the appearance of one).
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PENDING = Path.home() / ".datacore" / "state" / "commit-decisions"


@dataclass
class Decision:
    """What the caller is permitted to commit, and what it must not."""
    allowed: list[str] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)
    record: Path | None = None

    @property
    def clean(self) -> bool:
        return not self.withheld


def dirty_paths(repo: Path) -> list[str]:
    """Every path git reports as changed, staged or not, tracked or not."""
    r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                       capture_output=True, text=True, timeout=120)
    out = []
    for line in (r.stdout or "").splitlines():
        if len(line) < 4:
            continue
        p = line[3:]
        # Renames read "old -> new"; the new name is what would be committed.
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        out.append(p.strip().strip('"'))
    return out


def decide(repo: Path, produced: list[str] | None, *,
           task_id: str = "unknown", actor: str = "unknown",
           at: str = "") -> Decision:
    """Split the dirty tree into what this run made and what it merely found.

    `produced=None` means the caller did not declare its outputs. That is
    RECORDED, NOT BLOCKED, and the distinction cost a production batch:

    Withholding everything in that case looked principled — "not knowing what
    you made is the case this exists for" — but every real caller in
    nightshift's run.py calls git_commit_push(repo, message) with no file list.
    The result on 2026-08-12 was eight tasks executed and NOTHING committed:
    their own output files, their ledger events and their org state updates all
    withheld, with `committed: []` in every decision record. A gate that stops
    the system doing its job is not a safety feature.

    So the strict guarantee — anything you did not declare is withheld — applies
    where a caller DECLARES its outputs. Where it does not, the gate degrades to
    an audit trail: the commit proceeds and the record says exactly what went in
    under that message, which is still strictly more than existed before.
    """
    dirty = dirty_paths(repo)
    if not dirty:
        return Decision()

    if produced is None:
        dec = Decision(allowed=list(dirty), withheld=[])
        dec.record = _record(repo, dec, task_id=task_id, actor=actor, at=at)
        return dec

    wanted = set(produced or [])
    allowed = [p for p in dirty if p in wanted]
    withheld = [p for p in dirty if p not in wanted]
    dec = Decision(allowed=allowed, withheld=withheld)
    if withheld:
        dec.record = _record(repo, dec, task_id=task_id, actor=actor, at=at)
    return dec


def _record(repo: Path, dec: Decision, *, task_id: str, actor: str, at: str) -> Path:
    """Persist the decision. An audit artifact, not a log line: it has to be
    countable by a detector and resolvable by a human, and a line in a rotating
    log is neither."""
    PENDING.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_id)[:60]
    stamp = at or _now()
    path = PENDING / f"{stamp}-{safe}.json"
    path.write_text(json.dumps({
        "repo": str(repo), "task_id": task_id, "actor": actor, "at": stamp,
        "committed": dec.allowed, "withheld": dec.withheld,
        "note": "Unattended run found changes it did not produce. Nothing was "
                "discarded; review and commit or revert by hand.",
    }, indent=2))
    return path


def _now() -> str:
    # Timestamps come from the caller where possible so a run's artifacts share
    # one stamp; this is the fallback.
    from datetime import datetime
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def pending() -> list[dict]:
    """Unresolved decisions. Resolution is deleting the file, which is the
    cheapest possible affordance and needs no tooling to exist first."""
    if not PENDING.is_dir():
        return []
    out = []
    for f in sorted(PENDING.glob("*.json")):
        try:
            out.append({**json.loads(f.read_text()), "_file": str(f)})
        except (OSError, ValueError):
            out.append({"_file": str(f), "at": "", "task_id": "<unreadable>",
                        "withheld": []})
    return out


def enabled() -> bool:
    """On unless explicitly disabled. A safety gate defaulting to off is a
    safety gate that is off."""
    return os.environ.get("DATACORE_COMMIT_GATE", "1") != "0"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="commit-decision gate")
    ap.add_argument("op", choices=["check", "pending"])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--produced", nargs="*", default=None)
    a = ap.parse_args()

    if a.op == "pending":
        rows = pending()
        for r in rows:
            print(f"  {r.get('at','?'):<16} {r.get('task_id','?'):<28} "
                  f"{len(r.get('withheld') or [])} withheld")
        print(f"\ncommit-decisions: {len(rows)} pending")
        raise SystemExit(1 if rows else 0)

    d = decide(a.repo, a.produced, task_id="cli")
    print(json.dumps({"allowed": d.allowed, "withheld": d.withheld,
                      "record": str(d.record) if d.record else None}, indent=2))
    raise SystemExit(0 if d.clean else 1)
